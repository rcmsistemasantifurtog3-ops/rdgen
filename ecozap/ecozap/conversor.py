"""Transforma o que chegou da impressora virtual em PDF, JPEG ou PNG.

Entradas aceitas:
- PDF (driver "Microsoft Print to PDF")       -> mantém o layout original
- Texto / ESC-POS (driver "Generic / Text Only" ou impressão RAW do PDV)
                                              -> monta um cupom em PDF
- Imagem (PNG/JPEG)                            -> embala em PDF
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from fpdf import FPDF
from PIL import Image

from . import escpos

# largura útil (mm) e margem de cada tamanho de papel
PAPEIS = {
    "58mm": {"largura": 58, "margem": 3},
    "80mm": {"largura": 80, "margem": 4},
    "a4": {"largura": 210, "margem": 15},
}
DPI_IMAGEM = 200
_MARCA_QR = re.compile(r"^\s*\[\[QR:(.*)\]\]\s*$")


def _tamanho_qr(papel: str, util: float) -> float:
    return min(util * 0.7, 40.0 if papel == "a4" else 34.0)


def _imagem_qr(conteudo: str):
    import qrcode
    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(conteudo)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")


@dataclass
class Documento:
    pdf: bytes
    texto: str          # texto extraído (para achar telefone, busca etc.)
    origem: str         # pdf | texto | imagem
    paginas: int


def detectar_tipo(dados: bytes) -> str:
    if dados[:5] == b"%PDF-":
        return "pdf"
    if dados[:8] == b"\x89PNG\r\n\x1a\n" or dados[:3] == b"\xff\xd8\xff":
        return "imagem"
    return "texto"


def decodificar_texto(dados: bytes, codificacao: str = "auto") -> str:
    if escpos.parece_escpos(dados):
        dados = escpos.limpar(dados)
    dados = dados.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if codificacao != "auto":
        return dados.decode(codificacao, errors="replace")
    try:
        return dados.decode("utf-8")
    except UnicodeDecodeError:
        # Windows (Generic / Text Only) usa cp1252; térmicas antigas usam cp850.
        # cp1252 deixa acentos de cp850 como símbolos estranhos; testamos ambos
        # e ficamos com o que gerar mais letras acentuadas "normais".
        candidatos = [dados.decode(c, errors="replace") for c in ("cp850", "cp1252")]
        acentos = set("áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ")
        return max(candidatos, key=lambda t: sum(ch in acentos for ch in t))


def _latin1(texto: str) -> str:
    """As fontes padrão do PDF só aceitam latin-1; troca o resto por '?'."""
    return texto.encode("latin-1", errors="replace").decode("latin-1")


def texto_para_pdf(texto: str, papel: str = "80mm", rodape: str = "") -> bytes:
    """Monta um cupom em PDF com fonte monoespaçada (igual à térmica)."""
    cfg = PAPEIS[papel]
    linhas = texto.rstrip("\n").split("\n")
    while linhas and not linhas[0].strip():
        linhas.pop(0)
    util = cfg["largura"] - 2 * cfg["margem"]

    # Escolhe o tamanho de fonte para a maior linha caber na largura.
    # Courier: cada caractere tem 0,6 x tamanho da fonte (em pt); 1pt = 0,3528mm
    maior = max((len(l) for l in linhas if not _MARCA_QR.match(l)), default=1) or 1
    fonte = min(10.0 if papel == "a4" else 9.0, util / (maior * 0.6 * 0.3528))
    fonte = max(fonte, 5.0)
    altura_linha = fonte * 0.3528 * 1.25

    if papel == "a4":
        pdf = FPDF(format="A4")
        pdf.set_auto_page_break(True, margin=cfg["margem"])
    else:
        # bobina: uma página só, do tamanho exato do cupom
        extra = 12 if rodape else 4
        qrs = sum(1 for l in linhas if _MARCA_QR.match(l))
        altura = (2 * cfg["margem"] + altura_linha * (len(linhas) - qrs)
                  + qrs * (_tamanho_qr(papel, util) + 2) + extra)
        pdf = FPDF(format=(cfg["largura"], max(altura, 40)))
        pdf.set_auto_page_break(False)
    pdf.set_margins(cfg["margem"], cfg["margem"], cfg["margem"])
    pdf.add_page()
    pdf.set_font("Courier", size=fonte)
    for linha in linhas:
        qr = _MARCA_QR.match(linha)
        if qr:
            lado = _tamanho_qr(papel, util)
            if papel == "a4" and pdf.get_y() + lado > pdf.h - cfg["margem"]:
                pdf.add_page()
            pdf.image(_imagem_qr(qr.group(1)), x=(cfg["largura"] - lado) / 2,
                      y=pdf.get_y() + 1, w=lado, h=lado)
            pdf.set_y(pdf.get_y() + lado + 2)
            continue
        pdf.cell(util, altura_linha, _latin1(linha.expandtabs(4)),
                 new_x="LMARGIN", new_y="NEXT")
    if rodape:
        pdf.ln(3)
        pdf.set_draw_color(46, 125, 50)
        pdf.line(cfg["margem"], pdf.get_y(), cfg["largura"] - cfg["margem"], pdf.get_y())
        pdf.ln(1.5)
        pdf.set_font("Helvetica", "I", size=min(fonte, 7))
        pdf.set_text_color(46, 125, 50)
        pdf.multi_cell(util, altura_linha, _latin1(rodape), align="C")
    pdf.set_title("Comprovante")
    pdf.set_creator("EcoZap")
    return bytes(pdf.output())


def imagem_para_pdf(dados: bytes) -> bytes:
    img = Image.open(io.BytesIO(dados)).convert("RGB")
    saida = io.BytesIO()
    img.save(saida, "PDF", resolution=DPI_IMAGEM)
    return saida.getvalue()


def texto_do_pdf(dados: bytes) -> tuple[str, int]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(dados)
    try:
        partes = [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
        return "\n".join(partes), len(doc)
    finally:
        doc.close()


def preparar(dados: bytes, papel: str = "80mm", rodape: str = "",
             codificacao: str = "auto") -> Documento:
    """Converte qualquer entrada para um Documento com PDF + texto."""
    tipo = detectar_tipo(dados)
    if tipo == "pdf":
        texto, paginas = texto_do_pdf(dados)
        return Documento(dados, texto, "pdf", paginas)
    if tipo == "imagem":
        return Documento(imagem_para_pdf(dados), "", "imagem", 1)
    texto = decodificar_texto(dados, codificacao)
    pdf = texto_para_pdf(texto, papel, rodape)
    return Documento(pdf, texto, "texto", texto_do_pdf(pdf)[1])


def pdf_para_imagem(pdf: bytes, formato: str = "jpeg", dpi: int = DPI_IMAGEM) -> bytes:
    """Renderiza o PDF em imagem. Várias páginas viram uma imagem comprida
    (fica bom de ler no celular, rolando para baixo)."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    try:
        paginas = [doc[i].render(scale=dpi / 72).to_pil().convert("RGB")
                   for i in range(len(doc))]
    finally:
        doc.close()
    largura = max(p.width for p in paginas)
    altura = sum(p.height for p in paginas)
    final = Image.new("RGB", (largura, altura), "white")
    y = 0
    for p in paginas:
        final.paste(p, (0, y))
        y += p.height
    saida = io.BytesIO()
    if formato == "png":
        final.save(saida, "PNG", optimize=True)
    else:
        final.save(saida, "JPEG", quality=88, optimize=True)
    return saida.getvalue()


def exportar(doc: Documento, formato: str) -> tuple[bytes, str, str]:
    """Devolve (conteúdo, extensão, mimetype) no formato pedido."""
    if formato == "pdf":
        return doc.pdf, "pdf", "application/pdf"
    if formato == "png":
        return pdf_para_imagem(doc.pdf, "png"), "png", "image/png"
    return pdf_para_imagem(doc.pdf, "jpeg"), "jpg", "image/jpeg"


def miniatura(pdf: bytes, largura_max: int = 320) -> Image.Image:
    """Imagem pequena da 1ª página, para a tela de pré-visualização."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    try:
        img = doc[0].render(scale=2).to_pil().convert("RGB")
    finally:
        doc.close()
    img.thumbnail((largura_max, largura_max * 4))
    return img

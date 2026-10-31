"""Relatório mensal "papel economizado" (PDF) + imagem para redes sociais.

Os números de equivalência são ESTIMATIVAS e estão identificados assim:
- bobina térmica padrão: 80 mm x 40 m  (= 3.200 cm² de papel)
- gramatura do papel térmico: ~60 g/m²
"""
from __future__ import annotations

import calendar
import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

from .armazenamento import Armazenamento
from .icone import desenhar_folha

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
CM2_BOBINA = 8 * 4000
GRAMAS_POR_M2 = 60
VERDE = (46, 125, 50)
VERDE_CLARO = (200, 230, 201)


@dataclass
class ResumoMes:
    ano: int
    mes: int
    loja: str
    dias: list[dict]          # um item por dia do mês: {"dia", "enviados", "papel_mm", "papel_cm2"}

    @property
    def titulo(self) -> str:
        return f"{MESES[self.mes - 1]}/{self.ano}"

    @property
    def enviados(self) -> int:
        return sum(d["enviados"] for d in self.dias)

    @property
    def metros(self) -> float:
        return round(sum(d["papel_mm"] for d in self.dias) / 1000, 2)

    @property
    def cm2(self) -> float:
        return sum(d["papel_cm2"] for d in self.dias)

    @property
    def bobinas(self) -> float:
        return round(self.cm2 / CM2_BOBINA, 1)

    @property
    def gramas(self) -> float:
        return round(self.cm2 / 10000 * GRAMAS_POR_M2)


def mes_de_texto(texto: str | None) -> tuple[int, int]:
    """'2026-10' -> (2026, 10). Vazio = mês atual."""
    if not texto:
        hoje = date.today()
        return hoje.year, hoje.month
    try:
        ano, mes = (int(x) for x in texto.split("-"))
        date(ano, mes, 1)
    except ValueError as e:
        raise ValueError("Mês inválido. Use o formato AAAA-MM, ex.: 2026-10") from e
    return ano, mes


def resumir(arm: Armazenamento, ano: int, mes: int, loja: str) -> ResumoMes:
    ultimo = calendar.monthrange(ano, mes)[1]
    linhas = {l["dia"]: l for l in arm.estatisticas_periodo(date(ano, mes, 1), date(ano, mes, ultimo))}
    dias = []
    for d in range(1, ultimo + 1):
        chave = date(ano, mes, d).isoformat()
        l = linhas.get(chave, {})
        dias.append({"dia": chave, "enviados": l.get("enviados", 0),
                     "papel_mm": l.get("papel_mm", 0.0), "papel_cm2": l.get("papel_cm2", 0.0)})
    return ResumoMes(ano, mes, loja, dias)


def _fmt(numero: float, casas: int = 0) -> str:
    texto = f"{numero:,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _peso(gramas: float) -> str:
    return f"{_fmt(gramas / 1000, 2)} kg" if gramas >= 1000 else f"{_fmt(gramas)} g"


def gerar_pdf(r: ResumoMes) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_title(f"EcoZap - {r.titulo}")
    pdf.set_creator("EcoZap")

    pdf.set_fill_color(*VERDE)
    pdf.rect(0, 0, 210, 38, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(15, 10)
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 10, "Relatório de papel economizado")
    pdf.set_xy(15, 22)
    pdf.set_font("Helvetica", "", 13)
    pdf.cell(0, 8, f"{r.loja}  -  {r.titulo}")

    cartoes = [("Comprovantes digitais", _fmt(r.enviados)),
               ("Papel não impresso", f"{_fmt(r.metros, 2)} m"),
               ("Bobinas de 80mm x 40m*", _fmt(r.bobinas, 1)),
               ("Peso de papel*", _peso(r.gramas))]
    x, y, larg = 15, 48, 42
    for titulo, valor in cartoes:
        pdf.set_fill_color(*VERDE_CLARO)
        pdf.rect(x, y, larg, 30, "F")
        pdf.set_text_color(*VERDE)
        pdf.set_xy(x, y + 5)
        pdf.set_font("Helvetica", "B", 17)
        pdf.cell(larg, 10, valor, align="C")
        pdf.set_xy(x, y + 18)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(60, 60, 60)
        pdf.cell(larg, 6, titulo, align="C")
        x += larg + 3

    # gráfico de barras: comprovantes por dia
    pdf.set_xy(15, 90)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(40, 40, 40)
    pdf.cell(0, 8, "Comprovantes enviados por dia")
    topo, base, esq, larg_total = 102, 182, 18, 177
    maximo = max((d["enviados"] for d in r.dias), default=0) or 1
    passo = larg_total / len(r.dias)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(esq, base, esq + larg_total, base)
    pdf.set_font("Helvetica", "", 6)
    for i, d in enumerate(r.dias):
        altura = (base - topo) * d["enviados"] / maximo
        bx = esq + i * passo + passo * 0.15
        if altura:
            pdf.set_fill_color(*VERDE)
            pdf.rect(bx, base - altura, passo * 0.7, altura, "F")
            pdf.set_text_color(*VERDE)
            pdf.set_xy(bx - 1, base - altura - 4)
            pdf.cell(passo * 0.7 + 2, 3, str(d["enviados"]), align="C")
        pdf.set_text_color(120, 120, 120)
        pdf.set_xy(bx - 1, base + 1)
        pdf.cell(passo * 0.7 + 2, 3, str(i + 1), align="C")

    pdf.set_xy(15, 195)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(40, 40, 40)
    melhor = max(r.dias, key=lambda d: d["enviados"])
    texto = (f"Em {r.titulo}, {r.loja} enviou {_fmt(r.enviados)} comprovantes pelo WhatsApp "
             f"em vez de imprimir, deixando de gastar {_fmt(r.metros, 2)} metros de papel.")
    if melhor["enviados"]:
        dia = date.fromisoformat(melhor["dia"]).strftime("%d/%m")
        texto += f" O dia com mais comprovantes digitais foi {dia} ({melhor['enviados']})."
    texto += (" Papel térmico de cupom geralmente não é reciclável e costuma ir direto "
              "para o lixo - cada comprovante digital faz diferença.")
    pdf.multi_cell(180, 6, texto)

    pdf.set_xy(15, 270)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(180, 4, "* Estimativas: bobina padrão de 80 mm x 40 m e papel térmico de "
                           "aproximadamente 60 g/m². Gerado pelo EcoZap.")
    return bytes(pdf.output())


def _fonte(tamanho: int, negrito: bool = False):
    nomes = (["arialbd.ttf", "DejaVuSans-Bold.ttf"] if negrito else []) + ["arial.ttf", "DejaVuSans.ttf"]
    for nome in nomes:
        try:
            return ImageFont.truetype(nome, tamanho)
        except OSError:
            continue
    return ImageFont.load_default(size=tamanho)


def gerar_imagem(r: ResumoMes) -> bytes:
    """Card 1080x1080 para Instagram/WhatsApp Status."""
    img = Image.new("RGB", (1080, 1080), VERDE)
    d = ImageDraw.Draw(img)
    d.ellipse((700, -200, 1300, 400), fill=(56, 142, 60))
    d.ellipse((-250, 750, 350, 1350), fill=(56, 142, 60))
    desenhar_folha(d, 545, 185, 230, VERDE_CLARO, VERDE)

    def centro(y, texto, fonte, cor=(255, 255, 255)):
        w = d.textlength(texto, font=fonte)
        d.text(((1080 - w) / 2, y), texto, font=fonte, fill=cor)

    centro(330, f"Em {r.titulo}", _fonte(48))
    centro(400, r.loja, _fonte(56, True))
    centro(490, "deixou de imprimir", _fonte(48))
    centro(570, f"{_fmt(r.metros, 1)} m", _fonte(150, True), VERDE_CLARO)
    centro(750, "de papel", _fonte(48))
    centro(840, f"{_fmt(r.enviados)} comprovantes enviados pelo WhatsApp", _fonte(38))
    centro(980, "Menos papel, mais planeta  -  EcoZap", _fonte(30), VERDE_CLARO)
    saida = io.BytesIO()
    img.save(saida, "JPEG", quality=92)
    return saida.getvalue()


def gerar(arm: Armazenamento, loja: str, mes_texto: str | None,
          pasta_saida: str | Path) -> tuple[Path, Path]:
    ano, mes = mes_de_texto(mes_texto)
    r = resumir(arm, ano, mes, loja)
    pasta = Path(pasta_saida)
    pasta.mkdir(parents=True, exist_ok=True)
    base = f"ecozap-relatorio-{ano}-{mes:02d}"
    arq_pdf, arq_img = pasta / f"{base}.pdf", pasta / f"{base}.jpg"
    arq_pdf.write_bytes(gerar_pdf(r))
    arq_img.write_bytes(gerar_imagem(r))
    return arq_pdf, arq_img

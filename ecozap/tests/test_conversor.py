import io

from PIL import Image

from ecozap import conversor, escpos
from exemplos import CUPOM, ESCPOS


def test_escpos_limpa_comandos():
    texto = escpos.limpar(ESCPOS).decode("cp850")
    assert "MERCADINHO BOM PRECO" in texto
    assert "Feijão" in texto
    assert "123456" not in texto and "1A2" not in texto
    assert "\x1b" not in texto and "\x1d" not in texto


def test_decodifica_cp850_automatico():
    texto = conversor.decodificar_texto(ESCPOS)
    assert "Feijão" in texto


def test_texto_vira_pdf_80mm_com_altura_do_cupom():
    doc = conversor.preparar(CUPOM.encode("utf-8"), "80mm", rodape="Eco")
    assert doc.origem == "texto" and doc.pdf.startswith(b"%PDF")
    import pypdfium2 as pdfium
    pg = pdfium.PdfDocument(doc.pdf)[0]
    assert round(pg.get_width() * 25.4 / 72) == 80
    assert 60 < pg.get_height() * 25.4 / 72 < 200
    assert "TOTAL" in conversor.texto_do_pdf(doc.pdf)[0]


def test_tamanhos_de_papel():
    for papel in ("58mm", "a4"):
        assert conversor.texto_para_pdf(CUPOM, papel).startswith(b"%PDF")


def test_pdf_entra_e_sai_como_imagem():
    pdf = conversor.texto_para_pdf(CUPOM)
    doc = conversor.preparar(pdf)
    assert doc.origem == "pdf" and "TOTAL" in doc.texto
    jpg, ext, mime = conversor.exportar(doc, "jpeg")
    assert ext == "jpg" and mime == "image/jpeg"
    img = Image.open(io.BytesIO(jpg))
    assert abs(img.width - 630) < 5  # 80 mm a 200 dpi
    png, ext, _ = conversor.exportar(doc, "png")
    assert ext == "png" and png.startswith(b"\x89PNG")


def test_imagem_vira_pdf():
    buf = io.BytesIO()
    Image.new("RGB", (300, 600), "white").save(buf, "PNG")
    doc = conversor.preparar(buf.getvalue())
    assert doc.origem == "imagem" and doc.pdf.startswith(b"%PDF")

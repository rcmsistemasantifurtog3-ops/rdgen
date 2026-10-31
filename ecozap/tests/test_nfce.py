import pypdfium2 as pdfium
import pytest

from ecozap import conversor, nfce
from exemplos import CHAVE, URL_QR, escpos_nfce


def test_chave_valida():
    assert nfce.chave_valida(CHAVE)
    errada = CHAVE[:-1] + str((int(CHAVE[-1]) + 1) % 10)
    assert not nfce.chave_valida(errada)
    assert not nfce.chave_valida("123")


def test_extrai_chave_em_grupos():
    texto = "Chave:\n" + " ".join(CHAVE[i:i + 4] for i in range(0, 44, 4))
    assert nfce.extrair_chave(texto) == CHAVE


def test_qr_do_escpos_vira_link_e_volta_desenhado_no_pdf():
    doc = conversor.preparar(escpos_nfce())
    assert f"[[QR:{URL_QR}]]" in doc.texto
    info = nfce.analisar(doc.texto)
    assert info.link == URL_QR and info.chave == CHAVE and info.modelo == "NFC-e"
    # o QR desenhado no PDF pode ser lido de novo (prova que ficou correto)
    pytest.importorskip("zxingcpp")
    assert URL_QR in nfce.qrs_do_pdf(doc.pdf)
    pg = pdfium.PdfDocument(doc.pdf)[0]
    assert pg.get_height() * 25.4 / 72 > 60  # cabe texto + QR


def test_sem_qr_usa_endereco_impresso():
    info = nfce.analisar("Consulte em www.sefaz.mt.gov.br/nfce/consultanfce\nTOTAL 5,00")
    assert info.link == "https://www.sefaz.mt.gov.br/nfce/consultanfce"
    assert info.chave is None


def test_qr_dentro_de_pdf():
    pytest.importorskip("zxingcpp")
    pdf = conversor.texto_para_pdf(f"NFC-e\n[[QR:{URL_QR}]]\nfim", "a4")
    assert nfce.analisar("", pdf).link == URL_QR

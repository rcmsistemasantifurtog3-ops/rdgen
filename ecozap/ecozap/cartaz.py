"""Cartaz para o balcão: QR Code que abre o WhatsApp da loja com a mensagem pronta.

O cliente aponta a câmera, manda a mensagem e pronto: a "janela de 24h" fica
aberta e o comprovante chega sem custo de template.
"""
from __future__ import annotations

from urllib.parse import quote

from fpdf import FPDF

from . import telefone as tel
from .conversor import _imagem_qr, _latin1
from .icone import gerar as gerar_icone

VERDE = (46, 125, 50)


def link_whatsapp(numero_loja: str, texto: str) -> str:
    numero = tel.normalizar(numero_loja)
    if not numero:
        raise ValueError("Configure o WhatsApp da loja (numero_loja) nas configurações")
    return f"https://wa.me/{numero}?text={quote(texto)}"


def gerar_pdf(cfg: dict) -> bytes:
    link = link_whatsapp(cfg["numero_loja"], cfg["atendimento"]["texto_cartaz"])
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_title("EcoZap - cartaz do balcão")
    pdf.set_fill_color(*VERDE)
    pdf.rect(0, 0, 210, 62, "F")
    pdf.image(gerar_icone(256), x=20, y=13, w=36)
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(62, 16)
    pdf.set_font("Helvetica", "B", 30)
    pdf.cell(0, 13, _latin1("Comprovante sem papel"))
    pdf.set_xy(62, 32)
    pdf.set_font("Helvetica", "", 17)
    pdf.cell(0, 9, _latin1(cfg["nome_loja"]))

    pdf.set_text_color(40, 40, 40)
    pdf.set_xy(15, 74)
    pdf.set_font("Helvetica", "B", 20)
    pdf.multi_cell(180, 10, _latin1("Receba seu comprovante no WhatsApp"), align="C")
    pdf.image(_imagem_qr(link), x=55, y=94, w=100, h=100)

    passos = ["1. Aponte a câmera do celular para o QR Code",
              "2. Envie a mensagem que aparece pronta",
              "3. Seu comprovante chega na hora no seu WhatsApp"]
    pdf.set_font("Helvetica", "", 15)
    y = 202
    for passo in passos:
        pdf.set_xy(30, y)
        pdf.cell(150, 9, _latin1(passo))
        y += 11
    numero = tel.formatar(cfg["numero_loja"])
    pdf.set_xy(15, y + 4)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*VERDE)
    pdf.cell(180, 8, _latin1(f"Ou salve nosso número: {numero}"), align="C")

    pdf.set_xy(15, 268)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(180, 5, _latin1(
        "Ofertas só se você pedir: responda PROMO. Para cancelar, responda SAIR. "
        "Menos papel, mais planeta."), align="C")
    return bytes(pdf.output())

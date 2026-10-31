"""Acha o link de consulta e a chave de acesso da NFC-e / NF-e no comprovante.

De onde vem o link (em ordem de preferência):
1. QR Code enviado pelo PDV em ESC/POS (vira a marca [[QR:...]] no texto);
2. QR Code desenhado no PDF (lido com zxing-cpp, se estiver instalado);
3. Endereço de consulta impresso no texto (ex.: www.nfce.fazenda.sp.gov.br/consulta).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("ecozap.nfce")

_MARCA_QR = re.compile(r"\[\[QR:(.*?)\]\]")
_URL = re.compile(r"(?:https?://|www\.)[^\s\]]+", re.IGNORECASE)
_CHAVE = re.compile(r"(?<!\d)(?:\d{4}[ .]?){10}\d{4}(?!\d)")


@dataclass
class InfoNota:
    link: str | None = None
    chave: str | None = None

    @property
    def modelo(self) -> str | None:
        if not self.chave:
            return None
        return {"65": "NFC-e", "55": "NF-e"}.get(self.chave[20:22])


def chave_valida(chave: str) -> bool:
    """Confere os 44 dígitos e o dígito verificador (módulo 11)."""
    if not re.fullmatch(r"\d{44}", chave or ""):
        return False
    soma, peso = 0, 2
    for d in reversed(chave[:43]):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    dv = 11 - soma % 11
    return (0 if dv >= 10 else dv) == int(chave[43])


def extrair_chave(texto: str) -> str | None:
    for m in _CHAVE.finditer(texto or ""):
        digitos = re.sub(r"\D", "", m.group())
        if chave_valida(digitos):
            return digitos
    return None


def qrs_do_texto(texto: str) -> list[str]:
    return _MARCA_QR.findall(texto or "")


def qrs_do_pdf(pdf: bytes) -> list[str]:
    """Lê QR Codes desenhados no PDF. Precisa do pacote opcional zxing-cpp."""
    try:
        import pypdfium2 as pdfium
        import zxingcpp
    except ImportError:
        return []
    achados = []
    doc = pdfium.PdfDocument(pdf)
    try:
        for i in range(min(len(doc), 3)):
            img = doc[i].render(scale=3).to_pil().convert("L")
            achados += [r.text for r in zxingcpp.read_barcodes(img)
                        if r.format == zxingcpp.BarcodeFormat.QRCode]
    except Exception:  # leitura de QR é bônus: nunca derruba o envio
        log.debug("Falha lendo QR do PDF", exc_info=True)
    finally:
        doc.close()
    return achados


def _melhor_link(candidatos: list[str]) -> str | None:
    urls = [c.strip() for c in candidatos if _URL.match(c.strip())]
    for u in urls:  # prefere o que tem cara de consulta de nota
        if re.search(r"nfc-?e|qrcode|sefa|fazenda|sef\.|consulta", u, re.IGNORECASE):
            return u
    return urls[0] if urls else None


def analisar(texto: str, pdf: bytes | None = None) -> InfoNota:
    qrs = qrs_do_texto(texto)
    if not qrs and pdf:
        qrs = qrs_do_pdf(pdf)
    link = _melhor_link(qrs)
    sem_marcas = _MARCA_QR.sub(" ", texto or "")
    if not link:
        link = _melhor_link(_URL.findall(sem_marcas))
    if link and link.lower().startswith("www."):
        link = "https://" + link
    chave = extrair_chave(sem_marcas)
    if not chave:  # a chave também vem dentro do link do QR (p=CHAVE|...)
        for q in qrs:
            m = re.search(r"p=(\d{44})", q)
            if m and chave_valida(m.group(1)):
                chave = m.group(1)
                break
    return InfoNota(link, chave)

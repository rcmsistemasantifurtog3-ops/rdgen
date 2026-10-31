"""Agenda de clientes: lembra o WhatsApp de quem já recebeu comprovante.

Na próxima compra, se o cupom trouxer o CPF (ou você digitar o nome), o
telefone aparece sozinho. O CPF NÃO é guardado em texto: guardamos só um
"resumo" (HMAC-SHA256 com uma chave secreta desta instalação), que serve
para comparar mas não para descobrir o CPF.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from pathlib import Path

_CPF_COM_PALAVRA = re.compile(r"CPF[^\d\n]{0,25}(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)", re.IGNORECASE)
_NOME = re.compile(
    r"(?:cliente|consumidor|nome|destinat[aá]rio)\s*:\s*"
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'.]*(?: [A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'.]*){0,5})", re.IGNORECASE)
_NAO_NOME = {"cpf", "cnpj", "consumidor", "nao", "não", "identificado", "cel", "tel",
             "telefone", "celular", "whatsapp", "fone"}


_PARTICULAS = {"da", "de", "do", "das", "dos", "e"}


def _capitalizar(palavra: str, posicao: int) -> str:
    if posicao and palavra.lower() in _PARTICULAS:
        return palavra.lower()
    return palavra.capitalize() if palavra.isupper() or palavra.islower() else palavra


def cpf_valido(cpf: str) -> bool:
    d = re.sub(r"\D", "", cpf or "")
    if len(d) != 11 or d == d[0] * 11:
        return False
    for tam in (9, 10):
        soma = sum(int(d[i]) * (tam + 1 - i) for i in range(tam))
        dv = soma * 10 % 11 % 10
        if dv != int(d[tam]):
            return False
    return True


def extrair_cpf(texto: str) -> str | None:
    for m in _CPF_COM_PALAVRA.finditer(texto or ""):
        if cpf_valido(m.group(1)):
            return re.sub(r"\D", "", m.group(1))
    return None


def extrair_nome(texto: str) -> str | None:
    for m in _NOME.finditer(texto or ""):
        palavras = []
        for p in m.group(1).split():
            if p.lower().strip(".:") in _NAO_NOME:
                break
            palavras.append(p)
        if palavras and len(" ".join(palavras)) >= 3:
            return " ".join(_capitalizar(w, i) for i, w in enumerate(palavras))
    return None


class ResumoCPF:
    """Gera o resumo irreversível do CPF com uma chave guardada em disco."""

    def __init__(self, pasta: str | Path):
        arquivo = Path(pasta) / "chave_cpf.bin"
        if not arquivo.exists():
            arquivo.parent.mkdir(parents=True, exist_ok=True)
            arquivo.write_bytes(os.urandom(32))
        self._chave = arquivo.read_bytes()

    def __call__(self, cpf: str | None) -> str | None:
        if not cpf:
            return None
        return hmac.new(self._chave, re.sub(r"\D", "", cpf).encode(), hashlib.sha256).hexdigest()

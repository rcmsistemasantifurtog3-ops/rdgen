"""Normalização de telefones brasileiros e extração do número no texto do cupom."""
from __future__ import annotations

import re

# Palavras que costumam vir antes do telefone do cliente no cupom
_PALAVRAS = r"(?:whats(?:app|zap)?|zap|cel(?:ular)?|tel(?:efone)?|fone|contato)"
_COM_PALAVRA = re.compile(
    _PALAVRAS + r"\s*[:\-.]?\s*(\+?[\d\s().\-]{10,20})", re.IGNORECASE)
# Celular formatado: (11) 98765-4321 / 11 98765-4321 / 11987654321 com hífen
_CELULAR_FORMATADO = re.compile(r"\(?\b(\d{2})\)?\s?(9\d{4})[\s-](\d{4})\b")


def so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def normalizar(numero: str) -> str | None:
    """Converte para o formato internacional usado pelo WhatsApp: 55DDNNNNNNNNN.

    Retorna None se o número não parecer um telefone brasileiro válido.
    """
    d = so_digitos(numero)
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("0") and len(d) in (11, 12):  # 0 + DDD (discagem antiga)
        d = d[1:]
    if len(d) in (10, 11):
        d = "55" + d
    if not (d.startswith("55") and len(d) in (12, 13)):
        return None
    ddd = d[2:4]
    if ddd[0] == "0" or ddd[1] == "0":
        return None
    if len(d) == 13 and d[4] != "9":  # celular com 9 dígitos começa com 9
        return None
    return d


def formatar(numero: str) -> str:
    """55 11 98765-4321 -> +55 (11) 98765-4321 (só para exibir)."""
    d = normalizar(numero)
    if not d:
        return numero
    ddd, resto = d[2:4], d[4:]
    return f"+55 ({ddd}) {resto[:-4]}-{resto[-4:]}"


def extrair(texto: str) -> str | None:
    """Procura o telefone do cliente no texto do cupom.

    Primeiro tenta números com palavra-chave (Tel:, Celular:, WhatsApp:),
    depois celulares formatados. Evita confundir com CPF/CNPJ porque exige
    palavra-chave ou formatação típica de telefone.
    """
    for m in _COM_PALAVRA.finditer(texto or ""):
        numero = normalizar(m.group(1))
        if numero:
            return numero
    for m in _CELULAR_FORMATADO.finditer(texto or ""):
        numero = normalizar("".join(m.groups()))
        if numero:
            return numero
    return None

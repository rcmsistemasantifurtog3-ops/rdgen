"""Carrega e valida a configuração (config.json)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

PADRAO: dict = {
    "pasta_entrada": "dados/entrada",
    "pasta_dados": "dados",
    "dias_retencao": 15,
    "formato_padrao": "pdf",          # pdf | jpeg | png
    "tamanho_papel_padrao": "80mm",   # 58mm | 80mm | a4
    "codificacao_texto": "auto",      # auto | utf-8 | cp1252 | cp850 ...
    "nome_loja": "Minha Loja",
    "mensagem": "Olá{nome}! Segue o seu comprovante da {loja}. "
                "Obrigado por economizar papel com a gente 🌱",
    "rodape_eco": "Comprovante digital - EcoZap. Menos papel, mais planeta.",
    "whatsapp": {
        "provedor": "simulado",       # simulado | cloud_api | evolution
        "cloud_api": {"token": "", "phone_number_id": "", "versao_api": "v21.0",
                      "template": "", "idioma_template": "pt_BR"},
        "evolution": {"url_base": "", "instancia": "", "apikey": ""},
    },
}

FORMATOS = ("pdf", "jpeg", "png")
TAMANHOS = ("58mm", "80mm", "a4")


class ErroConfig(ValueError):
    pass


def _mesclar(base: dict, extra: dict) -> dict:
    """Mescla dicionários recursivamente (extra sobrescreve base)."""
    saida = copy.deepcopy(base)
    for chave, valor in extra.items():
        if isinstance(valor, dict) and isinstance(saida.get(chave), dict):
            saida[chave] = _mesclar(saida[chave], valor)
        else:
            saida[chave] = valor
    return saida


def carregar(caminho: str | os.PathLike | None = None) -> dict:
    """Lê o config.json (se existir) por cima dos valores padrão."""
    caminho = Path(caminho or os.environ.get("ECOZAP_CONFIG", "config.json"))
    cfg = copy.deepcopy(PADRAO)
    if caminho.exists():
        with open(caminho, encoding="utf-8") as f:
            cfg = _mesclar(cfg, json.load(f))
    validar(cfg)
    return cfg


def validar(cfg: dict) -> None:
    if cfg["formato_padrao"] not in FORMATOS:
        raise ErroConfig(f"formato_padrao deve ser um de {FORMATOS}")
    if cfg["tamanho_papel_padrao"] not in TAMANHOS:
        raise ErroConfig(f"tamanho_papel_padrao deve ser um de {TAMANHOS}")
    if int(cfg["dias_retencao"]) < 1:
        raise ErroConfig("dias_retencao precisa ser pelo menos 1")
    if cfg["whatsapp"]["provedor"] not in ("simulado", "cloud_api", "evolution"):
        raise ErroConfig("whatsapp.provedor deve ser simulado, cloud_api ou evolution")

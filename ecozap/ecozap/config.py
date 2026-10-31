"""Carrega, valida e salva a configuração (config.json).

Onde o config.json é procurado (o primeiro que existir):
1. --config na linha de comando
2. variável de ambiente ECOZAP_CONFIG
3. config.json na pasta atual
4. %ProgramData%\\EcoZap\\config.json  (onde o instalador .exe coloca)
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path


def pasta_base() -> Path:
    """Pasta padrão de dados: ProgramData no Windows, ./dados nos outros."""
    if sys.platform.startswith("win"):
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "EcoZap"
    return Path("dados")


PADRAO: dict = {
    "pasta_entrada": str(pasta_base() / "entrada"),
    "pasta_dados": str(pasta_base() / "dados"),
    "dias_retencao": 15,
    "dias_retencao_clientes": 365,    # agenda de clientes (0 = nunca apaga)
    "formato_padrao": "pdf",          # pdf | jpeg | png
    "tamanho_papel_padrao": "80mm",   # 58mm | 80mm | a4
    "codificacao_texto": "auto",      # auto | utf-8 | cp1252 | cp850 ...
    "identificacao_caixa": "Caixa 1",
    "nome_loja": "Minha Loja",
    "mensagem": "Olá{nome}! Segue o seu comprovante da {loja}. "
                "Obrigado por economizar papel com a gente 🌱",
    "mensagem_nfce": "\n\n🧾 Consulte sua nota fiscal: {link}",
    "mensagem_chave": "\n🔑 Chave de acesso: {chave}",
    "rodape_eco": "Comprovante digital - EcoZap. Menos papel, mais planeta.",
    "usar_bandeja": True,             # ícone perto do relógio do Windows
    "whatsapp": {
        "provedor": "simulado",       # simulado | cloud_api | evolution
        "cloud_api": {"token": "", "phone_number_id": "", "versao_api": "v21.0",
                      "template": "", "idioma_template": "pt_BR",
                      "parametros_template": ["{loja}"]},
        "evolution": {"url_base": "", "instancia": "", "apikey": ""},
    },
    "painel": {
        "ativo": False,
        "host": "127.0.0.1",          # 0.0.0.0 = acessível pela rede (exige senha)
        "porta": 8765,
        "usuario": "admin",
        "senha": "",
        "caixas": [],                 # [{"nome": "Caixa 2", "url": "http://192.168.0.12:8765"}]
    },
}

FORMATOS = ("pdf", "jpeg", "png")
TAMANHOS = ("58mm", "80mm", "a4")
PROVEDORES = ("simulado", "cloud_api", "evolution")


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


def localizar(caminho: str | os.PathLike | None = None) -> Path:
    if caminho:
        return Path(caminho)
    if os.environ.get("ECOZAP_CONFIG"):
        return Path(os.environ["ECOZAP_CONFIG"])
    local = Path("config.json")
    if local.exists() or not sys.platform.startswith("win"):
        return local
    return pasta_base() / "config.json"


def carregar(caminho: str | os.PathLike | None = None) -> dict:
    """Lê o config.json (se existir) por cima dos valores padrão."""
    arquivo = localizar(caminho)
    cfg = copy.deepcopy(PADRAO)
    if arquivo.exists():
        with open(arquivo, encoding="utf-8") as f:
            try:
                cfg = _mesclar(cfg, json.load(f))
            except json.JSONDecodeError as e:
                raise ErroConfig(f"{arquivo} não é um JSON válido: {e}") from e
    cfg["_arquivo"] = str(arquivo)
    validar(cfg)
    return cfg


def salvar(cfg: dict) -> Path:
    validar(cfg)
    arquivo = Path(cfg.get("_arquivo") or localizar())
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    limpo = {k: v for k, v in cfg.items() if not k.startswith("_")}
    tmp = arquivo.with_suffix(".tmp")
    tmp.write_text(json.dumps(limpo, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, arquivo)
    return arquivo


def validar(cfg: dict) -> None:
    if cfg["formato_padrao"] not in FORMATOS:
        raise ErroConfig(f"formato_padrao deve ser um de {FORMATOS}")
    if cfg["tamanho_papel_padrao"] not in TAMANHOS:
        raise ErroConfig(f"tamanho_papel_padrao deve ser um de {TAMANHOS}")
    if int(cfg["dias_retencao"]) < 1:
        raise ErroConfig("dias_retencao precisa ser pelo menos 1")
    if int(cfg["dias_retencao_clientes"]) < 0:
        raise ErroConfig("dias_retencao_clientes não pode ser negativo")
    if cfg["whatsapp"]["provedor"] not in PROVEDORES:
        raise ErroConfig(f"whatsapp.provedor deve ser um de {PROVEDORES}")
    painel = cfg["painel"]
    if painel["ativo"] and painel["host"] not in ("127.0.0.1", "localhost") and not painel["senha"]:
        raise ErroConfig("painel acessível pela rede precisa de senha (painel.senha)")

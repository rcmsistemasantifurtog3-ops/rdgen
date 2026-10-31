"""Envio do comprovante pelo WhatsApp.

Provedores:
- simulado  : não envia nada, só grava em <pasta_dados>/simulado/ (para testes)
- cloud_api : API oficial da Meta (WhatsApp Business Cloud API) - recomendado
- evolution : Evolution API (self-hosted, não oficial; risco de bloqueio do número)
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

import requests

TIMEOUT = 30


class ErroEnvio(Exception):
    pass


class Provedor:
    nome = "base"

    def enviar(self, telefone: str, conteudo: bytes, nome_arquivo: str,
               mimetype: str, legenda: str) -> str:
        """Envia e devolve um identificador da mensagem. Lança ErroEnvio."""
        raise NotImplementedError


class Simulado(Provedor):
    nome = "simulado"

    def __init__(self, pasta: str | Path):
        self.pasta = Path(pasta)

    def enviar(self, telefone, conteudo, nome_arquivo, mimetype, legenda):
        self.pasta.mkdir(parents=True, exist_ok=True)
        destino = self.pasta / f"{telefone}_{nome_arquivo}"
        destino.write_bytes(conteudo)
        destino.with_suffix(destino.suffix + ".json").write_text(json.dumps({
            "telefone": telefone, "arquivo": nome_arquivo, "mimetype": mimetype,
            "legenda": legenda, "quando": datetime.now().isoformat(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"simulado:{destino.name}"


def _erro_http(resp: requests.Response) -> ErroEnvio:
    try:
        detalhe = resp.json()
        detalhe = detalhe.get("error", {}).get("message") or detalhe
    except ValueError:
        detalhe = resp.text[:300]
    return ErroEnvio(f"HTTP {resp.status_code}: {detalhe}")


class CloudAPI(Provedor):
    """WhatsApp Business Cloud API (graph.facebook.com).

    Atenção às regras da Meta: fora da "janela de 24h" (cliente não mandou
    mensagem para a loja nas últimas 24h) só é permitido enviar TEMPLATE
    aprovado. Por isso, configure `template` com um modelo que tenha
    cabeçalho do tipo DOCUMENTO (ou IMAGEM) e um parâmetro {{1}} no corpo
    (nome da loja). Sem template, enviamos documento comum (só funciona
    dentro da janela de 24h).
    """
    nome = "cloud_api"

    def __init__(self, token: str, phone_number_id: str, versao_api: str = "v21.0",
                 template: str = "", idioma_template: str = "pt_BR",
                 sessao: requests.Session | None = None):
        if not token or not phone_number_id:
            raise ErroEnvio("Cloud API: preencha token e phone_number_id no config.json")
        self.base = f"https://graph.facebook.com/{versao_api}/{phone_number_id}"
        self.template = template
        self.idioma = idioma_template
        self.http = sessao or requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"

    def _subir_midia(self, conteudo, nome_arquivo, mimetype) -> str:
        resp = self.http.post(f"{self.base}/media", timeout=TIMEOUT,
                              data={"messaging_product": "whatsapp", "type": mimetype},
                              files={"file": (nome_arquivo, conteudo, mimetype)})
        if not resp.ok:
            raise _erro_http(resp)
        return resp.json()["id"]

    def enviar(self, telefone, conteudo, nome_arquivo, mimetype, legenda,
               parametros_template: list[str] | None = None):
        midia_id = self._subir_midia(conteudo, nome_arquivo, mimetype)
        tipo = "image" if mimetype.startswith("image/") else "document"
        midia = {"id": midia_id}
        if tipo == "document":
            midia["filename"] = nome_arquivo

        if self.template:
            componentes = [{"type": "header",
                            "parameters": [{"type": tipo, tipo: midia}]}]
            if parametros_template:
                componentes.append({"type": "body", "parameters": [
                    {"type": "text", "text": p} for p in parametros_template]})
            corpo = {"messaging_product": "whatsapp", "to": telefone,
                     "type": "template",
                     "template": {"name": self.template,
                                  "language": {"code": self.idioma},
                                  "components": componentes}}
        else:
            midia["caption"] = legenda
            corpo = {"messaging_product": "whatsapp", "to": telefone,
                     "type": tipo, tipo: midia}

        resp = self.http.post(f"{self.base}/messages", json=corpo, timeout=TIMEOUT)
        if not resp.ok:
            raise _erro_http(resp)
        return resp.json()["messages"][0]["id"]


class Evolution(Provedor):
    """Evolution API v2 (https://doc.evolution-api.com)."""
    nome = "evolution"

    def __init__(self, url_base: str, instancia: str, apikey: str,
                 sessao: requests.Session | None = None):
        if not (url_base and instancia and apikey):
            raise ErroEnvio("Evolution: preencha url_base, instancia e apikey")
        self.url = f"{url_base.rstrip('/')}/message/sendMedia/{instancia}"
        self.http = sessao or requests.Session()
        self.http.headers["apikey"] = apikey

    def enviar(self, telefone, conteudo, nome_arquivo, mimetype, legenda):
        corpo = {
            "number": telefone,
            "mediatype": "image" if mimetype.startswith("image/") else "document",
            "mimetype": mimetype,
            "caption": legenda,
            "fileName": nome_arquivo,
            "media": base64.b64encode(conteudo).decode(),
        }
        resp = self.http.post(self.url, json=corpo, timeout=TIMEOUT)
        if not resp.ok:
            raise _erro_http(resp)
        dados = resp.json()
        return str(dados.get("key", {}).get("id", "ok"))


def criar_provedor(cfg: dict) -> Provedor:
    wa = cfg["whatsapp"]
    nome = wa["provedor"]
    if nome == "cloud_api":
        return CloudAPI(**wa["cloud_api"])
    if nome == "evolution":
        return Evolution(**wa["evolution"])
    return Simulado(Path(cfg["pasta_dados"]) / "simulado")

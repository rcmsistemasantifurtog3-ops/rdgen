"""Envio de mensagens pelo WhatsApp.

Provedores:
- simulado  : não envia nada, só grava em <pasta_dados>/simulado/ (para testes)
- cloud_api : API oficial da Meta (WhatsApp Business Cloud API) - recomendado
- evolution : Evolution API (self-hosted, não oficial; risco de bloqueio do número)

Cada provedor sabe:
- enviar(...)          arquivo (PDF/imagem) com legenda, ou dentro de um template
- enviar_texto(...)    texto simples (respostas automáticas, promoções)
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import requests

TIMEOUT = 30


class ErroEnvio(Exception):
    pass


def limpar_parametro(texto: str) -> str:
    """A Meta recusa parâmetro de template com quebra de linha, tab ou 4+ espaços."""
    texto = re.sub(r"[\r\n\t]+", " · ", str(texto or "")).strip(" ·")
    return re.sub(r" {4,}", "   ", texto) or "-"


class Provedor:
    nome = "base"
    # Cloud API: fora da janela de 24h só template. Os outros não têm essa regra.
    exige_template_fora_da_janela = False

    def enviar(self, telefone: str, conteudo: bytes, nome_arquivo: str,
               mimetype: str, legenda: str, template: str | None = None,
               parametros_template: list[str] | None = None) -> str:
        """Envia e devolve o id da mensagem. Lança ErroEnvio."""
        raise NotImplementedError

    def enviar_texto(self, telefone: str, texto: str, template: str | None = None,
                     parametros_template: list[str] | None = None) -> str:
        raise NotImplementedError


class Simulado(Provedor):
    nome = "simulado"

    def __init__(self, pasta: str | Path):
        self.pasta = Path(pasta)

    def _registrar(self, telefone, dados: dict, nome: str, conteudo: bytes | None = None) -> str:
        self.pasta.mkdir(parents=True, exist_ok=True)
        ident = f"simulado-{uuid.uuid4().hex[:10]}"
        destino = self.pasta / f"{telefone}_{nome}"
        if conteudo is not None:
            destino.write_bytes(conteudo)
        destino.with_suffix(destino.suffix + ".json").write_text(json.dumps(
            {"telefone": telefone, "id": ident, "quando": datetime.now().isoformat(), **dados},
            ensure_ascii=False, indent=2), encoding="utf-8")
        return ident

    def enviar(self, telefone, conteudo, nome_arquivo, mimetype, legenda,
               template=None, parametros_template=None):
        return self._registrar(telefone, {"arquivo": nome_arquivo, "mimetype": mimetype,
                                          "legenda": legenda, "template": template,
                                          "parametros": parametros_template},
                               nome_arquivo, conteudo)

    def enviar_texto(self, telefone, texto, template=None, parametros_template=None):
        nome = f"texto-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}.txt"
        return self._registrar(telefone, {"texto": texto, "template": template,
                                          "parametros": parametros_template}, nome)


def _erro_http(resp: requests.Response) -> ErroEnvio:
    try:
        detalhe = resp.json()
        detalhe = detalhe.get("error", {}).get("message") or detalhe
    except ValueError:
        detalhe = resp.text[:300]
    return ErroEnvio(f"HTTP {resp.status_code}: {detalhe}")


class CloudAPI(Provedor):
    """WhatsApp Business Cloud API (graph.facebook.com).

    Regra da Meta: se o cliente NÃO escreveu para a loja nas últimas 24h, só
    é permitido enviar TEMPLATE aprovado. Se escreveu, pode mandar mensagem
    livre (e o atendimento é gratuito). O EcoZap decide sozinho.
    """
    nome = "cloud_api"
    exige_template_fora_da_janela = True

    def __init__(self, token: str, phone_number_id: str, versao_api: str = "v21.0",
                 idioma_template: str = "pt_BR", sessao: requests.Session | None = None,
                 **_ignorados):
        if not token or not phone_number_id:
            raise ErroEnvio("Cloud API: preencha token e phone_number_id nas configurações")
        self.base = f"https://graph.facebook.com/{versao_api}/{phone_number_id}"
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

    def _mensagem(self, corpo: dict) -> str:
        resp = self.http.post(f"{self.base}/messages", timeout=TIMEOUT,
                              json={"messaging_product": "whatsapp", **corpo})
        if not resp.ok:
            raise _erro_http(resp)
        return resp.json()["messages"][0]["id"]

    def _template(self, telefone, template, parametros, cabecalho: dict | None = None) -> str:
        componentes = []
        if cabecalho:
            componentes.append({"type": "header", "parameters": [cabecalho]})
        if parametros:
            componentes.append({"type": "body", "parameters": [
                {"type": "text", "text": limpar_parametro(p)} for p in parametros]})
        return self._mensagem({"to": telefone, "type": "template", "template": {
            "name": template, "language": {"code": self.idioma}, "components": componentes}})

    def enviar(self, telefone, conteudo, nome_arquivo, mimetype, legenda,
               template=None, parametros_template=None):
        midia_id = self._subir_midia(conteudo, nome_arquivo, mimetype)
        tipo = "image" if mimetype.startswith("image/") else "document"
        midia = {"id": midia_id}
        if tipo == "document":
            midia["filename"] = nome_arquivo
        if template:
            return self._template(telefone, template, parametros_template, {"type": tipo, tipo: midia})
        midia["caption"] = legenda
        return self._mensagem({"to": telefone, "type": tipo, tipo: midia})

    def enviar_texto(self, telefone, texto, template=None, parametros_template=None):
        if template:
            return self._template(telefone, template, parametros_template)
        return self._mensagem({"to": telefone, "type": "text",
                               "text": {"body": texto, "preview_url": True}})


class Evolution(Provedor):
    """Evolution API v2 (https://doc.evolution-api.com)."""
    nome = "evolution"

    def __init__(self, url_base: str, instancia: str, apikey: str,
                 sessao: requests.Session | None = None, **_ignorados):
        if not (url_base and instancia and apikey):
            raise ErroEnvio("Evolution: preencha url_base, instancia e apikey")
        self.base = url_base.rstrip("/")
        self.instancia = instancia
        self.url = f"{self.base}/message/sendMedia/{instancia}"
        self.http = sessao or requests.Session()
        self.http.headers["apikey"] = apikey

    def _post(self, url, corpo) -> str:
        resp = self.http.post(url, json=corpo, timeout=TIMEOUT)
        if not resp.ok:
            raise _erro_http(resp)
        return str(resp.json().get("key", {}).get("id", "ok"))

    def enviar(self, telefone, conteudo, nome_arquivo, mimetype, legenda,
               template=None, parametros_template=None):
        return self._post(self.url, {
            "number": telefone,
            "mediatype": "image" if mimetype.startswith("image/") else "document",
            "mimetype": mimetype,
            "caption": legenda,
            "fileName": nome_arquivo,
            "media": base64.b64encode(conteudo).decode(),
        })

    def enviar_texto(self, telefone, texto, template=None, parametros_template=None):
        return self._post(f"{self.base}/message/sendText/{self.instancia}",
                          {"number": telefone, "text": texto})


def criar_provedor(cfg: dict) -> Provedor:
    wa = cfg["whatsapp"]
    nome = wa["provedor"]
    if nome == "cloud_api":
        return CloudAPI(**wa["cloud_api"])
    if nome == "evolution":
        return Evolution(**wa["evolution"])
    return Simulado(Path(cfg["pasta_dados"]) / "simulado")

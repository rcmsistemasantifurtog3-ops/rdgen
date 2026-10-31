"""Lê o que o WhatsApp manda para o EcoZap (webhook): mensagens dos clientes
e confirmações de entregue/lido.

Cloud API (Meta):
  GET  /webhook/whatsapp  -> verificação (hub.verify_token)
  POST /webhook/whatsapp  -> eventos, assinados com X-Hub-Signature-256 (app_secret)
Evolution API:
  POST /webhook/evolution?token=SEU_TOKEN  -> eventos messages.upsert / messages.update
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime


@dataclass
class MensagemRecebida:
    telefone: str
    nome: str | None
    texto: str
    quando: datetime


@dataclass
class StatusEntrega:
    msg_id: str
    entrega: str  # enviado | entregue | lido | falhou


def assinatura_valida(corpo: bytes, cabecalho: str | None, app_secret: str) -> bool:
    if not app_secret or not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(app_secret.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(cabecalho.removeprefix("sha256="), esperado)


def token_valido(recebido: str | None, esperado: str) -> bool:
    return bool(esperado) and hmac.compare_digest((recebido or "").encode(), esperado.encode())


_STATUS_CLOUD = {"sent": "enviado", "delivered": "entregue", "read": "lido", "failed": "falhou"}
_STATUS_EVOLUTION = {"SERVER_ACK": "enviado", "DELIVERY_ACK": "entregue", "READ": "lido",
                     "PLAYED": "lido", "ERROR": "falhou", "FAILED": "falhou"}


def _texto_cloud(msg: dict) -> str:
    tipo = msg.get("type")
    if tipo == "text":
        return msg.get("text", {}).get("body", "")
    if tipo == "button":
        return msg.get("button", {}).get("text", "")
    if tipo == "interactive":
        i = msg.get("interactive", {})
        return (i.get("button_reply") or i.get("list_reply") or {}).get("title", "")
    return f"[{tipo}]"


def ler_cloud(dados: dict) -> tuple[list[MensagemRecebida], list[StatusEntrega]]:
    mensagens, status = [], []
    for entrada in dados.get("entry", []):
        for mudanca in entrada.get("changes", []):
            valor = mudanca.get("value", {})
            nomes = {c.get("wa_id"): c.get("profile", {}).get("name") for c in valor.get("contacts", [])}
            for m in valor.get("messages", []):
                quando = datetime.fromtimestamp(int(m.get("timestamp", 0)) or datetime.now().timestamp())
                mensagens.append(MensagemRecebida(m["from"], nomes.get(m["from"]), _texto_cloud(m),
                                                  quando.replace(microsecond=0)))
            for s in valor.get("statuses", []):
                if s.get("status") in _STATUS_CLOUD:
                    status.append(StatusEntrega(s["id"], _STATUS_CLOUD[s["status"]]))
    return mensagens, status


def _texto_evolution(msg: dict) -> str:
    return (msg.get("conversation")
            or msg.get("extendedTextMessage", {}).get("text")
            or msg.get("buttonsResponseMessage", {}).get("selectedDisplayText")
            or msg.get("imageMessage", {}).get("caption")
            or ("[mídia]" if msg else ""))


def ler_evolution(dados: dict) -> tuple[list[MensagemRecebida], list[StatusEntrega]]:
    evento = (dados.get("event") or "").lower().replace("_", ".")
    itens = dados.get("data")
    itens = itens if isinstance(itens, list) else [itens or {}]
    mensagens, status = [], []
    for d in itens:
        if evento == "messages.upsert":
            chave = d.get("key", {})
            jid = chave.get("remoteJid", "")
            if chave.get("fromMe") or not jid.endswith("@s.whatsapp.net"):
                continue  # ignora o que a própria loja mandou e grupos
            ts = d.get("messageTimestamp") or datetime.now().timestamp()
            mensagens.append(MensagemRecebida(jid.split("@")[0], d.get("pushName"),
                                              _texto_evolution(d.get("message") or {}),
                                              datetime.fromtimestamp(int(ts)).replace(microsecond=0)))
        elif evento == "messages.update":
            ident = d.get("keyId") or d.get("key", {}).get("id")
            st = _STATUS_EVOLUTION.get(str(d.get("status", "")).upper())
            if ident and st:
                status.append(StatusEntrega(ident, st))
    return mensagens, status

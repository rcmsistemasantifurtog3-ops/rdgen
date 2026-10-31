"""Painel web do EcoZap (só biblioteca padrão do Python, sem instalar nada).

Abra no navegador: http://127.0.0.1:8765  (ou o IP do caixa, se liberado na rede)

- Lista, busca, envia/reenvia e abre os comprovantes;
- Relatório mensal (PDF e imagem);
- "Visão geral": soma os números de vários caixas/lojas (config painel.caixas);
- API JSON (/api/...) usada pela visão geral e por integrações.
"""
from __future__ import annotations

import base64
import hmac
import html
import json
import logging
import re
import threading
import urllib.request
from datetime import date, datetime
from email.parser import BytesParser
from email.policy import default as politica_email
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import __version__, cartaz, relatorio, telefone as tel, webhook
from .servico import EcoZap

log = logging.getLogger("ecozap.painel")
_ID = re.compile(r"^[\w-]{1,64}$")
LIMITE_CORPO = 8 * 1024 * 1024
ACOES = {"comprovante_por_codigo": "📄 enviou o comprovante (código)",
         "comprovante_associado": "📄 enviou o comprovante pendente",
         "segunda_via": "📄 enviou 2ª via", "aguardando_caixa": "⏳ caixa precisa escolher",
         "boas_vindas": "👋 boas-vindas", "promocoes_aceita": "🎉 aceitou promoções",
         "promocoes_cancelada": "🚫 saiu das promoções", "registrada": "registrada"}
STATUS_CAMPANHA = {"agendada": "🕒 Agendada", "enviando": "📤 Enviando", "pausada": "⏸ Pausada",
                   "concluida": "✅ Concluída", "cancelada": "✖ Cancelada"}
STATUS_ROTULO = {"pendente": "⏳ Pendente", "enviado": "✅ Enviado", "erro": "❌ Erro",
                 "salvo": "💾 Salvo", "descartado": "🗑 Descartado"}

_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f4f7f4;color:#222}
header{background:#2e7d32;color:#fff;padding:14px 24px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
header h1{margin:0;font-size:22px} header a{color:#fff;margin-right:14px}
main{padding:18px 24px}.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.card{background:#c8e6c9;border-radius:8px;padding:12px 18px;min-width:150px}
.card b{display:block;font-size:24px;color:#2e7d32}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{padding:7px 8px;border-bottom:1px solid #e3e3e3;text-align:left;font-size:14px}
th{background:#e8f5e9} input,select,button{padding:5px 7px;font-size:14px}
button{background:#2e7d32;color:#fff;border:0;border-radius:4px;cursor:pointer}
.msg{padding:10px;border-radius:6px;margin-bottom:12px;background:#e8f5e9}
.erro{background:#ffebee;color:#b71c1c} .cinza{color:#777;font-size:12px}
"""


def _e(valor) -> str:
    return html.escape(str(valor if valor is not None else ""))


class _Handler(BaseHTTPRequestHandler):
    server_version = f"EcoZap/{__version__}"
    eco: EcoZap  # preenchido pela classe Painel
    disparador = None

    # ---------- infraestrutura ----------
    def log_message(self, formato, *args):
        log.debug("%s - %s", self.address_string(), formato % args)

    def _autorizado(self) -> bool:
        p = self.eco.cfg["painel"]
        if not p.get("senha"):
            return True
        esperado = base64.b64encode(f"{p['usuario']}:{p['senha']}".encode()).decode()
        recebido = self.headers.get("Authorization", "").removeprefix("Basic ").strip()
        return hmac.compare_digest(recebido, esperado)

    def _mesma_origem(self) -> bool:
        """Bloqueia formulários enviados por outros sites (CSRF)."""
        origem = self.headers.get("Origin") or self.headers.get("Referer")
        if not origem:
            return True
        return urlparse(origem).netloc == self.headers.get("Host")

    def _responder(self, corpo, tipo="text/html; charset=utf-8", status=HTTPStatus.OK,
                   extra: dict | None = None):
        dados = corpo.encode() if isinstance(corpo, str) else corpo
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(dados)

    def _json(self, obj, status=HTTPStatus.OK):
        self._responder(json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", status)

    def _redirecionar(self, destino: str):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", destino)
        self.end_headers()

    def _corpo(self) -> bytes:
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > LIMITE_CORPO:
            raise ValueError("Envio grande demais (máximo 8 MB)")
        return self.rfile.read(tamanho)

    def _form(self) -> dict:
        """Lê formulário comum, JSON ou multipart (com arquivo). Arquivos vêm como bytes."""
        tipo = self.headers.get("Content-Type", "")
        bruto = self._corpo()
        if tipo.startswith("application/json"):
            return json.loads(bruto or b"{}")
        if tipo.startswith("multipart/form-data"):
            msg = BytesParser(policy=politica_email).parsebytes(
                f"Content-Type: {tipo}\r\n\r\n".encode() + bruto)
            dados = {}
            for parte in msg.iter_parts():
                nome = parte.get_param("name", header="content-disposition")
                if not nome:
                    continue
                conteudo = parte.get_payload(decode=True) or b""
                dados[nome] = conteudo if parte.get_filename() else conteudo.decode("utf-8", "replace")
            return dados
        return {k: v[0] for k, v in parse_qs(bruto.decode("utf-8", "replace")).items()}

    # ---------- webhooks (não usam a senha do painel: têm token/assinatura próprios) ----------
    def _webhook_get(self, q: dict) -> bool:
        if urlparse(self.path).path != "/webhook/whatsapp":
            return False
        esperado = self.eco.cfg["whatsapp"]["cloud_api"].get("verify_token", "")
        if q.get("hub.mode") == "subscribe" and webhook.token_valido(q.get("hub.verify_token"), esperado):
            self._responder(q.get("hub.challenge", ""), "text/plain")
        else:
            self._responder("token inválido", "text/plain", HTTPStatus.FORBIDDEN)
        return True

    def _webhook_post(self) -> bool:
        url = urlparse(self.path)
        if url.path not in ("/webhook/whatsapp", "/webhook/evolution"):
            return False
        corpo = self._corpo()
        wa = self.eco.cfg["whatsapp"]
        if url.path == "/webhook/whatsapp":
            ok = webhook.assinatura_valida(corpo, self.headers.get("X-Hub-Signature-256"),
                                           wa["cloud_api"].get("app_secret", ""))
            leitor = webhook.ler_cloud
        else:
            token = parse_qs(url.query).get("token", [None])[0] or self.headers.get("X-EcoZap-Token")
            ok = webhook.token_valido(token, wa["evolution"].get("webhook_token", ""))
            leitor = webhook.ler_evolution
        if not ok:
            log.warning("Webhook recusado (assinatura/token inválido) de %s", self.address_string())
            self._responder("não autorizado", "text/plain", HTTPStatus.FORBIDDEN)
            return True
        try:
            mensagens, status = leitor(json.loads(corpo or b"{}"))
        except (ValueError, KeyError, TypeError):
            self._responder("json inválido", "text/plain", HTTPStatus.BAD_REQUEST)
            return True
        self._responder("ok", "text/plain")  # responde logo: a Meta exige resposta rápida

        def processar():
            for st in status:
                self.eco.receber_status(st)
            for m in mensagens:
                try:
                    self.eco.receber_mensagem(m)
                except Exception:
                    log.exception("Erro ao tratar mensagem de %s", m.telefone)
        threading.Thread(target=processar, name="webhook", daemon=True).start()
        return True

    def _negar(self):
        self._responder("Acesso negado", "text/plain; charset=utf-8", HTTPStatus.UNAUTHORIZED,
                        {"WWW-Authenticate": 'Basic realm="EcoZap"'})

    # ---------- rotas ----------
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if self._webhook_get(q):
            return
        if not self._autorizado():
            return self._negar()
        try:
            if url.path == "/promocoes":
                return self._promocoes(q)
            if url.path == "/mensagens":
                return self._mensagens()
            if url.path == "/cartaz":
                return self._responder(cartaz.gerar_pdf(self.eco.cfg), "application/pdf",
                                       extra={"Content-Disposition": 'inline; filename="cartaz-ecozap.pdf"'})
            if url.path == "/":
                return self._pagina_inicial(q)
            if url.path.startswith("/pdf/"):
                return self._pdf(url.path[5:])
            if url.path == "/relatorio":
                return self._relatorio(q)
            if url.path == "/geral":
                return self._geral()
            if url.path == "/api/estatisticas":
                return self._json(self._minhas_estatisticas())
            if url.path == "/api/comprovantes":
                return self._json([self._resumo(c) for c in self.eco.armazenamento.listar(
                    min(int(q.get("limite", 50)), 500), q.get("busca", ""), q.get("status") or None)])
            self._responder("Página não encontrada", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
        except ValueError as e:
            self._responder(_e(e), "text/plain; charset=utf-8", HTTPStatus.BAD_REQUEST)

    def do_POST(self):
        try:
            if self._webhook_post():
                return
        except ValueError as e:
            return self._responder(str(e), "text/plain", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if not self._autorizado():
            return self._negar()
        if not self._mesma_origem():
            return self._responder("Origem não permitida", "text/plain", HTTPStatus.FORBIDDEN)
        url = urlparse(self.path)
        if url.path.startswith("/promocoes"):
            return self._post_promocoes(url.path)
        dados = self._form()
        api = url.path.startswith("/api/")
        ident = dados.get("id") if api else url.path.rsplit("/", 1)[-1]
        if not ident or not _ID.match(ident):
            return self._json({"erro": "id inválido"}, HTTPStatus.BAD_REQUEST)
        acao = url.path.rsplit("/", 2)[-2] if not api else url.path.removeprefix("/api/")
        try:
            if acao == "enviar":
                comp = self.eco.enviar(ident, dados.get("telefone") or None,
                                       (dados.get("nome") or "").strip() or None,
                                       dados.get("formato") or None)
                msg = f"Enviado para {tel.formatar(comp.telefone)}"
            elif acao == "descartar":
                self.eco.marcar(ident, "descartado")
                msg = "Comprovante descartado"
            else:
                return self._json({"erro": "ação desconhecida"}, HTTPStatus.NOT_FOUND)
        except Exception as e:  # erro de envio vira mensagem na tela
            if api:
                return self._json({"erro": str(e)}, HTTPStatus.BAD_REQUEST)
            return self._redirecionar(f"/?erro={quote(str(e))}")
        if api:
            return self._json({"ok": True, "mensagem": msg})
        self._redirecionar(f"/?msg={quote(msg)}")

    # ---------- páginas ----------
    def _minhas_estatisticas(self) -> dict:
        hoje = date.today()
        mes = relatorio.resumir(self.eco.armazenamento, hoje.year, hoje.month, self.eco.cfg["nome_loja"])
        return {"loja": self.eco.cfg["nome_loja"], "caixa": self.eco.cfg.get("identificacao_caixa", ""),
                **self.eco.armazenamento.estatisticas(),
                "mes_enviados": mes.enviados, "mes_metros": mes.metros}

    @staticmethod
    def _resumo(c) -> dict:
        return {"id": c.id, "criado_em": c.criado_em, "telefone": c.telefone, "nome": c.nome,
                "status": c.status, "origem": c.origem, "formato": c.formato,
                "papel_mm": c.papel_mm, "link_nfce": c.link_nfce, "chave_nfce": c.chave_nfce,
                "ultimo_erro": c.ultimo_erro}

    def _layout(self, titulo: str, corpo: str) -> str:
        cfg = self.eco.cfg
        return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(titulo)}</title>
<style>{_CSS}</style></head><body>
<header><h1>🌱 EcoZap</h1><span>{_e(cfg['nome_loja'])} · {_e(cfg.get('identificacao_caixa', ''))}</span>
<nav><a href="/">Comprovantes</a><a href="/mensagens">Mensagens</a><a href="/promocoes">Promoções</a>
<a href="/geral">Visão geral</a><a href="/cartaz">Cartaz do balcão</a>
<a href="/relatorio">Relatório do mês (PDF)</a><a href="/relatorio?tipo=jpg">Imagem para redes</a></nav></header>
<main>{corpo}</main>
<script>/* atualiza a lista a cada 30 s, se você não estiver digitando */
setInterval(()=>{{const a=document.activeElement;if(!a||!['INPUT','SELECT'].includes(a.tagName))location.reload()}},30000)</script>
</body></html>"""

    def _pagina_inicial(self, q: dict):
        arm = self.eco.armazenamento
        est = arm.estatisticas()
        busca, status = q.get("busca", ""), q.get("status", "")
        linhas = []
        for c in arm.listar(200, busca, status or None):
            fmt = c.formato or self.eco.cfg["formato_padrao"]
            opcoes = "".join(f'<option value="{f}"{" selected" if f == fmt else ""}>{f.upper()}</option>'
                             for f in ("pdf", "jpeg", "png"))
            nota = f'<a href="{_e(c.link_nfce)}" target="_blank" rel="noopener">🧾 nota</a>' if c.link_nfce else ""
            erro = f'<div class="cinza">{_e(c.ultimo_erro)}</div>' if c.status == "erro" else ""
            entrega = f'<div class="cinza">{_e(c.entrega)}</div>' if c.entrega and c.status == "enviado" else ""
            linhas.append(f"""<tr><td>{_e(c.criado_em.replace('T', ' '))}<div class="cinza">código {_e(c.codigo)}</div></td>
<td>{STATUS_ROTULO.get(c.status, _e(c.status))}{erro}{entrega}</td><td>{_e(c.origem)} {nota}</td>
<td><a href="/pdf/{_e(c.id)}" target="_blank">abrir</a></td>
<td><form method="post" action="/enviar/{_e(c.id)}" style="display:flex;gap:4px;flex-wrap:wrap">
<input name="telefone" list="recentes" placeholder="(11) 98765-4321" value="{_e(tel.formatar(c.telefone) if c.telefone else '')}" size="18">
<input name="nome" placeholder="Nome" value="{_e(c.nome)}" size="14">
<select name="formato">{opcoes}</select><button>📲 Enviar</button></form></td>
<td><form method="post" action="/descartar/{_e(c.id)}"><button style="background:#999">✕</button></form></td></tr>""")
        aviso = ""
        if q.get("msg"):
            aviso = f'<div class="msg">{_e(q["msg"])}</div>'
        if q.get("erro"):
            aviso = f'<div class="msg erro">Erro: {_e(q["erro"])}</div>'
        filtros = "".join(f'<option value="{s}"{" selected" if s == status else ""}>{r}</option>'
                          for s, r in [("", "Todos")] + list(STATUS_ROTULO.items()))
        recentes = "".join(f'<option value="{_e(tel.formatar(m.telefone))}">{_e(m.nome or "")} - {_e((m.texto or "")[:30])}</option>'
                           for m in arm.mensagens_recentes(30))
        corpo = f"""{aviso}<datalist id="recentes">{recentes}</datalist><div class="cards">
<div class="card"><b>{est['pendentes']}</b>pendentes / com erro</div>
<div class="card"><b>{est['enviados']}</b>comprovantes digitais</div>
<div class="card"><b>{est['papel_economizado_m']} m</b>de papel economizados</div>
<div class="card"><b>{est['guardados']}</b>guardados ({self.eco.cfg['dias_retencao']} dias)</div></div>
<form style="margin-bottom:12px"><input name="busca" value="{_e(busca)}" placeholder="telefone, nome, valor..." size="30">
<select name="status">{filtros}</select> <button>Buscar</button></form>
<table><tr><th>Data</th><th>Situação</th><th>Origem</th><th>Arquivo</th><th>Enviar / reenviar</th><th></th></tr>
{''.join(linhas) or '<tr><td colspan="6">Nenhum comprovante.</td></tr>'}</table>"""
        self._responder(self._layout("EcoZap", corpo))

    def _pdf(self, ident: str):
        comp = self.eco.armazenamento.obter(ident) if _ID.match(ident) else None
        if not comp:
            return self._responder("Não encontrado", "text/plain", HTTPStatus.NOT_FOUND)
        self._responder(comp.pdf, "application/pdf",
                        extra={"Content-Disposition": f'inline; filename="{ident}.pdf"'})

    def _relatorio(self, q: dict):
        ano, mes = relatorio.mes_de_texto(q.get("mes"))
        r = relatorio.resumir(self.eco.armazenamento, ano, mes, self.eco.cfg["nome_loja"])
        if q.get("tipo") == "jpg":
            return self._responder(relatorio.gerar_imagem(r), "image/jpeg")
        self._responder(relatorio.gerar_pdf(r), "application/pdf",
                        extra={"Content-Disposition": f'inline; filename="ecozap-{ano}-{mes:02d}.pdf"'})

    def _geral(self):
        resultados = [("Este caixa", self._minhas_estatisticas(), None)]
        for caixa in self.eco.cfg["painel"].get("caixas", []):
            resultados.append((caixa.get("nome", caixa["url"]), *buscar_remoto(caixa)))
        linhas, soma_env, soma_m, soma_mes = [], 0, 0.0, 0
        for nome, est, erro in resultados:
            if erro:
                linhas.append(f"<tr><td>{_e(nome)}</td><td colspan='5' class='erro'>{_e(erro)}</td></tr>")
                continue
            soma_env += est["enviados"]; soma_m += est["papel_economizado_m"]; soma_mes += est["mes_enviados"]
            linhas.append(f"<tr><td>{_e(nome)}</td><td>{_e(est['loja'])} · {_e(est['caixa'])}</td>"
                          f"<td>{est['pendentes']}</td><td>{est['mes_enviados']}</td>"
                          f"<td>{est['enviados']}</td><td>{est['papel_economizado_m']} m</td></tr>")
        corpo = f"""<div class="cards"><div class="card"><b>{soma_mes}</b>enviados neste mês (todos)</div>
<div class="card"><b>{soma_env}</b>enviados no total</div><div class="card"><b>{round(soma_m, 2)} m</b>de papel economizados</div></div>
<table><tr><th>Caixa</th><th>Loja</th><th>Pendentes</th><th>Mês</th><th>Total</th><th>Papel</th></tr>{''.join(linhas)}</table>
<p class="cinza">Para incluir outros caixas, liste-os em <code>painel.caixas</code> no config.json.</p>"""
        self._responder(self._layout("EcoZap - visão geral", corpo))


    # ---------- mensagens e promoções ----------
    def _mensagens(self):
        linhas = "".join(
            f"<tr><td>{_e(m.recebida_em.replace('T', ' '))}</td><td>{_e(tel.formatar(m.telefone))}</td>"
            f"<td>{_e(m.nome)}</td><td>{_e(m.texto)}</td><td>{_e(ACOES.get(m.acao or '', m.acao))}</td></tr>"
            for m in self.eco.armazenamento.mensagens_recentes(24 * 60, 300))
        corpo = f"""<h2>Mensagens recebidas (últimas 24h)</h2>
<p class="cinza">Quem escreveu nas últimas 24h pode receber comprovante sem template (atendimento gratuito na API oficial).</p>
<table><tr><th>Quando</th><th>WhatsApp</th><th>Nome</th><th>Mensagem</th><th>O que o EcoZap fez</th></tr>
{linhas or '<tr><td colspan="5">Nenhuma mensagem. Configure o webhook (veja o README).</td></tr>'}</table>"""
        self._responder(self._layout("EcoZap - mensagens", corpo))

    def _promocoes(self, q: dict):
        from .promocoes import Disparador
        disp = self.disparador or Disparador(self.eco)
        arm = self.eco.armazenamento
        linhas = []
        for c in arm.listar_campanhas():
            botoes = ""
            if c.status in ("agendada", "enviando"):
                botoes += f'<form method="post" action="/promocoes/pausar/{c.id}" style="display:inline"><button>⏸</button></form> '
            if c.status == "pausada":
                botoes += f'<form method="post" action="/promocoes/retomar/{c.id}" style="display:inline"><button>▶</button></form> '
            if c.status in ("agendada", "enviando", "pausada"):
                botoes += f'<form method="post" action="/promocoes/cancelar/{c.id}" style="display:inline"><button style="background:#999">✖</button></form>'
            linhas.append(f"<tr><td>{c.id}</td><td>{_e(c.titulo)}<div class='cinza'>{_e(c.texto[:80])}</div></td>"
                          f"<td>{STATUS_CAMPANHA.get(c.status, _e(c.status))}</td><td>{_e(c.agendada_para.replace('T', ' '))}</td>"
                          f"<td>{c.enviados}/{c.total}<div class='cinza'>{c.falhas} falha(s), {c.pulados} pulado(s)</div></td>"
                          f"<td>{botoes}</td></tr>")
        cfg = self.eco.cfg["promocoes"]
        aviso = f'<div class="msg">{_e(q["msg"])}</div>' if q.get("msg") else ""
        if q.get("erro"):
            aviso = f'<div class="msg erro">Erro: {_e(q["erro"])}</div>'
        corpo = f"""{aviso}<div class="cards"><div class="card"><b>{disp.previa()}</b>clientes podem receber agora</div>
<div class="card"><b>{arm.promocoes_enviadas_hoje()}/{cfg['limite_diario']}</b>enviadas hoje</div>
<div class="card"><b>{_e(cfg['horario_inicio'])}–{_e(cfg['horario_fim'])}</b>horário de envio</div></div>
<h2>Nova promoção</h2>
<form method="post" action="/promocoes/criar" enctype="multipart/form-data" style="display:grid;gap:8px;max-width:640px">
<input name="titulo" placeholder="Título interno (ex.: Black Friday)" required>
<textarea name="texto" rows="5" placeholder="Olá{{nome}}! Só hoje: pão francês com 20% de desconto 🥖" required></textarea>
<label>Imagem (opcional): <input type="file" name="imagem" accept="image/*"></label>
<label>Só para quem comprou nos últimos <input name="dias_recentes" type="number" min="1" size="4"> dias (vazio = todos que aceitaram)</label>
<label>Agendar para: <input name="agendar" type="datetime-local"> (vazio = agora)</label>
<button>Criar promoção</button></form>
<p class="cinza">Só recebe quem respondeu PROMO (ou autorizou no balcão). Toda mensagem termina com "responda SAIR".
No máximo 1 promoção a cada {cfg['dias_entre_promocoes']} dias por cliente. Use {{nome}} e {{loja}} no texto.</p>
<h2>Campanhas</h2><table><tr><th>#</th><th>Promoção</th><th>Situação</th><th>Quando</th><th>Enviadas</th><th></th></tr>
{''.join(linhas) or '<tr><td colspan="6">Nenhuma campanha ainda.</td></tr>'}</table>"""
        self._responder(self._layout("EcoZap - promoções", corpo))

    def _post_promocoes(self, caminho: str):
        from .promocoes import Disparador
        disp = self.disparador or Disparador(self.eco)
        try:
            dados = self._form()
            partes = caminho.strip("/").split("/")
            if partes[1:2] == ["criar"]:
                agendar = dados.get("agendar") or ""
                camp = disp.criar(dados.get("titulo", ""), dados.get("texto", ""),
                                  dados.get("imagem") or None,
                                  int(dados["dias_recentes"]) if dados.get("dias_recentes") else None,
                                  datetime.fromisoformat(agendar) if agendar else None)
                msg = f"Promoção criada para {camp.total} cliente(s)"
            elif len(partes) == 3 and partes[1] in ("pausar", "retomar", "cancelar") and partes[2].isdigit():
                getattr(disp, partes[1])(int(partes[2]))
                msg = "Campanha atualizada"
            else:
                return self._responder("Ação desconhecida", "text/plain", HTTPStatus.NOT_FOUND)
        except Exception as e:
            return self._redirecionar(f"/promocoes?erro={quote(str(e))}")
        self._redirecionar(f"/promocoes?msg={quote(msg)}")


def buscar_remoto(caixa: dict) -> tuple[dict | None, str | None]:
    """Busca /api/estatisticas de outro caixa. Retorna (dados, erro)."""
    req = urllib.request.Request(caixa["url"].rstrip("/") + "/api/estatisticas")
    if caixa.get("senha"):
        token = base64.b64encode(f"{caixa.get('usuario', 'admin')}:{caixa['senha']}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()), None
    except Exception as e:
        return None, f"sem resposta ({e})"


class Painel:
    def __init__(self, eco: EcoZap, disparador=None):
        self.eco = eco
        self.disparador = disparador
        self._servidor: ThreadingHTTPServer | None = None

    @property
    def endereco(self) -> str:
        p = self.eco.cfg["painel"]
        host = "127.0.0.1" if p["host"] in ("0.0.0.0", "") else p["host"]
        porta = self._servidor.server_address[1] if self._servidor else p["porta"]
        return f"http://{host}:{porta}"

    def iniciar(self) -> None:
        p = self.eco.cfg["painel"]
        handler = type("Handler", (_Handler,), {"eco": self.eco, "disparador": self.disparador})
        self._servidor = ThreadingHTTPServer((p["host"], int(p["porta"])), handler)
        threading.Thread(target=self._servidor.serve_forever, name="painel", daemon=True).start()
        log.info("Painel web em %s", self.endereco)

    def parar(self) -> None:
        if self._servidor:
            self._servidor.shutdown()
            self._servidor.server_close()

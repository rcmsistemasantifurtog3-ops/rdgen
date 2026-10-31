"""Regra de negócio: recebe o trabalho de impressão, guarda e envia."""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

from typing import Callable

from . import clientes, conversor, nfce, telefone as tel
from .armazenamento import Armazenamento, Comprovante
from .webhook import MensagemRecebida, StatusEntrega
from .whatsapp import ErroEnvio, Provedor, criar_provedor

log = logging.getLogger("ecozap")


PALAVRAS_SAIR = {"SAIR", "PARAR", "STOP", "CANCELAR", "DESCADASTRAR", "NAO QUERO", "NAO QUERO PROMO"}
PALAVRAS_PROMO = {"PROMO", "PROMOCAO", "PROMOCOES", "OFERTA", "OFERTAS", "QUERO PROMO",
                  "QUERO PROMOCOES", "QUERO OFERTAS"}
PALAVRAS_SEGUNDA_VIA = ("2 VIA", "2A VIA", "SEGUNDA VIA", "COMPROVANTE", "RECIBO", "NOTA", "CUPOM")
_CODIGO = re.compile(r"\b([A-HJ-NP-Z2-9]{4})\b")


def normalizar_texto(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().upper()
    texto = re.sub(r"[^A-Z0-9 ]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def traduzir_erro(msg: str) -> str:
    """Deixa os erros mais comuns da Meta em português claro."""
    if "131047" in msg or "Re-engagement" in msg or "24 hours" in msg:
        return ("Fora da janela de 24h: o cliente precisa mandar uma mensagem para a loja "
                "primeiro (cartaz do balcão), ou configure um template aprovado. " + msg)
    if "131026" in msg:
        return "Este número não tem WhatsApp ou não pode receber mensagens. " + msg
    if "190" in msg and "token" in msg.lower():
        return "Token da Cloud API inválido ou expirado. " + msg
    return msg


def _slug(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", texto).strip("-").lower() or "loja"


class EcoZap:
    def __init__(self, cfg: dict, provedor: Provedor | None = None):
        self.cfg = cfg
        self.armazenamento = Armazenamento(cfg["pasta_dados"], cfg["dias_retencao"],
                                           cfg.get("dias_retencao_clientes", 365))
        self.resumo_cpf = clientes.ResumoCPF(cfg["pasta_dados"])
        self._provedor = provedor
        self.pausado = threading.Event()  # pausa o recebimento (menu da bandeja)
        self.ouvintes: list[Callable[[str, object], None]] = []  # avisos para a tela

    @property
    def provedor(self) -> Provedor:
        if self._provedor is None:  # cria só quando for enviar (config pode estar incompleta)
            self._provedor = criar_provedor(self.cfg)
        return self._provedor

    def reconfigurar(self, cfg: dict) -> None:
        """Aplica uma configuração nova (tela de Configurações) sem reiniciar."""
        self.cfg = cfg
        self._provedor = None
        self.armazenamento.dias_retencao = int(cfg["dias_retencao"])
        self.armazenamento.dias_retencao_clientes = int(cfg["dias_retencao_clientes"])

    # 1) chegou um trabalho da impressora
    def receber(self, dados: bytes, papel: str | None = None) -> Comprovante:
        doc = conversor.preparar(
            dados, papel or self.cfg["tamanho_papel_padrao"],
            rodape=self.cfg.get("rodape_eco", ""),
            codificacao=self.cfg["codificacao_texto"])
        nota = nfce.analisar(doc.texto, doc.pdf if doc.origem == "pdf" else None)
        cpf_hash = self.resumo_cpf(clientes.extrair_cpf(doc.texto))
        numero = tel.extrair(doc.texto)
        nome = clientes.extrair_nome(doc.texto)

        # agenda: CPF já conhecido -> telefone e nome aparecem sozinhos
        conhecido = (self.armazenamento.cliente_por_telefone(numero) if numero
                     else self.armazenamento.cliente_por_cpf(cpf_hash))
        if conhecido:
            numero = numero or conhecido.telefone
            nome = nome or conhecido.nome

        comp = self.armazenamento.salvar(doc.pdf, doc.texto, doc.origem, numero,
                                         nome=nome, cpf_hash=cpf_hash,
                                         chave_nfce=nota.chave, link_nfce=nota.link)
        log.info("Comprovante %s recebido (%s, %.0f mm)%s%s%s", comp.id, doc.origem,
                 comp.papel_mm, f", telefone {numero}" if numero else "",
                 " [agenda]" if conhecido else "", f", {nota.modelo}" if nota.modelo else "")
        return comp

    def receber_arquivo(self, caminho: Path) -> Comprovante:
        return self.receber(Path(caminho).read_bytes())

    # 2) refazer o layout em outro tamanho (só para cupons de texto)
    def trocar_papel(self, ident: str, papel: str) -> Comprovante:
        comp = self.armazenamento.obter(ident)
        if comp.origem != "texto":
            raise ValueError("Só dá para trocar o tamanho de cupons em texto; "
                             "PDF já vem com o tamanho escolhido na impressão.")
        pdf = conversor.texto_para_pdf(comp.texto, papel, self.cfg.get("rodape_eco", ""))
        self.armazenamento.substituir_pdf(ident, pdf)
        return self.armazenamento.obter(ident)

    # 3) enviar / reenviar
    def _valores(self, comp: Comprovante | None, nome: str | None) -> dict:
        return {"nome": f", {nome.split()[0]}" if nome else "",
                "nome_completo": nome or "",
                "loja": self.cfg["nome_loja"],
                "link": (comp.link_nfce if comp else "") or "",
                "link_nfce": (comp.link_nfce if comp else "") or "",
                "chave": (comp.chave_nfce if comp else "") or "",
                "caixa": self.cfg.get("identificacao_caixa", "")}

    def _avisar(self, evento: str, dados: object) -> None:
        for ouvinte in list(self.ouvintes):
            try:
                ouvinte(evento, dados)
            except Exception:
                log.debug("ouvinte falhou", exc_info=True)

    def _formatar(self, modelo: str, comp=None, nome=None) -> str:
        try:
            return modelo.format(**self._valores(comp, nome))
        except (KeyError, IndexError, ValueError):
            return modelo  # texto com chaves "{...}" desconhecidas: manda como está

    def legenda(self, nome: str | None, comp: Comprovante | None = None,
                convidar: bool = False) -> str:
        v = self._valores(comp, nome)
        texto = self._formatar(self.cfg["mensagem"], comp, nome)
        if v["link"]:
            texto += self._formatar(self.cfg.get("mensagem_nfce", ""), comp, nome)
        elif v["chave"]:  # sem link, ao menos a chave para consultar no portal
            texto += self._formatar(self.cfg.get("mensagem_chave", ""), comp, nome)
        if convidar:
            texto += self.cfg["atendimento"]["convite_promocoes"]
        return texto

    def _deve_convidar(self, telefone: str) -> bool:
        """Convida para promoções só quem nunca respondeu (nem PROMO, nem SAIR)."""
        if not self.cfg["promocoes"]["ativo"]:
            return False
        cli = self.armazenamento.cliente_por_telefone(telefone)
        return not (cli and cli.consentimento_em)

    def enviar(self, ident: str, telefone: str | None = None, nome: str | None = None,
               formato: str | None = None) -> Comprovante:
        comp = self.armazenamento.obter(ident)
        if comp is None:
            raise ValueError(f"Comprovante {ident} não encontrado (pode ter sido limpo)")
        numero = tel.normalizar(telefone or comp.telefone or "")
        if not numero:
            raise ValueError("Telefone inválido. Use DDD + número, ex.: (11) 98765-4321")
        formato = formato or comp.formato or self.cfg["formato_padrao"]
        nome = nome if nome is not None else comp.nome
        self.armazenamento.atualizar(ident, telefone=numero, nome=nome, formato=formato)
        comp = self.armazenamento.obter(ident)

        conteudo, ext, mime = conversor.exportar(
            conversor.Documento(comp.pdf, comp.texto, comp.origem, 1), formato)
        data = datetime.fromisoformat(comp.criado_em).strftime("%Y%m%d-%H%M")
        nome_arquivo = f"comprovante-{_slug(self.cfg['nome_loja'])}-{data}.{ext}"
        try:
            provedor = self.provedor
            janela = self.armazenamento.janela_aberta(numero)
            cloud = self.cfg["whatsapp"]["cloud_api"]
            template = params = None
            if getattr(provedor, "exige_template_fora_da_janela", False) and not janela and cloud.get("template"):
                template = cloud["template"]
                params = [self._formatar(p, comp, nome) for p in cloud.get("parametros_template", ["{loja}"])]
            convidar = (template is None) and self._deve_convidar(numero)
            msg_id = provedor.enviar(numero, conteudo, nome_arquivo, mime,
                                     self.legenda(nome, comp, convidar),
                                     template=template, parametros_template=params)
        except (ErroEnvio, OSError) as e:
            erro = traduzir_erro(str(e))
            self.armazenamento.registrar_envio(ident, False, erro)
            log.error("Falha ao enviar %s para %s: %s", ident, numero, erro)
            self._avisar("erro_envio", self.armazenamento.obter(ident))
            raise ErroEnvio(erro) from e
        self.armazenamento.registrar_envio(ident, True, msg_id=msg_id)
        log.info("Comprovante %s enviado para %s (%s%s)", ident, numero, msg_id,
                 ", template" if template else "")
        comp = self.armazenamento.obter(ident)
        self._avisar("enviado", comp)
        return comp

    # 4) atendimento: o cliente escreveu para a loja
    def responder(self, telefone: str, modelo: str, nome: str | None = None) -> bool:
        """Resposta automática (texto livre: o cliente acabou de escrever)."""
        if not self.cfg["atendimento"]["respostas_automaticas"] or not modelo:
            return False
        try:
            self.provedor.enviar_texto(telefone, self._formatar(modelo, None, nome))
            self.armazenamento.marcar_resposta_auto(telefone)
            return True
        except Exception as e:
            log.warning("Resposta automática para %s falhou: %s", telefone, e)
            return False

    def receber_mensagem(self, m: MensagemRecebida) -> str:
        """Decide o que fazer com a mensagem do cliente. Retorna a ação tomada."""
        numero = tel.normalizar(m.telefone)
        if not numero:
            return "ignorada"
        arm, at = self.armazenamento, self.cfg["atendimento"]
        ident = arm.registrar_mensagem(numero, m.nome, m.texto, m.quando)
        cli = arm.cliente_por_telefone(numero)
        nome = (cli.nome_exibicao if cli else None) or m.nome
        texto = normalizar_texto(m.texto)
        acao = "registrada"

        if texto in PALAVRAS_SAIR or texto.startswith("SAIR"):
            arm.definir_promocoes(numero, False, f"whatsapp: {m.texto[:40]}")
            self.responder(numero, at["promo_cancelada"], nome)
            acao = "promocoes_cancelada"
        elif texto in PALAVRAS_PROMO:
            arm.definir_promocoes(numero, True, f"whatsapp: {m.texto[:40]}")
            self.responder(numero, at["promo_aceita"], nome)
            acao = "promocoes_aceita"
        else:
            acao = self._entregar_comprovante(numero, nome, m.texto, texto)
        arm.marcar_acao_mensagem(ident, acao)
        log.info("Mensagem de %s: %r -> %s", numero, (m.texto or "")[:60], acao)
        self._avisar("mensagem", {"telefone": numero, "nome": nome, "texto": m.texto, "acao": acao})
        return acao

    def _entregar_comprovante(self, numero: str, nome: str | None, original: str, texto: str) -> str:
        arm, at = self.armazenamento, self.cfg["atendimento"]
        # a) mandou o código do comprovante (aparece na tela do caixa)
        for codigo in _CODIGO.findall(original.upper()):
            comp = arm.por_codigo(codigo)
            if comp and (comp.telefone in (None, numero)):
                return self._tentar_enviar(comp.id, numero, nome, "comprovante_por_codigo")
        # b) só existe UM comprovante esperando cliente nos últimos minutos: é dele
        pendentes = arm.pendentes_sem_telefone(int(at["minutos_associacao"]))
        if len(pendentes) == 1:
            return self._tentar_enviar(pendentes[0].id, numero, nome, "comprovante_associado")
        if len(pendentes) > 1:  # o caixa escolhe na tela (lista "mensagens recentes")
            if arm.pode_responder_auto(numero, horas=1):
                self.responder(numero, at["comprovante_a_caminho"], nome)
            return "aguardando_caixa"
        # c) pediu segunda via
        if any(p in texto for p in PALAVRAS_SEGUNDA_VIA):
            ultimo = arm.ultimo_do_telefone(numero)
            if ultimo:
                return self._tentar_enviar(ultimo.id, numero, nome, "segunda_via")
        # d) só disse oi: boas-vindas (no máximo 1 a cada 12h)
        if arm.pode_responder_auto(numero):
            modelo = at["boas_vindas"] + (at["convite_promocoes"] if self._deve_convidar(numero) else "")
            if self.responder(numero, modelo, nome):
                return "boas_vindas"
        return "registrada"

    def _tentar_enviar(self, ident: str, numero: str, nome: str | None, acao: str) -> str:
        try:
            self.enviar(ident, numero, nome)
            return acao
        except Exception as e:
            log.error("Não consegui entregar %s para %s: %s", ident, numero, e)
            return f"{acao}_falhou"

    def receber_status(self, s: StatusEntrega) -> None:
        if self.armazenamento.atualizar_entrega(s.msg_id, s.entrega):
            self._avisar("entrega", s)

    def marcar(self, ident: str, status: str) -> None:
        self.armazenamento.atualizar(ident, status=status)

    def limpar(self) -> int:
        qtd = self.armazenamento.limpar_antigos()
        if qtd:
            log.info("Limpeza: %d comprovante(s) com mais de %d dias apagados",
                     qtd, self.armazenamento.dias_retencao)
        return qtd

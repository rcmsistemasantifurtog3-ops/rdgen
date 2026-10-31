"""Regra de negócio: recebe o trabalho de impressão, guarda e envia."""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

from . import clientes, conversor, nfce, telefone as tel
from .armazenamento import Armazenamento, Comprovante
from .whatsapp import ErroEnvio, Provedor, criar_provedor

log = logging.getLogger("ecozap")


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

    def legenda(self, nome: str | None, comp: Comprovante | None = None) -> str:
        v = self._valores(comp, nome)
        texto = self.cfg["mensagem"].format(**v)
        if v["link"]:
            texto += self.cfg.get("mensagem_nfce", "").format(**v)
        elif v["chave"]:  # sem link, ao menos a chave para consultar no portal
            texto += self.cfg.get("mensagem_chave", "").format(**v)
        return texto

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
            kwargs = {}
            cloud = self.cfg["whatsapp"]["cloud_api"]
            if self.provedor.nome == "cloud_api" and cloud.get("template"):
                v = self._valores(comp, nome)
                kwargs["parametros_template"] = [
                    (p.format(**v) or "-") for p in cloud.get("parametros_template", ["{loja}"])]
            msg_id = self.provedor.enviar(numero, conteudo, nome_arquivo, mime,
                                          self.legenda(nome, comp), **kwargs)
        except (ErroEnvio, OSError) as e:
            self.armazenamento.registrar_envio(ident, False, str(e))
            log.error("Falha ao enviar %s para %s: %s", ident, numero, e)
            raise ErroEnvio(str(e)) from e
        self.armazenamento.registrar_envio(ident, True)
        log.info("Comprovante %s enviado para %s (%s)", ident, numero, msg_id)
        return self.armazenamento.obter(ident)

    def marcar(self, ident: str, status: str) -> None:
        self.armazenamento.atualizar(ident, status=status)

    def limpar(self) -> int:
        qtd = self.armazenamento.limpar_antigos()
        if qtd:
            log.info("Limpeza: %d comprovante(s) com mais de %d dias apagados",
                     qtd, self.armazenamento.dias_retencao)
        return qtd

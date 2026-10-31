"""Regra de negócio: recebe o trabalho de impressão, guarda e envia."""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import conversor, telefone as tel
from .armazenamento import Armazenamento, Comprovante
from .whatsapp import ErroEnvio, Provedor, criar_provedor

log = logging.getLogger("ecozap")


def _slug(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", texto).strip("-").lower() or "loja"


class EcoZap:
    def __init__(self, cfg: dict, provedor: Provedor | None = None):
        self.cfg = cfg
        self.armazenamento = Armazenamento(cfg["pasta_dados"], cfg["dias_retencao"])
        self._provedor = provedor

    @property
    def provedor(self) -> Provedor:
        if self._provedor is None:  # cria só quando for enviar (config pode estar incompleta)
            self._provedor = criar_provedor(self.cfg)
        return self._provedor

    # 1) chegou um trabalho da impressora
    def receber(self, dados: bytes, papel: str | None = None) -> Comprovante:
        doc = conversor.preparar(
            dados, papel or self.cfg["tamanho_papel_padrao"],
            rodape=self.cfg.get("rodape_eco", ""),
            codificacao=self.cfg["codificacao_texto"])
        numero = tel.extrair(doc.texto)
        comp = self.armazenamento.salvar(doc.pdf, doc.texto, doc.origem, numero)
        log.info("Comprovante %s recebido (%s, %.0f mm de papel)%s", comp.id,
                 doc.origem, comp.papel_mm, f", telefone {numero}" if numero else "")
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
        Path(comp.arquivo_pdf).write_bytes(pdf)
        return comp

    # 3) enviar / reenviar
    def legenda(self, nome: str | None) -> str:
        return self.cfg["mensagem"].format(
            nome=f", {nome.split()[0]}" if nome else "", loja=self.cfg["nome_loja"])

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

        conteudo, ext, mime = conversor.exportar(
            conversor.Documento(comp.pdf, comp.texto, comp.origem, 1), formato)
        data = datetime.fromisoformat(comp.criado_em).strftime("%Y%m%d-%H%M")
        nome_arquivo = f"comprovante-{_slug(self.cfg['nome_loja'])}-{data}.{ext}"
        try:
            kwargs = {}
            if self.provedor.nome == "cloud_api" and self.cfg["whatsapp"]["cloud_api"].get("template"):
                kwargs["parametros_template"] = [self.cfg["nome_loja"]]
            msg_id = self.provedor.enviar(numero, conteudo, nome_arquivo, mime,
                                          self.legenda(nome), **kwargs)
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

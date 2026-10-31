"""Ícone do EcoZap perto do relógio do Windows (bandeja do sistema).

Usa o pacote opcional `pystray`. Se ele não estiver instalado (ou o sistema
não tiver bandeja), o EcoZap funciona normalmente só com a janela.
"""
from __future__ import annotations

import logging
from typing import Callable

from . import icone

log = logging.getLogger("ecozap.bandeja")


class Bandeja:
    def __init__(self, acoes: dict[str, Callable[[], None]], pausado: Callable[[], bool],
                 painel_ativo: Callable[[], bool]):
        """`acoes`: abrir, pausar, relatorio, painel, configuracoes, sair.
        As funções são chamadas na thread da bandeja: devem só agendar o trabalho."""
        self.acoes, self.pausado, self.painel_ativo = acoes, pausado, painel_ativo
        self._icone = None

    def iniciar(self) -> bool:
        try:
            import pystray
        except ImportError:
            log.info("pystray não instalado: sem ícone na bandeja")
            return False
        Item, Menu = pystray.MenuItem, pystray.Menu
        menu = Menu(
            Item("Abrir EcoZap", lambda: self.acoes["abrir"](), default=True),
            Item("Pausar recebimento", lambda: self._alternar_pausa(),
                 checked=lambda _i: self.pausado()),
            Menu.SEPARATOR,
            Item("Relatório do mês", lambda: self.acoes["relatorio"]()),
            Item("Abrir painel web", lambda: self.acoes["painel"](),
                 visible=lambda _i: self.painel_ativo()),
            Item("Configurações", lambda: self.acoes["configuracoes"]()),
            Menu.SEPARATOR,
            Item("Sair do EcoZap", lambda: self.acoes["sair"]()),
        )
        try:
            self._icone = pystray.Icon("EcoZap", icone.gerar(64), "EcoZap - impressora verde", menu)
            self._icone.run_detached()
        except Exception:
            log.warning("Não foi possível criar o ícone da bandeja", exc_info=True)
            self._icone = None
            return False
        return True

    def _alternar_pausa(self) -> None:
        self.acoes["pausar"]()
        self.atualizar()

    def atualizar(self) -> None:
        if self._icone:
            pausado = self.pausado()
            self._icone.icon = icone.gerar(64, pausado)
            self._icone.title = "EcoZap - PAUSADO" if pausado else "EcoZap - impressora verde"

    def notificar(self, mensagem: str, titulo: str = "EcoZap") -> None:
        if self._icone:
            try:
                self._icone.notify(mensagem, titulo)
            except Exception:
                log.debug("Notificação falhou", exc_info=True)

    def parar(self) -> None:
        if self._icone:
            self._icone.stop()

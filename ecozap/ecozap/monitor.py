"""Vigia a pasta de entrada (onde a impressora virtual grava os trabalhos).

Como sabemos que o Windows terminou de escrever o arquivo?
1. O tamanho precisa ficar igual por duas leituras seguidas; e
2. Movemos o arquivo com os.replace(). No Windows isso FALHA enquanto o
   spooler ainda está com o arquivo aberto - então só pegamos trabalhos
   prontos (mesma ideia do printer-adapter deste repositório).
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

log = logging.getLogger("ecozap.monitor")
IGNORAR = {".tmp", ".part", ".crdownload"}


class Monitor:
    def __init__(self, pasta_entrada: str | Path, ao_receber: Callable[[Path], None],
                 intervalo: float = 0.5, pausado: threading.Event | None = None):
        self.entrada = Path(pasta_entrada)
        self.processando = self.entrada / "processando"
        self.com_erro = self.entrada / "com_erro"
        for p in (self.entrada, self.processando, self.com_erro):
            p.mkdir(parents=True, exist_ok=True)
        self.ao_receber = ao_receber
        self.intervalo = intervalo
        self.pausado = pausado or threading.Event()  # pausado: arquivos esperam na pasta
        self._tamanhos: dict[Path, int] = {}
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    def verificar_uma_vez(self) -> list[Path]:
        """Uma rodada de verificação. Retorna os arquivos que foram entregues."""
        entregues = []
        vistos = set()
        for arq in sorted(self.entrada.iterdir()):
            if not arq.is_file() or arq.suffix.lower() in IGNORAR:
                continue
            vistos.add(arq)
            try:
                tamanho = arq.stat().st_size
            except OSError:
                continue
            if tamanho == 0 or self._tamanhos.get(arq) != tamanho:
                self._tamanhos[arq] = tamanho  # ainda crescendo: espera a próxima rodada
                continue
            destino = self.processando / f"{uuid.uuid4().hex[:8]}_{arq.name}"
            try:
                os.replace(arq, destino)
            except OSError:
                continue  # spooler ainda escrevendo
            self._tamanhos.pop(arq, None)
            try:
                self.ao_receber(destino)
                destino.unlink(missing_ok=True)
                entregues.append(destino)
            except Exception:
                log.exception("Falha ao processar %s", arq.name)
                os.replace(destino, self.com_erro / destino.name)
        for antigo in set(self._tamanhos) - vistos:
            self._tamanhos.pop(antigo, None)
        return entregues

    def recuperar_pendentes(self) -> None:
        """Se o programa caiu no meio, devolve os arquivos para a fila."""
        for arq in self.processando.iterdir():
            if arq.is_file():
                os.replace(arq, self.entrada / arq.name.split("_", 1)[-1])

    def _laco(self) -> None:
        while not self._parar.is_set():
            try:
                if not self.pausado.is_set():
                    self.verificar_uma_vez()
            except Exception:
                log.exception("Erro no monitor")
            self._parar.wait(self.intervalo)

    def iniciar(self) -> None:
        self.recuperar_pendentes()
        self._thread = threading.Thread(target=self._laco, name="monitor", daemon=True)
        self._thread.start()
        log.info("Vigiando a pasta %s", self.entrada)

    def parar(self) -> None:
        self._parar.set()
        if self._thread:
            self._thread.join(timeout=5)

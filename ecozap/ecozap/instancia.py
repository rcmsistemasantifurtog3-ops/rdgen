"""Garante um EcoZap só por computador (por pasta de dados).

Se a interface e o modo serviço rodassem juntos, os dois disputariam os
arquivos da impressora. A trava é um arquivo bloqueado pelo sistema: se o
programa fechar (ou travar), o Windows libera sozinho.
"""
from __future__ import annotations

import sys
from pathlib import Path


class TravaInstancia:
    def __init__(self, pasta: str | Path):
        Path(pasta).mkdir(parents=True, exist_ok=True)
        self._arquivo = open(Path(pasta) / "ecozap.lock", "a+")

    def adquirir(self) -> bool:
        try:
            self._arquivo.seek(0)
            if sys.platform.startswith("win"):
                import msvcrt
                msvcrt.locking(self._arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def liberar(self) -> None:
        self._arquivo.close()

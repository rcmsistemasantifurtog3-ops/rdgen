"""Linha de comando do EcoZap.

    python -m ecozap                     abre a interface (padrão)
    python -m ecozap servico             roda sem tela (envia sozinho se achar o telefone)
    python -m ecozap enviar ARQ TEL      converte e envia um arquivo direto
    python -m ecozap historico [busca]   lista os últimos comprovantes
    python -m ecozap reenviar ID [TEL]   reenvia um comprovante guardado
    python -m ecozap limpar              apaga os mais velhos que a retenção
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from . import __version__, config, telefone as tel
from .monitor import Monitor
from .servico import EcoZap


def _servico(eco: EcoZap) -> None:
    def ao_receber(caminho: Path) -> None:
        comp = eco.receber_arquivo(caminho)
        if comp.telefone:
            try:
                eco.enviar(comp.id)
            except Exception as e:  # fica como "erro" no histórico para reenviar depois
                logging.error("Não enviado: %s", e)
        else:
            logging.warning("Sem telefone no comprovante %s - ficou pendente", comp.id)

    eco.limpar()
    mon = Monitor(eco.cfg["pasta_entrada"], ao_receber)
    mon.iniciar()
    parar = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: parar.set())
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    while not parar.wait(6 * 60 * 60):
        eco.limpar()
    mon.parar()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ecozap", description="EcoZap - impressora virtual que envia pelo WhatsApp")
    ap.add_argument("--config", help="caminho do config.json")
    ap.add_argument("--versao", action="version", version=f"EcoZap {__version__}")
    sub = ap.add_subparsers(dest="comando")
    sub.add_parser("interface", help="abre a janela (padrão)")
    sub.add_parser("servico", help="roda sem tela")
    p = sub.add_parser("enviar", help="converte e envia um arquivo")
    p.add_argument("arquivo")
    p.add_argument("telefone", nargs="?")
    p.add_argument("--formato", choices=config.FORMATOS)
    p.add_argument("--papel", choices=config.TAMANHOS)
    p.add_argument("--nome")
    p = sub.add_parser("historico", help="lista os comprovantes guardados")
    p.add_argument("busca", nargs="?", default="")
    p = sub.add_parser("reenviar", help="reenvia um comprovante guardado")
    p.add_argument("id")
    p.add_argument("telefone", nargs="?")
    p.add_argument("--formato", choices=config.FORMATOS)
    sub.add_parser("limpar", help="apaga comprovantes antigos")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%d/%m %H:%M:%S")
    try:
        cfg = config.carregar(args.config)
    except config.ErroConfig as e:
        print(f"Erro no config.json: {e}", file=sys.stderr)
        return 2
    eco = EcoZap(cfg)

    try:
        if args.comando in (None, "interface"):
            from .interface import Aplicativo  # só importa Tkinter se for usar
            eco.limpar()
            Aplicativo(eco).executar()
        elif args.comando == "servico":
            _servico(eco)
        elif args.comando == "enviar":
            comp = eco.receber(Path(args.arquivo).read_bytes(), args.papel)
            numero = args.telefone or comp.telefone
            if not numero:
                print(f"Telefone não encontrado no comprovante. Guardado como {comp.id}; "
                      f"use: python -m ecozap reenviar {comp.id} <telefone>")
                return 1
            eco.enviar(comp.id, numero, args.nome, args.formato)
            print(f"Enviado para {tel.formatar(numero)} (id {comp.id})")
        elif args.comando == "historico":
            for c in eco.armazenamento.listar(50, args.busca):
                print(f"{c.id}  {c.criado_em.replace('T', ' ')}  "
                      f"{tel.formatar(c.telefone) if c.telefone else '-':<20} {c.status:<10} {c.nome or ''}")
            est = eco.armazenamento.estatisticas()
            print(f"\n🌱 {est['enviados']} enviados · {est['papel_economizado_m']} m de papel economizados")
        elif args.comando == "reenviar":
            comp = eco.enviar(args.id, args.telefone, formato=args.formato)
            print(f"Reenviado para {tel.formatar(comp.telefone)}")
        elif args.comando == "limpar":
            print(f"{eco.limpar()} comprovante(s) apagado(s)")
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Linha de comando do EcoZap.

    ecozap                          abre a interface (padrão)
    ecozap --escondido              abre só o ícone na bandeja (usado ao iniciar o Windows)
    ecozap servico                  sem tela: envia sozinho se achar o telefone; painel web
    ecozap enviar ARQ [TEL]         converte e envia um arquivo direto
    ecozap historico [busca]        lista os últimos comprovantes
    ecozap reenviar ID [TEL]        reenvia um comprovante guardado
    ecozap relatorio [AAAA-MM]      gera o relatório de papel economizado (PDF + imagem)
    ecozap clientes [busca]         mostra a agenda de clientes
    ecozap esquecer TELEFONE        apaga um cliente da agenda (LGPD)
    ecozap limpar                   apaga os comprovantes mais velhos que a retenção
    ecozap cartaz                   gera o cartaz do balcão (QR Code do WhatsApp da loja)
    ecozap mensagens                mensagens recebidas nas últimas 24h
    ecozap promocao criar --titulo T --texto X [--imagem I] [--dias-recentes N] [--agendar "AAAA-MM-DD HH:MM"]
    ecozap promocao listar | previa | pausar ID | retomar ID | cancelar ID
    ecozap promocao autorizar TELEFONE   cliente autorizou promoções no balcão
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from . import __version__, config, relatorio, telefone as tel
from .instancia import TravaInstancia
from .monitor import Monitor
from .servico import EcoZap

log = logging.getLogger("ecozap")


def configurar_log(pasta_dados: str) -> None:
    pasta = Path(pasta_dados)
    pasta.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%d/%m/%Y %H:%M:%S")
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in raiz.handlers):
        return
    arquivo = logging.handlers.RotatingFileHandler(pasta / "ecozap.log", maxBytes=2_000_000,
                                                   backupCount=3, encoding="utf-8")
    arquivo.setFormatter(formato)
    raiz.addHandler(arquivo)
    if sys.stderr is not None:  # pythonw / .exe sem console não tem stderr
        tela = logging.StreamHandler()
        tela.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        raiz.addHandler(tela)


def _avisar_janela(texto: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showinfo("EcoZap", texto)
        raiz.destroy()
    except Exception:
        print(texto)


def _servico(eco: EcoZap) -> None:
    def ao_receber(caminho: Path) -> None:
        comp = eco.receber_arquivo(caminho)
        if comp.telefone:
            try:
                eco.enviar(comp.id)
            except Exception as e:  # fica como "erro" no histórico para reenviar depois
                log.error("Não enviado: %s", e)
        else:
            log.warning("Sem telefone no comprovante %s - ficou pendente (envie pelo painel)", comp.id)

    eco.limpar()
    from .promocoes import Disparador
    disparador = Disparador(eco)
    disparador.iniciar()
    painel = None
    if eco.cfg["painel"]["ativo"]:
        from .painel import Painel
        painel = Painel(eco, disparador)
        painel.iniciar()
    else:
        log.warning("Painel web desligado: o EcoZap não vai receber mensagens dos clientes (webhook)")
    mon = Monitor(eco.cfg["pasta_entrada"], ao_receber, pausado=eco.pausado)
    mon.iniciar()
    parar = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: parar.set())
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    log.info("EcoZap rodando como serviço (Ctrl+C para sair)")
    while not parar.wait(6 * 60 * 60):
        eco.limpar()
    mon.parar()
    disparador.parar()
    if painel:
        painel.parar()


def _promocao(eco: EcoZap, args) -> int:
    from .promocoes import Disparador
    disp, arm = Disparador(eco), eco.armazenamento
    if args.acao == "criar":
        if not (args.titulo and args.texto):
            print("Use --titulo e --texto")
            return 1
        imagem = Path(args.imagem).read_bytes() if args.imagem else None
        quando = datetime.strptime(args.agendar, "%Y-%m-%d %H:%M") if args.agendar else None
        camp = disp.criar(args.titulo, args.texto.replace("\\n", "\n"), imagem, args.dias_recentes, quando)
        print(f"Campanha {camp.id} criada para {camp.total} cliente(s). "
              "Ela é enviada pelo EcoZap aberto (interface ou serviço).")
    elif args.acao == "listar":
        for c in arm.listar_campanhas():
            print(f"#{c.id:<4} {c.status:<10} {c.enviados}/{c.total} enviadas  "
                  f"({c.falhas} falhas, {c.pulados} puladas)  {c.agendada_para.replace('T', ' ')}  {c.titulo}")
    elif args.acao == "previa":
        print(f"{disp.previa(args.dias_recentes)} cliente(s) receberiam uma promoção agora.")
    elif args.acao == "autorizar":
        numero = tel.normalizar(args.alvo or "")
        if not numero:
            print("Telefone inválido")
            return 1
        arm.definir_promocoes(numero, True, "balcão (autorizado pelo cliente)")
        print(f"{tel.formatar(numero)} vai receber promoções.")
    else:
        if not (args.alvo or "").isdigit():
            print("Informe o ID da campanha")
            return 1
        getattr(disp, args.acao)(int(args.alvo))
        print("Campanha atualizada.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ecozap", description="EcoZap - impressora virtual que envia pelo WhatsApp")
    ap.add_argument("--config", help="caminho do config.json")
    ap.add_argument("--escondido", action="store_true", help="inicia só com o ícone na bandeja")
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
    p = sub.add_parser("relatorio", help="relatório mensal de papel economizado")
    p.add_argument("mes", nargs="?", help="AAAA-MM (padrão: mês atual)")
    p.add_argument("--saida", help="pasta onde salvar (padrão: <pasta_dados>/relatorios)")
    p = sub.add_parser("clientes", help="mostra a agenda de clientes")
    p.add_argument("busca", nargs="?", default="")
    p = sub.add_parser("esquecer", help="apaga um cliente da agenda (LGPD)")
    p.add_argument("telefone")
    sub.add_parser("limpar", help="apaga comprovantes antigos")
    p = sub.add_parser("cartaz", help="gera o cartaz do balcão em PDF")
    p.add_argument("--saida", help="arquivo PDF (padrão: <pasta_dados>/cartaz-ecozap.pdf)")
    sub.add_parser("mensagens", help="mensagens recebidas nas últimas 24h")
    p = sub.add_parser("promocao", help="campanhas de promoção")
    p.add_argument("acao", choices=["criar", "listar", "previa", "pausar", "retomar", "cancelar", "autorizar"])
    p.add_argument("alvo", nargs="?", help="ID da campanha ou telefone")
    p.add_argument("--titulo")
    p.add_argument("--texto")
    p.add_argument("--imagem")
    p.add_argument("--dias-recentes", type=int)
    p.add_argument("--agendar", help='"AAAA-MM-DD HH:MM"')
    args = ap.parse_args(argv)

    try:
        cfg = config.carregar(args.config)
    except config.ErroConfig as e:
        print(f"Erro no config.json: {e}", file=sys.stderr)
        return 2
    configurar_log(cfg["pasta_dados"])
    eco = EcoZap(cfg)

    if args.comando in (None, "interface", "servico"):
        trava = TravaInstancia(cfg["pasta_dados"])
        if not trava.adquirir():
            aviso = ("O EcoZap já está rodando neste computador (procure o ícone perto do "
                     "relógio, ou ele está em segundo plano como serviço).")
            if cfg["painel"]["ativo"]:
                aviso += f"\nPainel web: http://127.0.0.1:{cfg['painel']['porta']}"
            log.warning(aviso)
            if args.comando != "servico" and not args.escondido:
                _avisar_janela(aviso)
            return 3

    try:
        if args.comando in (None, "interface"):
            from .interface import Aplicativo  # só importa Tkinter se for usar
            eco.limpar()
            Aplicativo(eco).executar(iniciar_escondido=args.escondido)
        elif args.comando == "servico":
            _servico(eco)
        elif args.comando == "enviar":
            comp = eco.receber(Path(args.arquivo).read_bytes(), args.papel)
            numero = args.telefone or comp.telefone
            if not numero:
                print(f"Telefone não encontrado no comprovante. Guardado como {comp.id}; "
                      f"use: ecozap reenviar {comp.id} <telefone>")
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
        elif args.comando == "relatorio":
            saida = args.saida or Path(cfg["pasta_dados"]) / "relatorios"
            pdf, img = relatorio.gerar(eco.armazenamento, cfg["nome_loja"], args.mes, saida)
            print(f"Relatório: {pdf}\nImagem para redes sociais: {img}")
        elif args.comando == "clientes":
            arm = eco.armazenamento
            lista = arm.buscar_clientes(args.busca, 200) if args.busca else arm.listar_clientes()
            for c in lista:
                print(f"{tel.formatar(c.telefone):<22} {c.nome or '-':<30} {c.envios:>4} envio(s)  "
                      f"último: {c.ultimo_envio.replace('T', ' ')}")
            if not lista:
                print("Nenhum cliente na agenda.")
        elif args.comando == "esquecer":
            numero = tel.normalizar(args.telefone)
            if not numero or not eco.armazenamento.cliente_por_telefone(numero):
                print("Cliente não encontrado na agenda.")
                return 1
            eco.armazenamento.esquecer_cliente(numero)
            print(f"Cliente {tel.formatar(numero)} apagado da agenda.")
        elif args.comando == "limpar":
            print(f"{eco.limpar()} comprovante(s) apagado(s)")
        elif args.comando == "cartaz":
            from . import cartaz
            destino = Path(args.saida or Path(cfg["pasta_dados"]) / "cartaz-ecozap.pdf")
            destino.write_bytes(cartaz.gerar_pdf(cfg))
            print(f"Cartaz: {destino}")
        elif args.comando == "mensagens":
            for m in eco.armazenamento.mensagens_recentes(24 * 60, 200):
                print(f"{m.recebida_em.replace('T', ' ')}  {tel.formatar(m.telefone):<20} "
                      f"{(m.nome or '-')[:18]:<18} {m.acao or '':<24} {(m.texto or '')[:50]}")
        elif args.comando == "promocao":
            return _promocao(eco, args)
    except Exception as e:
        log.debug("Erro", exc_info=True)
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

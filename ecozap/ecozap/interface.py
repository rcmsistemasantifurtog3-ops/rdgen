"""Interface gráfica (Tkinter, já vem com o Python no Windows).

- Janela principal: histórico, busca, reenviar, abrir arquivo, papel economizado.
- Janela "Enviar comprovante": abre sozinha quando chega uma impressão,
  com autocompletar de clientes (agenda).
- Configurações, Agenda de clientes e Relatório do mês pelo menu.
- Ícone na bandeja: fechar a janela só esconde; o EcoZap continua recebendo.
"""
from __future__ import annotations

import copy
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from PIL import ImageTk

from . import config, conversor, icone, relatorio, telefone as tel
from .armazenamento import Comprovante
from .bandeja import Bandeja
from .monitor import Monitor
from .servico import EcoZap

log = logging.getLogger("ecozap.ui")
VERDE = "#2e7d32"
LIMPEZA_MS = 6 * 60 * 60 * 1000  # roda a limpeza a cada 6 horas
NOMES_STATUS = {"pendente": "⏳ Pendente", "enviado": "✅ Enviado", "erro": "❌ Erro",
                "salvo": "💾 Salvo", "descartado": "🗑 Descartado"}
CUPOM_TESTE = """\
        LOJA TESTE ECOZAP
------------------------------------------
RECIBO DE PAGAMENTO
Produto teste                    R$ 1,00
------------------------------------------
TOTAL                            R$ 1,00
Forma de pagamento: Dinheiro
------------------------------------------
  Teste de envio do EcoZap
"""


def abrir_arquivo(caminho: str | Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(caminho))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(caminho)])


def em_segundo_plano(tarefa: Callable, ao_terminar: Callable, ao_falhar: Callable, widget: tk.Misc):
    """Roda `tarefa` numa thread e devolve o resultado para a tela com segurança."""
    def rodar():
        try:
            resultado = tarefa()
            widget.after(0, lambda: ao_terminar(resultado))
        except Exception as e:
            msg = str(e)
            widget.after(0, lambda: ao_falhar(msg))
    threading.Thread(target=rodar, daemon=True).start()


class CampoAutocompletar(ttk.Combobox):
    """Combobox que sugere clientes da agenda enquanto você digita."""

    def __init__(self, mestre, app: "Aplicativo", ao_escolher: Callable, **kw):
        super().__init__(mestre, **kw)
        self.app, self.ao_escolher, self._clientes = app, ao_escolher, []
        self.bind("<KeyRelease>", self._digitou)
        self.bind("<<ComboboxSelected>>", self._escolheu)

    def _digitou(self, evento):
        if evento.keysym in ("Return", "Escape", "Up", "Down", "Tab"):
            return
        self._clientes = self.app.eco.armazenamento.buscar_clientes(self.get())
        self["values"] = [f"{c.nome or 'Sem nome'} — {tel.formatar(c.telefone)}" for c in self._clientes]

    def _escolheu(self, _evento):
        i = self.current()
        if 0 <= i < len(self._clientes):
            self.ao_escolher(self._clientes[i])


class JanelaEnvio(tk.Toplevel):
    """Pergunta o WhatsApp do cliente e mostra a prévia do comprovante."""

    def __init__(self, app: "Aplicativo", comp: Comprovante):
        super().__init__(app.raiz)
        self.app, self.comp = app, comp
        self.title("EcoZap - Enviar comprovante")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        app.definir_icone(self)

        cfg = app.eco.cfg
        self.var_tel = tk.StringVar(value=tel.formatar(comp.telefone) if comp.telefone else "")
        self.var_nome = tk.StringVar(value=comp.nome or "")
        self.var_formato = tk.StringVar(value=comp.formato or cfg["formato_padrao"])
        self.var_papel = tk.StringVar(value=cfg["tamanho_papel_padrao"])

        corpo = ttk.Frame(self, padding=12)
        corpo.pack(fill="both", expand=True)

        self.lbl_previa = ttk.Label(corpo, relief="solid")
        self.lbl_previa.grid(row=0, column=0, rowspan=9, padx=(0, 12), sticky="n")
        self._atualizar_previa()

        ttk.Label(corpo, text="WhatsApp do cliente (com DDD):").grid(row=0, column=1, sticky="w")
        self.ent_tel = CampoAutocompletar(corpo, app, self._preencher, textvariable=self.var_tel,
                                          width=30, font=("Segoe UI", 13))
        self.ent_tel.grid(row=1, column=1, sticky="we", pady=(0, 8))

        ttk.Label(corpo, text="Nome do cliente (opcional):").grid(row=2, column=1, sticky="w")
        CampoAutocompletar(corpo, app, self._preencher, textvariable=self.var_nome,
                           width=30).grid(row=3, column=1, sticky="we", pady=(0, 8))

        caixa_fmt = ttk.LabelFrame(corpo, text="Formato do arquivo", padding=6)
        caixa_fmt.grid(row=4, column=1, sticky="we", pady=(0, 8))
        for valor, rotulo in (("pdf", "PDF"), ("jpeg", "Imagem JPEG"), ("png", "Imagem PNG")):
            ttk.Radiobutton(caixa_fmt, text=rotulo, value=valor,
                            variable=self.var_formato).pack(side="left", padx=4)

        caixa_papel = ttk.LabelFrame(corpo, text="Tamanho do papel", padding=6)
        caixa_papel.grid(row=5, column=1, sticky="we", pady=(0, 8))
        cb = ttk.Combobox(caixa_papel, textvariable=self.var_papel, state="readonly",
                          values=config.TAMANHOS, width=10)
        cb.pack(side="left")
        if comp.origem == "texto":
            cb.bind("<<ComboboxSelected>>", self._trocar_papel)
        else:
            cb.configure(state="disabled")
            ttk.Label(caixa_papel, text="(definido na hora de imprimir)").pack(side="left", padx=6)

        if comp.link_nfce or comp.chave_nfce:
            info = "🧾 Nota fiscal detectada: o link de consulta vai na mensagem." if comp.link_nfce \
                else "🧾 Chave de acesso detectada: vai na mensagem."
            ttk.Label(corpo, text=info, foreground=VERDE, wraplength=280).grid(row=6, column=1, sticky="w")

        self.lbl_status = ttk.Label(corpo, text="", foreground="red", wraplength=280)
        self.lbl_status.grid(row=7, column=1, sticky="w")

        botoes = ttk.Frame(corpo)
        botoes.grid(row=8, column=1, sticky="we", pady=(8, 0))
        self.btn_enviar = ttk.Button(botoes, text="📲 Enviar (Enter)", command=self.enviar)
        self.btn_enviar.pack(side="left", padx=(0, 4))
        ttk.Button(botoes, text="Só salvar", command=lambda: self._fechar("salvo")).pack(side="left", padx=4)
        ttk.Button(botoes, text="Descartar", command=lambda: self._fechar("descartado")).pack(side="left", padx=4)

        self.bind("<Return>", lambda _e: self.enviar())
        self.bind("<Escape>", lambda _e: self._fechar("salvo"))
        self.protocol("WM_DELETE_WINDOW", lambda: self._fechar("salvo"))
        self.ent_tel.focus_force()

    def _preencher(self, cliente) -> None:
        self.var_tel.set(tel.formatar(cliente.telefone))
        if cliente.nome:
            self.var_nome.set(cliente.nome)

    def _atualizar_previa(self) -> None:
        self.comp = self.app.eco.armazenamento.obter(self.comp.id)
        self._img = ImageTk.PhotoImage(conversor.miniatura(self.comp.pdf, 260))
        self.lbl_previa.configure(image=self._img)

    def _trocar_papel(self, _evento=None) -> None:
        self.app.eco.trocar_papel(self.comp.id, self.var_papel.get())
        self._atualizar_previa()

    def enviar(self) -> None:
        numero = tel.normalizar(self.var_tel.get().split("—")[-1])
        if not numero:
            self.lbl_status.configure(text="Telefone inválido. Ex.: (11) 98765-4321", foreground="red")
            self.ent_tel.focus_set()
            return
        self.btn_enviar.configure(state="disabled")
        self.lbl_status.configure(text="Enviando...", foreground="gray")
        nome = self.var_nome.get().split("—")[0].strip() or None

        def falhou(msg):
            self.lbl_status.configure(text=f"Erro: {msg}", foreground="red")
            self.btn_enviar.configure(state="normal")

        em_segundo_plano(
            lambda: self.app.eco.enviar(self.comp.id, numero, nome, self.var_formato.get()),
            lambda _c: (self.app.bandeja.notificar(f"Comprovante enviado para {tel.formatar(numero)}"),
                        self._fechar(None)),
            falhou, self)

    def _fechar(self, status: str | None) -> None:
        if status:
            self.app.eco.armazenamento.atualizar(
                self.comp.id, status=status, nome=self.var_nome.get().strip() or None,
                formato=self.var_formato.get())
        self.destroy()
        self.app.atualizar_lista()


class JanelaConfiguracoes(tk.Toplevel):
    """Edita o config.json por tela (sem precisar abrir o arquivo)."""

    # (caminho no config, rótulo, tipo, opções)
    CAMPOS = {
        "Geral": [
            (("nome_loja",), "Nome da loja", "texto", None),
            (("identificacao_caixa",), "Identificação deste caixa", "texto", None),
            (("mensagem",), "Mensagem ({nome} e {loja} são trocados)", "longo", None),
            (("formato_padrao",), "Formato padrão", "escolha", config.FORMATOS),
            (("tamanho_papel_padrao",), "Papel padrão (cupom de texto)", "escolha", config.TAMANHOS),
            (("dias_retencao",), "Guardar comprovantes por (dias)", "numero", None),
            (("dias_retencao_clientes",), "Guardar agenda de clientes por (dias, 0 = sempre)", "numero", None),
            (("usar_bandeja",), "Ícone perto do relógio (reinicie para aplicar)", "booleano", None),
        ],
        "WhatsApp": [
            (("whatsapp", "provedor"), "Provedor", "escolha", config.PROVEDORES),
            (("whatsapp", "cloud_api", "token"), "Cloud API - token", "senha", None),
            (("whatsapp", "cloud_api", "phone_number_id"), "Cloud API - phone_number_id", "texto", None),
            (("whatsapp", "cloud_api", "template"), "Cloud API - nome do template", "texto", None),
            (("whatsapp", "cloud_api", "idioma_template"), "Cloud API - idioma do template", "texto", None),
            (("whatsapp", "evolution", "url_base"), "Evolution - URL", "texto", None),
            (("whatsapp", "evolution", "instancia"), "Evolution - instância", "texto", None),
            (("whatsapp", "evolution", "apikey"), "Evolution - apikey", "senha", None),
        ],
        "Painel web": [
            (("painel", "ativo"), "Ativar painel web (reinicie para aplicar)", "booleano", None),
            (("painel", "host"), "Endereço (127.0.0.1 = só este PC; 0.0.0.0 = rede)", "texto", None),
            (("painel", "porta"), "Porta", "numero", None),
            (("painel", "usuario"), "Usuário", "texto", None),
            (("painel", "senha"), "Senha (obrigatória se liberar na rede)", "senha", None),
        ],
    }

    def __init__(self, app: "Aplicativo"):
        super().__init__(app.raiz)
        self.app = app
        self.title("EcoZap - Configurações")
        app.definir_icone(self)
        self.cfg = copy.deepcopy(app.eco.cfg)
        self.vars: dict[tuple, tuple] = {}
        abas = ttk.Notebook(self)
        abas.pack(fill="both", expand=True, padx=10, pady=10)
        for nome_aba, campos in self.CAMPOS.items():
            aba = ttk.Frame(abas, padding=10)
            abas.add(aba, text=nome_aba)
            for linha, (caminho, rotulo, tipo, opcoes) in enumerate(campos):
                self._campo(aba, linha, caminho, rotulo, tipo, opcoes)
        rodape = ttk.Frame(self, padding=(10, 0, 10, 10))
        rodape.pack(fill="x")
        ttk.Button(rodape, text="Salvar", command=self.salvar).pack(side="right")
        ttk.Button(rodape, text="Enviar teste...", command=self.testar).pack(side="right", padx=6)
        ttk.Label(rodape, text=f"Arquivo: {self.cfg.get('_arquivo', '')}", foreground="gray").pack(side="left")

    def _valor(self, caminho):
        v = self.cfg
        for parte in caminho:
            v = v[parte]
        return v

    def _campo(self, aba, linha, caminho, rotulo, tipo, opcoes):
        valor = self._valor(caminho)
        if tipo == "booleano":
            var = tk.BooleanVar(value=bool(valor))
            ttk.Checkbutton(aba, text=rotulo, variable=var).grid(row=linha, column=0, columnspan=2, sticky="w", pady=3)
            self.vars[caminho] = (tipo, var)
            return
        ttk.Label(aba, text=rotulo).grid(row=linha, column=0, sticky="nw", pady=3, padx=(0, 8))
        if tipo == "longo":
            caixa = tk.Text(aba, width=48, height=4, wrap="word")
            caixa.insert("1.0", valor)
            caixa.grid(row=linha, column=1, sticky="we", pady=3)
            self.vars[caminho] = (tipo, caixa)
            return
        var = tk.StringVar(value=str(valor))
        if tipo == "escolha":
            w = ttk.Combobox(aba, textvariable=var, values=opcoes, state="readonly", width=20)
        else:
            w = ttk.Entry(aba, textvariable=var, width=50, show="•" if tipo == "senha" else "")
        w.grid(row=linha, column=1, sticky="w", pady=3)
        self.vars[caminho] = (tipo, var)

    def _ler(self) -> dict:
        novo = copy.deepcopy(self.cfg)
        for caminho, (tipo, var) in self.vars.items():
            if tipo == "longo":
                valor = var.get("1.0", "end").rstrip("\n")
            elif tipo == "numero":
                try:
                    valor = int(var.get())
                except ValueError as e:
                    raise config.ErroConfig(f"'{'.'.join(caminho)}' precisa ser um número") from e
            else:
                valor = var.get()
            destino = novo
            for parte in caminho[:-1]:
                destino = destino[parte]
            destino[caminho[-1]] = valor
        return novo

    def salvar(self) -> bool:
        try:
            novo = self._ler()
            arquivo = config.salvar(novo)
        except (config.ErroConfig, OSError) as e:
            messagebox.showerror("EcoZap", f"Não foi possível salvar:\n{e}", parent=self)
            return False
        self.app.eco.reconfigurar(novo)
        self.cfg = copy.deepcopy(novo)
        messagebox.showinfo("EcoZap", f"Configurações salvas em\n{arquivo}", parent=self)
        self.app.atualizar_lista()
        return True

    def testar(self) -> None:
        if not self.salvar():
            return
        numero = simpledialog.askstring("Enviar teste", "WhatsApp para o teste (com DDD):", parent=self)
        if not numero:
            return
        eco = self.app.eco

        def tarefa():
            comp = eco.receber(CUPOM_TESTE.encode("utf-8"))
            return eco.enviar(comp.id, numero)
        em_segundo_plano(tarefa,
                         lambda c: (messagebox.showinfo("EcoZap", f"Teste enviado para {tel.formatar(c.telefone)} "
                                                        f"(provedor: {eco.provedor.nome}).", parent=self),
                                    self.app.atualizar_lista()),
                         lambda m: messagebox.showerror("EcoZap", f"Falha no teste:\n{m}", parent=self), self)


class JanelaAgenda(tk.Toplevel):
    """Agenda de clientes: busca e exclusão (direito do titular - LGPD)."""

    def __init__(self, app: "Aplicativo"):
        super().__init__(app.raiz)
        self.app = app
        self.title("EcoZap - Agenda de clientes")
        self.geometry("560x380")
        app.definir_icone(self)
        topo = ttk.Frame(self, padding=8)
        topo.pack(fill="x")
        self.var = tk.StringVar()
        e = ttk.Entry(topo, textvariable=self.var, width=30)
        e.pack(side="left")
        e.bind("<KeyRelease>", lambda _e: self.atualizar())
        ttk.Label(topo, text=" digite nome ou telefone (mín. 2 letras)").pack(side="left")
        self.tabela = ttk.Treeview(self, columns=("nome", "tel", "envios", "ultimo"), show="headings")
        for col, titulo, larg in (("nome", "Nome", 170), ("tel", "WhatsApp", 160),
                                  ("envios", "Envios", 60), ("ultimo", "Último envio", 140)):
            self.tabela.heading(col, text=titulo)
            self.tabela.column(col, width=larg)
        self.tabela.pack(fill="both", expand=True, padx=8)
        ttk.Button(self, text="Esquecer cliente selecionado (LGPD)", command=self.esquecer).pack(pady=8)
        self.atualizar()

    def atualizar(self):
        self.tabela.delete(*self.tabela.get_children())
        arm = self.app.eco.armazenamento
        termo = self.var.get().strip()
        for c in (arm.buscar_clientes(termo, 100) if termo else arm.listar_clientes(100)):
            self.tabela.insert("", "end", iid=c.telefone, values=(
                c.nome or "", tel.formatar(c.telefone), c.envios, c.ultimo_envio.replace("T", " ")))

    def esquecer(self):
        sel = self.tabela.selection()
        if sel and messagebox.askyesno("EcoZap", "Apagar este cliente da agenda?", parent=self):
            self.app.eco.armazenamento.esquecer_cliente(sel[0])
            self.atualizar()


class Aplicativo:
    def __init__(self, eco: EcoZap):
        self.eco = eco
        self.tarefas: queue.Queue[Callable[[], None]] = queue.Queue()
        self.raiz = tk.Tk()
        self.raiz.title("EcoZap - Impressora Verde")
        self.raiz.geometry("900x540")
        self._icone_tk = ImageTk.PhotoImage(icone.gerar(64))
        self.definir_icone(self.raiz)
        self.painel = None
        self.bandeja = Bandeja(
            {"abrir": lambda: self.tarefas.put(self.mostrar),
             "pausar": self.alternar_pausa,
             "relatorio": lambda: self.tarefas.put(self.relatorio_mes),
             "painel": lambda: self.tarefas.put(self.abrir_painel),
             "configuracoes": lambda: self.tarefas.put(lambda: JanelaConfiguracoes(self)),
             "sair": lambda: self.tarefas.put(self.sair)},
            pausado=eco.pausado.is_set,
            painel_ativo=lambda: self.painel is not None)
        self._montar()
        self.monitor = Monitor(eco.cfg["pasta_entrada"], self._chegou, pausado=eco.pausado)

    def definir_icone(self, janela: tk.Misc) -> None:
        try:
            janela.iconphoto(False, self._icone_tk)
        except tk.TclError:
            pass

    def _chegou(self, caminho: Path) -> None:
        comp = self.eco.receber_arquivo(caminho)  # roda na thread do monitor
        self.tarefas.put(lambda: (JanelaEnvio(self, comp), self.atualizar_lista()))

    def _montar(self) -> None:
        barra = tk.Menu(self.raiz)
        m_arq = tk.Menu(barra, tearoff=False)
        m_arq.add_command(label="Importar arquivo...", command=self.importar)
        m_arq.add_separator()
        m_arq.add_command(label="Sair do EcoZap", command=self.sair)
        barra.add_cascade(label="Arquivo", menu=m_arq)
        m_fer = tk.Menu(barra, tearoff=False)
        m_fer.add_command(label="Configurações...", command=lambda: JanelaConfiguracoes(self))
        m_fer.add_command(label="Agenda de clientes...", command=lambda: JanelaAgenda(self))
        m_fer.add_command(label="Relatório do mês...", command=self.relatorio_mes)
        m_fer.add_command(label="Abrir painel web", command=self.abrir_painel)
        m_fer.add_command(label="Pausar / retomar recebimento", command=self.alternar_pausa)
        m_fer.add_command(label="Limpar comprovantes antigos agora",
                          command=lambda: (self.eco.limpar(), self.atualizar_lista()))
        barra.add_cascade(label="Ferramentas", menu=m_fer)
        self.raiz.config(menu=barra)

        topo = ttk.Frame(self.raiz, padding=10)
        topo.pack(fill="x")
        self.lbl_eco = tk.Label(topo, text="", fg=VERDE, font=("Segoe UI", 12, "bold"))
        self.lbl_eco.pack(side="left")
        self.var_busca = tk.StringVar()
        ttk.Button(topo, text="Buscar", command=self.atualizar_lista).pack(side="right")
        busca = ttk.Entry(topo, textvariable=self.var_busca, width=28)
        busca.pack(side="right", padx=4)
        busca.bind("<Return>", lambda _e: self.atualizar_lista())
        ttk.Label(topo, text="Telefone, nome ou texto:").pack(side="right")

        colunas = ("data", "telefone", "nome", "status", "origem", "nota", "papel")
        self.tabela = ttk.Treeview(self.raiz, columns=colunas, show="headings", selectmode="browse")
        for col, titulo, larg in (("data", "Data", 135), ("telefone", "WhatsApp", 160),
                                  ("nome", "Cliente", 140), ("status", "Situação", 110),
                                  ("origem", "Origem", 60), ("nota", "Nota", 60), ("papel", "Papel", 60)):
            self.tabela.heading(col, text=titulo)
            self.tabela.column(col, width=larg)
        self.tabela.pack(fill="both", expand=True, padx=10)
        self.tabela.bind("<Double-1>", lambda _e: self.reenviar())

        rodape = ttk.Frame(self.raiz, padding=10)
        rodape.pack(fill="x")
        ttk.Button(rodape, text="📲 Enviar / Reenviar", command=self.reenviar).pack(side="left")
        ttk.Button(rodape, text="Abrir PDF", command=self.abrir).pack(side="left", padx=4)
        ttk.Button(rodape, text="Salvar cópia...", command=self.salvar_copia).pack(side="left")
        ttk.Button(rodape, text="Importar arquivo...", command=self.importar).pack(side="left", padx=4)
        self.lbl_rodape = ttk.Label(rodape, text="", foreground="gray")
        self.lbl_rodape.pack(side="right")

    def _selecionado(self) -> Comprovante | None:
        sel = self.tabela.selection()
        if not sel:
            messagebox.showinfo("EcoZap", "Selecione um comprovante na lista.")
            return None
        return self.eco.armazenamento.obter(sel[0])

    def atualizar_lista(self) -> None:
        self.tabela.delete(*self.tabela.get_children())
        for c in self.eco.armazenamento.listar(300, self.var_busca.get().strip()):
            nota = "NFC-e" if c.link_nfce or (c.chave_nfce and c.chave_nfce[20:22] == "65") else \
                   ("NF-e" if c.chave_nfce else "")
            self.tabela.insert("", "end", iid=c.id, values=(
                c.criado_em.replace("T", " "), tel.formatar(c.telefone) if c.telefone else "-",
                c.nome or "", NOMES_STATUS.get(c.status, c.status), c.origem, nota,
                f"{c.papel_mm / 10:.0f} cm"))
        est = self.eco.armazenamento.estatisticas()
        pausa = "  ⏸ RECEBIMENTO PAUSADO" if self.eco.pausado.is_set() else ""
        self.lbl_eco.configure(text=f"🌱 {est['enviados']} comprovantes digitais · "
                                    f"{est['papel_economizado_m']} m de papel economizados{pausa}")
        self.lbl_rodape.configure(text=f"Comprovantes ficam guardados por {self.eco.cfg['dias_retencao']} dias · "
                                       f"provedor: {self.eco.cfg['whatsapp']['provedor']}")

    def reenviar(self) -> None:
        comp = self._selecionado()
        if comp:
            JanelaEnvio(self, comp)

    def abrir(self) -> None:
        comp = self._selecionado()
        if comp:
            abrir_arquivo(comp.arquivo_pdf)

    def salvar_copia(self) -> None:
        comp = self._selecionado()
        if not comp:
            return
        destino = filedialog.asksaveasfilename(defaultextension=".pdf",
                                               initialfile=f"comprovante-{comp.id}.pdf",
                                               filetypes=[("PDF", "*.pdf")])
        if destino:
            Path(destino).write_bytes(comp.pdf)

    def importar(self) -> None:
        caminho = filedialog.askopenfilename(
            filetypes=[("Comprovantes", "*.pdf *.txt *.prn *.png *.jpg"), ("Todos", "*.*")])
        if caminho:
            JanelaEnvio(self, self.eco.receber_arquivo(Path(caminho)))
            self.atualizar_lista()

    def relatorio_mes(self) -> None:
        mes = simpledialog.askstring("Relatório do mês", "Mês (AAAA-MM). Vazio = mês atual:",
                                     parent=self.raiz) if self.raiz.winfo_viewable() else ""
        if mes is None:
            return
        try:
            pdf, img = relatorio.gerar(self.eco.armazenamento, self.eco.cfg["nome_loja"], mes.strip(),
                                       Path(self.eco.cfg["pasta_dados"]) / "relatorios")
        except ValueError as e:
            messagebox.showerror("EcoZap", str(e))
            return
        abrir_arquivo(pdf)
        self.bandeja.notificar(f"Relatório salvo em {pdf.parent}")

    def abrir_painel(self) -> None:
        if self.painel is None:
            messagebox.showinfo("EcoZap", "O painel web está desligado. Ative em "
                                          "Ferramentas > Configurações > Painel web e reinicie.")
            return
        webbrowser.open(self.painel.endereco)

    def alternar_pausa(self) -> None:
        if self.eco.pausado.is_set():
            self.eco.pausado.clear()
        else:
            self.eco.pausado.set()
        self.tarefas.put(self.atualizar_lista)

    def mostrar(self) -> None:
        self.raiz.deiconify()
        self.raiz.lift()
        self.raiz.focus_force()
        self.atualizar_lista()

    def _ao_fechar_janela(self) -> None:
        if self._com_bandeja:
            self.raiz.withdraw()
            self.bandeja.notificar("O EcoZap continua recebendo impressões aqui perto do relógio.")
        else:
            self.sair()

    def sair(self) -> None:
        self.raiz.quit()

    def _executar_tarefas(self) -> None:
        try:
            while True:
                self.tarefas.get_nowait()()
        except queue.Empty:
            pass
        self.raiz.after(250, self._executar_tarefas)

    def _limpeza_periodica(self) -> None:
        threading.Thread(target=self.eco.limpar, daemon=True).start()
        self.raiz.after(LIMPEZA_MS, self._limpeza_periodica)

    def executar(self, iniciar_escondido: bool = False) -> None:
        if self.eco.cfg["painel"]["ativo"]:
            from .painel import Painel
            try:
                self.painel = Painel(self.eco)
                self.painel.iniciar()
            except OSError as e:
                self.painel = None
                messagebox.showwarning("EcoZap", f"Painel web não iniciou: {e}")
        self._com_bandeja = bool(self.eco.cfg.get("usar_bandeja")) and self.bandeja.iniciar()
        self.raiz.protocol("WM_DELETE_WINDOW", self._ao_fechar_janela)
        if iniciar_escondido and self._com_bandeja:
            self.raiz.withdraw()
        self.atualizar_lista()
        self.monitor.iniciar()
        self._limpeza_periodica()
        self._executar_tarefas()
        try:
            self.raiz.mainloop()
        finally:
            self.monitor.parar()
            self.bandeja.parar()
            if self.painel:
                self.painel.parar()

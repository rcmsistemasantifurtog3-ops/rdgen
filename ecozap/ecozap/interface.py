"""Interface gráfica (Tkinter, já vem com o Python no Windows).

- Janela principal: histórico, busca, reenviar, abrir arquivo, papel economizado.
- Janela "Enviar comprovante": abre sozinha quando chega uma impressão.
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from . import conversor, telefone as tel
from .armazenamento import Comprovante
from .monitor import Monitor
from .servico import EcoZap

log = logging.getLogger("ecozap.ui")
VERDE = "#2e7d32"
LIMPEZA_MS = 6 * 60 * 60 * 1000  # roda a limpeza a cada 6 horas
NOMES_STATUS = {"pendente": "⏳ Pendente", "enviado": "✅ Enviado", "erro": "❌ Erro",
                "salvo": "💾 Salvo", "descartado": "🗑 Descartado"}


def abrir_arquivo(caminho: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(caminho)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", caminho])


class JanelaEnvio(tk.Toplevel):
    """Pergunta o WhatsApp do cliente e mostra a prévia do comprovante."""

    def __init__(self, app: "Aplicativo", comp: Comprovante):
        super().__init__(app.raiz)
        self.app, self.comp = app, comp
        self.title("EcoZap - Enviar comprovante")
        self.attributes("-topmost", True)
        self.resizable(False, False)

        cfg = app.eco.cfg
        self.var_tel = tk.StringVar(value=tel.formatar(comp.telefone) if comp.telefone else "")
        self.var_nome = tk.StringVar(value=comp.nome or "")
        self.var_formato = tk.StringVar(value=comp.formato or cfg["formato_padrao"])
        self.var_papel = tk.StringVar(value=cfg["tamanho_papel_padrao"])

        corpo = ttk.Frame(self, padding=12)
        corpo.pack(fill="both", expand=True)

        # prévia
        self.lbl_previa = ttk.Label(corpo, relief="solid")
        self.lbl_previa.grid(row=0, column=0, rowspan=8, padx=(0, 12), sticky="n")
        self._atualizar_previa()

        ttk.Label(corpo, text="WhatsApp do cliente (com DDD):").grid(row=0, column=1, sticky="w")
        self.ent_tel = ttk.Entry(corpo, textvariable=self.var_tel, width=28, font=("Segoe UI", 13))
        self.ent_tel.grid(row=1, column=1, sticky="we", pady=(0, 8))

        ttk.Label(corpo, text="Nome do cliente (opcional):").grid(row=2, column=1, sticky="w")
        ttk.Entry(corpo, textvariable=self.var_nome, width=28).grid(row=3, column=1, sticky="we", pady=(0, 8))

        caixa_fmt = ttk.LabelFrame(corpo, text="Formato do arquivo", padding=6)
        caixa_fmt.grid(row=4, column=1, sticky="we", pady=(0, 8))
        for valor, rotulo in (("pdf", "PDF"), ("jpeg", "Imagem JPEG"), ("png", "Imagem PNG")):
            ttk.Radiobutton(caixa_fmt, text=rotulo, value=valor,
                            variable=self.var_formato).pack(side="left", padx=4)

        caixa_papel = ttk.LabelFrame(corpo, text="Tamanho do papel", padding=6)
        caixa_papel.grid(row=5, column=1, sticky="we", pady=(0, 8))
        cb = ttk.Combobox(caixa_papel, textvariable=self.var_papel, state="readonly",
                          values=("58mm", "80mm", "a4"), width=10)
        cb.pack(side="left")
        if comp.origem == "texto":
            cb.bind("<<ComboboxSelected>>", self._trocar_papel)
        else:
            cb.configure(state="disabled")
            ttk.Label(caixa_papel, text="(definido na hora de imprimir)").pack(side="left", padx=6)

        self.lbl_status = ttk.Label(corpo, text="", foreground="red", wraplength=260)
        self.lbl_status.grid(row=6, column=1, sticky="w")

        botoes = ttk.Frame(corpo)
        botoes.grid(row=7, column=1, sticky="we", pady=(8, 0))
        self.btn_enviar = ttk.Button(botoes, text="📲 Enviar (Enter)", command=self.enviar)
        self.btn_enviar.pack(side="left", padx=(0, 4))
        ttk.Button(botoes, text="Só salvar", command=lambda: self._fechar("salvo")).pack(side="left", padx=4)
        ttk.Button(botoes, text="Descartar", command=lambda: self._fechar("descartado")).pack(side="left", padx=4)

        self.bind("<Return>", lambda _e: self.enviar())
        self.bind("<Escape>", lambda _e: self._fechar("salvo"))
        self.protocol("WM_DELETE_WINDOW", lambda: self._fechar("salvo"))
        self.ent_tel.focus_force()

    def _atualizar_previa(self) -> None:
        self.comp = self.app.eco.armazenamento.obter(self.comp.id)
        self._img = ImageTk.PhotoImage(conversor.miniatura(self.comp.pdf, 260))
        self.lbl_previa.configure(image=self._img)

    def _trocar_papel(self, _evento=None) -> None:
        self.app.eco.trocar_papel(self.comp.id, self.var_papel.get())
        self._atualizar_previa()

    def enviar(self) -> None:
        numero = tel.normalizar(self.var_tel.get())
        if not numero:
            self.lbl_status.configure(text="Telefone inválido. Ex.: (11) 98765-4321")
            self.ent_tel.focus_set()
            return
        self.btn_enviar.configure(state="disabled")
        self.lbl_status.configure(text="Enviando...", foreground="gray")

        def tarefa():
            try:
                self.app.eco.enviar(self.comp.id, numero, self.var_nome.get().strip() or None,
                                    self.var_formato.get())
                self.after(0, lambda: self._fechar(None))
            except Exception as e:  # mostra o erro sem travar a tela
                msg = str(e)
                self.after(0, lambda: (self.lbl_status.configure(text=f"Erro: {msg}", foreground="red"),
                                       self.btn_enviar.configure(state="normal")))
        threading.Thread(target=tarefa, daemon=True).start()

    def _fechar(self, status: str | None) -> None:
        if status:
            self.app.eco.armazenamento.atualizar(
                self.comp.id, status=status, nome=self.var_nome.get().strip() or None,
                formato=self.var_formato.get())
        self.destroy()
        self.app.atualizar_lista()


class Aplicativo:
    def __init__(self, eco: EcoZap):
        self.eco = eco
        self.fila: queue.Queue[Comprovante] = queue.Queue()
        self.raiz = tk.Tk()
        self.raiz.title("EcoZap - Impressora Verde")
        self.raiz.geometry("860x520")
        self._montar()
        self.monitor = Monitor(eco.cfg["pasta_entrada"],
                               lambda caminho: self.fila.put(eco.receber_arquivo(caminho)))

    def _montar(self) -> None:
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

        colunas = ("data", "telefone", "nome", "status", "origem", "papel")
        self.tabela = ttk.Treeview(self.raiz, columns=colunas, show="headings", selectmode="browse")
        for col, titulo, larg in (("data", "Data", 140), ("telefone", "WhatsApp", 170),
                                  ("nome", "Cliente", 150), ("status", "Situação", 120),
                                  ("origem", "Origem", 70), ("papel", "Papel", 80)):
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
        dias = self.eco.cfg["dias_retencao"]
        ttk.Label(rodape, text=f"Comprovantes ficam guardados por {dias} dias.",
                  foreground="gray").pack(side="right")

    def _selecionado(self) -> Comprovante | None:
        sel = self.tabela.selection()
        if not sel:
            messagebox.showinfo("EcoZap", "Selecione um comprovante na lista.")
            return None
        return self.eco.armazenamento.obter(sel[0])

    def atualizar_lista(self) -> None:
        self.tabela.delete(*self.tabela.get_children())
        for c in self.eco.armazenamento.listar(300, self.var_busca.get().strip()):
            self.tabela.insert("", "end", iid=c.id, values=(
                c.criado_em.replace("T", " "), tel.formatar(c.telefone) if c.telefone else "-",
                c.nome or "", NOMES_STATUS.get(c.status, c.status), c.origem,
                f"{c.papel_mm / 10:.0f} cm"))
        est = self.eco.armazenamento.estatisticas()
        self.lbl_eco.configure(text=f"🌱 {est['enviados']} comprovantes digitais · "
                                    f"{est['papel_economizado_m']} m de papel economizados")

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
            self.fila.put(self.eco.receber_arquivo(Path(caminho)))

    def _verificar_fila(self) -> None:
        try:
            while True:
                JanelaEnvio(self, self.fila.get_nowait())
                self.atualizar_lista()
        except queue.Empty:
            pass
        self.raiz.after(300, self._verificar_fila)

    def _limpeza_periodica(self) -> None:
        threading.Thread(target=self.eco.limpar, daemon=True).start()
        self.raiz.after(LIMPEZA_MS, self._limpeza_periodica)

    def executar(self) -> None:
        self.atualizar_lista()
        self.monitor.iniciar()
        self._limpeza_periodica()
        self._verificar_fila()
        try:
            self.raiz.mainloop()
        finally:
            self.monitor.parar()

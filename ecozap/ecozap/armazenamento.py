"""Guarda temporariamente os comprovantes (para reenviar), a agenda de
clientes e as estatísticas de papel economizado.

Estrutura em disco:
    <pasta_dados>/ecozap.db                      -> histórico, agenda, estatísticas (SQLite)
    <pasta_dados>/comprovantes/AAAA-MM-DD/<id>.pdf
    <pasta_dados>/chave_cpf.bin                  -> chave para o resumo do CPF

- Comprovantes: apagados depois de `dias_retencao` (padrão 15).
- Agenda de clientes: apagada se o cliente sumir por `dias_retencao_clientes`.
- Estatísticas diárias: ficam para sempre (só números, sem dados pessoais).
"""
from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

STATUS = ("pendente", "enviado", "erro", "salvo", "descartado")

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS comprovantes (
    id           TEXT PRIMARY KEY,
    criado_em    TEXT NOT NULL,
    origem       TEXT NOT NULL,
    arquivo_pdf  TEXT NOT NULL,
    texto        TEXT NOT NULL DEFAULT '',
    papel_mm     REAL NOT NULL DEFAULT 0,
    telefone     TEXT,
    nome         TEXT,
    formato      TEXT,
    status       TEXT NOT NULL DEFAULT 'pendente',
    tentativas   INTEGER NOT NULL DEFAULT 0,
    ultimo_erro  TEXT,
    enviado_em   TEXT
);
CREATE INDEX IF NOT EXISTS idx_criado ON comprovantes(criado_em);
CREATE INDEX IF NOT EXISTS idx_telefone ON comprovantes(telefone);

CREATE TABLE IF NOT EXISTS clientes (
    telefone      TEXT PRIMARY KEY,
    nome          TEXT,
    cpf_hash      TEXT,
    envios        INTEGER NOT NULL DEFAULT 0,
    ultimo_envio  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cli_cpf ON clientes(cpf_hash);

CREATE TABLE IF NOT EXISTS mensagens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telefone    TEXT NOT NULL,
    nome        TEXT,
    texto       TEXT,
    recebida_em TEXT NOT NULL,
    acao        TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_quando ON mensagens(recebida_em);

CREATE TABLE IF NOT EXISTS campanhas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo        TEXT NOT NULL,
    texto         TEXT NOT NULL,
    imagem        TEXT,
    publico       TEXT NOT NULL DEFAULT '{}',
    criada_em     TEXT NOT NULL,
    agendada_para TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'agendada',
    total         INTEGER NOT NULL DEFAULT 0,
    enviados      INTEGER NOT NULL DEFAULT 0,
    falhas        INTEGER NOT NULL DEFAULT 0,
    pulados       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS campanha_envios (
    campanha_id INTEGER NOT NULL,
    telefone    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'fila',
    detalhe     TEXT,
    msg_id      TEXT,
    entrega     TEXT,
    enviado_em  TEXT,
    PRIMARY KEY (campanha_id, telefone)
);
CREATE INDEX IF NOT EXISTS idx_envio_msg ON campanha_envios(msg_id);

CREATE TABLE IF NOT EXISTS estatisticas_diarias (
    dia        TEXT PRIMARY KEY,
    enviados   INTEGER NOT NULL DEFAULT 0,
    papel_mm   REAL NOT NULL DEFAULT 0,
    papel_cm2  REAL NOT NULL DEFAULT 0
);
"""

# colunas adicionadas depois da versão 0.1 (migração automática)
_NOVAS_COLUNAS = {
    "comprovantes": {
        "papel_cm2": "REAL NOT NULL DEFAULT 0",
        "cpf_hash": "TEXT",
        "chave_nfce": "TEXT",
        "link_nfce": "TEXT",
        "codigo": "TEXT",          # código curto que o cliente manda no WhatsApp
        "msg_id": "TEXT",          # id da mensagem no WhatsApp (para entregue/lido)
        "entrega": "TEXT",         # enviado | entregue | lido | falhou
    },
    "clientes": {
        "nome_whatsapp": "TEXT",
        "ultima_mensagem": "TEXT",          # última vez que o CLIENTE escreveu (janela 24h)
        "aceita_promocoes": "INTEGER NOT NULL DEFAULT 0",
        "consentimento_em": "TEXT",
        "origem_consentimento": "TEXT",
        "ultima_promocao": "TEXT",
        "ultima_resposta_auto": "TEXT",
    },
}
_ALFABETO_CODIGO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sem 0/O, 1/I/L
JANELA_ATENDIMENTO = timedelta(hours=23, minutes=50)  # 24h da Meta, com folga


@dataclass
class Comprovante:
    id: str
    criado_em: str
    origem: str
    arquivo_pdf: str
    texto: str
    papel_mm: float
    telefone: str | None
    nome: str | None
    formato: str | None
    status: str
    tentativas: int
    ultimo_erro: str | None
    enviado_em: str | None
    papel_cm2: float = 0
    cpf_hash: str | None = None
    chave_nfce: str | None = None
    link_nfce: str | None = None
    codigo: str | None = None
    msg_id: str | None = None
    entrega: str | None = None

    @property
    def pdf(self) -> bytes:
        return Path(self.arquivo_pdf).read_bytes()


@dataclass
class Cliente:
    telefone: str
    nome: str | None
    cpf_hash: str | None
    envios: int
    ultimo_envio: str              # último contato (envio ou mensagem)
    nome_whatsapp: str | None = None
    ultima_mensagem: str | None = None
    aceita_promocoes: int = 0
    consentimento_em: str | None = None
    origem_consentimento: str | None = None
    ultima_promocao: str | None = None
    ultima_resposta_auto: str | None = None

    @property
    def nome_exibicao(self) -> str | None:
        return self.nome or self.nome_whatsapp


@dataclass
class Mensagem:
    id: int
    telefone: str
    nome: str | None
    texto: str | None
    recebida_em: str
    acao: str | None


@dataclass
class Campanha:
    id: int
    titulo: str
    texto: str
    imagem: str | None
    publico: str
    criada_em: str
    agendada_para: str
    status: str
    total: int
    enviados: int
    falhas: int
    pulados: int

    @property
    def filtro(self) -> dict:
        return json.loads(self.publico or "{}")


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def medidas_papel(pdf: bytes) -> tuple[float, float]:
    """(comprimento em mm, área em cm²) que o comprovante gastaria impresso."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    try:
        comprimento = area = 0.0
        for i in range(len(doc)):
            l_mm, a_mm = (x * 25.4 / 72 for x in doc[i].get_size())
            comprimento += a_mm
            area += l_mm * a_mm / 100
        return round(comprimento, 1), round(area, 1)
    finally:
        doc.close()


def altura_papel_mm(pdf: bytes) -> float:
    return medidas_papel(pdf)[0]


class Armazenamento:
    def __init__(self, pasta_dados: str | Path, dias_retencao: int = 15,
                 dias_retencao_clientes: int = 365):
        self.pasta = Path(pasta_dados)
        self.pasta_arquivos = self.pasta / "comprovantes"
        self.pasta_arquivos.mkdir(parents=True, exist_ok=True)
        self.dias_retencao = int(dias_retencao)
        self.dias_retencao_clientes = int(dias_retencao_clientes)
        self._trava = threading.RLock()
        self._db = sqlite3.connect(self.pasta / "ecozap.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_ESQUEMA)
        self._migrar()

    def _migrar(self) -> None:
        with self._db:
            for tabela, colunas in _NOVAS_COLUNAS.items():
                existentes = {l["name"] for l in self._db.execute(f"PRAGMA table_info({tabela})")}
                for coluna, tipo in colunas.items():
                    if coluna not in existentes:
                        self._db.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_codigo ON comprovantes(codigo)")
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_comp_msg ON comprovantes(msg_id)")

    def _novo_codigo(self) -> str:
        for _ in range(50):
            codigo = "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(4))
            if not self._db.execute("SELECT 1 FROM comprovantes WHERE codigo = ?", (codigo,)).fetchone():
                return codigo
        return "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(6))

    # ---------- comprovantes ----------
    def salvar(self, pdf: bytes, texto: str, origem: str,
               telefone: str | None = None, quando: datetime | None = None,
               nome: str | None = None, cpf_hash: str | None = None,
               chave_nfce: str | None = None, link_nfce: str | None = None) -> Comprovante:
        quando = quando or _agora()
        ident = quando.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        pasta_dia = self.pasta_arquivos / quando.strftime("%Y-%m-%d")
        pasta_dia.mkdir(parents=True, exist_ok=True)
        arquivo = pasta_dia / f"{ident}.pdf"
        arquivo.write_bytes(pdf)
        mm, cm2 = medidas_papel(pdf)
        with self._trava, self._db:
            self._db.execute(
                "INSERT INTO comprovantes (id, criado_em, origem, arquivo_pdf, texto,"
                " papel_mm, papel_cm2, telefone, nome, cpf_hash, chave_nfce, link_nfce, codigo)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident, quando.isoformat(), origem, str(arquivo), texto, mm, cm2,
                 telefone, nome, cpf_hash, chave_nfce, link_nfce, self._novo_codigo()))
        return self.obter(ident)

    def substituir_pdf(self, ident: str, pdf: bytes) -> None:
        comp = self.obter(ident)
        Path(comp.arquivo_pdf).write_bytes(pdf)
        mm, cm2 = medidas_papel(pdf)
        with self._trava, self._db:
            self._db.execute("UPDATE comprovantes SET papel_mm = ?, papel_cm2 = ? WHERE id = ?",
                             (mm, cm2, ident))

    def atualizar(self, ident: str, **campos) -> None:
        if "status" in campos and campos["status"] not in STATUS:
            raise ValueError(f"status inválido: {campos['status']}")
        permitidos = {"telefone", "nome", "formato", "status", "ultimo_erro", "enviado_em"}
        invalidos = set(campos) - permitidos
        if invalidos:
            raise ValueError(f"campos não permitidos: {invalidos}")
        sets = ", ".join(f"{c} = ?" for c in campos)
        with self._trava, self._db:
            self._db.execute(f"UPDATE comprovantes SET {sets} WHERE id = ?",
                             (*campos.values(), ident))

    def registrar_envio(self, ident: str, ok: bool, erro: str | None = None,
                        msg_id: str | None = None) -> None:
        agora = _agora()
        with self._trava, self._db:
            antes = self._db.execute(
                "SELECT status, papel_mm, papel_cm2, telefone, nome, cpf_hash"
                " FROM comprovantes WHERE id = ?", (ident,)).fetchone()
            self._db.execute(
                "UPDATE comprovantes SET tentativas = tentativas + 1, status = ?,"
                " ultimo_erro = ?, enviado_em = CASE WHEN ? THEN ? ELSE enviado_em END,"
                " msg_id = CASE WHEN ? THEN ? ELSE msg_id END,"
                " entrega = CASE WHEN ? THEN 'enviado' ELSE entrega END WHERE id = ?",
                ("enviado" if ok else "erro", erro, ok, agora.isoformat(), ok, msg_id, ok, ident))
            if not ok:
                return
            if antes["status"] != "enviado":  # reenvio não conta papel de novo
                self._db.execute(
                    "INSERT INTO estatisticas_diarias (dia, enviados, papel_mm, papel_cm2)"
                    " VALUES (?, 1, ?, ?) ON CONFLICT(dia) DO UPDATE SET"
                    " enviados = enviados + 1, papel_mm = papel_mm + excluded.papel_mm,"
                    " papel_cm2 = papel_cm2 + excluded.papel_cm2",
                    (agora.date().isoformat(), antes["papel_mm"], antes["papel_cm2"]))
            self._lembrar_cliente(antes["telefone"], antes["nome"], antes["cpf_hash"], agora)

    def atualizar_entrega(self, msg_id: str, entrega: str) -> bool:
        """Status vindo do webhook (entregue, lido, falhou). Nunca "volta" de lido p/ entregue."""
        ordem = {"enviado": 1, "entregue": 2, "lido": 3, "falhou": 4}
        with self._trava, self._db:
            alterou = 0
            for tabela in ("comprovantes", "campanha_envios"):
                linha = self._db.execute(f"SELECT entrega FROM {tabela} WHERE msg_id = ?",
                                         (msg_id,)).fetchone()
                if linha and ordem.get(entrega, 0) > ordem.get(linha["entrega"] or "", 0):
                    alterou += self._db.execute(f"UPDATE {tabela} SET entrega = ? WHERE msg_id = ?",
                                                (entrega, msg_id)).rowcount
            return bool(alterou)

    def por_codigo(self, codigo: str) -> Comprovante | None:
        l = self._db.execute("SELECT * FROM comprovantes WHERE codigo = ? AND status != 'descartado'"
                             " ORDER BY criado_em DESC LIMIT 1", (codigo.upper(),)).fetchone()
        return Comprovante(**dict(l)) if l else None

    def pendentes_sem_telefone(self, minutos: int, agora: datetime | None = None) -> list[Comprovante]:
        """Comprovantes recentes que ainda esperam o WhatsApp do cliente."""
        desde = ((agora or _agora()) - timedelta(minutes=minutos)).isoformat()
        return [Comprovante(**dict(l)) for l in self._db.execute(
            "SELECT * FROM comprovantes WHERE status IN ('pendente', 'salvo') AND telefone IS NULL"
            " AND criado_em >= ? ORDER BY criado_em DESC", (desde,))]

    def ultimo_do_telefone(self, telefone: str) -> Comprovante | None:
        l = self._db.execute("SELECT * FROM comprovantes WHERE telefone = ? AND status != 'descartado'"
                             " ORDER BY criado_em DESC LIMIT 1", (telefone,)).fetchone()
        return Comprovante(**dict(l)) if l else None

    def obter(self, ident: str) -> Comprovante | None:
        linha = self._db.execute("SELECT * FROM comprovantes WHERE id = ?",
                                 (ident,)).fetchone()
        return Comprovante(**dict(linha)) if linha else None

    def listar(self, limite: int = 100, busca: str = "",
               status: str | None = None) -> list[Comprovante]:
        """Últimos comprovantes. `busca` procura no telefone, nome ou texto."""
        sql, args, filtros = "SELECT * FROM comprovantes", [], []
        if busca:
            filtros.append("(telefone LIKE ? OR nome LIKE ? OR texto LIKE ?)")
            args += [f"%{busca}%"] * 3
        if status:
            filtros.append("status = ?")
            args.append(status)
        if filtros:
            sql += " WHERE " + " AND ".join(filtros)
        sql += " ORDER BY criado_em DESC LIMIT ?"
        args.append(limite)
        return [Comprovante(**dict(l)) for l in self._db.execute(sql, args)]

    # ---------- agenda de clientes ----------
    def _lembrar_cliente(self, telefone, nome, cpf_hash, quando: datetime) -> None:
        if not telefone:
            return
        self._db.execute(
            "INSERT INTO clientes (telefone, nome, cpf_hash, envios, ultimo_envio)"
            " VALUES (?, ?, ?, 1, ?) ON CONFLICT(telefone) DO UPDATE SET"
            " nome = COALESCE(excluded.nome, nome),"
            " cpf_hash = COALESCE(excluded.cpf_hash, cpf_hash),"
            " envios = envios + 1, ultimo_envio = excluded.ultimo_envio",
            (telefone, nome, cpf_hash, quando.isoformat()))

    def cliente_por_cpf(self, cpf_hash: str | None) -> Cliente | None:
        if not cpf_hash:
            return None
        l = self._db.execute("SELECT * FROM clientes WHERE cpf_hash = ?"
                             " ORDER BY ultimo_envio DESC LIMIT 1", (cpf_hash,)).fetchone()
        return Cliente(**dict(l)) if l else None

    def cliente_por_telefone(self, telefone: str | None) -> Cliente | None:
        if not telefone:
            return None
        l = self._db.execute("SELECT * FROM clientes WHERE telefone = ?", (telefone,)).fetchone()
        return Cliente(**dict(l)) if l else None

    def buscar_clientes(self, termo: str, limite: int = 8) -> list[Cliente]:
        """Autocompletar: procura por pedaço do nome ou do telefone."""
        termo = (termo or "").strip()
        if len(termo) < 2:
            return []
        digitos = "".join(c for c in termo if c.isdigit())
        sql = "SELECT * FROM clientes WHERE nome LIKE ?"
        args: list = [f"%{termo}%"]
        if len(digitos) >= 3:
            sql += " OR telefone LIKE ?"
            args.append(f"%{digitos}%")
        sql += " ORDER BY envios DESC, ultimo_envio DESC LIMIT ?"
        args.append(limite)
        return [Cliente(**dict(l)) for l in self._db.execute(sql, args)]

    # ---------- mensagens recebidas / atendimento ----------
    def registrar_mensagem(self, telefone: str, nome: str | None, texto: str,
                           quando: datetime | None = None) -> int:
        """Guarda a mensagem do cliente e abre a "janela de 24h" dele."""
        quando = (quando or _agora()).isoformat()
        with self._trava, self._db:
            self._db.execute(
                "INSERT INTO clientes (telefone, nome_whatsapp, envios, ultimo_envio, ultima_mensagem)"
                " VALUES (?, ?, 0, ?, ?) ON CONFLICT(telefone) DO UPDATE SET"
                " nome_whatsapp = COALESCE(excluded.nome_whatsapp, nome_whatsapp),"
                " ultimo_envio = excluded.ultimo_envio, ultima_mensagem = excluded.ultima_mensagem",
                (telefone, nome, quando, quando))
            return self._db.execute(
                "INSERT INTO mensagens (telefone, nome, texto, recebida_em) VALUES (?,?,?,?)",
                (telefone, nome, (texto or "")[:1000], quando)).lastrowid

    def marcar_acao_mensagem(self, ident: int, acao: str) -> None:
        with self._trava, self._db:
            self._db.execute("UPDATE mensagens SET acao = ? WHERE id = ?", (acao, ident))

    def mensagens_recentes(self, minutos: int = 15, limite: int = 50,
                           agora: datetime | None = None) -> list[Mensagem]:
        desde = ((agora or _agora()) - timedelta(minutes=minutos)).isoformat()
        return [Mensagem(**dict(l)) for l in self._db.execute(
            "SELECT * FROM mensagens WHERE recebida_em >= ? ORDER BY recebida_em DESC LIMIT ?",
            (desde, limite))]

    def janela_aberta(self, telefone: str, agora: datetime | None = None) -> bool:
        """O cliente escreveu nas últimas 24h? (aí pode receber mensagem livre)"""
        cli = self.cliente_por_telefone(telefone)
        if not cli or not cli.ultima_mensagem:
            return False
        return datetime.fromisoformat(cli.ultima_mensagem) > (agora or _agora()) - JANELA_ATENDIMENTO

    def pode_responder_auto(self, telefone: str, horas: int = 12, agora: datetime | None = None) -> bool:
        cli = self.cliente_por_telefone(telefone)
        if not cli or not cli.ultima_resposta_auto:
            return True
        return datetime.fromisoformat(cli.ultima_resposta_auto) < (agora or _agora()) - timedelta(hours=horas)

    def marcar_resposta_auto(self, telefone: str) -> None:
        with self._trava, self._db:
            self._db.execute("UPDATE clientes SET ultima_resposta_auto = ? WHERE telefone = ?",
                             (_agora().isoformat(), telefone))

    def definir_promocoes(self, telefone: str, aceita: bool, origem: str) -> None:
        """Consentimento (opt-in) ou cancelamento (opt-out) das promoções."""
        agora = _agora().isoformat()
        with self._trava, self._db:
            self._db.execute(
                "INSERT INTO clientes (telefone, envios, ultimo_envio) VALUES (?, 0, ?)"
                " ON CONFLICT(telefone) DO NOTHING", (telefone, agora))
            self._db.execute(
                "UPDATE clientes SET aceita_promocoes = ?, consentimento_em = ?,"
                " origem_consentimento = ? WHERE telefone = ?",
                (1 if aceita else 0, agora, origem, telefone))
            if not aceita:  # sai também das campanhas que ainda estão na fila
                self._db.execute("UPDATE campanha_envios SET status = 'pulado',"
                                 " detalhe = 'cliente pediu para sair' WHERE telefone = ? AND status = 'fila'",
                                 (telefone,))

    # ---------- campanhas de promoção ----------
    def publico_promocao(self, filtro: dict, dias_entre: int, agora: datetime | None = None) -> list[Cliente]:
        """Clientes que aceitaram promoções e não receberam nenhuma recentemente."""
        agora = agora or _agora()
        sql = ("SELECT * FROM clientes WHERE aceita_promocoes = 1"
               " AND (ultima_promocao IS NULL OR ultima_promocao < ?)")
        args: list = [(agora - timedelta(days=dias_entre)).isoformat()]
        if filtro.get("dias_recentes"):
            sql += " AND ultimo_envio >= ?"
            args.append((agora - timedelta(days=int(filtro["dias_recentes"]))).isoformat())
        return [Cliente(**dict(l)) for l in self._db.execute(sql + " ORDER BY ultimo_envio DESC", args)]

    def criar_campanha(self, titulo: str, texto: str, imagem: str | None, filtro: dict,
                       agendada_para: datetime | None = None) -> Campanha:
        agora = _agora()
        with self._trava, self._db:
            ident = self._db.execute(
                "INSERT INTO campanhas (titulo, texto, imagem, publico, criada_em, agendada_para)"
                " VALUES (?,?,?,?,?,?)",
                (titulo, texto, imagem, json.dumps(filtro), agora.isoformat(),
                 (agendada_para or agora).isoformat())).lastrowid
        return self.obter_campanha(ident)

    def preencher_fila(self, campanha_id: int, telefones: list[str]) -> int:
        with self._trava, self._db:
            self._db.executemany("INSERT OR IGNORE INTO campanha_envios (campanha_id, telefone)"
                                 " VALUES (?, ?)", [(campanha_id, t) for t in telefones])
            total = self._db.execute("SELECT COUNT(*) FROM campanha_envios WHERE campanha_id = ?",
                                     (campanha_id,)).fetchone()[0]
            self._db.execute("UPDATE campanhas SET total = ? WHERE id = ?", (total, campanha_id))
            return total

    def obter_campanha(self, ident: int) -> Campanha | None:
        l = self._db.execute("SELECT * FROM campanhas WHERE id = ?", (ident,)).fetchone()
        return Campanha(**dict(l)) if l else None

    def listar_campanhas(self, limite: int = 50) -> list[Campanha]:
        return [Campanha(**dict(l)) for l in self._db.execute(
            "SELECT * FROM campanhas ORDER BY id DESC LIMIT ?", (limite,))]

    def mudar_status_campanha(self, ident: int, status: str) -> None:
        with self._trava, self._db:
            self._db.execute("UPDATE campanhas SET status = ? WHERE id = ?", (status, ident))
            if status == "cancelada":
                self._db.execute("UPDATE campanha_envios SET status = 'pulado', detalhe = 'campanha cancelada'"
                                 " WHERE campanha_id = ? AND status = 'fila'", (ident,))
                self._recontar(ident)

    def campanhas_prontas(self, agora: datetime | None = None) -> list[Campanha]:
        return [Campanha(**dict(l)) for l in self._db.execute(
            "SELECT * FROM campanhas WHERE status IN ('agendada', 'enviando') AND agendada_para <= ?"
            " ORDER BY agendada_para, id", ((agora or _agora()).isoformat(),))]

    def proximo_da_fila(self, campanha_id: int) -> str | None:
        l = self._db.execute("SELECT telefone FROM campanha_envios WHERE campanha_id = ? AND status = 'fila'"
                             " LIMIT 1", (campanha_id,)).fetchone()
        return l["telefone"] if l else None

    def registrar_envio_promocao(self, campanha_id: int, telefone: str, status: str,
                                 detalhe: str | None = None, msg_id: str | None = None,
                                 quando: datetime | None = None) -> None:
        agora = (quando or _agora()).isoformat()
        with self._trava, self._db:
            self._db.execute(
                "UPDATE campanha_envios SET status = ?, detalhe = ?, msg_id = ?, enviado_em = ?,"
                " entrega = CASE WHEN ? = 'enviado' THEN 'enviado' ELSE entrega END"
                " WHERE campanha_id = ? AND telefone = ?",
                (status, detalhe, msg_id, agora, status, campanha_id, telefone))
            if status == "enviado":
                self._db.execute("UPDATE clientes SET ultima_promocao = ? WHERE telefone = ?",
                                 (agora, telefone))
            self._recontar(campanha_id)

    def _recontar(self, campanha_id: int) -> None:
        self._db.execute(
            "UPDATE campanhas SET"
            " enviados = (SELECT COUNT(*) FROM campanha_envios WHERE campanha_id = :c AND status = 'enviado'),"
            " falhas = (SELECT COUNT(*) FROM campanha_envios WHERE campanha_id = :c AND status = 'falhou'),"
            " pulados = (SELECT COUNT(*) FROM campanha_envios WHERE campanha_id = :c AND status = 'pulado')"
            " WHERE id = :c", {"c": campanha_id})

    def promocoes_enviadas_hoje(self, agora: datetime | None = None) -> int:
        hoje = (agora or _agora()).date().isoformat()
        return self._db.execute("SELECT COUNT(*) FROM campanha_envios WHERE status = 'enviado'"
                                " AND substr(enviado_em, 1, 10) = ?", (hoje,)).fetchone()[0]

    def envios_da_campanha(self, campanha_id: int) -> list[dict]:
        return [dict(l) for l in self._db.execute(
            "SELECT * FROM campanha_envios WHERE campanha_id = ? ORDER BY enviado_em DESC", (campanha_id,))]

    def listar_clientes(self, limite: int = 200) -> list[Cliente]:
        return [Cliente(**dict(l)) for l in self._db.execute(
            "SELECT * FROM clientes ORDER BY ultimo_envio DESC LIMIT ?", (limite,))]

    def esquecer_cliente(self, telefone: str) -> None:
        """Direito de exclusão (LGPD): apaga o cliente da agenda."""
        with self._trava, self._db:
            self._db.execute("DELETE FROM clientes WHERE telefone = ?", (telefone,))
            self._db.execute("DELETE FROM mensagens WHERE telefone = ?", (telefone,))
            self._db.execute("DELETE FROM campanha_envios WHERE telefone = ?", (telefone,))

    # ---------- estatísticas ----------
    def estatisticas(self) -> dict:
        l = self._db.execute(
            "SELECT COALESCE(SUM(enviados), 0) AS enviados, COALESCE(SUM(papel_mm), 0) AS mm,"
            " COALESCE(SUM(papel_cm2), 0) AS cm2 FROM estatisticas_diarias").fetchone()
        guardados = self._db.execute("SELECT COUNT(*) FROM comprovantes").fetchone()[0]
        pendentes = self._db.execute(
            "SELECT COUNT(*) FROM comprovantes WHERE status IN ('pendente', 'erro')").fetchone()[0]
        return {"total": guardados, "guardados": guardados, "pendentes": pendentes,
                "enviados": l["enviados"], "papel_economizado_m": round(l["mm"] / 1000, 2),
                "papel_cm2": round(l["cm2"], 1)}

    def estatisticas_periodo(self, inicio: date, fim: date) -> list[dict]:
        """Linhas por dia entre inicio e fim (inclusive)."""
        return [dict(l) for l in self._db.execute(
            "SELECT * FROM estatisticas_diarias WHERE dia BETWEEN ? AND ? ORDER BY dia",
            (inicio.isoformat(), fim.isoformat()))]

    # ---------- limpeza ----------
    def limpar_antigos(self, agora: datetime | None = None) -> int:
        """Apaga comprovantes mais velhos que a retenção. Retorna quantos."""
        agora = agora or _agora()
        limite = agora - timedelta(days=self.dias_retencao)
        with self._trava, self._db:
            antigos = self._db.execute(
                "SELECT id, arquivo_pdf FROM comprovantes WHERE criado_em < ?",
                (limite.isoformat(),)).fetchall()
            for linha in antigos:
                Path(linha["arquivo_pdf"]).unlink(missing_ok=True)
            self._db.execute("DELETE FROM comprovantes WHERE criado_em < ?",
                             (limite.isoformat(),))
            self._db.execute("DELETE FROM mensagens WHERE recebida_em < ?", (limite.isoformat(),))
            if self.dias_retencao_clientes:
                limite_cli = (agora - timedelta(days=self.dias_retencao_clientes)).isoformat()
                self._db.execute("DELETE FROM clientes WHERE ultimo_envio < ?", (limite_cli,))
                self._db.execute("DELETE FROM campanha_envios WHERE enviado_em < ?", (limite_cli,))
        # remove pastas de dias que ficaram vazias (ou que são mais velhas que o limite)
        for pasta in self.pasta_arquivos.iterdir():
            if not pasta.is_dir():
                continue
            try:
                dia = datetime.strptime(pasta.name, "%Y-%m-%d")
            except ValueError:
                continue
            if dia.date() < limite.date() or not any(pasta.iterdir()):
                shutil.rmtree(pasta, ignore_errors=True)
        return len(antigos)

    def fechar(self) -> None:
        self._db.close()

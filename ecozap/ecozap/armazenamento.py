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

CREATE TABLE IF NOT EXISTS estatisticas_diarias (
    dia        TEXT PRIMARY KEY,
    enviados   INTEGER NOT NULL DEFAULT 0,
    papel_mm   REAL NOT NULL DEFAULT 0,
    papel_cm2  REAL NOT NULL DEFAULT 0
);
"""

# colunas adicionadas depois da versão 0.1 (migração automática)
_NOVAS_COLUNAS = {
    "papel_cm2": "REAL NOT NULL DEFAULT 0",
    "cpf_hash": "TEXT",
    "chave_nfce": "TEXT",
    "link_nfce": "TEXT",
}


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

    @property
    def pdf(self) -> bytes:
        return Path(self.arquivo_pdf).read_bytes()


@dataclass
class Cliente:
    telefone: str
    nome: str | None
    cpf_hash: str | None
    envios: int
    ultimo_envio: str


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
        existentes = {l["name"] for l in self._db.execute("PRAGMA table_info(comprovantes)")}
        with self._db:
            for coluna, tipo in _NOVAS_COLUNAS.items():
                if coluna not in existentes:
                    self._db.execute(f"ALTER TABLE comprovantes ADD COLUMN {coluna} {tipo}")

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
                " papel_mm, papel_cm2, telefone, nome, cpf_hash, chave_nfce, link_nfce)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident, quando.isoformat(), origem, str(arquivo), texto, mm, cm2,
                 telefone, nome, cpf_hash, chave_nfce, link_nfce))
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

    def registrar_envio(self, ident: str, ok: bool, erro: str | None = None) -> None:
        agora = _agora()
        with self._trava, self._db:
            antes = self._db.execute(
                "SELECT status, papel_mm, papel_cm2, telefone, nome, cpf_hash"
                " FROM comprovantes WHERE id = ?", (ident,)).fetchone()
            self._db.execute(
                "UPDATE comprovantes SET tentativas = tentativas + 1, status = ?,"
                " ultimo_erro = ?, enviado_em = CASE WHEN ? THEN ? ELSE enviado_em END"
                " WHERE id = ?",
                ("enviado" if ok else "erro", erro, ok, agora.isoformat(), ident))
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

    def listar_clientes(self, limite: int = 200) -> list[Cliente]:
        return [Cliente(**dict(l)) for l in self._db.execute(
            "SELECT * FROM clientes ORDER BY ultimo_envio DESC LIMIT ?", (limite,))]

    def esquecer_cliente(self, telefone: str) -> None:
        """Direito de exclusão (LGPD): apaga o cliente da agenda."""
        with self._trava, self._db:
            self._db.execute("DELETE FROM clientes WHERE telefone = ?", (telefone,))

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
            if self.dias_retencao_clientes:
                limite_cli = agora - timedelta(days=self.dias_retencao_clientes)
                self._db.execute("DELETE FROM clientes WHERE ultimo_envio < ?",
                                 (limite_cli.isoformat(),))
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

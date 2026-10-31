"""Guarda temporariamente os comprovantes (para reenviar) e o histórico.

Estrutura em disco:
    <pasta_dados>/ecozap.db                      -> histórico (SQLite)
    <pasta_dados>/comprovantes/AAAA-MM-DD/<id>.pdf

A limpeza apaga tudo que for mais velho que `dias_retencao` (padrão 15).
"""
from __future__ import annotations

import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
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
"""


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

    @property
    def pdf(self) -> bytes:
        return Path(self.arquivo_pdf).read_bytes()


def _agora() -> datetime:
    return datetime.now().replace(microsecond=0)


def altura_papel_mm(pdf: bytes) -> float:
    """Quantos mm de papel esse comprovante gastaria impresso."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    try:
        return round(sum(doc[i].get_height() for i in range(len(doc))) * 25.4 / 72, 1)
    finally:
        doc.close()


class Armazenamento:
    def __init__(self, pasta_dados: str | Path, dias_retencao: int = 15):
        self.pasta = Path(pasta_dados)
        self.pasta_arquivos = self.pasta / "comprovantes"
        self.pasta_arquivos.mkdir(parents=True, exist_ok=True)
        self.dias_retencao = int(dias_retencao)
        self._trava = threading.Lock()
        self._db = sqlite3.connect(self.pasta / "ecozap.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_ESQUEMA)

    # ---------- gravação ----------
    def salvar(self, pdf: bytes, texto: str, origem: str,
               telefone: str | None = None, quando: datetime | None = None) -> Comprovante:
        quando = quando or _agora()
        ident = quando.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        pasta_dia = self.pasta_arquivos / quando.strftime("%Y-%m-%d")
        pasta_dia.mkdir(parents=True, exist_ok=True)
        arquivo = pasta_dia / f"{ident}.pdf"
        arquivo.write_bytes(pdf)
        with self._trava, self._db:
            self._db.execute(
                "INSERT INTO comprovantes (id, criado_em, origem, arquivo_pdf, texto,"
                " papel_mm, telefone) VALUES (?,?,?,?,?,?,?)",
                (ident, quando.isoformat(), origem, str(arquivo), texto,
                 altura_papel_mm(pdf), telefone))
        return self.obter(ident)

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
        with self._trava, self._db:
            self._db.execute(
                "UPDATE comprovantes SET tentativas = tentativas + 1, status = ?,"
                " ultimo_erro = ?, enviado_em = CASE WHEN ? THEN ? ELSE enviado_em END"
                " WHERE id = ?",
                ("enviado" if ok else "erro", erro, ok, _agora().isoformat(), ident))

    # ---------- consulta ----------
    def obter(self, ident: str) -> Comprovante | None:
        linha = self._db.execute("SELECT * FROM comprovantes WHERE id = ?",
                                 (ident,)).fetchone()
        return Comprovante(**dict(linha)) if linha else None

    def listar(self, limite: int = 100, busca: str = "") -> list[Comprovante]:
        """Últimos comprovantes. `busca` procura no telefone, nome ou texto."""
        sql = "SELECT * FROM comprovantes"
        args: list = []
        if busca:
            sql += " WHERE telefone LIKE ? OR nome LIKE ? OR texto LIKE ?"
            args = [f"%{busca}%"] * 3
        sql += " ORDER BY criado_em DESC LIMIT ?"
        args.append(limite)
        return [Comprovante(**dict(l)) for l in self._db.execute(sql, args)]

    def estatisticas(self) -> dict:
        l = self._db.execute(
            "SELECT COUNT(*) AS total,"
            " COALESCE(SUM(CASE WHEN status='enviado' THEN 1 END), 0) AS enviados,"
            " COALESCE(SUM(CASE WHEN status='enviado' THEN papel_mm END), 0) AS mm"
            " FROM comprovantes").fetchone()
        return {"total": l["total"], "enviados": l["enviados"],
                "papel_economizado_m": round(l["mm"] / 1000, 2)}

    # ---------- limpeza ----------
    def limpar_antigos(self, agora: datetime | None = None) -> int:
        """Apaga comprovantes mais velhos que a retenção. Retorna quantos."""
        limite = (agora or _agora()) - timedelta(days=self.dias_retencao)
        with self._trava, self._db:
            antigos = self._db.execute(
                "SELECT id, arquivo_pdf FROM comprovantes WHERE criado_em < ?",
                (limite.isoformat(),)).fetchall()
            for linha in antigos:
                Path(linha["arquivo_pdf"]).unlink(missing_ok=True)
            self._db.execute("DELETE FROM comprovantes WHERE criado_em < ?",
                             (limite.isoformat(),))
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

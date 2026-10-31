"""Remarketing: campanhas de promoção pelo WhatsApp, com responsabilidade.

Regras que o EcoZap SEMPRE aplica (não dá para desligar):
- só recebe quem pediu (respondeu PROMO ou autorizou no balcão) - opt-in;
- toda promoção termina com "para não receber mais, responda SAIR";
- quem respondeu SAIR sai na hora, inclusive das campanhas já na fila;
- no máximo 1 promoção por cliente a cada `dias_entre_promocoes`;
- só dentro do horário configurado, com pausa aleatória entre envios e
  limite diário (protege o número da loja contra bloqueio por spam).
"""
from __future__ import annotations

import io
import logging
import random
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from .armazenamento import Campanha, Cliente
from .whatsapp import ErroEnvio

log = logging.getLogger("ecozap.promocoes")
RODAPE = "\n\n_{loja}_ · Para não receber mais ofertas, responda *SAIR*."


def _hora(texto: str):
    h, m = (int(x) for x in texto.split(":"))
    return h * 60 + m


class Disparador:
    def __init__(self, eco, relogio=None):
        self.eco = eco
        self.relogio = relogio or (lambda: datetime.now().replace(microsecond=0))
        self._parar = threading.Event()
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def cfg(self) -> dict:
        return self.eco.cfg["promocoes"]

    # ---------- criação ----------
    def previa(self, dias_recentes: int | None = None) -> int:
        """Quantos clientes receberiam uma campanha criada agora."""
        filtro = {"dias_recentes": dias_recentes} if dias_recentes else {}
        return len(self.eco.armazenamento.publico_promocao(
            filtro, int(self.cfg["dias_entre_promocoes"]), self.relogio()))

    def _guardar_imagem(self, dados: bytes) -> str:
        img = Image.open(io.BytesIO(dados)).convert("RGB")
        img.thumbnail((1600, 1600))
        pasta = Path(self.eco.cfg["pasta_dados"]) / "campanhas"
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / f"{uuid.uuid4().hex[:12]}.jpg"
        img.save(destino, "JPEG", quality=88)
        return str(destino)

    def criar(self, titulo: str, texto: str, imagem: bytes | None = None,
              dias_recentes: int | None = None, agendada_para: datetime | None = None) -> Campanha:
        if not titulo.strip() or not texto.strip():
            raise ValueError("Preencha o título e o texto da promoção")
        if len(texto) > 900:
            raise ValueError("Texto muito longo (máximo 900 caracteres)")
        arm = self.eco.armazenamento
        filtro = {"dias_recentes": int(dias_recentes)} if dias_recentes else {}
        caminho = self._guardar_imagem(imagem) if imagem else None
        camp = arm.criar_campanha(titulo.strip(), texto.strip(), caminho, filtro,
                                   agendada_para or self.relogio())
        publico = arm.publico_promocao(filtro, int(self.cfg["dias_entre_promocoes"]), self.relogio())
        total = arm.preencher_fila(camp.id, [c.telefone for c in publico])
        if total == 0:
            arm.mudar_status_campanha(camp.id, "concluida")
        log.info("Campanha %s '%s' criada para %d cliente(s)", camp.id, titulo, total)
        self._acordar.set()
        return arm.obter_campanha(camp.id)

    def pausar(self, ident: int) -> None:
        self.eco.armazenamento.mudar_status_campanha(ident, "pausada")

    def retomar(self, ident: int) -> None:
        self.eco.armazenamento.mudar_status_campanha(ident, "enviando")
        self._acordar.set()

    def cancelar(self, ident: int) -> None:
        self.eco.armazenamento.mudar_status_campanha(ident, "cancelada")

    # ---------- envio ----------
    def dentro_do_horario(self, agora: datetime | None = None) -> bool:
        agora = agora or self.relogio()
        minuto = agora.hour * 60 + agora.minute
        return _hora(self.cfg["horario_inicio"]) <= minuto < _hora(self.cfg["horario_fim"])

    def montar_texto(self, camp: Campanha, cli: Cliente) -> str:
        nome = cli.nome_exibicao
        loja = self.eco.cfg["nome_loja"]
        # troca só as variáveis conhecidas (o texto pode ter chaves { } normais)
        texto = (camp.texto.replace("{nome}", f", {nome.split()[0]}" if nome else "")
                 .replace("{nome_completo}", nome or "").replace("{loja}", loja))
        return texto + RODAPE.format(loja=loja)

    def _enviar(self, camp: Campanha, cli: Cliente) -> str:
        eco, arm = self.eco, self.eco.armazenamento
        provedor = eco.provedor
        texto = self.montar_texto(camp, cli)
        imagem = Path(camp.imagem).read_bytes() if camp.imagem else None
        template = params = None
        if getattr(provedor, "exige_template_fora_da_janela", False) and not arm.janela_aberta(cli.telefone):
            cloud = eco.cfg["whatsapp"]["cloud_api"]
            template = cloud.get("template_promocao_imagem") if imagem else cloud.get("template_promocao")
            if not template:
                raise _Pular("fora da janela de 24h e sem template de promoção aprovado")
            nome = cli.nome_exibicao or ""
            valores = {"texto": camp.texto.replace("{nome}", "").replace("{loja}", eco.cfg["nome_loja"]),
                       "nome": nome.split()[0] if nome else "", "nome_completo": nome,
                       "loja": eco.cfg["nome_loja"], "titulo": camp.titulo}
            params = [p.format(**valores) for p in cloud.get("parametros_template_promocao", ["{texto}"])]
        if imagem:
            return provedor.enviar(cli.telefone, imagem, "promocao.jpg", "image/jpeg", texto,
                                   template=template, parametros_template=params)
        return provedor.enviar_texto(cli.telefone, texto, template=template, parametros_template=params)

    def processar_um(self) -> str | None:
        """Envia UMA promoção (se for a hora). Retorna o telefone tratado, ou None."""
        if not self.cfg["ativo"] or not self.dentro_do_horario():
            return None
        arm = self.eco.armazenamento
        agora = self.relogio()
        if arm.promocoes_enviadas_hoje(agora) >= int(self.cfg["limite_diario"]):
            return None
        for camp in arm.campanhas_prontas(agora):
            telefone = arm.proximo_da_fila(camp.id)
            if not telefone:
                arm.mudar_status_campanha(camp.id, "concluida")
                log.info("Campanha %s concluída", camp.id)
                continue
            if camp.status == "agendada":
                arm.mudar_status_campanha(camp.id, "enviando")
            cli = arm.cliente_por_telefone(telefone)
            limite = agora - timedelta(days=int(self.cfg["dias_entre_promocoes"]))
            if not cli or not cli.aceita_promocoes:
                arm.registrar_envio_promocao(camp.id, telefone, "pulado", "sem consentimento", quando=agora)
            elif cli.ultima_promocao and datetime.fromisoformat(cli.ultima_promocao) > limite:
                arm.registrar_envio_promocao(camp.id, telefone, "pulado", "recebeu promoção há pouco tempo",
                                             quando=agora)
            else:
                try:
                    msg_id = self._enviar(camp, cli)
                    arm.registrar_envio_promocao(camp.id, telefone, "enviado", msg_id=msg_id, quando=agora)
                except _Pular as e:
                    arm.registrar_envio_promocao(camp.id, telefone, "pulado", str(e), quando=agora)
                except (ErroEnvio, OSError) as e:
                    arm.registrar_envio_promocao(camp.id, telefone, "falhou", str(e)[:300], quando=agora)
                    log.warning("Promoção para %s falhou: %s", telefone, e)
            return telefone
        return None

    # ---------- segundo plano ----------
    def _laco(self) -> None:
        while not self._parar.is_set():
            try:
                tratou = self.processar_um()
            except Exception:
                log.exception("Erro no disparador de promoções")
                tratou = None
            if tratou:
                espera = random.uniform(float(self.cfg["intervalo_min_segundos"]),
                                        float(self.cfg["intervalo_max_segundos"]))
            else:
                espera = 60
            self._acordar.wait(espera)
            self._acordar.clear()

    def iniciar(self) -> None:
        self._thread = threading.Thread(target=self._laco, name="promocoes", daemon=True)
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        self._acordar.set()


class _Pular(Exception):
    pass

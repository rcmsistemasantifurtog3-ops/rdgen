import io
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from ecozap import config
from ecozap.promocoes import Disparador
from ecozap.servico import EcoZap
from ecozap.webhook import MensagemRecebida


class Relogio:
    def __init__(self, quando): self.agora = quando
    def __call__(self): return self.agora


@pytest.fixture
def ambiente(tmp_path):
    cfg = config.carregar(tmp_path / "x.json")
    cfg["pasta_dados"] = str(tmp_path / "dados")
    cfg["nome_loja"] = "Padaria"
    eco = EcoZap(cfg)
    rel = Relogio(datetime.now().replace(hour=10, minute=0, second=0, microsecond=0))
    for numero, texto in (("11911111111", "PROMO"), ("11922222222", "promoções"), ("11933333333", "oi")):
        eco.receber_mensagem(MensagemRecebida(numero, "Ana Lima", texto, rel.agora))
    return eco, Disparador(eco, relogio=rel), rel


def textos(eco):
    pasta = Path(eco.cfg["pasta_dados"]) / "simulado"
    return [json.loads(p.read_text(encoding="utf-8")) for p in pasta.glob("*.json")]


def test_so_envia_para_quem_aceitou_e_sempre_com_sair(ambiente):
    eco, disp, _ = ambiente
    assert disp.previa() == 2
    camp = disp.criar("Pão", "Olá{nome}! Pão com 20% hoje na {loja} {sem_variavel}")
    assert camp.total == 2
    while disp.processar_um():
        pass
    camp = eco.armazenamento.obter_campanha(camp.id)
    assert camp.enviados == 2 and camp.status == "concluida"
    promos = [t for t in textos(eco) if "20%" in (t.get("texto") or "")]
    assert len(promos) == 2
    assert all("responda *SAIR*" in t["texto"] and "Olá, Ana!" in t["texto"] for t in promos)
    assert all("{sem_variavel}" in t["texto"] for t in promos)  # chave desconhecida não quebra
    assert not any(t["telefone"] == "5511933333333" and "20%" in (t.get("texto") or "") for t in textos(eco))


def test_respeita_frequencia_horario_e_limite(ambiente):
    eco, disp, rel = ambiente
    disp.criar("A", "Oferta A")
    while disp.processar_um():
        pass
    assert disp.criar("B", "Oferta B").total == 0       # receberam há menos de 7 dias
    rel.agora += timedelta(days=8)
    camp = disp.criar("C", "Oferta C")
    assert camp.total == 2
    rel.agora = rel.agora.replace(hour=22)               # fora do horário
    assert disp.processar_um() is None
    rel.agora = rel.agora.replace(hour=11)
    eco.cfg["promocoes"]["limite_diario"] = 1
    assert disp.processar_um() is not None
    assert disp.processar_um() is None                   # bateu o limite do dia


def test_sair_depois_de_entrar_na_fila(ambiente):
    eco, disp, rel = ambiente
    camp = disp.criar("A", "Oferta")
    eco.receber_mensagem(MensagemRecebida("11911111111", None, "Sair", rel.agora))
    while disp.processar_um():
        pass
    camp = eco.armazenamento.obter_campanha(camp.id)
    assert camp.enviados == 1 and camp.pulados == 1


def test_agendada_e_pausada(ambiente):
    eco, disp, rel = ambiente
    camp = disp.criar("A", "Oferta", agendada_para=rel.agora + timedelta(hours=2))
    assert disp.processar_um() is None
    rel.agora += timedelta(hours=3)
    disp.pausar(camp.id)
    assert disp.processar_um() is None
    disp.retomar(camp.id)
    assert disp.processar_um() is not None


def test_imagem_e_publico_recente(ambiente):
    eco, disp, rel = ambiente
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), "red").save(buf, "PNG")
    camp = disp.criar("Img", "Com foto", imagem=buf.getvalue(), dias_recentes=30)
    assert camp.total == 2 and Image.open(camp.imagem).size[0] == 1600
    disp.processar_um()
    assert any(t.get("mimetype") == "image/jpeg" for t in textos(eco))


def test_cloud_fora_da_janela_sem_template_pula(ambiente):
    eco, disp, rel = ambiente
    rel.agora += timedelta(days=2)  # janela de 24h fechou
    class FakeCloud:
        nome = "cloud_api"
        exige_template_fora_da_janela = True
        def enviar_texto(self, *a, **k): raise AssertionError("não deveria enviar")
    eco._provedor = FakeCloud()
    disp.relogio = rel
    # o armazenamento usa o relógio real para a janela: simulamos mensagem antiga
    eco.armazenamento._db.execute("UPDATE clientes SET ultima_mensagem = '2000-01-01T00:00:00'")
    camp = disp.criar("A", "Oferta")
    while disp.processar_um():
        pass
    camp = eco.armazenamento.obter_campanha(camp.id)
    assert camp.pulados == 2 and camp.enviados == 0

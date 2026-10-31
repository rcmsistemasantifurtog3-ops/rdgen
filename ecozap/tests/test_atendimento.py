"""Cliente manda mensagem para a loja -> comprovante, PROMO, SAIR, segunda via."""
import json
from datetime import datetime

import pytest

from ecozap import config
from ecozap.servico import EcoZap
from ecozap.webhook import MensagemRecebida, ler_cloud, ler_evolution
from exemplos import CUPOM


@pytest.fixture
def eco(tmp_path):
    cfg = config.carregar(tmp_path / "x.json")
    cfg["pasta_dados"] = str(tmp_path / "dados")
    cfg["nome_loja"] = "Padaria Pão Quente"
    return EcoZap(cfg)


def msg(texto, telefone="5511987654321", nome="Maria"):
    return MensagemRecebida(telefone, nome, texto, datetime.now().replace(microsecond=0))


def enviados(eco):
    pasta = __import__("pathlib").Path(eco.cfg["pasta_dados"]) / "simulado"
    if not pasta.exists():
        return []
    itens = [json.loads(p.read_text(encoding="utf-8")) for p in pasta.glob("*.json")]
    return sorted(itens, key=lambda e: e["quando"])


def test_oi_recebe_boas_vindas_com_convite_uma_vez_so(eco):
    assert eco.receber_mensagem(msg("Oi!")) == "boas_vindas"
    textos = [e["texto"] for e in enviados(eco)]
    assert "Padaria Pão Quente" in textos[0] and "PROMO" in textos[0]
    assert eco.receber_mensagem(msg("oi de novo")) == "registrada"  # não repete em 12h
    assert eco.armazenamento.janela_aberta("5511987654321")


def test_comprovante_pendente_vai_para_quem_escreveu(eco):
    comp = eco.receber(b"cupom sem telefone\nTOTAL 9,90\n")
    assert comp.telefone is None and comp.codigo
    assert eco.receber_mensagem(msg("Oi! Quero meu comprovante digital")) == "comprovante_associado"
    comp = eco.armazenamento.obter(comp.id)
    assert comp.status == "enviado" and comp.telefone == "5511987654321" and comp.nome == "Maria"
    # cliente escreveu primeiro: sem template e com convite para promoções na legenda
    e = enviados(eco)[-1]
    assert e["template"] is None and "PROMO" in e["legenda"]


def test_varios_pendentes_espera_o_caixa_ou_codigo(eco):
    a = eco.receber(b"cupom A\n")
    b = eco.receber(b"cupom B\n")
    assert eco.receber_mensagem(msg("oi")) == "aguardando_caixa"
    assert eco.armazenamento.obter(a.id).status == "pendente"
    # com o código mostrado na tela do caixa, vai o comprovante certo
    assert eco.receber_mensagem(msg(f"comprovante {b.codigo.lower()}", "21998765432")) == "comprovante_por_codigo"
    assert eco.armazenamento.obter(b.id).telefone == "5521998765432"
    assert eco.armazenamento.obter(a.id).status == "pendente"


def test_segunda_via(eco):
    comp = eco.receber(CUPOM.encode())
    eco.enviar(comp.id)
    eco.armazenamento.atualizar(comp.id, status="enviado")
    assert eco.receber_mensagem(msg("Pode mandar a 2ª via do recibo?")) == "segunda_via"
    assert eco.armazenamento.obter(comp.id).tentativas == 2


def test_promo_e_sair(eco):
    assert eco.receber_mensagem(msg("Promo")) == "promocoes_aceita"
    cli = eco.armazenamento.cliente_por_telefone("5511987654321")
    assert cli.aceita_promocoes == 1 and cli.origem_consentimento.startswith("whatsapp")
    assert eco.receber_mensagem(msg("SAIR")) == "promocoes_cancelada"
    assert eco.armazenamento.cliente_por_telefone("5511987654321").aceita_promocoes == 0
    assert "não vai mais receber" in enviados(eco)[-1]["texto"]
    # quem já respondeu não é mais convidado
    assert not eco._deve_convidar("5511987654321")


def test_numero_antigo_sem_nove_e_o_mesmo_cliente(eco):
    eco.receber_mensagem(msg("promo", telefone="551187654321"))
    assert eco.armazenamento.cliente_por_telefone("5511987654321").aceita_promocoes == 1


def test_fora_da_janela_usa_template_dentro_nao(eco):
    class FakeCloud:
        nome = "cloud_api"
        exige_template_fora_da_janela = True
        def __init__(self): self.chamadas = []
        def enviar(self, *a, **k): self.chamadas.append(k); return "wamid"
        def enviar_texto(self, *a, **k): return "wamid"
    fake = FakeCloud()
    eco._provedor = fake
    eco.cfg["whatsapp"]["cloud_api"]["template"] = "recibo"
    c = eco.receber(CUPOM.encode())
    eco.enviar(c.id)                       # cliente nunca escreveu -> template
    assert fake.chamadas[-1]["template"] == "recibo"
    eco.receber_mensagem(msg("oi"))        # abriu a janela
    eco.enviar(c.id)
    assert fake.chamadas[-1]["template"] is None


def test_status_de_entrega(eco):
    c = eco.receber(CUPOM.encode())
    c = eco.enviar(c.id)
    assert c.entrega == "enviado" and c.msg_id
    from ecozap.webhook import StatusEntrega
    eco.receber_status(StatusEntrega(c.msg_id, "lido"))
    eco.receber_status(StatusEntrega(c.msg_id, "entregue"))  # chegou atrasado: não volta
    assert eco.armazenamento.obter(c.id).entrega == "lido"


def test_leitura_payload_cloud():
    dados = {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "551187654321", "profile": {"name": "Maria"}}],
        "messages": [{"from": "551187654321", "timestamp": "1761900000", "type": "text",
                      "text": {"body": "PROMO"}}],
        "statuses": [{"id": "wamid.X", "status": "read"}]}}]}]}
    mensagens, status = ler_cloud(dados)
    assert mensagens[0].texto == "PROMO" and mensagens[0].nome == "Maria"
    assert status[0].entrega == "lido"


def test_leitura_payload_evolution():
    dados = {"event": "messages.upsert", "data": {
        "key": {"remoteJid": "5511987654321@s.whatsapp.net", "fromMe": False, "id": "ABC"},
        "pushName": "João", "message": {"extendedTextMessage": {"text": "oi"}},
        "messageTimestamp": 1761900000}}
    mensagens, _ = ler_evolution(dados)
    assert mensagens[0].telefone == "5511987654321" and mensagens[0].texto == "oi"
    grupo = {"event": "messages.upsert", "data": {"key": {"remoteJid": "123@g.us"}, "message": {}}}
    assert ler_evolution(grupo) == ([], [])
    proprio = {"event": "messages.upsert", "data": {"key": {"remoteJid": "5511@s.whatsapp.net", "fromMe": True}}}
    assert ler_evolution(proprio) == ([], [])

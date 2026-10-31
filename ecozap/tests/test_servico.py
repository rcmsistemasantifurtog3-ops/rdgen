import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ecozap import config
from ecozap.monitor import Monitor
from ecozap.servico import EcoZap
from ecozap.whatsapp import CloudAPI, ErroEnvio, Evolution
from exemplos import CUPOM, ESCPOS


@pytest.fixture
def eco(tmp_path):
    cfg = config.carregar(tmp_path / "nao-existe.json")
    cfg["pasta_dados"] = str(tmp_path / "dados")
    cfg["pasta_entrada"] = str(tmp_path / "entrada")
    cfg["nome_loja"] = "Mercadinho São João"
    return EcoZap(cfg)


def test_recebe_extrai_telefone_e_envia(eco, tmp_path):
    comp = eco.receber(CUPOM.encode())
    assert comp.telefone == "5511987654321" and comp.status == "pendente"
    enviado = eco.enviar(comp.id, nome="Maria Souza", formato="jpeg")
    assert enviado.status == "enviado" and enviado.tentativas == 1
    saida = list((tmp_path / "dados" / "simulado").glob("*.jpg"))
    assert len(saida) == 1 and "mercadinho-sao-joao" in saida[0].name
    meta = json.loads(saida[0].with_suffix(".jpg.json").read_text(encoding="utf-8"))
    assert meta["legenda"].startswith("Olá, Maria!")
    assert eco.armazenamento.estatisticas()["enviados"] == 1


def test_reenvio_para_outro_numero(eco):
    comp = eco.receber(ESCPOS)
    assert comp.telefone == "5521998765432"
    eco.enviar(comp.id)
    comp = eco.enviar(comp.id, "(31) 91234-5678", formato="pdf")
    assert comp.telefone == "5531912345678" and comp.tentativas == 2


def test_telefone_invalido(eco):
    comp = eco.receber(b"cupom sem telefone\nTOTAL 10,00\n")
    assert comp.telefone is None
    with pytest.raises(ValueError):
        eco.enviar(comp.id)


def test_erro_no_envio_fica_registrado(eco):
    class Falha:
        nome = "falha"
        def enviar(self, *a, **k):
            raise ErroEnvio("sem internet")
    eco._provedor = Falha()
    comp = eco.receber(CUPOM.encode())
    with pytest.raises(ErroEnvio):
        eco.enviar(comp.id)
    comp = eco.armazenamento.obter(comp.id)
    assert comp.status == "erro" and comp.ultimo_erro == "sem internet"


def test_trocar_papel(eco):
    comp = eco.receber(CUPOM.encode(), "80mm")
    antes = comp.pdf
    eco.trocar_papel(comp.id, "a4")
    assert eco.armazenamento.obter(comp.id).pdf != antes


def test_limpeza_15_dias(eco):
    arm = eco.armazenamento
    from ecozap.conversor import texto_para_pdf
    pdf = texto_para_pdf("x")
    velho = arm.salvar(pdf, "", "texto", quando=datetime.now() - timedelta(days=16))
    novo = arm.salvar(pdf, "", "texto", quando=datetime.now() - timedelta(days=3))
    assert arm.limpar_antigos() == 1
    assert arm.obter(velho.id) is None and not Path(velho.arquivo_pdf).exists()
    assert not Path(velho.arquivo_pdf).parent.exists()
    assert arm.obter(novo.id) is not None


def test_busca_no_historico(eco):
    eco.receber(CUPOM.encode())
    eco.receber(b"outro cupom\n")
    assert len(eco.armazenamento.listar(busca="98765")) == 1
    assert len(eco.armazenamento.listar(busca="Arroz")) == 1


def test_monitor_espera_arquivo_estabilizar(tmp_path):
    recebidos = []
    mon = Monitor(tmp_path / "entrada", lambda p: recebidos.append(p.read_bytes()))
    (tmp_path / "entrada" / "job.pdf").write_bytes(b"abc")
    assert mon.verificar_uma_vez() == []        # 1ª leitura: só anota o tamanho
    assert len(mon.verificar_uma_vez()) == 1    # 2ª: tamanho igual -> entrega
    assert recebidos == [b"abc"]
    assert not any((tmp_path / "entrada" / "processando").iterdir())


def test_monitor_erro_vai_para_pasta(tmp_path):
    def quebra(_):
        raise RuntimeError("x")
    mon = Monitor(tmp_path / "e", quebra)
    (tmp_path / "e" / "job.txt").write_bytes(b"abc")
    mon.verificar_uma_vez(); mon.verificar_uma_vez()
    assert len(list((tmp_path / "e" / "com_erro").iterdir())) == 1


class SessaoFalsa:
    def __init__(self, respostas):
        self.headers, self.chamadas, self.respostas = {}, [], list(respostas)

    def post(self, url, **kw):
        self.chamadas.append((url, kw))
        status, corpo = self.respostas.pop(0)
        class R:
            ok = status < 400
            status_code = status
            text = json.dumps(corpo)
            def json(self_inner):
                return corpo
        return R()


def test_cloud_api_com_template():
    s = SessaoFalsa([(200, {"id": "MID"}), (200, {"messages": [{"id": "wamid.1"}]})])
    api = CloudAPI("tok", "123", template="recibo_ecozap", sessao=s)
    assert api.enviar("5511987654321", b"%PDF", "c.pdf", "application/pdf", "oi",
                      parametros_template=["Loja"]) == "wamid.1"
    assert s.headers["Authorization"] == "Bearer tok"
    corpo = s.chamadas[1][1]["json"]
    assert corpo["type"] == "template"
    header = corpo["template"]["components"][0]["parameters"][0]
    assert header == {"type": "document", "document": {"id": "MID", "filename": "c.pdf"}}


def test_cloud_api_erro():
    s = SessaoFalsa([(401, {"error": {"message": "token expirado"}})])
    api = CloudAPI("tok", "123", sessao=s)
    with pytest.raises(ErroEnvio, match="token expirado"):
        api.enviar("5511987654321", b"x", "c.jpg", "image/jpeg", "oi")


def test_evolution():
    s = SessaoFalsa([(201, {"key": {"id": "ABC"}})])
    api = Evolution("http://srv:8080/", "loja", "k", sessao=s)
    assert api.enviar("5511987654321", b"x", "c.jpg", "image/jpeg", "oi") == "ABC"
    url, kw = s.chamadas[0]
    assert url == "http://srv:8080/message/sendMedia/loja"
    assert kw["json"]["mediatype"] == "image"

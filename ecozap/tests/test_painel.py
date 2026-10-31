import base64
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from ecozap import config
from ecozap.painel import Painel
from ecozap.servico import EcoZap
from exemplos import CUPOM


@pytest.fixture
def painel(tmp_path):
    cfg = config.carregar(tmp_path / "x.json")
    cfg["pasta_dados"] = str(tmp_path / "dados")
    cfg["painel"].update(ativo=True, porta=0, senha="segredo")
    eco = EcoZap(cfg)
    p = Painel(eco)
    p.iniciar()
    yield p
    p.parar()


def _req(p, caminho, dados=None, senha="segredo", json_body=False):
    corpo, cab = None, {}
    if dados is not None:
        if json_body:
            corpo, cab["Content-Type"] = json.dumps(dados).encode(), "application/json"
        else:
            corpo = urllib.parse.urlencode(dados).encode()
    if senha:
        cab["Authorization"] = "Basic " + base64.b64encode(f"admin:{senha}".encode()).decode()
    req = urllib.request.Request(p.endereco + caminho, data=corpo, headers=cab)
    return urllib.request.urlopen(req, timeout=5)


def test_exige_senha(painel):
    with pytest.raises(urllib.error.HTTPError) as e:
        _req(painel, "/", senha="errada")
    assert e.value.code == 401


def test_lista_envia_e_relatorio(painel):
    c = painel.eco.receber(CUPOM.encode())
    html = _req(painel, "/").read().decode()
    assert c.id in html and "Pendente" in html
    resp = _req(painel, f"/enviar/{c.id}", {"telefone": "(11) 98765-4321", "formato": "png"})
    assert "Enviado" in urllib.parse.unquote(resp.url)
    assert painel.eco.armazenamento.obter(c.id).status == "enviado"
    est = json.loads(_req(painel, "/api/estatisticas").read())
    assert est["enviados"] == 1 and est["mes_enviados"] == 1
    assert _req(painel, f"/pdf/{c.id}").read().startswith(b"%PDF")
    assert _req(painel, "/relatorio").read().startswith(b"%PDF")
    assert _req(painel, "/relatorio?tipo=jpg").read()[:3] == b"\xff\xd8\xff"


def test_api_enviar_com_erro(painel):
    c = painel.eco.receber(b"sem telefone\n")
    with pytest.raises(urllib.error.HTTPError) as e:
        _req(painel, "/api/enviar", {"id": c.id}, json_body=True)
    assert e.value.code == 400 and "Telefone" in json.loads(e.value.read())["erro"]


def test_bloqueia_outra_origem(painel):
    c = painel.eco.receber(CUPOM.encode())
    req = urllib.request.Request(painel.endereco + f"/descartar/{c.id}", data=b"x=1", headers={
        "Origin": "http://site-malicioso.com",
        "Authorization": "Basic " + base64.b64encode(b"admin:segredo").decode()})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403


def test_visao_geral_soma_caixas(tmp_path, painel):
    cfg2 = config.carregar(tmp_path / "y.json")
    cfg2["pasta_dados"] = str(tmp_path / "dados2")
    cfg2["painel"].update(ativo=True, porta=0)
    cfg2["painel"]["caixas"] = [{"nome": "Caixa 2", "url": painel.endereco, "senha": "segredo"},
                                {"nome": "Caixa 3", "url": "http://127.0.0.1:1"}]
    p2 = Painel(EcoZap(cfg2))
    p2.iniciar()
    try:
        html = urllib.request.urlopen(p2.endereco + "/geral", timeout=10).read().decode()
        assert "Caixa 2" in html and "Caixa 3" in html and "sem resposta" in html
    finally:
        p2.parar()

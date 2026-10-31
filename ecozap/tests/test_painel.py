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


def _post_bruto(p, caminho, corpo: bytes, cabecalhos: dict):
    req = urllib.request.Request(p.endereco + caminho, data=corpo, headers=cabecalhos)
    return urllib.request.urlopen(req, timeout=5)


def _esperar(condicao, segundos=5):
    import time
    fim = time.time() + segundos
    while time.time() < fim:
        if condicao():
            return True
        time.sleep(0.05)
    return False


def test_webhook_cloud_verificacao_e_assinatura(painel):
    import hashlib, hmac
    cloud = painel.eco.cfg["whatsapp"]["cloud_api"]
    cloud.update(verify_token="verifica123", app_secret="segredo-app")
    # verificação da Meta: não usa a senha do painel
    ok = urllib.request.urlopen(painel.endereco + "/webhook/whatsapp?hub.mode=subscribe"
                                "&hub.verify_token=verifica123&hub.challenge=4242", timeout=5)
    assert ok.read() == b"4242"
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(painel.endereco + "/webhook/whatsapp?hub.mode=subscribe"
                               "&hub.verify_token=errado&hub.challenge=1", timeout=5)
    assert e.value.code == 403

    corpo = json.dumps({"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "5511987654321", "profile": {"name": "Maria"}}],
        "messages": [{"from": "5511987654321", "timestamp": "1761900000",
                      "type": "text", "text": {"body": "PROMO"}}]}}]}]}).encode()
    with pytest.raises(urllib.error.HTTPError) as e:  # sem assinatura válida: recusado
        _post_bruto(painel, "/webhook/whatsapp", corpo, {"X-Hub-Signature-256": "sha256=000",
                                                         "Content-Type": "application/json"})
    assert e.value.code == 403
    assinatura = "sha256=" + hmac.new(b"segredo-app", corpo, hashlib.sha256).hexdigest()
    _post_bruto(painel, "/webhook/whatsapp", corpo, {"X-Hub-Signature-256": assinatura,
                                                     "Content-Type": "application/json"})
    arm = painel.eco.armazenamento
    assert _esperar(lambda: (c := arm.cliente_por_telefone("5511987654321")) and c.aceita_promocoes == 1)


def test_webhook_evolution_exige_token(painel):
    painel.eco.cfg["whatsapp"]["evolution"]["webhook_token"] = "tok-evo"
    corpo = json.dumps({"event": "messages.upsert", "data": {
        "key": {"remoteJid": "5521998765432@s.whatsapp.net", "fromMe": False},
        "pushName": "João", "message": {"conversation": "oi"}, "messageTimestamp": int(__import__("time").time())}}).encode()
    with pytest.raises(urllib.error.HTTPError) as e:
        _post_bruto(painel, "/webhook/evolution?token=errado", corpo, {"Content-Type": "application/json"})
    assert e.value.code == 403
    _post_bruto(painel, "/webhook/evolution?token=tok-evo", corpo, {"Content-Type": "application/json"})
    assert _esperar(lambda: painel.eco.armazenamento.mensagens_recentes(60))
    html = _req(painel, "/mensagens").read().decode()
    assert "João" in html


def test_webhook_sem_segredo_configurado_recusa(painel):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post_bruto(painel, "/webhook/evolution?token=", b"{}", {"Content-Type": "application/json"})
    assert e.value.code == 403


def test_criar_promocao_com_imagem_pelo_painel(painel):
    import io
    from PIL import Image
    painel.eco.armazenamento.definir_promocoes("5511987654321", True, "balcão")
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "blue").save(buf, "PNG")
    limite = "----ecozapteste"
    partes = []
    for nome, valor in (("titulo", "Black Friday"), ("texto", "Tudo 10% off{nome}!")):
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n'.encode())
    partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="imagem"; filename="a.png"\r\n'
                  f'Content-Type: image/png\r\n\r\n'.encode() + buf.getvalue() + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    req = urllib.request.Request(painel.endereco + "/promocoes/criar", data=b"".join(partes), headers={
        "Content-Type": f"multipart/form-data; boundary={limite}",
        "Authorization": "Basic " + base64.b64encode(b"admin:segredo").decode()})
    resp = urllib.request.urlopen(req, timeout=5)
    assert "1 cliente" in urllib.parse.unquote(resp.url)
    camp = painel.eco.armazenamento.listar_campanhas()[0]
    assert camp.titulo == "Black Friday" and camp.imagem
    assert "Black Friday" in _req(painel, "/promocoes").read().decode()


def test_cartaz(painel):
    painel.eco.cfg["numero_loja"] = "(11) 3333-4444"
    pdf = _req(painel, "/cartaz").read()
    assert pdf.startswith(b"%PDF")
    pytest.importorskip("zxingcpp")
    from ecozap import nfce
    qrs = nfce.qrs_do_pdf(pdf)
    assert qrs and qrs[0].startswith("https://wa.me/551133334444?text=")

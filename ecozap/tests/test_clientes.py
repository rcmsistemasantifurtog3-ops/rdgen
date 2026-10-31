import pytest

from ecozap import clientes, config
from ecozap.servico import EcoZap
from exemplos import escpos_nfce


def test_cpf():
    assert clientes.cpf_valido("529.982.247-25")
    assert not clientes.cpf_valido("111.111.111-11")
    assert not clientes.cpf_valido("529.982.247-26")
    assert clientes.extrair_cpf("CONSUMIDOR - CPF: 529.982.247-25") == "52998224725"
    assert clientes.extrair_cpf("CNPJ 12.345.678/0001-90") is None


def test_nome():
    assert clientes.extrair_nome("Cliente: MARIA SOUZA  Cel: (11) 9") == "Maria Souza"
    assert clientes.extrair_nome("Consumidor: João da Silva CPF 1") == "João da Silva"
    assert clientes.extrair_nome("CONSUMIDOR NÃO IDENTIFICADO") is None


def test_resumo_cpf_e_irreversivel_e_estavel(tmp_path):
    r = clientes.ResumoCPF(tmp_path)
    a = r("529.982.247-25")
    assert a == r("52998224725") and "52998224725" not in a
    assert clientes.ResumoCPF(tmp_path)("52998224725") == a  # mesma chave em disco


@pytest.fixture
def eco(tmp_path):
    cfg = config.carregar(tmp_path / "x.json")
    cfg["pasta_dados"] = str(tmp_path / "dados")
    return EcoZap(cfg)


def test_agenda_lembra_telefone_pelo_cpf(eco):
    # 1ª compra: sem telefone no cupom, o caixa digita
    c1 = eco.receber(escpos_nfce())
    assert c1.telefone is None
    eco.enviar(c1.id, "(11) 98765-4321", nome="Maria Souza")
    # 2ª compra com o mesmo CPF: telefone e nome vêm da agenda
    c2 = eco.receber(escpos_nfce())
    assert c2.telefone == "5511987654321" and c2.nome == "Maria Souza"
    cli = eco.armazenamento.cliente_por_telefone("5511987654321")
    assert cli.envios == 1
    # o CPF não fica em texto puro em nenhuma tabela de clientes
    assert "52998224725" not in (cli.cpf_hash or "")


def test_autocompletar_e_esquecer(eco):
    c = eco.receber(b"cupom\n")
    eco.enviar(c.id, "21 99999-0000", nome="Carlos Lima")
    assert [x.nome for x in eco.armazenamento.buscar_clientes("carl")] == ["Carlos Lima"]
    assert eco.armazenamento.buscar_clientes("99990000")[0].telefone == "5521999990000"
    assert eco.armazenamento.buscar_clientes("c") == []  # muito curto
    eco.armazenamento.esquecer_cliente("5521999990000")
    assert eco.armazenamento.buscar_clientes("carl") == []


def test_mensagem_leva_link_da_nfce(eco, tmp_path):
    import json
    c = eco.receber(escpos_nfce())
    eco.enviar(c.id, "11987654321")
    meta = next((tmp_path / "dados" / "simulado").glob("*.json"))
    legenda = json.loads(meta.read_text(encoding="utf-8"))["legenda"]
    assert "Consulte sua nota fiscal: https://www.nfce.fazenda.sp.gov.br/qrcode?p=" in legenda

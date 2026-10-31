from ecozap import telefone as tel


def test_normalizar():
    assert tel.normalizar("(11) 98765-4321") == "5511987654321"
    assert tel.normalizar("+55 11 98765-4321") == "5511987654321"
    assert tel.normalizar("011 98765-4321") == "5511987654321"
    assert tel.normalizar("1133334444") == "551133334444"     # fixo
    assert tel.normalizar("123") is None
    assert tel.normalizar("11 88765-4321") is None             # celular sem 9
    assert tel.normalizar("") is None


def test_formatar():
    assert tel.formatar("5511987654321") == "+55 (11) 98765-4321"


def test_extrair_com_palavra_chave():
    assert tel.extrair("Cliente: Maria  Cel: (11) 98765-4321") == "5511987654321"
    assert tel.extrair("WhatsApp: 21999998888") == "5521999998888"


def test_extrair_nao_confunde_cpf():
    assert tel.extrair("CPF: 123.456.789-09\nCNPJ 12.345.678/0001-90") is None

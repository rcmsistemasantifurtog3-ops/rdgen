from datetime import datetime, timedelta

import pytest
from PIL import Image
import io

from ecozap import config, relatorio
from ecozap.servico import EcoZap
from exemplos import CUPOM


@pytest.fixture
def eco(tmp_path):
    cfg = config.carregar(tmp_path / "x.json")
    cfg["pasta_dados"] = str(tmp_path / "dados")
    return EcoZap(cfg)


def test_estatisticas_sobrevivem_a_limpeza(eco):
    c = eco.receber(CUPOM.encode())
    eco.enviar(c.id, "11987654321")
    eco.enviar(c.id)  # reenvio não conta papel de novo
    antes = eco.armazenamento.estatisticas()
    assert antes["enviados"] == 1 and antes["papel_economizado_m"] > 0
    eco.armazenamento.limpar_antigos(agora=datetime.now() + timedelta(days=30))
    depois = eco.armazenamento.estatisticas()
    assert depois["guardados"] == 0
    assert depois["enviados"] == 1 and depois["papel_economizado_m"] == antes["papel_economizado_m"]


def test_relatorio_mensal(eco, tmp_path):
    c = eco.receber(CUPOM.encode())
    eco.enviar(c.id, "11987654321")
    hoje = datetime.now()
    r = relatorio.resumir(eco.armazenamento, hoje.year, hoje.month, "Loja X")
    assert r.enviados == 1 and r.metros > 0 and len(r.dias) >= 28
    pdf, img = relatorio.gerar(eco.armazenamento, "Loja X", None, tmp_path / "rel")
    assert pdf.read_bytes().startswith(b"%PDF")
    assert Image.open(io.BytesIO(img.read_bytes())).size == (1080, 1080)


def test_mes_invalido():
    assert relatorio.mes_de_texto("2026-02") == (2026, 2)
    with pytest.raises(ValueError):
        relatorio.mes_de_texto("fevereiro")

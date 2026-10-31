import json

import pytest

from ecozap import config


def test_salvar_e_recarregar(tmp_path):
    arq = tmp_path / "config.json"
    cfg = config.carregar(arq)
    cfg["nome_loja"] = "Açaí da Praça"
    cfg["whatsapp"]["provedor"] = "evolution"
    config.salvar(cfg)
    salvo = json.loads(arq.read_text(encoding="utf-8"))
    assert "_arquivo" not in salvo and salvo["nome_loja"] == "Açaí da Praça"
    assert config.carregar(arq)["whatsapp"]["provedor"] == "evolution"


def test_painel_na_rede_exige_senha(tmp_path):
    cfg = config.carregar(tmp_path / "c.json")
    cfg["painel"].update(ativo=True, host="0.0.0.0", senha="")
    with pytest.raises(config.ErroConfig):
        config.validar(cfg)


def test_json_quebrado(tmp_path):
    arq = tmp_path / "c.json"
    arq.write_text("{ quebrado", encoding="utf-8")
    with pytest.raises(config.ErroConfig):
        config.carregar(arq)


def test_exemplo_e_valido():
    from pathlib import Path
    config.carregar(Path(__file__).resolve().parents[1] / "config.exemplo.json")


def test_instancia_unica(tmp_path):
    import subprocess, sys
    from ecozap.instancia import TravaInstancia
    t = TravaInstancia(tmp_path)
    assert t.adquirir()
    # outro processo não consegue pegar a mesma trava
    codigo = ("import sys; sys.path.insert(0, r'%s'); from ecozap.instancia import TravaInstancia;"
              "sys.exit(0 if TravaInstancia(r'%s').adquirir() else 7)") % (
        __import__("pathlib").Path(__file__).resolve().parents[1], tmp_path)
    assert subprocess.run([sys.executable, "-c", codigo]).returncode == 7
    t.liberar()
    assert subprocess.run([sys.executable, "-c", codigo]).returncode == 0

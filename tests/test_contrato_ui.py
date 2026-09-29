# -*- coding: utf-8 -*-
"""Os diálogos só podem usar widgets que existem no .ui.

Este teste existe por causa de um erro real: uma função foi listada como
dependência sem nunca ter existido, e os testes usavam dublês construídos a
partir da mesma lista — a lista concordava consigo mesma. Aqui a verificação
é contra o .ui de verdade.
"""

import ast
import os
import xml.etree.ElementTree as ET

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(RAIZ, "risco_garimpo", "ui")

PARES = [("estatico_dialog.py", "estatico_dialog_base.ui"),
         ("main_dialog.py", "main_dialog_base.ui")]

# atributos do próprio QDialog ou definidos no __init__ do diálogo
HERDADOS = {
    "setupUi", "show", "raise_", "activateWindow", "close", "windowTitle",
    "iface", "tarefa", "parent", "setWindowTitle", "setEnabled", "isVisible",
}


def _widgets_do_ui(caminho):
    raiz = ET.parse(caminho).getroot()
    nomes = {w.get("name") for w in raiz.iter("widget") if w.get("name")}
    nomes |= {lay.get("name") for lay in raiz.iter("layout") if lay.get("name")}
    return nomes


def _atributos_proprios(arvore):
    """Nomes atribuídos a self em qualquer ponto da classe."""
    proprios = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Attribute) and isinstance(no.ctx, ast.Store) \
                and isinstance(no.value, ast.Name) and no.value.id == "self":
            proprios.add(no.attr)
    return proprios


def _metodos(arvore):
    return {no.name for no in ast.walk(arvore)
            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _acessos(arvore):
    return {no.attr for no in ast.walk(arvore)
            if isinstance(no, ast.Attribute) and isinstance(no.ctx, ast.Load)
            and isinstance(no.value, ast.Name) and no.value.id == "self"}


@pytest.mark.parametrize("fonte,arquivo_ui", PARES)
def test_dialogo_so_usa_widgets_existentes(fonte, arquivo_ui):
    caminho_py = os.path.join(UI, fonte)
    caminho_ui = os.path.join(UI, arquivo_ui)
    if not (os.path.exists(caminho_py) and os.path.exists(caminho_ui)):
        pytest.skip("par ausente: %s / %s" % (fonte, arquivo_ui))

    arvore = ast.parse(open(caminho_py, encoding="utf-8").read())
    conhecidos = (_widgets_do_ui(caminho_ui) | _atributos_proprios(arvore)
                  | _metodos(arvore) | HERDADOS)
    desconhecidos = sorted(a for a in _acessos(arvore)
                           if a not in conhecidos and not a.startswith("__"))
    assert not desconhecidos, (
        "%s usa self.<nome> que não existe em %s nem na própria classe: %s"
        % (fonte, arquivo_ui, ", ".join(desconhecidos)))


@pytest.mark.parametrize("fonte,arquivo_ui", PARES)
def test_ui_declara_a_classe_que_o_dialogo_espera(fonte, arquivo_ui):
    caminho_ui = os.path.join(UI, arquivo_ui)
    if not os.path.exists(caminho_ui):
        pytest.skip("ui ausente")
    raiz = ET.parse(caminho_ui).getroot()
    classe = raiz.find("class").text
    dialogo = raiz.find("widget")
    assert dialogo.get("class") == "QDialog"
    assert dialogo.get("name") == classe


def test_o_ui_estatico_tem_os_campos_que_a_configuracao_exige():
    """Cada argumento da configuração precisa de um widget que o alimente."""
    nomes = _widgets_do_ui(os.path.join(UI, "estatico_dialog_base.ui"))
    exigidos = ["comboIBx", "comboEst", "comboLito", "comboMu",
                "comboMuAntigo", "comboMuAnterior", "comboMascara",
                "comboAgua", "comboCRS", "comboSubbacias", "linhaCampoId",
                "checkRemanescente",
                "spinResolucao", "spinPesoIBx", "spinPesoEst", "spinPesoLito",
                "spinGamma", "spinPiso", "checkEstimarNivel", "spinJanela",
                "spinQuantil", "spinFaixas", "spinMinimoFaixa", "spinTeto",
                "linhaPasta", "btnPasta", "linhaPrefixo", "checkEscore",
                "checkTabela", "checkCarregar", "btnExecutar", "btnCancelar",
                "btnFechar", "barraProgresso", "lblStatus", "textoLog",
                "abas", "abaRegistro", "lblGrade"]
    faltando = [e for e in exigidos if e not in nomes]
    assert not faltando, "faltam no .ui: %s" % ", ".join(faltando)


def test_o_gerador_reproduz_o_ui_versionado():
    """Rodar o gerador não pode divergir do arquivo commitado."""
    import sys
    sys.path.insert(0, os.path.join(RAIZ, "tools"))
    import gerar_ui_estatico

    atual = open(os.path.join(UI, "estatico_dialog_base.ui"),
                 encoding="utf-8").read()
    assert gerar_ui_estatico.construir() == atual

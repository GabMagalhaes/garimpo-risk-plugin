# -*- coding: utf-8 -*-
"""metadata.txt: o QGIS só carrega o plugin nas versões que ele declara."""

import configparser
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METADATA = os.path.join(RAIZ, "risco_garimpo", "metadata.txt")


def _metadados():
    # o QGIS lê o metadata.txt com o configparser, sem interpolação
    leitor = configparser.ConfigParser(interpolation=None)
    leitor.read(METADATA, encoding="utf-8")
    return leitor["general"]


def test_declara_o_qgis_3_e_o_qgis_4():
    metadados = _metadados()
    assert metadados["qgisMinimumVersion"] == "3.22"
    # sem o máximo, o QGIS assume "<mínimo>.99" — 3.99 — e marca o plugin
    # como incompatível no QGIS 4
    maximo = metadados.get("qgisMaximumVersion", "")
    assert maximo.split(".")[0] == "4", maximo
    assert metadados.get("supportsQt6", "").lower() == "true"


def test_a_versao_abre_o_changelog():
    metadados = _metadados()
    assert metadados["changelog"].strip().startswith(
        metadados["version"] + " - ")

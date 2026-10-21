# -*- coding: utf-8 -*-
"""Teste ponta a ponta dentro do QGIS real (pulado onde não há QGIS).

Roda o mesmo roteiro que o usuário roda no Console Python do QGIS
(``testar_ponta_a_ponta`` em ``tools/gerar_dados_teste.py``): gera os dados
sintéticos, executa os dois modelos pelo núcleo do plugin, com GDAL e a API
vetorial do QGIS de verdade, e confere os registros de execução. Foi este
roteiro que revelou a gravação do GeoPackage do modelo estático com os
campos do dinâmico — erro que os testes com dublês não viam.

Para rodar localmente: um Python com ``qgis.core`` (no Ubuntu,
``apt install python3-qgis``) e ``python3 -m pytest tests``.
"""

import json
import os
import sys

import pytest

if os.environ.get("EXIGIR_QGIS"):          # no CI com QGIS, não pular
    import qgis.core as qgis_core
else:
    qgis_core = pytest.importorskip("qgis.core")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qgis():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = qgis_core.QgsApplication([], False)
    app.initQgis()
    yield app


def test_roteiro_do_console_roda_os_dois_modelos(qgis, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(RAIZ)
    # o roteiro descarta e reimporta os módulos do plugin; os demais testes
    # precisam reencontrar, depois, os mesmos objetos que importaram
    for nome in [m for m in sys.modules if m.split(".")[0] == "risco_garimpo"]:
        monkeypatch.setitem(sys.modules, nome, sys.modules[nome])
    caminho = os.path.join(RAIZ, "tools", "gerar_dados_teste.py")
    # o console do QGIS executa com __name__ == "__console__"
    espaco = {"__name__": "__console__"}
    exec(compile(open(caminho, encoding="utf-8").read(), caminho, "exec"),
         espaco)
    resultado = espaco["testar_ponta_a_ponta"](str(tmp_path))

    assert resultado["falhas"] == 0
    with open(resultado["dinamico"], encoding="utf-8") as arquivo:
        dinamico = json.load(arquivo)
    assert dinamico["ambiente"]["qgis"]
    assert os.path.isfile(dinamico["saidas"]["subbacias"])
    with open(resultado["estatico"], encoding="utf-8") as arquivo:
        estatico = json.load(arquivo)
    assert os.path.isfile(estatico["saidas"]["subbacias"])

    # o GeoPackage do estático tem os campos pa_*, e o do dinâmico, rg_*
    for chave, prefixo, nome in ((estatico, "pa_", "potencial_subbacias"),
                                 (dinamico, "rg_", "risco_subbacias")):
        camada = qgis_core.QgsVectorLayer(
            "%s|layername=%s" % (chave["saidas"]["subbacias"], nome), nome,
            "ogr")
        assert camada.isValid()
        campos = [c.name() for c in camada.fields()]
        assert any(c.startswith(prefixo) for c in campos), campos
        assert camada.featureCount() == 4

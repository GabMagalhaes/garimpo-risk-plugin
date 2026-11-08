# -*- coding: utf-8 -*-
"""Abre o diálogo da validação num QGIS com interface e o usa de ponta a ponta.

Roda em processo próprio (tests/test_qgis_ponta_a_ponta.py o chama): o
diálogo precisa de uma QgsApplication com interface, e os outros testes do
QGIS usam uma sem. Uso: python roteiro_dialogo_validacao.py <pasta>
"""

import datetime as dt
import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication, QgsProject, QgsVectorLayer  # noqa: E402

app = QgsApplication([], True)
app.initQgis()


class _Barra(object):
    def __init__(self):
        self.mensagens = []

    def pushMessage(self, *args, **kwargs):  # noqa: N802 - API do QGIS
        self.mensagens.append(args)


class _Iface(object):
    def __init__(self):
        self.barra = _Barra()

    def mainWindow(self):  # noqa: N802 - API do QGIS
        return None

    def messageBar(self):  # noqa: N802 - API do QGIS
        return self.barra


def principal(pasta):
    espaco = {"__name__": "__console__"}
    caminho = os.path.join(RAIZ, "tools", "gerar_dados_teste.py")
    exec(compile(open(caminho, encoding="utf-8").read(), caminho, "exec"),
         espaco)
    dados = espaco["gerar"](pasta)
    subbacias_gpkg = espaco["_gravar_subbacias"](pasta)

    from risco_garimpo.core import execucao
    cfg = execucao.Configuracao(
        caminho_k=dados["K"], caminho_mu0=dados["mu0"],
        caminho_mascara=dados["mascara"],
        camada_alertas=QgsVectorLayer(dados["eventos"] + "|layername=alertas",
                                      "alertas", "ogr"),
        campo_data_alertas="data",
        camada_fiscalizacao=QgsVectorLayer(
            dados["eventos"] + "|layername=autos", "autos", "ogr"),
        campo_data_fiscalizacao="data",
        data_inicio=dt.date(2025, 1, 1), data_fim=dt.date(2026, 12, 31),
        pasta_saida=os.path.join(pasta, "saidas"), prefixo="teste")
    saida = execucao.executar(cfg, execucao.preparar(cfg))
    subbacias = QgsVectorLayer(subbacias_gpkg + "|layername=subbacias",
                               "subbacias", "ogr")
    QgsProject.instance().addMapLayer(subbacias)

    import risco_garimpo.ui.validacao_dialog as modulo
    avisos = []
    modulo.QMessageBox.warning = lambda *a: avisos.append(a[-1])
    modulo.QMessageBox.critical = lambda *a: avisos.append(a[-1])
    iface = _Iface()
    dialogo = modulo.ValidacaoDialog(iface)
    dialogo.show()

    # sem o garimpo final, a configuração é recusada com uma explicação
    dialogo.linhaRegistro.setText(saida["registro"])
    dialogo._ler_registro()
    assert dialogo.linhaPrevisto.text() == saida["saidas"]["expansao"]
    assert dialogo.linhaMuInicial.text() == dados["mu0"]
    assert dialogo.linhaK.text() == dados["K"]
    assert "RECONSTRUÇÃO" in dialogo.lblExecucao.text()
    dialogo._executar()
    assert avisos and "garimpo na data final" in avisos[-1], avisos

    # com ele, valida em segundo plano e grava tudo
    dialogo.linhaMuFinal.setText(saida["saidas"]["mu_final"])
    dialogo.comboSubbacias.setCurrentIndex(
        dialogo.comboSubbacias.findData(subbacias.id()))
    dialogo.linhaCampoId.setText("nome")
    dialogo._executar()
    assert dialogo.tarefa is not None
    limite = time.time() + 300
    while dialogo.tarefa is not None and time.time() < limite:
        app.processEvents()
        time.sleep(0.05)
    log = dialogo.textoLog.toPlainText()
    assert dialogo.lblStatus.text() == "Pronto.", (dialogo.lblStatus.text(),
                                                   log, avisos)
    for nome in ("metricas.csv", "curva_captura.csv", "curva_captura.png",
                 "subbacias.gpkg", "registro.json"):
        caminho = os.path.join(pasta, "saidas", "teste_validacao_" + nome)
        assert os.path.isfile(caminho), (caminho, log)
    assert "área prevista / observada 1.00" in log, log
    nomes = [c.name() for c in QgsProject.instance().mapLayers().values()]
    assert "Validação por sub-bacia" in nomes, nomes
    assert iface.barra.mensagens, "sem mensagem de conclusão"
    print("DIALOGO_OK")


if __name__ == "__main__":
    try:
        principal(sys.argv[1])
    finally:
        app.exitQgis()

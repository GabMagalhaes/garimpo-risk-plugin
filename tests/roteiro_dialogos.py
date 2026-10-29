# -*- coding: utf-8 -*-
"""Abre o plugin e os diálogos dos dois modelos num QGIS com interface.

Roda em processo próprio (tests/test_qgis_ponta_a_ponta.py o chama), como o
roteiro da validação. Cobre o que só aparece com Qt de verdade — e que muda
entre o QGIS 3 (Qt5) e o QGIS 4 (Qt6): o registro das ações no menu, a
leitura dos .ui, as preferências do modelo dinâmico, a pergunta ao fechar
com uma simulação em andamento e o estilo das saídas.

Uso: python roteiro_dialogos.py <pasta>
"""

import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (QgsApplication, QgsProject, QgsRasterLayer,  # noqa: E402
                       QgsSingleBandPseudoColorRenderer, QgsVectorLayer)
from qgis.PyQt.QtCore import QDate  # noqa: E402

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
        self.menu = []
        self.barra_de_ferramentas = []

    def mainWindow(self):  # noqa: N802 - API do QGIS
        return None

    def messageBar(self):  # noqa: N802 - API do QGIS
        return self.barra

    def addToolBarIcon(self, acao):  # noqa: N802 - API do QGIS
        self.barra_de_ferramentas.append(acao)

    def removeToolBarIcon(self, acao):  # noqa: N802 - API do QGIS
        self.barra_de_ferramentas.remove(acao)

    def addPluginToRasterMenu(self, menu, acao):  # noqa: N802 - API do QGIS
        self.menu.append((menu, acao))

    def removePluginRasterMenu(self, menu, acao):  # noqa: N802 - API do QGIS
        self.menu.remove((menu, acao))


def _esperar(dialogo, segundos=300):
    limite = time.time() + segundos
    while dialogo.tarefa is not None and time.time() < limite:
        app.processEvents()
        time.sleep(0.05)
    assert dialogo.tarefa is None, "a tarefa não terminou em %d s" % segundos


def _sem_janelas_modais(modulo):
    """Troca os QMessageBox de aviso e erro por uma lista.

    Fora da tela, uma janela modal não tem quem a feche e o roteiro
    trava; assim, o erro aparece na asserção.
    """
    mensagens = []
    modulo.QMessageBox.warning = lambda *a, **k: mensagens.append(a[-1])
    modulo.QMessageBox.critical = lambda *a, **k: mensagens.append(a[-1])
    return mensagens


def _selecionar(combo, camada):
    indice = combo.findData(camada.id())
    assert indice >= 0, (combo.objectName(), camada.name())
    combo.setCurrentIndex(indice)


def _plugin(iface):
    import risco_garimpo
    plugin = risco_garimpo.classFactory(iface)
    plugin.initGui()
    assert len(iface.menu) == 3, iface.menu
    assert len(iface.barra_de_ferramentas) == 2
    for _menu, acao in iface.menu:
        acao.trigger()          # abre cada diálogo pelo menu
    assert plugin.dialogo_estatico is not None
    assert plugin.dialogo_dinamico is not None
    assert plugin.dialogo_validacao is not None
    plugin.unload()
    assert not iface.menu and not iface.barra_de_ferramentas


def _estatico(iface, pasta, K, mu0, mascara):
    import risco_garimpo.ui.estatico_dialog as modulo
    erros = _sem_janelas_modais(modulo)
    dialogo = modulo.EstaticoDialog(iface)
    dialogo.show()
    # sem o mapa do ano mais antigo: os três focos sintéticos dão poucos
    # pixels maduros para estimar o nível (a maturidade tem testes próprios)
    _selecionar(dialogo.comboIBx, K)
    _selecionar(dialogo.comboMu, mu0)
    _selecionar(dialogo.comboMascara, mascara)
    dialogo.spinResolucao.setValue(100.0)
    dialogo.linhaPasta.setText(os.path.join(pasta, "estatico"))
    dialogo.linhaPrefixo.setText("dlg")
    dialogo._executar()
    _esperar(dialogo)
    log = dialogo.textoLog.toPlainText()
    assert dialogo.lblStatus.text() == "Pronto.", (dialogo.lblStatus.text(),
                                                   log, erros)
    for nome in ("dlg_K.tif", "dlg_registro.json"):
        assert os.path.isfile(os.path.join(pasta, "estatico", nome)), log
    dialogo.close()


def _dinamico(iface, pasta, K, mu0, mascara, alertas, autos):
    import risco_garimpo.ui.main_dialog as modulo
    erros = _sem_janelas_modais(modulo)
    dialogo = modulo.RiscoGarimpoDialog(iface)
    dialogo.show()
    _selecionar(dialogo.comboK, K)
    _selecionar(dialogo.comboMu0, mu0)
    _selecionar(dialogo.comboMascara, mascara)
    _selecionar(dialogo.comboAlertas, alertas)
    _selecionar(dialogo.comboFisc, autos)
    assert dialogo.comboCampoAlertas.currentText() == "data"
    dialogo.dateInicio.setDate(QDate(2025, 1, 1))
    dialogo.dateFim.setDate(QDate(2026, 12, 31))
    dialogo.linePasta.setText(os.path.join(pasta, "dinamico"))
    dialogo.linePrefixo.setText("dlg")
    dialogo.checkCarregar.setChecked(True)
    dialogo._executar()          # grava as preferências antes de rodar
    assert dialogo.tarefa is not None, (dialogo.textoLog.toPlainText(), erros)
    _esperar(dialogo)
    log = dialogo.textoLog.toPlainText()
    assert dialogo.lblStatus.text() == "Concluído.", (dialogo.lblStatus.text(),
                                                      log, erros)
    assert iface.barra.mensagens, "sem mensagem de conclusão"
    camadas = {c.name(): c for c in QgsProject.instance().mapLayers().values()}
    expansao = camadas.get("Expansão prevista (Δμ)")
    assert expansao is not None, sorted(camadas)
    # o estilo é acessório e falha em silêncio: confira que foi aplicado
    assert isinstance(expansao.renderer(), QgsSingleBandPseudoColorRenderer), \
        type(expansao.renderer())

    # fechar com uma simulação em andamento pergunta antes; "não" mantém aberto
    class _Tarefa(object):
        canceladas = 0

        def cancel(self):
            _Tarefa.canceladas += 1

    respostas = []
    original = modulo.QMessageBox.question

    def responder(*args, **kwargs):
        respostas.append(args)
        return modulo.compat.membro(modulo.QMessageBox, "StandardButton",
                                    respostas_desejadas.pop(0))
    respostas_desejadas = ["No", "Yes"]
    modulo.QMessageBox.question = responder
    try:
        dialogo.tarefa = _Tarefa()
        dialogo.close()
        app.processEvents()
        assert dialogo.isVisible() and _Tarefa.canceladas == 0
        dialogo.close()
        app.processEvents()
        assert not dialogo.isVisible() and _Tarefa.canceladas == 1
    finally:
        modulo.QMessageBox.question = original
        dialogo.tarefa = None
    assert len(respostas) == 2

    # as preferências gravadas voltam num diálogo novo
    outro = modulo.RiscoGarimpoDialog(iface)
    assert outro.dateInicio.date() == QDate(2025, 1, 1), outro.dateInicio.date()
    assert outro.dateFim.date() == QDate(2026, 12, 31)
    assert outro.linePrefixo.text() == "dlg"
    outro.close()


def principal(pasta):
    espaco = {"__name__": "__console__"}
    caminho = os.path.join(RAIZ, "tools", "gerar_dados_teste.py")
    exec(compile(open(caminho, encoding="utf-8").read(), caminho, "exec"),
         espaco)
    dados = espaco["gerar"](pasta)

    projeto = QgsProject.instance()
    K = QgsRasterLayer(dados["K"], "K")
    mu0 = QgsRasterLayer(dados["mu0"], "mu0")
    mascara = QgsRasterLayer(dados["mascara"], "mascara")
    alertas = QgsVectorLayer(dados["eventos"] + "|layername=alertas",
                             "alertas", "ogr")
    autos = QgsVectorLayer(dados["eventos"] + "|layername=autos", "autos",
                           "ogr")
    for camada in (K, mu0, mascara, alertas, autos):
        assert camada.isValid(), camada.name()
        projeto.addMapLayer(camada)

    iface = _Iface()
    _plugin(iface)
    _estatico(iface, pasta, K, mu0, mascara)
    _dinamico(iface, pasta, K, mu0, mascara, alertas, autos)
    print("DIALOGOS_OK")


if __name__ == "__main__":
    try:
        principal(sys.argv[1])
    finally:
        app.exitQgis()

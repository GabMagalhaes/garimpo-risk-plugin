# -*- coding: utf-8 -*-
"""Registro do plugin no QGIS: duas ferramentas, um menu.

A separação entre elas é de arquivo, não de chamada: o modelo estático grava
`K.tif`; o modelo dinâmico o lê como qualquer raster. Nenhum dos dois importa
o código do outro, e nada reprocessa o estático a cada simulação.
"""

import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

PLUGIN_DIR = os.path.dirname(__file__)
MENU_TITLE = "Risco Garimpo"


class RiscoGarimpoPlugin:
    """Modelo estático (K) e modelo dinâmico (expansão)."""

    def __init__(self, iface):
        self.iface = iface
        self.acoes = []
        self.dialogo_dinamico = None
        self.dialogo_estatico = None

    # ------------------------------------------------------------------ QGIS
    def _icone(self):
        caminho = os.path.join(PLUGIN_DIR, "resources", "icon.svg")
        return QIcon(caminho) if os.path.exists(caminho) else QIcon()

    def _acao(self, texto, dica, objeto, alvo, barra):
        acao = QAction(self._icone(), texto, self.iface.mainWindow())
        acao.setObjectName(objeto)
        acao.setWhatsThis(dica)
        acao.setStatusTip(dica)
        acao.triggered.connect(alvo)
        if barra:
            self.iface.addToolBarIcon(acao)
        self.iface.addPluginToRasterMenu(MENU_TITLE, acao)
        self.acoes.append((acao, barra))
        return acao

    def initGui(self):  # noqa: N802
        self._acao(
            "Modelo estático — gerar K…",
            "Potencial aurífero físico: proporção da área que se espera "
            "explorada no máximo, em [0, 1].",
            "riscoGarimpoEstaticoAction", self.abrir_estatico, barra=True)
        self._acao(
            "Modelo dinâmico de risco…",
            "Simula a expansão do garimpo ilegal em superfície contínua, "
            "consumindo K como capacidade de suporte.",
            "riscoGarimpoAction", self.abrir_dinamico, barra=True)

    def unload(self):
        for acao, barra in self.acoes:
            self.iface.removePluginRasterMenu(MENU_TITLE, acao)
            if barra:
                self.iface.removeToolBarIcon(acao)
        self.acoes = []
        for atributo in ("dialogo_dinamico", "dialogo_estatico"):
            dialogo = getattr(self, atributo)
            if dialogo is not None:
                dialogo.close()
                setattr(self, atributo, None)

    # ----------------------------------------------------------------- ações
    @staticmethod
    def _mostrar(dialogo):
        dialogo.show()
        dialogo.raise_()
        dialogo.activateWindow()
        return dialogo

    def abrir_dinamico(self):
        from .ui.main_dialog import RiscoGarimpoDialog

        if self.dialogo_dinamico is None:
            self.dialogo_dinamico = RiscoGarimpoDialog(
                self.iface, self.iface.mainWindow())
        self._mostrar(self.dialogo_dinamico)

    def abrir_estatico(self):
        from .ui.estatico_dialog import EstaticoDialog

        if self.dialogo_estatico is None:
            self.dialogo_estatico = EstaticoDialog(
                self.iface, self.iface.mainWindow())
        self._mostrar(self.dialogo_estatico)

    # --------------------------------------------------------- compatibilidade
    def run(self):
        """Mantido para quem chamava a ação antiga."""
        self.abrir_dinamico()

    @staticmethod
    def tr(message):
        return QCoreApplication.translate("RiscoGarimpo", message)

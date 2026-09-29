# -*- coding: utf-8 -*-
"""Execução da simulação em segundo plano (QgsTask)."""

import traceback

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.execucao import executar
from ..core.model import SimulacaoCancelada


class TarefaSimulacao(QgsTask):
    """Roda a fase pesada (GDAL + NumPy) fora da thread da interface."""

    registro = pyqtSignal(str)
    etapa = pyqtSignal(str)

    def __init__(self, config, preparo):
        super().__init__("Modelo dinâmico de risco de garimpo",
                         QgsTask.CanCancel)
        self.config = config
        self.preparo = preparo
        self.saida = None
        self.excecao = None
        self.detalhe = ""

    def run(self):  # executado em outra thread
        try:
            self.saida = executar(self.config, self.preparo,
                                  progresso=self._progresso,
                                  log=self._registrar)
            return True
        except SimulacaoCancelada:
            self._registrar("Simulação cancelada.")
            return False
        except Exception as erro:  # noqa: BLE001 - relatado à interface
            self.excecao = erro
            self.detalhe = traceback.format_exc()
            return False

    def _progresso(self, fracao, mensagem):
        if self.isCanceled():
            return False
        self.setProgress(max(0.0, min(100.0, float(fracao) * 100.0)))
        self.etapa.emit(str(mensagem))
        return True

    def _registrar(self, mensagem):
        self.registro.emit(str(mensagem))

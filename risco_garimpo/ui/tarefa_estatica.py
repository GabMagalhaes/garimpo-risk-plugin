# -*- coding: utf-8 -*-
"""Execução do modelo estático em segundo plano (QgsTask)."""

import traceback

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.execucao_estatica import SimulacaoCancelada, executar


class TarefaEstatica(QgsTask):
    """Roda a fase pesada (GDAL + NumPy) fora da thread da interface."""

    registro = pyqtSignal(str)

    def __init__(self, preparo):
        super().__init__("Modelo estático — geração de K", QgsTask.CanCancel)
        self.preparo = preparo
        self.saida = None
        self.excecao = None
        self.detalhe = ""

    def run(self):  # executado em outra thread
        try:
            self.saida = executar(self.preparo,
                                  progresso=self._progresso,
                                  cancelado=self.isCanceled,
                                  registrar=self._registrar)
            return True
        except SimulacaoCancelada:
            self._registrar("Geração cancelada.")
            return False
        except Exception as erro:  # noqa: BLE001 - relatado à interface
            self.excecao = erro
            self.detalhe = traceback.format_exc()
            return False

    def _progresso(self, pct):
        self.setProgress(max(0.0, min(100.0, float(pct))))

    def _registrar(self, mensagem):
        self.registro.emit(str(mensagem))

# -*- coding: utf-8 -*-
"""Validação em segundo plano (QgsTask)."""

import traceback

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from .. import compat

from ..core.execucao_validacao import ValidacaoCancelada, executar


class TarefaValidacao(QgsTask):
    """Lê os rasters e calcula as métricas fora da thread da interface."""

    registro = pyqtSignal(str)

    def __init__(self, preparado):
        super().__init__("Validação de uma execução",
                         compat.tarefa_cancelavel())
        self.preparado = preparado
        self.saida = None
        self.excecao = None
        self.detalhe = ""

    def run(self):  # executado em outra thread
        try:
            self.saida = executar(self.preparado,
                                  progresso=self._progresso,
                                  cancelado=self.isCanceled,
                                  registrar=self._registrar)
            return True
        except ValidacaoCancelada:
            self._registrar("Validação cancelada.")
            return False
        except Exception as erro:  # noqa: BLE001 - relatado à interface
            self.excecao = erro
            self.detalhe = traceback.format_exc()
            return False

    def _progresso(self, pct):
        self.setProgress(max(0.0, min(100.0, float(pct))))

    def _registrar(self, mensagem):
        self.registro.emit(str(mensagem))

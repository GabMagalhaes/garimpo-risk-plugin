# -*- coding: utf-8 -*-
"""Calibração em segundo plano (QgsTask), estática ou dinâmica."""

import traceback

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from .. import compat
from ..core import calibracao_dinamica, calibracao_estatica


class TarefaCalibracao(QgsTask):
    """Lê os rasters e roda o plano fora da thread da interface.

    O motor chega pronto da thread principal (no dinâmico, já com os
    eventos lidos das camadas). Cada combinação vai para o CSV assim que
    termina: cancelar não perde o que já foi feito.
    """

    registro = pyqtSignal(str)

    def __init__(self, motor, titulo):
        super().__init__(titulo, compat.tarefa_cancelavel())
        self.motor = motor
        self.motor.registrar = self._registrar
        self.motor.detalhar = True
        self.feitas = 0
        self.excecao = None
        self.detalhe = ""

    def run(self):  # executado em outra thread
        try:
            self.motor.carregar()
            self.feitas = self.motor.rodar(cancelado=self.isCanceled,
                                           progresso=self._progresso)
            return True
        except (calibracao_dinamica.Cancelada, calibracao_estatica.Cancelada):
            self._registrar("Calibração cancelada; o que já foi feito ficou "
                            "no resultados.csv e é retomado na próxima vez.")
            return False
        except Exception as erro:  # noqa: BLE001 - relatado à interface
            self.excecao = erro
            self.detalhe = traceback.format_exc()
            return False

    def _progresso(self, feitas, total):
        if total:
            self.setProgress(max(0.0, min(100.0, 100.0 * feitas / total)))

    def _registrar(self, mensagem):
        self.registro.emit(str(mensagem))

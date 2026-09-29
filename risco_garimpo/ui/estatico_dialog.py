# -*- coding: utf-8 -*-
"""Diálogo do modelo estático: gera K, a proporção máxima esperada."""

import os

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,
                       QgsProject, QgsRasterLayer, QgsVectorLayer,
                       QgsWkbTypes)
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QFileDialog, QMessageBox

from ..core.execucao_estatica import (ConfiguracaoEstatica, caminhos_de_saida,
                                      escrever, grade_de_trabalho, preparar)
from .tarefa_estatica import TarefaEstatica

CAMINHO_UI = os.path.join(os.path.dirname(__file__),
                          "estatico_dialog_base.ui")
FORM_CLASS, _ = uic.loadUiType(CAMINHO_UI)

SEM_CAMADA = "— nenhuma —"


class EstaticoDialog(QDialog, FORM_CLASS):
    """Modelo estático de potencial aurífero físico."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.iface = iface
        self.tarefa = None
        self._preparo = None

        self.btnPasta.clicked.connect(self._escolher_pasta)
        self.btnExecutar.clicked.connect(self._executar)
        self.btnCancelar.clicked.connect(self._cancelar)
        self.btnFechar.clicked.connect(self.close)
        self.checkEstimarNivel.toggled.connect(self._alternar_nivel)

        for combo in (self.comboIBx, self.comboEst, self.comboLito,
                      self.comboMu, self.comboMuAntigo, self.comboMuAnterior,
                      self.comboMascara, self.comboAgua):
            combo.currentIndexChanged.connect(self._atualizar_grade)
        self.spinResolucao.valueChanged.connect(self._atualizar_grade)
        self.comboCRS.currentIndexChanged.connect(self._atualizar_grade)

        self.lblGrade.setReadOnly(True)
        self.recarregar()
        self._alternar_nivel(self.checkEstimarNivel.isChecked())

    # ------------------------------------------------------------- camadas
    def recarregar(self):
        rasters = [c for c in QgsProject.instance().mapLayers().values()
                   if isinstance(c, QgsRasterLayer)]
        rasters.sort(key=lambda c: c.name().lower())
        for combo, opcional in ((self.comboIBx, False),
                                (self.comboEst, True),
                                (self.comboLito, True),
                                (self.comboMu, False),
                                (self.comboMuAntigo, True),
                                (self.comboMuAnterior, True),
                                (self.comboMascara, True),
                                (self.comboAgua, True)):
            self._preencher(combo, rasters, opcional)

        poligonos = [c for c in QgsProject.instance().mapLayers().values()
                     if isinstance(c, QgsVectorLayer)
                     and c.geometryType() == QgsWkbTypes.PolygonGeometry]
        poligonos.sort(key=lambda c: c.name().lower())
        self._preencher(self.comboSubbacias, poligonos, True)

        self.comboCRS.clear()
        vistos = []
        for camada in rasters:
            crs = camada.crs()
            if crs.isValid() and not crs.isGeographic() \
                    and crs.authid() not in [v[0] for v in vistos]:
                vistos.append((crs.authid(), crs.description()))
        for authid, descricao in vistos:
            self.comboCRS.addItem("%s — %s" % (authid, descricao), authid)
        if not vistos:
            self.comboCRS.addItem("(nenhum CRS projetado no projeto)", None)
        self._atualizar_grade()

    @staticmethod
    def _preencher(combo, camadas, opcional):
        anterior = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        if opcional:
            combo.addItem(SEM_CAMADA, None)
        for camada in camadas:
            combo.addItem(camada.name(), camada.id())
        if anterior is not None:
            indice = combo.findData(anterior)
            if indice >= 0:
                combo.setCurrentIndex(indice)
        combo.blockSignals(False)

    @staticmethod
    def _camada(combo):
        identificador = combo.currentData()
        return (QgsProject.instance().mapLayer(identificador)
                if identificador else None)

    @staticmethod
    def _fonte(combo):
        identificador = combo.currentData()
        if not identificador:
            return None
        camada = QgsProject.instance().mapLayer(identificador)
        return camada.source().split("|")[0] if camada else None

    # ------------------------------------------------------------ interface
    def _alternar_nivel(self, ligado):
        for widget in (self.spinJanela, self.spinQuantil, self.spinFaixas,
                       self.spinMinimoFaixa):
            widget.setEnabled(ligado)
        self.spinTeto.setEnabled(not ligado)

    def _escolher_pasta(self):
        pasta = QFileDialog.getExistingDirectory(
            self, "Pasta de saída", self.linhaPasta.text() or "")
        if pasta:
            self.linhaPasta.setText(pasta)

    def _atualizar_grade(self):
        try:
            grade = grade_de_trabalho(self._config())
        except Exception:  # noqa: BLE001 - só alimenta o rótulo
            self.lblGrade.setText("—")
            return
        mb = grade.nx * grade.ny * 4 / (1024.0 ** 2)
        self.lblGrade.setText(
            "%d × %d px de %.0f m — %.0f km², %.0f MB por camada"
            % (grade.nx, grade.ny, grade.pixel_m,
               grade.nx * grade.ny * grade.pixel_m ** 2 / 1e6, mb))

    def _registrar(self, mensagem):
        self.textoLog.appendPlainText(str(mensagem))

    # ---------------------------------------------------------- configuração
    def _config(self):
        return ConfiguracaoEstatica(
            caminho_ibx=self._fonte(self.comboIBx),
            caminho_est=self._fonte(self.comboEst),
            caminho_lito=self._fonte(self.comboLito),
            caminho_mu=self._fonte(self.comboMu),
            caminho_mu_antigo=self._fonte(self.comboMuAntigo),
            caminho_mu_anterior=self._fonte(self.comboMuAnterior),
            camada_subbacias=self._camada(self.comboSubbacias),
            campo_id_subbacia=self.linhaCampoId.text().strip() or None,
            caminho_mascara=self._fonte(self.comboMascara),
            caminho_agua=self._fonte(self.comboAgua),
            pasta_saida=self.linhaPasta.text().strip(),
            resolucao_m=self.spinResolucao.value(),
            wkt_trabalho=self._wkt_escolhido(),
            peso_ibx=self.spinPesoIBx.value(),
            peso_est=self.spinPesoEst.value(),
            peso_lito=self.spinPesoLito.value(),
            gamma=self.spinGamma.value(),
            piso=self.spinPiso.value(),
            estimar_nivel=self.checkEstimarNivel.isChecked(),
            janela_m=self.spinJanela.value(),
            quantil=self.spinQuantil.value(),
            n_faixas=self.spinFaixas.value(),
            minimo_por_faixa=self.spinMinimoFaixa.value(),
            teto=self.spinTeto.value(),
            prefixo=self.linhaPrefixo.text().strip() or "PMT",
            salvar_escore=self.checkEscore.isChecked(),
            salvar_remanescente=self.checkRemanescente.isChecked(),
            salvar_tabela=self.checkTabela.isChecked(),
            carregar=self.checkCarregar.isChecked())

    def _wkt_escolhido(self):
        authid = self.comboCRS.currentData()
        if not authid:
            return None
        crs = QgsCoordinateReferenceSystem(authid)
        return crs.toWkt() if crs.isValid() else None

    # -------------------------------------------------------------- execução
    def _liberar_saidas(self, config):
        """Remove do projeto as camadas que apontam para os arquivos de saída.

        No Windows, um raster carregado no projeto trava a sobrescrita.
        """
        alvos = {os.path.normcase(os.path.abspath(c))
                 for c in caminhos_de_saida(config)}
        projeto = QgsProject.instance()
        remover = []
        for identificador, camada in projeto.mapLayers().items():
            fonte = camada.source().split("|")[0]
            if os.path.normcase(os.path.abspath(fonte)) in alvos:
                remover.append(identificador)
        if remover:
            projeto.removeMapLayers(remover)
            self._registrar("Liberadas %d camadas de saída já carregadas."
                            % len(remover))

    def _executar(self):
        config = self._config()
        try:
            self._liberar_saidas(config)
            self._preparo = preparar(config, registrar=self._registrar)
        except Exception as erro:  # noqa: BLE001 - erro de configuração
            QMessageBox.warning(self, "Configuração", str(erro))
            return

        self.abas.setCurrentWidget(self.abaRegistro)
        self.btnExecutar.setEnabled(False)
        self.btnCancelar.setEnabled(True)
        self.lblStatus.setText("Calculando…")
        self.barraProgresso.setValue(0)

        self.tarefa = TarefaEstatica(self._preparo)
        self.tarefa.registro.connect(self._registrar)
        self.tarefa.progressChanged.connect(
            lambda v: self.barraProgresso.setValue(int(v)))
        self.tarefa.taskCompleted.connect(self._concluir)
        self.tarefa.taskTerminated.connect(self._falhar)
        QgsApplication.taskManager().addTask(self.tarefa)

    def _cancelar(self):
        if self.tarefa is not None:
            self.tarefa.cancel()

    def _concluir(self):
        config = self._preparo["config"]
        try:
            gravados = escrever(self.tarefa.saida, registrar=self._registrar)
        except Exception as erro:  # noqa: BLE001 - relatado à interface
            QMessageBox.critical(self, "Escrita", str(erro))
            self._restaurar("Falhou ao gravar.")
            return

        if config.carregar:
            for caminho in gravados:
                nome = os.path.splitext(os.path.basename(caminho))[0]
                if caminho.lower().endswith(".tif"):
                    camada = QgsRasterLayer(caminho, nome)
                elif caminho.lower().endswith(".gpkg"):
                    camada = QgsVectorLayer(caminho, nome, "ogr")
                else:
                    continue
                if camada.isValid():
                    QgsProject.instance().addMapLayer(camada)

        totais = self.tarefa.saida.get("totais") or {}
        teto = self.tarefa.saida["resultado"].teto_estimado
        self._registrar("Concluído. K máximo previsto: %.4f da área do pixel."
                        % teto)
        if totais:
            self._registrar(
                "Potencial remanescente na área de estudo: %.0f ha "
                "(%.0f ha de potencial, %.1f%% já explotado)."
                % (totais["remanescente_ha"], totais["potencial_ha"],
                   100.0 * totais["explotacao"]))
        self._restaurar("Pronto.")

    def _falhar(self):
        if self.tarefa is not None and self.tarefa.excecao is not None:
            self._registrar(self.tarefa.detalhe)
            QMessageBox.critical(self, "Erro", str(self.tarefa.excecao))
            self._restaurar("Falhou.")
        else:
            self._restaurar("Cancelado.")

    def _restaurar(self, status):
        self.btnExecutar.setEnabled(True)
        self.btnCancelar.setEnabled(False)
        self.lblStatus.setText(status)
        self.tarefa = None

    def showEvent(self, evento):  # noqa: N802 - Qt
        super().showEvent(evento)
        self.recarregar()

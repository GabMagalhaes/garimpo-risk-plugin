# -*- coding: utf-8 -*-
"""Diálogo da validação: expansão prevista × garimpo observado."""

import os

from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QFileDialog, QMessageBox

from .. import compat
from ..core import registro as _registro
from ..core.execucao_validacao import (ConfiguracaoValidacao,
                                       avisos_de_honestidade,
                                       caminho_de_saida, caminhos_de_saida,
                                       de_registro, escrever, preparar)
from .tarefa_validacao import TarefaValidacao

CAMINHO_UI = os.path.join(os.path.dirname(__file__),
                          "validacao_dialog_base.ui")
FORM_CLASS, _ = uic.loadUiType(CAMINHO_UI)

SEM_CAMADA = "— nenhuma —"
RASTERS = "Rasters (*.tif *.tiff *.vrt *.img);;Todos (*)"


class ValidacaoDialog(QDialog, FORM_CLASS):
    """Valida uma execução do modelo dinâmico contra o garimpo observado."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.iface = iface
        self.tarefa = None
        self._preparo = None

        self.btnRegistro.clicked.connect(self._escolher_registro)
        self.linhaRegistro.editingFinished.connect(self._ler_registro)
        for botao, linha, titulo in (
                (self.btnPrevisto, self.linhaPrevisto, "Expansão prevista"),
                (self.btnMuInicial, self.linhaMuInicial,
                 "Garimpo na data inicial"),
                (self.btnK, self.linhaK, "K"),
                (self.btnMascara, self.linhaMascara, "Máscara da simulação"),
                (self.btnMuFinal, self.linhaMuFinal, "Garimpo na data final"),
                (self.btnAvaliacao, self.linhaAvaliacao,
                 "Máscara de avaliação")):
            botao.clicked.connect(
                lambda _=False, l=linha, t=titulo: self._escolher_raster(l, t))
        self.btnPasta.clicked.connect(self._escolher_pasta)
        self.btnExecutar.clicked.connect(self._executar)
        self.btnCancelar.clicked.connect(self._cancelar)
        self.btnFechar.clicked.connect(self.close)

        self.lblExecucao.setReadOnly(True)
        self.recarregar()

    # ------------------------------------------------------------- camadas
    def recarregar(self):
        poligonos = [c for c in QgsProject.instance().mapLayers().values()
                     if isinstance(c, QgsVectorLayer)
                     and c.geometryType() == compat.tipo_geometria("Polygon")]
        poligonos.sort(key=lambda c: c.name().lower())
        anterior = self.comboSubbacias.currentData()
        self.comboSubbacias.blockSignals(True)
        self.comboSubbacias.clear()
        self.comboSubbacias.addItem(SEM_CAMADA, None)
        for camada in poligonos:
            self.comboSubbacias.addItem(camada.name(), camada.id())
        if anterior is not None:
            indice = self.comboSubbacias.findData(anterior)
            if indice >= 0:
                self.comboSubbacias.setCurrentIndex(indice)
        self.comboSubbacias.blockSignals(False)

    def _subbacias(self):
        identificador = self.comboSubbacias.currentData()
        return (QgsProject.instance().mapLayer(identificador)
                if identificador else None)

    # ------------------------------------------------------------ arquivos
    def _escolher_registro(self):
        caminho, _ = QFileDialog.getOpenFileName(
            self, "Registro da execução", self.linhaRegistro.text() or "",
            "Registro (*.json);;Todos (*)")
        if caminho:
            self.linhaRegistro.setText(caminho)
            self._ler_registro()

    def _escolher_raster(self, linha, titulo):
        caminho, _ = QFileDialog.getOpenFileName(
            self, titulo, linha.text() or self._pasta_inicial(), RASTERS)
        if caminho:
            linha.setText(caminho)

    def _escolher_pasta(self):
        pasta = QFileDialog.getExistingDirectory(
            self, "Pasta de saída",
            self.linhaPasta.text() or self._pasta_inicial())
        if pasta:
            self.linhaPasta.setText(pasta)

    def _pasta_inicial(self):
        for linha in (self.linhaRegistro, self.linhaPrevisto):
            if linha.text().strip():
                return os.path.dirname(linha.text().strip())
        return ""

    def _ler_registro(self):
        """Preenche os campos a partir do registro da execução."""
        caminho = self.linhaRegistro.text().strip()
        if not caminho:
            self.lblExecucao.setText("")
            return
        try:
            dados = _registro.ler(caminho)
            config = de_registro(caminho, "")
        except Exception as erro:  # noqa: BLE001 - relatado ao usuário
            self.lblExecucao.setText("—")
            QMessageBox.warning(self, "Registro", str(erro))
            return
        self.linhaPrevisto.setText(config.caminho_previsto or "")
        self.linhaMuInicial.setText(config.caminho_mu_inicial or "")
        self.linhaK.setText(config.caminho_k or "")
        self.linhaMascara.setText(config.caminho_mascara or "")
        resumo = "%s → %s · prefixo %s" % (
            config.data_inicial, config.data_final,
            dados.get("configuracao", {}).get("prefixo") or "risco")
        avisos = avisos_de_honestidade(config)
        if avisos:
            resumo += " · RECONSTRUÇÃO"
        self.lblExecucao.setText(resumo)
        self.lblExecucao.setCursorPosition(0)
        self.lblExecucao.setToolTip(
            "\n\n".join(avisos) if avisos else
            "Eventos só até a data inicial: a validação mede antecipação.")

    # ---------------------------------------------------------- configuração
    @staticmethod
    def _texto(linha):
        return linha.text().strip() or None

    def _config(self):
        campos = dict(
            caminho_previsto=self._texto(self.linhaPrevisto),
            caminho_mu_inicial=self._texto(self.linhaMuInicial),
            caminho_k=self._texto(self.linhaK),
            caminho_mascara=self._texto(self.linhaMascara),
            caminho_mascara_avaliacao=self._texto(self.linhaAvaliacao),
            camada_subbacias=self._subbacias(),
            campo_id_subbacia=self.linhaCampoId.text().strip() or None,
            limiar=self.spinLimiar.value(),
            bloco_m=self.spinBloco.value(),
            fracao_topo=self.spinTopo.value(),
            salvar_figura=self.checkFigura.isChecked())
        mu_final = self._texto(self.linhaMuFinal)
        pasta = self._texto(self.linhaPasta)
        prefixo = self._texto(self.linhaPrefixo)
        registro = self._texto(self.linhaRegistro)
        if registro:
            # o registro dá datas, SHA-256 esperados e eventos; os campos,
            # que ele mesmo preencheu, valem se o usuário os mudou
            config = de_registro(registro, mu_final, pasta_saida=pasta,
                                 **campos)
            if prefixo:
                config.prefixo = prefixo
            return config
        previsto = campos["caminho_previsto"]
        return ConfiguracaoValidacao(
            caminho_mu_final=mu_final,
            pasta_saida=pasta or (os.path.dirname(previsto)
                                  if previsto else None),
            prefixo=prefixo or "validacao", **campos)

    # -------------------------------------------------------------- execução
    def _registrar(self, mensagem):
        self.textoLog.appendPlainText(str(mensagem))

    def _liberar_saidas(self, config):
        """Remove do projeto as camadas que apontam para as saídas.

        No Windows, um GeoPackage carregado no projeto trava a sobrescrita.
        """
        alvos = {os.path.normcase(os.path.abspath(c))
                 for c in caminhos_de_saida(config)}
        projeto = QgsProject.instance()
        remover = [identificador
                   for identificador, camada in projeto.mapLayers().items()
                   if os.path.normcase(os.path.abspath(
                       camada.source().split("|")[0])) in alvos]
        if remover:
            projeto.removeMapLayers(remover)
            self._registrar("Liberadas %d camadas de saída já carregadas."
                            % len(remover))

    def _executar(self):
        try:
            config = self._config()
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

        self.tarefa = TarefaValidacao(self._preparo)
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

        subbacias = caminho_de_saida(config, "subbacias", "gpkg")
        if self.checkCarregar.isChecked() and subbacias in gravados:
            camada = QgsVectorLayer(subbacias + "|layername=validacao_subbacias",
                                    "Validação por sub-bacia", "ogr")
            if camada.isValid():
                QgsProject.instance().addMapLayer(camada)
        self.barraProgresso.setValue(100)
        self._registrar("Concluído. Resultados em %s" % config.pasta_saida)
        self._restaurar("Pronto.")
        self.iface.messageBar().pushMessage(
            "Risco Garimpo", "Validação concluída.",
            level=compat.nivel_sucesso(), duration=6)

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

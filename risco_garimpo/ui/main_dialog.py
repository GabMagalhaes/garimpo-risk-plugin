# -*- coding: utf-8 -*-
"""Diálogo principal do plugin."""

import datetime as _dt
import os

from qgis.core import (Qgis, QgsApplication, QgsColorRampShader,
                       QgsCoordinateReferenceSystem, QgsProject,
                       QgsRasterLayer, QgsRasterShader, QgsSettings,
                       QgsSingleBandPseudoColorRenderer, QgsVectorLayer)
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QDate, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog,
                                 QDoubleSpinBox, QFileDialog, QLineEdit,
                                 QMessageBox, QSpinBox)

from ..core.execucao import (Configuracao, ConfiguracaoInvalida, agregar,
                             caminhos_de_saida, preparar)
from ..core.model import Parametros
from .tarefa import TarefaSimulacao

CAMINHO_UI = os.path.join(os.path.dirname(__file__), "main_dialog_base.ui")
FORM_CLASS, _ = uic.loadUiType(CAMINHO_UI)

GRUPO_CONFIG = "risco_garimpo"
VAZIO = "— nenhuma —"

# paleta das saídas: cinza (sem expansão) → amarelo → vermelho
CORES_RISCO = [(0.00, "#f2f2f2"), (0.25, "#ffe08a"), (0.50, "#f6a55c"),
               (0.75, "#e05263"), (1.00, "#7d1128")]


class RiscoGarimpoDialog(QDialog, FORM_CLASS):
    """Interface única do modelo dinâmico."""

    def __init__(self, iface, parent=None):
        super(RiscoGarimpoDialog, self).__init__(parent)
        self.setupUi(self)
        self.iface = iface
        self.tarefa = None
        self._config_atual = None

        self._conectar()
        self._atualizar_camadas()
        self._restaurar_preferencias()
        self._atualizar_dependencias()
        self._atualizar_info_grade()

        projeto = QgsProject.instance()
        projeto.layersAdded.connect(self._atualizar_camadas)
        projeto.layersRemoved.connect(self._atualizar_camadas)

    # ------------------------------------------------------------- ligações
    def _conectar(self):
        self.btnExecutar.clicked.connect(self._executar)
        self.btnCancelar.clicked.connect(self._cancelar)
        self.btnFechar.clicked.connect(self.close)
        self.btnPasta.clicked.connect(self._escolher_pasta)

        self.comboK.currentIndexChanged.connect(self._atualizar_info_grade)
        self.checkReprojetar.toggled.connect(self._atualizar_info_grade)
        self.lineCrs.textChanged.connect(self._atualizar_info_grade)
        self.checkResolucao.toggled.connect(self._atualizar_info_grade)
        self.spinResolucao.valueChanged.connect(self._atualizar_info_grade)

        self.comboAlertas.currentIndexChanged.connect(
            lambda: self._preencher_campos(self.comboAlertas,
                                           self.comboCampoAlertas))
        self.comboFisc.currentIndexChanged.connect(
            lambda: self._preencher_campos(self.comboFisc,
                                           self.comboCampoFisc))
        self.comboSubbacias.currentIndexChanged.connect(
            lambda: self._preencher_campos(self.comboSubbacias,
                                           self.comboCampoIdSub))

        for widget in (self.checkReprojetar, self.checkResolucao,
                       self.checkFrentes, self.checkSemeia, self.checkSerie):
            widget.toggled.connect(self._atualizar_dependencias)

    def _atualizar_dependencias(self):
        self.lineCrs.setEnabled(self.checkReprojetar.isChecked())
        self.spinResolucao.setEnabled(self.checkResolucao.isChecked())
        self.spinRaioFrente.setEnabled(self.checkFrentes.isChecked())
        self.spinSemente.setEnabled(self.checkSemeia.isChecked())
        self.spinPassoSerie.setEnabled(self.checkSerie.isChecked())

    # --------------------------------------------------------- camadas do QGIS
    def _atualizar_camadas(self):
        rasters = []
        vetores = []
        for camada in QgsProject.instance().mapLayers().values():
            if isinstance(camada, QgsRasterLayer) and camada.isValid():
                rasters.append(camada)
            elif isinstance(camada, QgsVectorLayer) and camada.isValid():
                vetores.append(camada)
        rasters.sort(key=lambda c: c.name().lower())
        vetores.sort(key=lambda c: c.name().lower())

        self._preencher(self.comboK, rasters, opcional=False)
        self._preencher(self.comboMu0, rasters, opcional=False)
        self._preencher(self.comboMascara, rasters, opcional=True)
        self._preencher(self.comboAlertas, vetores, opcional=True)
        self._preencher(self.comboFisc, vetores, opcional=True)
        self._preencher(self.comboSubbacias, vetores, opcional=True)

    @staticmethod
    def _preencher(combo, camadas, opcional):
        anterior = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        if opcional:
            combo.addItem(VAZIO, None)
        for camada in camadas:
            combo.addItem(camada.name(), camada.id())
        combo.blockSignals(False)
        if anterior is not None:
            indice = combo.findData(anterior)
            if indice >= 0:
                combo.setCurrentIndex(indice)

    @staticmethod
    def _camada(combo):
        identificador = combo.currentData()
        if not identificador:
            return None
        return QgsProject.instance().mapLayer(identificador)

    def _preencher_campos(self, combo_camada, combo_campo):
        camada = self._camada(combo_camada)
        anterior = combo_campo.currentText()
        combo_campo.blockSignals(True)
        combo_campo.clear()
        if camada is not None:
            for campo in camada.fields():
                combo_campo.addItem(campo.name())
        combo_campo.blockSignals(False)
        indice = combo_campo.findText(anterior)
        if indice >= 0:
            combo_campo.setCurrentIndex(indice)
        elif camada is not None:
            provavel = self._campo_provavel(camada)
            if provavel:
                combo_campo.setCurrentIndex(combo_campo.findText(provavel))

    @staticmethod
    def _campo_provavel(camada):
        """Palpite para o campo de data, apenas como conveniência inicial."""
        preferidos = ("data", "date", "dt_", "data_dete", "detect",
                      "data_auto", "dt_auto", "datainfrac")
        nomes = [campo.name() for campo in camada.fields()]
        for alvo in preferidos:
            for nome in nomes:
                if nome.lower().startswith(alvo):
                    return nome
        for nome in nomes:
            if "dat" in nome.lower():
                return nome
        return None

    # ------------------------------------------------------------------ grade
    def _caminho_de(self, combo):
        camada = self._camada(combo)
        if camada is None:
            return None
        fonte = camada.source().split("|")[0]
        return fonte

    def _wkt_trabalho(self):
        if not self.checkReprojetar.isChecked():
            return None
        texto = self.lineCrs.text().strip()
        if not texto:
            return None
        crs = QgsCoordinateReferenceSystem(texto)
        if not crs.isValid():
            return False  # sinaliza CRS inválido
        return crs.toWkt()

    def _atualizar_info_grade(self):
        caminho = self._caminho_de(self.comboK)
        if not caminho or not os.path.exists(caminho):
            self.lineGradeInfo.setText("—")
            return
        try:
            from ..core import raster as _raster

            grade = _raster.grade_de_raster(caminho)
            wkt = self._wkt_trabalho()
            if wkt is False:
                self.lineGradeInfo.setText("CRS de destino inválido")
                return
            resolucao = (self.spinResolucao.value()
                         if self.checkResolucao.isChecked() else None)
            grade = _raster.grade_reamostrada(grade, resolucao, wkt)
            memoria = grade.nx * grade.ny * 4 / (1024.0 ** 2)
            aviso = " — grade geográfica, reprojete!" if grade.geografica else ""
            self.lineGradeInfo.setText(
                "%s | ~%.0f MB por camada%s"
                % (grade.descricao(), memoria, aviso))
        except Exception as erro:  # noqa: BLE001
            self.lineGradeInfo.setText("erro ao ler a grade: %s" % erro)

    # ----------------------------------------------------------- configuração
    def _montar_configuracao(self):
        wkt = self._wkt_trabalho()
        if wkt is False:
            raise ConfiguracaoInvalida(
                "CRS de destino inválido: %r" % self.lineCrs.text())

        parametros = Parametros(
            r0=self.spinR0.value(),
            escala_contagio_m=self.spinEscalaContagio.value(),
            tipo_kernel=self.comboKernel.currentText(),
            excluir_centro=self.checkExcluirCentro.isChecked(),
            alerta_peso=self.spinAlertaPeso.value(),
            alerta_escala_m=self.spinAlertaEscala.value(),
            alerta_tau_dias=self.spinAlertaTau.value(),
            alerta_semeia=self.checkSemeia.isChecked(),
            alerta_semente=self.spinSemente.value(),
            fisc_max=self.spinFiscMax.value(),
            fisc_escala_m=self.spinFiscEscala.value(),
            fisc_k_subida=self.spinFiscSubida.value(),
            fisc_plato_dias=self.spinFiscPlato.value(),
            fisc_k_queda=self.spinFiscQueda.value(),
            combinacao=self.comboCombinacao.currentText(),
            passo_dias=self.spinPasso.value(),
        )

        return Configuracao(
            caminho_k=self._caminho_de(self.comboK),
            caminho_mu0=self._caminho_de(self.comboMu0),
            caminho_mascara=self._caminho_de(self.comboMascara),
            wkt_trabalho=wkt,
            resolucao_m=(self.spinResolucao.value()
                         if self.checkResolucao.isChecked() else None),
            camada_alertas=self._camada(self.comboAlertas),
            campo_data_alertas=self.comboCampoAlertas.currentText() or None,
            agrupar_em_frentes=self.checkFrentes.isChecked(),
            raio_frente_m=self.spinRaioFrente.value(),
            camada_fiscalizacao=self._camada(self.comboFisc),
            campo_data_fiscalizacao=self.comboCampoFisc.currentText() or None,
            janela_dias=self.spinJanela.value(),
            data_inicio=_para_data(self.dateInicio.date()),
            data_fim=_para_data(self.dateFim.date()),
            parametros=parametros,
            pasta_saida=self.linePasta.text().strip() or None,
            prefixo=self.linePrefixo.text().strip() or "risco",
            salvar_mu_final=self.checkMuFinal.isChecked(),
            salvar_taxa=self.checkTaxa.isChecked(),
            salvar_campos=self.checkCampos.isChecked(),
            salvar_serie=self.checkSerie.isChecked(),
            passo_serie=self.spinPassoSerie.value(),
            camada_subbacias=self._camada(self.comboSubbacias),
            campo_id_subbacias=self.comboCampoIdSub.currentText() or None,
        )

    # -------------------------------------------------------------- execução
    def _escolher_pasta(self):
        pasta = QFileDialog.getExistingDirectory(
            self, "Pasta de saída", self.linePasta.text().strip() or "")
        if pasta:
            self.linePasta.setText(pasta)

    def _executar(self):
        if self.tarefa is not None:
            return
        self.textoLog.clear()
        self.abas.setCurrentWidget(self.abaExecucao)

        try:
            config = self._montar_configuracao()
            config.validar()
            self._registrar("Preparando insumos…")
            preparo = preparar(config, log=self._registrar)
        except ConfiguracaoInvalida as erro:
            self._registrar("ERRO: %s" % erro)
            QMessageBox.warning(self, "Configuração incompleta", str(erro))
            return
        except Exception as erro:  # noqa: BLE001
            self._registrar("ERRO ao preparar: %s" % erro)
            QMessageBox.critical(self, "Erro ao preparar", str(erro))
            return

        self._salvar_preferencias()
        self._config_atual = config
        self._liberar_saidas(config)

        self.tarefa = TarefaSimulacao(config, preparo)
        self.tarefa.registro.connect(self._registrar)
        self.tarefa.etapa.connect(self._etapa)
        self.tarefa.progressChanged.connect(
            lambda v: self.barraProgresso.setValue(int(v)))
        self.tarefa.taskCompleted.connect(self._concluida)
        self.tarefa.taskTerminated.connect(self._terminada)

        self._travar(True)
        QgsApplication.taskManager().addTask(self.tarefa)

    def _liberar_saidas(self, config):
        """Tira do projeto as camadas que seguram os arquivos de saída.

        No Windows o QGIS mantém o raster aberto enquanto a camada existe, e
        a gravação falha com "Permission denied". Como é o próprio plugin que
        carrega as saídas ao final, é ele que precisa soltá-las antes de
        gravar de novo.
        """
        try:
            alvos = {os.path.normcase(os.path.abspath(caminho))
                     for caminho in caminhos_de_saida(config)}
        except Exception:  # noqa: BLE001 - liberar é conveniência, não etapa
            return 0

        projeto = QgsProject.instance()
        presas = []
        for identificador, camada in projeto.mapLayers().items():
            try:
                fonte = camada.source().split("|")[0]
            except Exception:  # noqa: BLE001
                continue
            if os.path.normcase(os.path.abspath(fonte)) in alvos:
                presas.append(identificador)
        for identificador in presas:
            projeto.removeMapLayer(identificador)
        if presas:
            self._registrar("Soltas %d camada(s) que seguravam arquivos de "
                            "saída da execução anterior." % len(presas))
        return len(presas)

    def _cancelar(self):
        if self.tarefa is not None:
            self.tarefa.cancel()
            self._etapa("Cancelando…")

    def _travar(self, ocupado):
        self.btnExecutar.setEnabled(not ocupado)
        self.btnCancelar.setEnabled(ocupado)
        self.abaInsumos.setEnabled(not ocupado)
        self.abaParametros.setEnabled(not ocupado)
        self.abaSaidas.setEnabled(not ocupado)

    def _concluida(self):
        tarefa, self.tarefa = self.tarefa, None
        self._travar(False)
        self.barraProgresso.setValue(100)
        saida = tarefa.saida or {}
        resultado = saida.get("resultado")
        grade = saida.get("grade")
        saidas = dict(saida.get("saidas", {}))

        if resultado is not None and self._config_atual.camada_subbacias:
            try:
                self._etapa("Agregando por sub-bacia…")
                caminho, tabela = agregar(self._config_atual, grade, resultado)
                if caminho:
                    saidas["subbacias"] = caminho
                    self._registrar("Sub-bacias agregadas: %s" % caminho)
                    self._registrar(_topo_subbacias(tabela))
            except Exception as erro:  # noqa: BLE001
                self._registrar("ERRO na agregação por sub-bacia: %s" % erro)

        self._registrar(_texto_diagnostico(saida.get("diagnostico", {})))
        self.lineResumo.setText(
            "Δμ total: %.1f ha equivalentes"
            % _hectares(saida.get("diagnostico", {}), grade))

        if self.checkCarregar.isChecked():
            self._carregar_saidas(saidas, resultado)

        self._etapa("Concluído.")
        self.iface.messageBar().pushMessage(
            "Risco Garimpo", "Simulação concluída.", level=Qgis.Success,
            duration=6)

    def _terminada(self):
        tarefa, self.tarefa = self.tarefa, None
        self._travar(False)
        if tarefa is not None and tarefa.excecao is not None:
            self._registrar("ERRO: %s" % tarefa.excecao)
            self._registrar(tarefa.detalhe)
            QMessageBox.critical(self, "Erro na simulação",
                                 str(tarefa.excecao))
            self._etapa("Erro.")
        else:
            self._etapa("Cancelado.")

    # ---------------------------------------------------------------- saídas
    def _carregar_saidas(self, saidas, resultado):
        rotulos = {"expansao": "Expansão prevista (Δμ)",
                   "mu_final": "μ final", "taxa": "Taxa instantânea",
                   "ativacao": "Ativação por alertas",
                   "supressao": "Supressão por fiscalização"}
        for chave, caminho in saidas.items():
            if chave in ("serie",):
                continue
            if chave == "subbacias":
                camada = QgsVectorLayer(caminho + "|layername=risco_subbacias",
                                        "Risco por sub-bacia", "ogr")
            else:
                camada = QgsRasterLayer(caminho, rotulos.get(chave, chave))
                if camada.isValid():
                    self._estilizar(camada)
            if camada.isValid():
                QgsProject.instance().addMapLayer(camada)
            else:
                self._registrar("Não foi possível carregar %s" % caminho)

    def _estilizar(self, camada):
        try:
            estatisticas = camada.dataProvider().bandStatistics(1)
            minimo = float(estatisticas.minimumValue)
            maximo = float(estatisticas.maximumValue)
            if not (maximo > minimo):
                return
            shader = QgsRasterShader()
            rampa = QgsColorRampShader()
            rampa.setColorRampType(QgsColorRampShader.Interpolated)
            itens = []
            for fracao, cor in CORES_RISCO:
                valor = minimo + fracao * (maximo - minimo)
                itens.append(QgsColorRampShader.ColorRampItem(
                    valor, QColor(cor), "%.4g" % valor))
            rampa.setColorRampItemList(itens)
            shader.setRasterShaderFunction(rampa)
            camada.setRenderer(QgsSingleBandPseudoColorRenderer(
                camada.dataProvider(), 1, shader))
            camada.triggerRepaint()
        except Exception:  # noqa: BLE001 - estilo é acessório
            pass

    # ------------------------------------------------------------ preferências
    def _salvar_preferencias(self):
        cfg = QgsSettings()
        cfg.beginGroup(GRUPO_CONFIG)
        for nome, widget in self._widgets_persistentes().items():
            cfg.setValue(nome, _valor_widget(widget))
        cfg.endGroup()

    def _restaurar_preferencias(self):
        cfg = QgsSettings()
        cfg.beginGroup(GRUPO_CONFIG)
        for nome, widget in self._widgets_persistentes().items():
            valor = cfg.value(nome, None)
            if valor is not None:
                _definir_widget(widget, valor)
        cfg.endGroup()

    def _widgets_persistentes(self):
        nomes = ["lineCrs", "checkReprojetar", "checkResolucao",
                 "spinResolucao", "checkFrentes", "spinRaioFrente",
                 "spinJanela", "spinPasso", "spinR0", "spinEscalaContagio",
                 "comboKernel", "checkExcluirCentro", "spinAlertaPeso",
                 "spinAlertaEscala", "spinAlertaTau", "checkSemeia",
                 "spinSemente", "spinFiscMax", "spinFiscEscala",
                 "spinFiscSubida", "spinFiscPlato", "spinFiscQueda",
                 "comboCombinacao", "linePasta", "linePrefixo",
                 "checkMuFinal", "checkTaxa", "checkCampos", "checkSerie",
                 "spinPassoSerie", "checkCarregar", "dateInicio", "dateFim"]
        return {nome: getattr(self, nome) for nome in nomes
                if hasattr(self, nome)}

    # -------------------------------------------------------------- utilidades
    def _registrar(self, mensagem):
        self.textoLog.appendPlainText(str(mensagem))

    def _etapa(self, mensagem):
        self.lblStatus.setText(str(mensagem))

    def closeEvent(self, evento):  # noqa: N802
        if self.tarefa is not None:
            resposta = QMessageBox.question(
                self, "Simulação em andamento",
                "Cancelar a simulação e fechar?",
                QMessageBox.Yes | QMessageBox.No)
            if resposta != QMessageBox.Yes:
                evento.ignore()
                return
            self.tarefa.cancel()
        self._salvar_preferencias()
        super(RiscoGarimpoDialog, self).closeEvent(evento)


# ------------------------------------------------------------------ auxiliares
def _para_data(qdate):
    return _dt.date(qdate.year(), qdate.month(), qdate.day())


def _valor_widget(widget):
    if isinstance(widget, QDateEdit):
        return widget.date().toString(Qt.ISODate)
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
        return widget.value()
    if isinstance(widget, QComboBox):
        return widget.currentText()
    if isinstance(widget, QLineEdit):
        return widget.text()
    return None


def _definir_widget(widget, valor):
    try:
        if isinstance(widget, QDateEdit):
            data = QDate.fromString(str(valor), Qt.ISODate)
            if data.isValid():
                widget.setDate(data)
        elif isinstance(widget, QCheckBox):
            widget.setChecked(str(valor).lower() in ("true", "1"))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(valor))
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(float(valor)))
        elif isinstance(widget, QComboBox):
            indice = widget.findText(str(valor))
            if indice >= 0:
                widget.setCurrentIndex(indice)
        elif isinstance(widget, QLineEdit):
            widget.setText(str(valor))
    except Exception:  # noqa: BLE001 - preferência inválida não trava a UI
        pass


def _texto_diagnostico(diagnostico):
    if not diagnostico:
        return ""
    ordem = ["n_passos", "passo_dias", "pixel_m", "raio_kernel_px",
             "area_valida_px", "frentes", "janelas_alertas",
             "janelas_fiscalizacao", "mu_inicial_total", "mu_final_total",
             "expansao_total_px_equivalente"]
    linhas = ["", "Diagnóstico:"]
    for chave in ordem:
        if chave in diagnostico:
            linhas.append("  %-32s %s" % (chave, _formatar(diagnostico[chave])))
    return "\n".join(linhas)


def _formatar(valor):
    if isinstance(valor, float):
        return "%.4g" % valor
    return str(valor)


def _hectares(diagnostico, grade):
    if not diagnostico or grade is None:
        return 0.0
    total = diagnostico.get("expansao_total_px_equivalente", 0.0)
    return float(total) * (grade.pixel_m ** 2) / 10000.0


def _topo_subbacias(tabela, quantas=10):
    if not tabela:
        return ""
    ordenada = sorted(tabela, key=lambda l: -l["rg_exp_ha"])[:quantas]
    linhas = ["", "Sub-bacias com maior expansão prevista:"]
    for linha in ordenada:
        linhas.append("  %2d. %-24s %10.2f ha  (estoque inicial %.2f ha)"
                      % (linha["rg_rank"], str(linha["id"])[:24],
                         linha["rg_exp_ha"], linha["rg_mu0_ha"]))
    return "\n".join(linhas)

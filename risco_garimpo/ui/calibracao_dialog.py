# -*- coding: utf-8 -*-
"""Diálogo da calibração: sugere os parâmetros dos dois modelos.

É a calibração noturna dentro do plugin, em segundo plano e com o QGIS
usável. A aba do modelo estático busca pesos e γ; a do dinâmico, r₀ e a
escala de contágio. Cada uma grava, na sua pasta, resultados.csv (retomável),
resumo.txt e sugestao.json; "Usar sugestão" leva os valores ao diálogo do
modelo. A versão em vários processos, para computadores de alto
desempenho, fica no repositório (tools/calibrar_paralelo.py) e usa o mesmo
motor e a mesma pasta.
"""

import json
import os

from qgis.core import (QgsApplication, QgsProject, QgsRasterLayer,
                       QgsSettings, QgsVectorLayer)
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QDialog, QFileDialog, QMessageBox

from ..core import calibracao_dinamica, calibracao_estatica
from .main_dialog import (_botao, _definir_widget, _para_data,
                          _valor_widget)
from .tarefa_calibracao import TarefaCalibracao

CAMINHO_UI = os.path.join(os.path.dirname(__file__),
                          "calibracao_dialog_base.ui")
FORM_CLASS, _ = uic.loadUiType(CAMINHO_UI)

SEM_CAMADA = "— nenhuma —"
GRUPO_CONFIG = "risco_garimpo/calibracao"
PERSISTENTES = (
    "linhaEstPasta", "linhaDinPasta", "linhaDinTreino", "spinEstResolucao",
    "spinEstEscalaP", "spinEstTolerancia", "spinEstHoras", "spinEstPesoPG",
    "spinEstPesoEst", "spinEstPesoLt", "spinEstGama", "spinEstPiso",
    "spinEstJanela", "spinEstQuantil", "spinEstFaixas", "spinEstMinimo",
    "dateDinInicio", "dateDinFim", "spinDinResolucao", "spinDinAleatorias",
    "spinDinSemente", "checkDinEstrutura", "spinDinHoras")


class CalibracaoDialog(QDialog, FORM_CLASS):
    """Calibração dos modelos estático e dinâmico."""

    def __init__(self, iface, parent=None, aplicar=None):
        super().__init__(parent)
        self.setupUi(self)
        self.iface = iface
        # aplicar(modelo, sugestao) -> lista de alterações (o plugin leva os
        # valores ao diálogo do modelo); sem ele, o botão só mostra a sugestão
        self.aplicar = aplicar
        self.tarefa = None
        self._modelo_em_curso = None
        self._executado = None
        self._ultimo_modelo = "estatico"

        self.btnExecutar.clicked.connect(self._executar)
        self.btnCancelar.clicked.connect(self._cancelar)
        self.btnFechar.clicked.connect(self.close)
        self.btnResumo.clicked.connect(self._abrir_resumo)
        self.btnSugestao.clicked.connect(self._usar_sugestao)
        self.btnForaDaAmostra.clicked.connect(self._fora_da_amostra)
        self.btnEstPasta.clicked.connect(
            lambda: self._escolher_pasta(self.linhaEstPasta))
        self.btnDinPasta.clicked.connect(
            lambda: self._escolher_pasta(self.linhaDinPasta))
        self.btnDinTreino.clicked.connect(
            lambda: self._escolher_pasta(self.linhaDinTreino))
        self.abas.currentChanged.connect(self._trocou_de_aba)
        self.comboDinAlertas.currentIndexChanged.connect(
            lambda _i: self._preencher_campos(self.comboDinAlertas,
                                              self.comboDinCampoAlertas))
        self.comboDinAutos.currentIndexChanged.connect(
            lambda _i: self._preencher_campos(self.comboDinAutos,
                                              self.comboDinCampoAutos))
        for linha in (self.linhaEstPasta, self.linhaDinPasta):
            linha.textChanged.connect(lambda _t: self._atualizar_botoes())

        self.recarregar()
        self._restaurar_preferencias()
        self._atualizar_botoes()

    # ------------------------------------------------------------- camadas
    def recarregar(self):
        rasters, vetores = [], []
        for camada in QgsProject.instance().mapLayers().values():
            if isinstance(camada, QgsRasterLayer) and camada.isValid():
                rasters.append(camada)
            elif isinstance(camada, QgsVectorLayer) and camada.isValid():
                vetores.append(camada)
        rasters.sort(key=lambda c: c.name().lower())
        vetores.sort(key=lambda c: c.name().lower())
        for combo, opcional in ((self.comboCalPGgeom, True),
                                (self.comboCalEst, True),
                                (self.comboCalLt, True),
                                (self.comboCalAgua, True),
                                (self.comboEstMuAntigo, True),
                                (self.comboEstMuInicial, False),
                                (self.comboEstMuFinal, False),
                                (self.comboEstMascara, True),
                                (self.comboEstAvaliacao, True),
                                (self.comboDinK, False),
                                (self.comboDinMuInicial, False),
                                (self.comboDinMuFinal, False),
                                (self.comboDinMascara, True),
                                (self.comboDinAvaliacao, True)):
            self._preencher(combo, rasters, opcional)
        for combo, campo in ((self.comboDinAlertas, self.comboDinCampoAlertas),
                             (self.comboDinAutos, self.comboDinCampoAutos)):
            self._preencher(combo, vetores, True)
            self._preencher_campos(combo, campo)

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

    def _fonte(self, combo):
        camada = self._camada(combo)
        return camada.source().split("|")[0] if camada is not None else None

    def _preencher_campos(self, combo_camada, combo_campo):
        from .main_dialog import RiscoGarimpoDialog
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
            provavel = RiscoGarimpoDialog._campo_provavel(camada)
            if provavel:
                combo_campo.setCurrentIndex(combo_campo.findText(provavel))

    # ------------------------------------------------------------ interface
    def _escolher_pasta(self, linha):
        pasta = QFileDialog.getExistingDirectory(self, "Pasta",
                                                 linha.text() or "")
        if pasta:
            linha.setText(pasta)

    def _trocou_de_aba(self, _indice):
        modelo = self._modelo_da_aba()
        if modelo:
            self._ultimo_modelo = modelo
        self._atualizar_botoes()

    def _modelo_da_aba(self):
        atual = self.abas.currentWidget()
        if atual is self.abaEstatico:
            return "estatico"
        if atual is self.abaDinamico:
            return "dinamico"
        return None

    def modelo(self):
        """O modelo da aba aberta (na aba Registro, o último escolhido)."""
        return self._modelo_em_curso or self._modelo_da_aba() or \
            self._ultimo_modelo

    def _pasta(self, modelo=None):
        linha = (self.linhaEstPasta if (modelo or self.modelo()) == "estatico"
                 else self.linhaDinPasta)
        return linha.text().strip()

    def _arquivo(self, nome, modelo=None):
        pasta = self._pasta(modelo)
        return os.path.join(pasta, nome) if pasta else ""

    def _atualizar_botoes(self):
        ocupado = self.tarefa is not None
        self.btnExecutar.setEnabled(not ocupado)
        self.btnCancelar.setEnabled(ocupado)
        self.btnResumo.setEnabled(os.path.isfile(self._arquivo("resumo.txt")))
        self.btnSugestao.setEnabled(not ocupado and os.path.isfile(
            self._arquivo("sugestao.json")))
        self.btnForaDaAmostra.setEnabled(not ocupado
                                         and self.modelo() == "dinamico")

    def _registrar(self, mensagem):
        self.textoLog.appendPlainText(str(mensagem))

    # ---------------------------------------------------------- configuração
    def config_estatica(self):
        return {
            "PGGEOM": self._fonte(self.comboCalPGgeom),
            "EST": self._fonte(self.comboCalEst),
            "LT": self._fonte(self.comboCalLt),
            "AGUA": self._fonte(self.comboCalAgua),
            "MU_ANTIGO": self._fonte(self.comboEstMuAntigo),
            "MU_INICIAL": self._fonte(self.comboEstMuInicial),
            "MU_FINAL": self._fonte(self.comboEstMuFinal),
            "MASCARA": self._fonte(self.comboEstMascara),
            "MASCARA_AVALIACAO": self._fonte(self.comboEstAvaliacao),
            "RESOLUCAO_M": self.spinEstResolucao.value(),
            "ESCALA_P_M": self.spinEstEscalaP.value(),
            "TOLERANCIA_PP": self.spinEstTolerancia.value(),
            "HORAS_MAXIMAS": self.spinEstHoras.value(),
            "PESOS_ATUAIS": (self.spinEstPesoPG.value(),
                             self.spinEstPesoEst.value(),
                             self.spinEstPesoLt.value()),
            "GAMA_ATUAL": self.spinEstGama.value(),
            "PISO": self.spinEstPiso.value(),
            "JANELA_M": self.spinEstJanela.value(),
            "QUANTIL": self.spinEstQuantil.value(),
            "N_FAIXAS": self.spinEstFaixas.value(),
            "MINIMO_POR_FAIXA": self.spinEstMinimo.value(),
            "PASTA_SAIDA": self.linhaEstPasta.text().strip() or None,
        }

    def config_dinamica(self):
        alertas = self._camada(self.comboDinAlertas)
        autos = self._camada(self.comboDinAutos)
        return {
            "K": self._fonte(self.comboDinK),
            "MU_INICIAL": self._fonte(self.comboDinMuInicial),
            "MU_FINAL": self._fonte(self.comboDinMuFinal),
            "MASCARA": self._fonte(self.comboDinMascara),
            "MASCARA_AVALIACAO": self._fonte(self.comboDinAvaliacao),
            "ALERTAS": alertas,
            "CAMPO_DATA_ALERTAS": (self.comboDinCampoAlertas.currentText()
                                   or "data"),
            "AUTOS": autos,
            "CAMPO_DATA_AUTOS": self.comboDinCampoAutos.currentText() or "data",
            "DATA_INICIAL": _para_data(self.dateDinInicio.date()).isoformat(),
            "DATA_FINAL": _para_data(self.dateDinFim.date()).isoformat(),
            "RESOLUCAO_M": self.spinDinResolucao.value(),
            "N_ALEATORIAS": self.spinDinAleatorias.value(),
            "SEMENTE": self.spinDinSemente.value(),
            "ESTRUTURA": self.checkDinEstrutura.isChecked(),
            "HORAS_MAXIMAS": self.spinDinHoras.value(),
            "PASTA_SAIDA": self.linhaDinPasta.text().strip() or None,
        }

    # -------------------------------------------------------------- execução
    def _executar(self):
        modelo = self.modelo()
        try:
            if modelo == "estatico":
                motor = calibracao_estatica.CalibracaoEstatica(
                    self.config_estatica(), registrar=self._registrar)
                motor.validar()
                titulo = "Calibração do modelo estático"
            else:
                motor = calibracao_dinamica.CalibracaoDinamica(
                    self.config_dinamica(), registrar=self._registrar)
                # thread principal: lê os eventos das camadas do projeto
                motor.preparar_eventos()
                titulo = "Calibração do modelo dinâmico"
        except Exception as erro:  # noqa: BLE001 - erro de configuração
            QMessageBox.warning(self, "Configuração", str(erro))
            return
        self._salvar_preferencias()
        self._modelo_em_curso = modelo
        self.abas.setCurrentWidget(self.abaRegistro)
        self.lblStatus.setText("Calibrando…")
        self.barraProgresso.setValue(0)
        self.tarefa = TarefaCalibracao(motor, titulo)
        self.tarefa.registro.connect(self._registrar)
        self.tarefa.progressChanged.connect(
            lambda v: self.barraProgresso.setValue(int(v)))
        self.tarefa.taskCompleted.connect(self._concluir)
        self.tarefa.taskTerminated.connect(self._falhar)
        self._atualizar_botoes()
        QgsApplication.taskManager().addTask(self.tarefa)

    def _cancelar(self):
        if self.tarefa is not None:
            self.tarefa.cancel()

    def _concluir(self):
        feitas = self.tarefa.feitas if self.tarefa is not None else 0
        self._restaurar("Concluída (%d nesta sessão)." % feitas)
        sugestao = self._ler_sugestao(self._ultimo_executado())
        if sugestao is not None:
            self._registrar("Sugestão gravada em %s. Use \"Usar sugestão\" "
                            "para levá-la ao modelo."
                            % self._arquivo("sugestao.json",
                                            self._ultimo_executado()))

    def _falhar(self):
        if self.tarefa is not None and self.tarefa.excecao is not None:
            self._registrar(self.tarefa.detalhe)
            erro = str(self.tarefa.excecao)
            self._restaurar("Falhou.")
            QMessageBox.critical(self, "Erro", erro)
        else:
            self._restaurar("Cancelada.")

    def _ultimo_executado(self):
        return self._executado or self.modelo()

    def _restaurar(self, status):
        self._executado = self._modelo_em_curso
        self._modelo_em_curso = None
        self.tarefa = None
        self.lblStatus.setText(status)
        self._atualizar_botoes()

    # ------------------------------------------------------------- resultados
    def _abrir_resumo(self):
        caminho = self._arquivo("resumo.txt")
        if os.path.isfile(caminho):
            QDesktopServices.openUrl(QUrl.fromLocalFile(caminho))

    def _ler_sugestao(self, modelo=None):
        caminho = self._arquivo("sugestao.json", modelo)
        if not caminho or not os.path.isfile(caminho):
            return None
        with open(caminho, encoding="utf-8") as arquivo:
            return json.load(arquivo)

    def _usar_sugestao(self):
        modelo = self.modelo()
        try:
            sugestao = self._ler_sugestao(modelo)
        except (OSError, ValueError) as erro:
            QMessageBox.warning(self, "Sugestão", str(erro))
            return
        if sugestao is None or sugestao.get("modelo") != modelo:
            QMessageBox.warning(self, "Sugestão",
                                "Não há sugestão deste modelo na pasta.")
            return
        if self.aplicar is None:
            QMessageBox.information(self, "Sugestão",
                                    json.dumps(sugestao, ensure_ascii=False,
                                               indent=2))
            return
        alteracoes = self.aplicar(modelo, sugestao)
        nome = ("modelo estático" if modelo == "estatico"
                else "modelo dinâmico")
        self._registrar("Sugestão levada ao %s: %s"
                        % (nome, "; ".join(alteracoes)))
        QMessageBox.information(
            self, "Sugestão",
            "Valores levados ao diálogo do %s:\n\n%s"
            % (nome, "\n".join(alteracoes)))

    def _fora_da_amostra(self):
        treino = self.linhaDinTreino.text().strip()
        teste = self.linhaDinPasta.text().strip()
        try:
            for pasta, papel in ((treino, "de treino"), (teste, "de saída")):
                if not pasta or not os.path.isfile(
                        os.path.join(pasta, "resultados.csv")):
                    raise ValueError("a pasta %s não tem resultados.csv: %s"
                                     % (papel, pasta or "—"))
            texto = calibracao_dinamica.fora_da_amostra(treino, teste)
        except Exception as erro:  # noqa: BLE001 - relatado à interface
            QMessageBox.warning(self, "Fora da amostra", str(erro))
            return
        self._registrar(texto)
        self.abas.setCurrentWidget(self.abaRegistro)

    # ------------------------------------------------------------ preferências
    def _salvar_preferencias(self):
        cfg = QgsSettings()
        cfg.beginGroup(GRUPO_CONFIG)
        for nome in PERSISTENTES:
            cfg.setValue(nome, _valor_widget(getattr(self, nome)))
        cfg.endGroup()

    def _restaurar_preferencias(self):
        cfg = QgsSettings()
        cfg.beginGroup(GRUPO_CONFIG)
        for nome in PERSISTENTES:
            valor = cfg.value(nome, None)
            if valor is not None:
                _definir_widget(getattr(self, nome), valor)
        cfg.endGroup()

    def closeEvent(self, evento):  # noqa: N802 - Qt
        if self.tarefa is not None:
            resposta = QMessageBox.question(
                self, "Calibração em andamento",
                "Cancelar a calibração e fechar? O que já foi feito fica no "
                "resultados.csv e é retomado na próxima vez.",
                _botao("Yes") | _botao("No"))
            if resposta != _botao("Yes"):
                evento.ignore()
                return
            self.tarefa.cancel()
        self._salvar_preferencias()
        super().closeEvent(evento)

    def showEvent(self, evento):  # noqa: N802 - Qt
        super().showEvent(evento)
        self.recarregar()


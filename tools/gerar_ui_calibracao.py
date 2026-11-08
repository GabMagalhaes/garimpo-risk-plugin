# -*- coding: utf-8 -*-
"""Gera ui/calibracao_dialog_base.ui — a calibração dos dois modelos.

Mesma convenção das outras ferramentas (primitivas de gerar_ui.py, só
widgets padrão do Qt). O .ui gerado é versionado; rodar este script é
opcional.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gerar_ui import (aba_xml, campo, espacador, grupo, item,  # noqa: E402
                      layout, prop, rotulo, widget)

SAIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "risco_garimpo", "ui", "calibracao_dialog_base.ui")

MAPA = ("Mapa anual como vem (ex.: MapBiomas, só a classe de garimpo): "
        "1 = garimpo, 0 = não, sem suavizar.")


def _pasta(nome, dica):
    return campo("linha_arquivo", nome, "Pasta de saída", dica,
                 botao="btn" + nome[len("linha"):])


def _estatico():
    return [
        grupo("grupoEstEvidencias", "Evidências",
              [campo("combo", "comboCalPGgeom", "PGgeom",
                     "Potencial geológico (a mesma camada do modelo "
                     "estático)."),
               campo("combo", "comboCalEst", "Est (opcional)",
                     "Evidência estrutural."),
               campo("combo", "comboCalLt", "Lt (opcional)",
                     "Evidência litológica."),
               campo("combo", "comboCalAgua", "Estratos (opcional)",
                     "Raster > 0 no leito dos rios: o nível de K é estimado "
                     "à parte em água e em terra, como no modelo estático.")],
              nota="Para cada combinação de pesos (passo de 0,1, incluindo "
                   "as que zeram uma ou duas evidências) e de γ (0,2 a 0,9), "
                   "gera K com o garimpo da data inicial e mede o garimpo "
                   "novo até a data final no topo de P × (K − O₀). Na "
                   "Província a 150 m, 1–2 s por combinação (~15 min ao "
                   "todo)."),
        grupo("grupoEstGarimpo", "Mapas de garimpo (0/1) e recortes",
              [campo("combo", "comboEstMuAntigo",
                     "Garimpo — ano mais antigo (opcional)",
                     "Define os pixels maduros (exposição longa) do nível. "
                     "Deve ser anterior à data inicial. " + MAPA),
               campo("combo", "comboEstMuInicial",
                     "Garimpo — data inicial",
                     "O K de cada combinação usa só este mapa (e o mais "
                     "antigo). " + MAPA),
               campo("combo", "comboEstMuFinal", "Garimpo — data final",
                     "Só avalia: o garimpo novo entre as duas datas. " + MAPA),
               campo("combo", "comboEstMascara", "Área de cálculo (opcional)",
                     "Onde K é gerado (ex.: a Província). Valores > 0."),
               campo("combo", "comboEstAvaliacao",
                     "Área de avaliação (opcional)",
                     "Onde as métricas são medidas (ex.: a APA). Vazio usa "
                     "a área de cálculo.")]),
        grupo("grupoEstBusca", "Busca e avaliação",
              [campo("double", "spinEstResolucao", "Resolução da busca",
                     "A busca roda numa grade grossa; o K definitivo se gera "
                     "depois, na resolução final, pelo modelo estático.",
                     valor=150.0, minimo=10.0, maximo=5000.0, passo=10.0,
                     decimais=0, sufixo="m"),
               campo("double", "spinEstEscalaP", "Escala de P",
                     "Núcleo da proximidade ao garimpo da data inicial (a do "
                     "experimento K).",
                     valor=200.0, minimo=10.0, maximo=5000.0, passo=50.0,
                     decimais=0, sufixo="m"),
               campo("double", "spinEstTolerancia", "Tolerância da escolha",
                     "Entre as combinações a menos disto da melhor captura "
                     "nos 5%, sugere a de menos evidências.",
                     valor=1.0, minimo=0.0, maximo=20.0, passo=0.5,
                     decimais=1, sufixo="pp"),
               campo("double", "spinEstHoras", "Parar depois de",
                     "Limite de tempo; rodar de novo retoma de onde parou.",
                     valor=11.0, minimo=0.1, maximo=200.0, passo=1.0,
                     decimais=1, sufixo="h")]),
        grupo("grupoEstAtual", "K de referência e parâmetros fixos",
              [campo("double", "spinEstPesoPG", "Peso atual de PGgeom", "",
                     valor=0.591, maximo=1.0, passo=0.01, decimais=3),
               campo("double", "spinEstPesoEst", "Peso atual de Est", "",
                     valor=0.075, maximo=1.0, passo=0.01, decimais=3),
               campo("double", "spinEstPesoLt", "Peso atual de Lt", "",
                     valor=0.334, maximo=1.0, passo=0.01, decimais=3),
               campo("double", "spinEstGama", "γ atual", "",
                     valor=0.4, maximo=1.0, passo=0.05, decimais=2),
               campo("double", "spinEstPiso", "Piso das variáveis", "",
                     valor=0.008, maximo=0.5, passo=0.001, decimais=4),
               campo("double", "spinEstJanela", "Janela de ocupação", "",
                     valor=380.0, minimo=30.0, maximo=5000.0, passo=10.0,
                     decimais=0, sufixo="m"),
               campo("double", "spinEstQuantil", "Quantil do nível", "",
                     valor=0.99, minimo=0.5, maximo=1.0, passo=0.005,
                     decimais=3),
               campo("int", "spinEstFaixas", "Faixas de escore", "",
                     valor=51, minimo=5, maximo=500),
               campo("int", "spinEstMinimo", "Mínimo por faixa", "",
                     valor=200, minimo=1, maximo=100000)],
              nota="O K com os pesos atuais entra como referência, ao lado "
                   "de P sozinho e de K = 1. Piso, janela, quantil e faixas "
                   "ficam fixos durante a busca (os do modelo estático)."),
        grupo("grupoEstSaida", "Onde gravar",
              [_pasta("linhaEstPasta",
                      "Uma pasta por busca: resultados.csv, resumo.txt, "
                      "sugestao.json e calibracao.log. Rodar de novo na "
                      "mesma pasta retoma de onde parou.")]),
    ]


def _dinamico():
    return [
        grupo("grupoDinRasters", "Capacidade e mapas de garimpo (0/1)",
              [campo("combo", "comboDinK", "K",
                     "A saída do modelo estático."),
               campo("combo", "comboDinMuInicial", "Garimpo — data inicial",
                     MAPA),
               campo("combo", "comboDinMuFinal", "Garimpo — data final",
                     MAPA),
               campo("combo", "comboDinMascara",
                     "Área simulada (opcional)",
                     "A área de estudo e uma faixa em volta (ex.: 5 km), "
                     "para que o entorno alimente o contágio na borda."),
               campo("combo", "comboDinAvaliacao",
                     "Área de avaliação (opcional)",
                     "Onde as métricas são medidas (ex.: a área de estudo). "
                     "Vazio usa a área simulada.")]),
        grupo("grupoDinEventos", "Eventos (opcionais)",
              [campo("combo", "comboDinAlertas", "Alertas", ""),
               campo("combo", "comboDinCampoAlertas", "Campo de data", ""),
               campo("combo", "comboDinAutos", "Autos de infração", ""),
               campo("combo", "comboDinCampoAutos", "Campo de data", "")]),
        grupo("grupoDinPeriodo", "Período e plano",
              [campo("date", "dateDinInicio", "Data inicial",
                     "Data do mapa de garimpo inicial.", valor=(2022, 12, 31)),
               campo("date", "dateDinFim", "Data final",
                     "Data do mapa de garimpo final.", valor=(2024, 12, 31)),
               campo("double", "spinDinResolucao", "Resolução",
                     "Grade das simulações. 150 m na Província cabe numa "
                     "noite com o plano completo.",
                     valor=150.0, minimo=10.0, maximo=5000.0, passo=10.0,
                     decimais=0, sufixo="m"),
               campo("int", "spinDinAleatorias", "Combinações aleatórias",
                     "Use o mesmo número e a mesma semente nos dois períodos "
                     "para o teste fora da amostra.",
                     valor=400, minimo=0, maximo=10000),
               campo("int", "spinDinSemente", "Semente", "",
                     valor=2026, minimo=0, maximo=2 ** 31 - 1),
               campo("check", "checkDinEstrutura", "", "",
                     rotulo="Etapa estrutura (escala de contágio × fator de "
                            "K): é dela que sai a escala sugerida",
                     valor=True),
               campo("double", "spinDinHoras", "Parar depois de",
                     "Limite de tempo; rodar de novo retoma de onde parou.",
                     valor=11.0, minimo=0.1, maximo=200.0, passo=1.0,
                     decimais=1, sufixo="h")],
              nota="Plano da calibração noturna: r₀ pela área, linhas de "
                   "base, sensibilidade, fiscalização, estrutura e "
                   "aleatórias (~505 simulações). Sugere r₀ e a escala de "
                   "contágio pela captura nos 5% no modo previsão."),
        grupo("grupoDinSaida", "Onde gravar",
              [_pasta("linhaDinPasta",
                      "Uma pasta por período: resultados.csv, resumo.txt, "
                      "sugestao.json e calibracao.log. A mesma pasta do "
                      "calibrar_paralelo.py continua a rodada dele.")]),
        grupo("grupoDinFora", "Teste fora da amostra (opcional)",
              [campo("linha_arquivo", "linhaDinTreino",
                     "Pasta do período de treino",
                     "Uma calibração de outro período, com o mesmo plano. "
                     "Os parâmetros são escolhidos lá e medidos na pasta "
                     "acima.", botao="btnDinTreino")],
              nota="Grava fora_da_amostra.txt na pasta de saída; não "
                   "simula nada."),
    ]


def construir():
    abas = [
        aba_xml("abaEstatico", "Modelo estático (pesos e γ)", _estatico()),
        aba_xml("abaDinamico", "Modelo dinâmico (r₀ e contágio)",
                _dinamico()),
        aba_xml("abaRegistro", "Registro", [
            widget("QGroupBox", "grupoLogCalibracao",
                   [prop("title", "string", "Registro")],
                   [layout("QVBoxLayout", "layoutv_grupoLogCalibracao",
                           [item(widget("QPlainTextEdit", "textoLog",
                                        [prop("readOnly", "bool", True),
                                         prop("lineWrapMode", "enum",
                                              "QPlainTextEdit::NoWrap")]))])])
        ], com_espacador=False),
    ]
    # as duas primeiras abas rolam: os grupos não cabem na altura
    rolaveis = []
    for aba, nome in zip(abas[:2], ("abaEstatico", "abaDinamico")):
        inicio = aba.index("<layout")
        fim = aba.rindex("</widget>")
        interno = ('<widget class="QWidget" name="conteudo_%s">\n%s\n</widget>'
                   % (nome, aba[inicio:fim]))
        area = widget("QScrollArea", "rolagem_%s" % nome,
                      [prop("widgetResizable", "bool", True),
                       prop("frameShape", "enum", "QFrame::NoFrame")],
                      [interno])
        rolaveis.append(aba[:inicio]
                        + layout("QVBoxLayout", "layout_rolagem_%s" % nome,
                                 [item(area)])
                        + "\n</widget>")
    abas = rolaveis + abas[2:]

    tabwidget = widget("QTabWidget", "abas",
                       [prop("currentIndex", "number", 0)], abas)
    barra = widget("QProgressBar", "barraProgresso",
                   [prop("value", "number", 0),
                    prop("textVisible", "bool", True)])
    botoes = layout("QHBoxLayout", "layoutBotoes", [
        item(rotulo("lblStatus", "Pronto.")),
        espacador("espBotoes", vertical=False),
        item(widget("QPushButton", "btnForaDaAmostra",
                    [prop("text", "string", "Fora da amostra"),
                     prop("toolTip", "string",
                          "Escolhe no período de treino e mede neste "
                          "(aba do modelo dinâmico).")])),
        item(widget("QPushButton", "btnResumo",
                    [prop("text", "string", "Abrir resumo")])),
        item(widget("QPushButton", "btnSugestao",
                    [prop("text", "string", "Usar sugestão"),
                     prop("toolTip", "string",
                          "Leva os valores sugeridos ao diálogo do modelo.")])),
        item(widget("QPushButton", "btnExecutar",
                    [prop("text", "string", "Calibrar"),
                     prop("default", "bool", True)])),
        item(widget("QPushButton", "btnCancelar",
                    [prop("text", "string", "Cancelar"),
                     prop("enabled", "bool", False)])),
        item(widget("QPushButton", "btnFechar",
                    [prop("text", "string", "Fechar")])),
    ])
    principal = layout("QVBoxLayout", "layoutPrincipal",
                       [item(tabwidget), item(barra), item(botoes)])
    dialogo = widget(
        "QDialog", "CalibracaoDialogBase",
        [prop("geometry", "rect", ""),
         prop("windowTitle", "string",
              "Risco Garimpo — Calibração dos modelos")],
        [principal])
    dialogo = dialogo.replace(
        '<property name="geometry"><rect></rect></property>',
        '<property name="geometry"><rect><x>0</x><y>0</y>'
        '<width>900</width><height>760</height></rect></property>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ui version="4.0">\n'
            ' <class>CalibracaoDialogBase</class>\n'
            '%s\n'
            ' <resources/>\n'
            ' <connections/>\n'
            '</ui>\n' % dialogo)


if __name__ == "__main__":
    xml = construir()
    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as arquivo:
        arquivo.write(xml)
    print("gravado:", SAIDA, len(xml), "bytes")

# -*- coding: utf-8 -*-
"""Gera ui/estatico_dialog_base.ui — a segunda ferramenta do plugin.

Reaproveita as primitivas de gerar_ui.py, de modo que as duas interfaces
sigam a mesma convenção: só widgets padrão do Qt, abrem no Qt Designer puro
e carregam com qualquer PyQt5/PyQt6. O .ui gerado é versionado; rodar este
script é opcional.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gerar_ui import (aba_xml, campo, espacador, grupo, item,  # noqa: E402
                      layout, prop, rotulo, widget)

SAIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "risco_garimpo", "ui", "estatico_dialog_base.ui")


def construir():
    insumos = [
        grupo("grupoVariaveis", "Variáveis de terreno",
              [campo("combo", "comboIBx",
                     "PGgeom — potencial geomorfológico (baixões)",
                     "Raster contínuo. Se você tiver o IBx cru, aplique "
                     "antes a transformação calibrada."),
               campo("combo", "comboEst",
                     "Est — favorabilidade estrutural",
                     "Proximidade a lineamentos. Meça o poder discriminante "
                     "antes de incluir: contraste sem raridade não "
                     "seleciona território."),
               campo("combo", "comboLito",
                     "Lt — favorabilidade litológica (opcional)",
                     "Prefira a litologia por pixel. Média por sub-bacia "
                     "dilui o sinal.")],
              nota="Variáveis de TERRENO apenas. O garimpo pretérito (μ) "
                   "não entra aqui: ele calibra o nível, nunca a ordenação."),
        grupo("grupoEstado", "Ocupação observada e recorte",
              [campo("combo", "comboMu",
                     "μ — garimpo observado na data de referência",
                     "Usado para estimar o NÍVEL de saturação por classe de "
                     "terreno. Não entra na ordenação."),
               campo("combo", "comboMuAntigo",
                     "μ do ano mais antigo da série (maturidade)",
                     "Define quais pixels tiveram exposição longa ao "
                     "processo. Sem exposição não há saturação observável."),
               campo("combo", "comboMuAnterior",
                     "μ do ano anterior (variação no último ano)",
                     "Usado só para a coluna de variação e para projetar "
                     "em quantos anos o remanescente se esgota no ritmo "
                     "atual."),
               campo("combo", "comboMascara",
                     "Máscara da área de estudo",
                     "Valores > 0 delimitam a área. Furos (corpos d'água) "
                     "são simplesmente excluídos do universo."),
               campo("combo", "comboAgua",
                     "Corpos d'água — estrato (opcional)",
                     "Tratados como ESTRATO, não como máscara: o leito "
                     "recebe nível próprio, porque garimpo de balsa não "
                     "obedece à geomorfologia de barranco.")]),
        grupo("grupoSubbacias", "Sub-bacias — unidade de saída",
              [campo("combo", "comboSubbacias",
                     "Camada de sub-bacias (HydroBASINS nível 12)",
                     "Opcional. Gera a tabela de Potencial Aurífero "
                     "Remanescente por sub-bacia."),
               campo("line", "linhaCampoId", "Campo identificador",
                     "Ex.: HYBAS_ID. Vazio usa o id interno da feição.",
                     placeholder="HYBAS_ID")],
              nota="A sub-bacia é unidade de SAÍDA. A combinação e a "
                   "subtração acontecem no pixel; só o resultado é somado "
                   "aqui. Somar conserva a grandeza; tirar média das "
                   "variáveis destruiria o sinal."),
        grupo("grupoGrade", "Grade de trabalho",
              [campo("combo", "comboCRS",
                     "CRS de referência",
                     "Precisa ser projetado, em metros."),
               campo("double", "spinResolucao", "Resolução",
                     "30 m é a resolução nativa do MapBiomas. A errata do "
                     "fluxograma registra erro de modelagem a 100 m.",
                     valor=30.0, minimo=1.0, maximo=5000.0, passo=10.0,
                     decimais=1, sufixo="m"),
               campo("line", "lblGrade", "Grade resultante",
                     "Tamanho em pixels e memória estimada.",
                     placeholder="—")]),
    ]

    parametros = [
        grupo("grupoPesos", "Pesos e combinação",
              [campo("double", "spinPesoIBx", "Peso de PGgeom",
                     "Peso AHP. São normalizados para somar 1.",
                     valor=0.591, maximo=1.0, passo=0.01, decimais=3),
               campo("double", "spinPesoEst", "Peso de Est",
                     "Peso AHP.", valor=0.075, maximo=1.0, passo=0.01,
                     decimais=3),
               campo("double", "spinPesoLito", "Peso de Lt",
                     "Peso AHP. Ignorado se a camada não for informada.",
                     valor=0.334, maximo=1.0, passo=0.01, decimais=3),
               campo("double", "spinGamma", "γ do fuzzy gamma",
                     "0 = produto (conjuntivo, pessimista); 1 = soma "
                     "algébrica (disjuntivo). Valores baixos preservam "
                     "sinergia relativamente a gammas altos.",
                     valor=0.4, maximo=1.0, passo=0.05, decimais=2)]),
        grupo("grupoPisos", "Pisos — o antídoto do veto",
              [campo("double", "spinPiso", "Piso das variáveis",
                     "No produto ponderado, 0^w = 0 para qualquer peso: uma "
                     "única variável zerada anula o pixel. O piso preserva "
                     "a ordem e impede que a variável mais fraca decida "
                     "sozinha onde não pode haver garimpo.",
                     valor=0.008, maximo=0.5, passo=0.001, decimais=4),
               campo("check", "checkDiagnosticarVeto",
                     "Relatar quanto da área cada variável zeraria", "",
                     valor=True)]),
        grupo("grupoNivel", "Nível — a proporção máxima esperada",
              [campo("check", "checkEstimarNivel",
                     "Estimar o nível a partir da saturação observada", "",
                     valor=True),
               campo("double", "spinJanela", "Janela de ocupação",
                     "Escala em que a saturação é medida. O pixel isolado "
                     "não mede saturação; a vizinhança mede.",
                     valor=380.0, minimo=30.0, maximo=5000.0, passo=10.0,
                     decimais=0, sufixo="m"),
               campo("double", "spinQuantil", "Quantil de saturação",
                     "Quantil alto da ocupação local entre os pixels "
                     "maduros. É limite INFERIOR do teto: nada garante que "
                     "algum lugar já tenha saturado.",
                     valor=0.99, minimo=0.5, maximo=1.0, passo=0.01,
                     decimais=3),
               campo("int", "spinFaixas", "Faixas de escore",
                     "Número de classes de terreno na estimativa do nível.",
                     valor=51, minimo=5, maximo=201),
               campo("int", "spinMinimoFaixa", "Mínimo por faixa",
                     "Faixas com menos pixels que isto não estimam quantil "
                     "e ficam de fora do ajuste monótono.",
                     valor=200, minimo=10, maximo=100000),
               campo("double", "spinTeto", "Teto fixo (se não estimar)",
                     "Só usado com a estimativa desligada. 0,70 embute "
                     "margem de expansão de ≈6,5× sobre a ocupação "
                     "observada — é hipótese, não medida.",
                     valor=0.70, maximo=1.0, passo=0.01, decimais=3)],
              nota="K sai em [0, 1]: a proporção da área do pixel que se "
                   "espera explorada no máximo, tudo mais constante."),
    ]

    saidas = [
        grupo("grupoSaida", "Onde gravar",
              [campo("linha_arquivo", "linhaPasta", "Pasta de saída",
                     "Onde os rasters serão gravados.",
                     botao="btnPasta"),
               campo("line", "linhaPrefixo", "Prefixo dos arquivos",
                     "", valor="PMT"),
               campo("check", "checkEscore",
                     "Gravar também o escore (ordenação, sem nível)", "",
                     valor=True),
               campo("check", "checkRemanescente",
                     "Gravar o potencial remanescente (K − μ)", "",
                     valor=True),
               campo("check", "checkTabela",
                     "Gravar a tabela de saturação por faixa (CSV)", "",
                     valor=True),
               campo("check", "checkCarregar",
                     "Carregar os resultados no projeto", "", valor=True)],
              nota="Sempre gerado: <prefixo>_K.tif — a proporção máxima "
                   "esperada, entrada do modelo dinâmico. Com sub-bacias "
                   "informadas, sai também <prefixo>_subbacias.gpkg com o "
                   "potencial remanescente, o grau de explotação e o "
                   "ranking."),
    ]

    abas = [
        aba_xml("abaInsumos", "Insumos", insumos),
        aba_xml("abaParametros", "Parâmetros", parametros),
        aba_xml("abaSaidas", "Saídas", saidas),
        aba_xml("abaRegistro", "Registro", [
            widget("QGroupBox", "grupoLogEstatico",
                   [prop("title", "string", "Registro")],
                   [layout("QVBoxLayout", "layoutv_grupoLogEstatico",
                           [item(widget("QPlainTextEdit", "textoLog",
                                        [prop("readOnly", "bool", True),
                                         prop("lineWrapMode", "enum",
                                              "QPlainTextEdit::NoWrap")]))])])
        ], com_espacador=False),
    ]

    tabwidget = widget("QTabWidget", "abas",
                       [prop("currentIndex", "number", 0)], abas)

    barra = widget("QProgressBar", "barraProgresso",
                   [prop("value", "number", 0),
                    prop("textVisible", "bool", True)])

    botoes = layout("QHBoxLayout", "layoutBotoes", [
        item(rotulo("lblStatus", "Pronto.")),
        espacador("espBotoes", vertical=False),
        item(widget("QPushButton", "btnExecutar",
                    [prop("text", "string", "Gerar K"),
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
        "QDialog", "EstaticoDialogBase",
        [prop("geometry", "rect", ""),
         prop("windowTitle", "string",
              "Risco Garimpo — Modelo Estático (K)")],
        [principal])
    dialogo = dialogo.replace(
        '<property name="geometry"><rect></rect></property>',
        '<property name="geometry"><rect><x>0</x><y>0</y>'
        '<width>820</width><height>720</height></rect></property>')

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ui version="4.0">\n'
            ' <class>EstaticoDialogBase</class>\n'
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

# -*- coding: utf-8 -*-
"""Gera ui/main_dialog_base.ui a partir de uma especificação compacta.

O .ui resultante usa apenas widgets padrão do Qt (nenhum widget customizado
do QGIS), de modo que abre no Qt Designer puro e carrega com qualquer
PyQt5/PyQt6. Rodar este script é opcional: o .ui gerado é versionado.
"""

import os
from xml.sax.saxutils import escape

SAIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "risco_garimpo", "ui", "main_dialog_base.ui")


def prop(nome, tipo, valor):
    if tipo == "bool":
        valor = "true" if valor else "false"
    if tipo == "date":
        ano, mes, dia = valor
        corpo = "<date><year>%d</year><month>%d</month><day>%d</day></date>" % (
            ano, mes, dia)
        return '<property name="%s">%s</property>' % (nome, corpo)
    return '<property name="%s"><%s>%s</%s></property>' % (
        nome, tipo, escape(str(valor)), tipo)


def widget(classe, nome, propriedades=(), filhos=()):
    partes = ['<widget class="%s" name="%s">' % (classe, nome)]
    partes += list(propriedades)
    partes += list(filhos)
    partes.append("</widget>")
    return "\n".join(partes)


def item(conteudo, atributos=""):
    return "<item%s>\n%s\n</item>" % (atributos, conteudo)


def layout(classe, nome, itens, extra=()):
    partes = ['<layout class="%s" name="%s">' % (classe, nome)]
    partes += list(extra)
    partes += list(itens)
    partes.append("</layout>")
    return "\n".join(partes)


def espacador(nome, vertical=True):
    orient = "Qt::Vertical" if vertical else "Qt::Horizontal"
    w, h = (20, 40) if vertical else (40, 20)
    return item(
        '<spacer name="%s">'
        '<property name="orientation"><enum>%s</enum></property>'
        '<property name="sizeHint" stdset="0"><size><width>%d</width>'
        '<height>%d</height></size></property></spacer>' % (nome, orient, w, h))


def rotulo(nome, texto, dica=None, quebra=False):
    props = [prop("text", "string", texto)]
    if quebra:
        props.append(prop("wordWrap", "bool", True))
    if dica:
        props.append(prop("toolTip", "string", dica))
    return widget("QLabel", nome, props)


# --------------------------------------------------------------- construtores
def campo(tipo, nome, texto, dica="", **kw):
    return dict(tipo=tipo, nome=nome, texto=texto, dica=dica, **kw)


def _widget_de_campo(c):
    tipo, nome, dica = c["tipo"], c["nome"], c.get("dica", "")
    props = []
    if dica:
        props.append(prop("toolTip", "string", dica))

    if tipo == "combo":
        for texto in c.get("itens", []):
            props.append("<item>%s</item>" % prop("text", "string", texto))
        return widget("QComboBox", nome, props)

    if tipo == "check":
        props.insert(0, prop("text", "string", c.get("rotulo", "")))
        props.append(prop("checked", "bool", c.get("valor", False)))
        return widget("QCheckBox", nome, props)

    if tipo == "double":
        props += [prop("decimals", "number", c.get("decimais", 3)),
                  prop("minimum", "double", c.get("minimo", 0.0)),
                  prop("maximum", "double", c.get("maximo", 1e9)),
                  prop("singleStep", "double", c.get("passo", 0.1)),
                  prop("value", "double", c.get("valor", 0.0))]
        if c.get("sufixo"):
            props.append(prop("suffix", "string", " " + c["sufixo"]))
        return widget("QDoubleSpinBox", nome, props)

    if tipo == "int":
        props += [prop("minimum", "number", c.get("minimo", 0)),
                  prop("maximum", "number", c.get("maximo", 100000)),
                  prop("value", "number", c.get("valor", 0))]
        if c.get("sufixo"):
            props.append(prop("suffix", "string", " " + c["sufixo"]))
        return widget("QSpinBox", nome, props)

    if tipo == "line":
        if c.get("placeholder"):
            props.append(prop("placeholderText", "string", c["placeholder"]))
        if c.get("valor"):
            props.append(prop("text", "string", c["valor"]))
        return widget("QLineEdit", nome, props)

    if tipo == "date":
        props += [prop("calendarPopup", "bool", True),
                  prop("displayFormat", "string", "dd/MM/yyyy"),
                  prop("date", "date", c.get("valor", (2025, 1, 1)))]
        return widget("QDateEdit", nome, props)

    if tipo == "linha_arquivo":
        linha = widget("QLineEdit", nome, props)
        botao = widget("QToolButton", c["botao"],
                       [prop("text", "string", "…"),
                        prop("toolTip", "string", "Escolher…")])
        return layout("QHBoxLayout", "layout_" + nome,
                      [item(linha), item(botao)])

    raise ValueError("tipo de campo desconhecido: %r" % (tipo,))


def formulario(nome, campos):
    itens = []
    for linha, c in enumerate(campos):
        if c["tipo"] == "check" and not c.get("texto"):
            itens.append(item(_widget_de_campo(c),
                              ' row="%d" column="0" colspan="2"' % linha))
            continue
        itens.append(item(rotulo("lbl_" + c["nome"], c["texto"],
                                 c.get("dica"), quebra=True),
                          ' row="%d" column="0"' % linha))
        itens.append(item(_widget_de_campo(c), ' row="%d" column="1"' % linha))
    return layout("QFormLayout", nome, itens,
                  [prop("fieldGrowthPolicy", "enum",
                        "QFormLayout::AllNonFixedFieldsGrow")])


def grupo(nome, titulo, campos, nota=None):
    itens = []
    if nota:
        itens.append(item(rotulo("nota_" + nome, nota, quebra=True)))
    itens.append(item(formulario("form_" + nome, campos)))
    return widget("QGroupBox", nome, [prop("title", "string", titulo)],
                  [layout("QVBoxLayout", "layoutv_" + nome, itens)])


def aba_xml(nome, titulo, grupos, com_espacador=True):
    """Aba com <attribute name='title'> (forma exigida pelo QTabWidget)."""
    itens = [item(g) for g in grupos]
    if com_espacador:
        itens.append(espacador("esp_" + nome))
    corpo = layout("QVBoxLayout", "layoutv_" + nome, itens)
    return ('<widget class="QWidget" name="%s">\n'
            '<attribute name="title"><string>%s</string></attribute>\n'
            '%s\n</widget>' % (nome, escape(titulo), corpo))


# ------------------------------------------------------------- especificação
def construir():
    insumos = [
        grupo("grupoRasters", "Rasters de entrada",
              [campo("combo", "comboK",
                     "K — capacidade de suporte (favorabilidade estática)",
                     "Raster do modelo estático. K exclui μ: o garimpo "
                     "pretérito entra no estado e no contágio, nunca em K."),
               campo("combo", "comboMu0",
                     "μ₀ — estado inicial (garimpo existente)",
                     "Fração ocupada por pixel na data inicial, na mesma "
                     "unidade de K."),
               campo("combo", "comboMascara",
                     "Máscara da área de estudo (opcional)",
                     "Valores > 0 delimitam a área simulada.")],
              nota="Todas as camadas são levadas à grade de trabalho antes "
                   "de qualquer cálculo — projeção, origem e resolução "
                   "idênticas."),
        grupo("grupoGrade", "Grade de trabalho",
              [campo("check", "checkReprojetar", "",
                     rotulo="Reprojetar para outro CRS",
                     dica="O modelo exige CRS projetado (metros)."),
               campo("line", "lineCrs", "CRS de destino",
                     "Código EPSG, por exemplo EPSG:31981 "
                     "(SIRGAS 2000 / UTM 21S).",
                     valor="EPSG:31981"),
               campo("check", "checkResolucao", "",
                     rotulo="Reamostrar a resolução de trabalho",
                     dica="Resoluções mais grossas aceleram e reduzem "
                          "memória, ao custo de resolver pior a escala "
                          "de 380 m."),
               campo("double", "spinResolucao", "Resolução de trabalho",
                     "Tamanho do pixel da simulação.",
                     decimais=1, minimo=1.0, maximo=5000.0, passo=10.0,
                     valor=100.0, sufixo="m"),
               campo("line", "lineGradeInfo", "Grade resultante",
                     "Atualizada ao escolher o raster K.",
                     placeholder="—")]),
        grupo("grupoAlertas", "Alertas (ativação)",
              [campo("combo", "comboAlertas", "Camada de alertas (opcional)"),
               campo("combo", "comboCampoAlertas", "Campo de data"),
               campo("check", "checkFrentes", "",
                     rotulo="Agrupar alertas em frentes",
                     valor=True,
                     dica="~85% dos alertas têm vizinho a menos de 100 m: o "
                          "alerta isolado é fragmento de detecção, não "
                          "evento independente."),
               campo("double", "spinRaioFrente", "Raio de agrupamento",
                     "Ligação simples entre alertas.",
                     decimais=0, minimo=1.0, maximo=5000.0, passo=10.0,
                     valor=100.0, sufixo="m")]),
        grupo("grupoFisc", "Fiscalização (supressão)",
              [campo("combo", "comboFisc",
                     "Camada de autos de infração (opcional)"),
               campo("combo", "comboCampoFisc", "Campo de data")]),
        grupo("grupoSub", "Sub-bacias (unidade de saída)",
              [campo("combo", "comboSubbacias",
                     "Camada de sub-bacias (opcional)"),
               campo("combo", "comboCampoIdSub", "Campo identificador")],
              nota="A sub-bacia é unidade de reporte, nunca de propagação: a "
                   "agregação ocorre depois de simular."),
    ]

    parametros = [
        grupo("grupoPeriodo", "Período e integração",
              [campo("date", "dateInicio", "Data inicial", valor=(2025, 1, 1)),
               campo("date", "dateFim", "Data final", valor=(2025, 12, 31)),
               campo("double", "spinPasso", "Passo de integração",
                     "Euler explícito. Passos grandes com r₀ alto podem "
                     "superestimar o crescimento.",
                     decimais=1, minimo=0.5, maximo=90.0, passo=1.0,
                     valor=7.0, sufixo="dias"),
               campo("int", "spinJanela", "Janela de agrupamento de eventos",
                     "Eventos da mesma janela compartilham data de "
                     "referência. Janelas maiores custam menos memória.",
                     minimo=1, maximo=365, valor=30, sufixo="dias")]),
        grupo("grupoCrescimento", "Crescimento e contágio",
              [campo("double", "spinR0", "r₀ — taxa intrínseca",
                     "Crescimento por dia sob contágio pleno e capacidade "
                     "livre.",
                     decimais=5, minimo=0.0, maximo=1.0, passo=0.001,
                     valor=0.004, sufixo="dia⁻¹"),
               campo("double", "spinEscalaContagio", "Escala de contágio",
                     "Distância característica da propagação local "
                     "(estimada em ~380 m).",
                     decimais=0, minimo=10.0, maximo=20000.0, passo=50.0,
                     valor=380.0, sufixo="m"),
               campo("combo", "comboKernel", "Núcleo de contágio",
                     itens=["exponencial", "gaussiano"]),
               campo("check", "checkExcluirCentro", "",
                     rotulo="Excluir o próprio pixel do contágio",
                     dica="Separa contágio da vizinhança de persistência "
                          "local.")]),
        grupo("grupoParamAlertas", "Alertas — ativação",
              [campo("double", "spinAlertaPeso", "α — peso da ativação",
                     "Multiplica a taxa: α=1 dobra o crescimento sob "
                     "ativação máxima.",
                     decimais=2, minimo=0.0, maximo=20.0, passo=0.1,
                     valor=1.0),
               campo("double", "spinAlertaEscala", "Alcance espacial",
                     "Decaimento exp(−d/d_A).",
                     decimais=0, minimo=10.0, maximo=20000.0, passo=100.0,
                     valor=1000.0, sufixo="m"),
               campo("double", "spinAlertaTau", "τ — constante de tempo",
                     "Decaimento exp(−Δt/τ), contínuo desde o dia zero.",
                     decimais=0, minimo=1.0, maximo=3650.0, passo=10.0,
                     valor=90.0, sufixo="dias"),
               campo("check", "checkSemeia", "",
                     rotulo="Alertas semeiam estado (μ mínimo na frente)",
                     valor=True,
                     dica="Um alerta é, ele próprio, detecção de garimpo "
                          "novo — permite chegada em área sem vizinhança "
                          "ocupada."),
               campo("double", "spinSemente", "Valor da semente",
                     decimais=3, minimo=0.0, maximo=1.0, passo=0.01,
                     valor=0.05)]),
        grupo("grupoParamFisc", "Fiscalização — supressão",
              [campo("double", "spinFiscMax", "F_max — supressão máxima",
                     "1,0 zera o crescimento no epicentro durante o platô.",
                     decimais=2, minimo=0.0, maximo=1.0, passo=0.05,
                     valor=0.8),
               campo("double", "spinFiscEscala", "Alcance espacial",
                     "O efeito repressivo se concentra em 0,5–1 km.",
                     decimais=0, minimo=10.0, maximo=20000.0, passo=50.0,
                     valor=750.0, sufixo="m"),
               campo("double", "spinFiscSubida", "k — subida",
                     "Valores altos reproduzem o início quase imediato do "
                     "efeito.",
                     decimais=3, minimo=0.001, maximo=10.0, passo=0.1,
                     valor=1.0, sufixo="dia⁻¹"),
               campo("double", "spinFiscPlato", "Duração do platô",
                     "Patamar estável antes da dissipação.",
                     decimais=0, minimo=1.0, maximo=3650.0, passo=10.0,
                     valor=180.0, sufixo="dias"),
               campo("double", "spinFiscQueda", "k — dissipação",
                     "Nitidez da queda ao fim do platô.",
                     decimais=3, minimo=0.001, maximo=10.0, passo=0.01,
                     valor=0.05, sufixo="dia⁻¹"),
               campo("combo", "comboCombinacao",
                     "Combinação entre eventos",
                     "Máximo evita que a mera quantidade de autos sature a "
                     "supressão.",
                     itens=["maximo", "soma_saturada"])]),
    ]

    saidas = [
        grupo("grupoSaida", "Destino",
              [campo("linha_arquivo", "linePasta", "Pasta de saída",
                     botao="btnPasta"),
               campo("line", "linePrefixo", "Prefixo dos arquivos",
                     valor="risco")]),
        grupo("grupoCamadas", "Camadas geradas",
              [campo("check", "checkMuFinal", "",
                     rotulo="μ final (estado ao fim do horizonte)",
                     valor=True),
               campo("check", "checkTaxa", "",
                     rotulo="Taxa instantânea no fim do horizonte",
                     valor=True),
               campo("check", "checkCampos", "",
                     rotulo="Campos de ativação e supressão (diagnóstico)"),
               campo("check", "checkSerie", "",
                     rotulo="Série temporal de μ"),
               campo("int", "spinPassoSerie", "Gravar a cada N passos",
                     minimo=1, maximo=1000, valor=4),
               campo("check", "checkCarregar", "",
                     rotulo="Carregar os resultados no projeto",
                     valor=True)],
              nota="A camada principal é sempre gerada: Δμ — a expansão "
                   "prevista no horizonte, que responde 'onde vai expandir'."),
    ]

    execucao = [
        grupo("grupoDiag", "Resumo",
              [campo("line", "lineResumo", "Última execução",
                     placeholder="—")]),
    ]

    abas = [
        aba_xml("abaInsumos", "Insumos", insumos),
        aba_xml("abaParametros", "Parâmetros", parametros),
        aba_xml("abaSaidas", "Saídas", saidas),
        aba_xml("abaExecucao", "Execução", execucao + [
            widget("QGroupBox", "grupoLog",
                   [prop("title", "string", "Registro")],
                   [layout("QVBoxLayout", "layoutv_grupoLog",
                           [item(widget("QPlainTextEdit", "textoLog",
                                        [prop("readOnly", "bool", True),
                                         prop("lineWrapMode", "enum",
                                              "QPlainTextEdit::NoWrap")]))])])
        ], com_espacador=False),
    ]

    tabwidget = widget("QTabWidget", "abas",
                       [prop("currentIndex", "number", 0)],
                       abas)

    barra = widget("QProgressBar", "barraProgresso",
                   [prop("value", "number", 0),
                    prop("textVisible", "bool", True)])

    botoes = layout("QHBoxLayout", "layoutBotoes", [
        item(rotulo("lblStatus", "Pronto.")),
        espacador("espBotoes", vertical=False),
        item(widget("QPushButton", "btnExecutar",
                    [prop("text", "string", "Executar"),
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
        "QDialog", "RiscoGarimpoDialogBase",
        [prop("geometry", "rect", ""),  # substituído abaixo
         prop("windowTitle", "string", "Risco Garimpo — Modelo Dinâmico")],
        [principal])
    dialogo = dialogo.replace(
        '<property name="geometry"><rect></rect></property>',
        '<property name="geometry"><rect><x>0</x><y>0</y>'
        '<width>820</width><height>720</height></rect></property>')

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ui version="4.0">\n'
            ' <class>RiscoGarimpoDialogBase</class>\n'
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

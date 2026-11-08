# -*- coding: utf-8 -*-
"""Gera ui/validacao_dialog_base.ui — a terceira ferramenta do plugin.

Mesma convenção das outras duas (primitivas de gerar_ui.py, só widgets
padrão do Qt). O .ui gerado é versionado; rodar este script é opcional.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gerar_ui import (aba_xml, campo, espacador, grupo, item,  # noqa: E402
                      layout, prop, rotulo, widget)

SAIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "risco_garimpo", "ui", "validacao_dialog_base.ui")


def _arquivo(nome, texto, dica):
    return campo("linha_arquivo", nome, texto, dica,
                 botao="btn" + nome[len("linha"):])


def construir():
    insumos = [
        grupo("grupoExecucao", "Execução a validar",
              [_arquivo("linhaRegistro", "Registro da execução",
                        "O <prefixo>_registro_<data>.json gravado pelo modelo "
                        "dinâmico. Preenche os campos abaixo e confere, pelo "
                        "SHA-256, se algum insumo mudou desde a execução."),
               campo("line", "lblExecucao", "Execução",
                     "Período, prefixo e eventos usados, lidos do registro.",
                     placeholder="—"),
               _arquivo("linhaPrevisto", "Expansão prevista (Δμ)",
                        "O <prefixo>_expansao_<data>.tif da execução. A "
                        "grade dele é a grade da validação."),
               _arquivo("linhaMuInicial",
                        "Mapa de garimpo (0/1) — data inicial",
                        "O mesmo mapa usado na execução."),
               _arquivo("linhaK", "K",
                        "Habilita as linhas de base que usam o potencial "
                        "(só K, remanescente, proximidade × remanescente)."),
               _arquivo("linhaMascara", "Máscara da simulação",
                        "Valores > 0. Sem máscara de avaliação, é também a "
                        "área avaliada.")],
              nota="Com o registro (o JSON da execução), só falta o garimpo "
                   "observado na data final. Sem ele, informe os arquivos à "
                   "mão. K e a máscara são opcionais."),
        grupo("grupoObservado", "Garimpo observado",
              [_arquivo("linhaMuFinal",
                        "Mapa de garimpo (0/1) — data final",
                        "Mapa que o modelo não viu, na data final da "
                        "execução, como vem do mapa anual (ex.: MapBiomas, "
                        "só a classe de garimpo): 1 = garimpo, 0 = não."),
               _arquivo("linhaAvaliacao", "Máscara de avaliação",
                        "Onde as métricas são medidas, se for menor que a "
                        "área simulada (ex.: a área de estudo, simulada com "
                        "uma faixa de entorno).")],
              nota="A máscara de avaliação é opcional."),
        grupo("grupoSubbacias", "Sub-bacias (opcional)",
              [campo("combo", "comboSubbacias",
                     "Camada de sub-bacias",
                     "Compara a priorização prevista com a observada por "
                     "sub-bacia (Spearman e coincidência do topo)."),
               campo("line", "linhaCampoId", "Campo identificador",
                     "Ex.: HYBAS_ID. Vazio usa o id interno da feição.",
                     placeholder="HYBAS_ID")]),
    ]

    parametros = [
        grupo("grupoMetricas", "Métricas",
              [campo("double", "spinLimiar", "Limiar de garimpo novo",
                     "Fração do pixel acima da qual o pixel conta como "
                     "garimpo novo na AUC.",
                     valor=0.02, minimo=0.0, maximo=1.0, passo=0.01,
                     decimais=3),
               campo("double", "spinBloco", "Bloco da correlação",
                     "Tamanho dos blocos em que previsto e observado são "
                     "somados antes da correlação.",
                     valor=1000.0, minimo=1.0, maximo=50000.0, passo=100.0,
                     decimais=0, sufixo="m"),
               campo("double", "spinTopo", "Topo das sub-bacias",
                     "Fração das sub-bacias de maior expansão observada que "
                     "se procura no topo previsto.",
                     valor=0.10, minimo=0.01, maximo=1.0, passo=0.05,
                     decimais=2)],
              nota="O modelo é comparado com linhas de base sem dinâmica: a "
                   "proximidade ao garimpo existente em 100, 150, 200, 300, "
                   "380 e 500 m e, com K, as que usam o potencial."),
        grupo("grupoSaida", "Onde gravar",
              [campo("linha_arquivo", "linhaPasta", "Pasta de saída",
                     "Vazio usa a pasta do registro da execução.",
                     botao="btnPasta"),
               campo("line", "linhaPrefixo", "Prefixo dos arquivos",
                     "Vazio usa o prefixo da execução + _validacao.",
                     placeholder="PMT_validacao"),
               campo("check", "checkFigura", "", "",
                     rotulo="Gravar a figura da curva de captura (PNG)",
                     valor=True),
               campo("check", "checkCarregar", "", "",
                     rotulo="Carregar as sub-bacias no projeto",
                     valor=True)],
              nota="Sempre gravados: <prefixo>_metricas.csv, "
                   "<prefixo>_curva_captura.csv e <prefixo>_registro.json."),
    ]

    abas = [
        aba_xml("abaInsumos", "Execução e observado", insumos),
        aba_xml("abaParametros", "Métricas e saída", parametros),
        aba_xml("abaRegistro", "Registro", [
            widget("QGroupBox", "grupoLogValidacao",
                   [prop("title", "string", "Registro")],
                   [layout("QVBoxLayout", "layoutv_grupoLogValidacao",
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
                    [prop("text", "string", "Validar"),
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
        "QDialog", "ValidacaoDialogBase",
        [prop("geometry", "rect", ""),
         prop("windowTitle", "string",
              "Risco Garimpo — Validar uma execução")],
        [principal])
    dialogo = dialogo.replace(
        '<property name="geometry"><rect></rect></property>',
        '<property name="geometry"><rect><x>0</x><y>0</y>'
        '<width>820</width><height>680</height></rect></property>')

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ui version="4.0">\n'
            ' <class>ValidacaoDialogBase</class>\n'
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

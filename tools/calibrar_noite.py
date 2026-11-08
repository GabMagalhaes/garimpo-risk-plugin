# -*- coding: utf-8 -*-
"""Calibração do modelo dinâmico pelo console do QGIS (e base do paralelo).

Desde a 0.5.0 a calibração faz parte do plugin: Raster → Risco Garimpo →
Calibração… roda o mesmo plano em segundo plano, com o QGIS usável, e
leva a sugestão ao modelo dinâmico. Este arquivo continua para duas coisas:

* guardar o CONFIG de uma rodada do ``calibrar_paralelo.py`` (vários
  processos, fora do QGIS — para computadores de alto desempenho);
* rodar pelo console, como antes da 0.5.0.

O motor (plano, ids, CSV, resumo, sugestão) é o do plugin
(``risco_garimpo/core/calibracao_dinamica.py``): o plugin, este console e o
paralelo produzem o mesmo resultados.csv e podem continuar a rodada um do
outro. Exige o plugin 0.5.0 ou mais novo instalado no QGIS.

COMO USAR (Console Python do QGIS)
----------------------------------
1. Preencha o bloco CONFIG abaixo.
2. No console:

       exec(open(r"C:\\caminho\\calibrar_noite.py", encoding="utf-8").read())
       verificar()          # 1–5 min: confere os dados e estima o tempo
       rodar_noite()        # roda até acabar ou até HORAS_MAXIMAS

   Enquanto roda, o QGIS fica sem responder — é esperado (a janela
   Calibração do plugin não tem esse problema). O progresso vai para
   ``calibracao.log`` na PASTA_SAIDA.
3. De manhã: ``resumo.txt`` (começa pela sugestão), ``sugestao.json`` e
   ``resultados.csv``. ``resumir()`` refaz o resumo a partir do CSV.

Retomada, ``PARAR.txt``, modos (reconstrução e previsão) e o teste
``fora_da_amostra(treino, teste)``: ver o motor e o README.
"""

import os
import sys

# =========================================================================
# CONFIG — preencha. Caminhos entre aspas, com r na frente: r"C:\pasta\x.tif"
# =========================================================================
CONFIG = dict(
    # capacidade de suporte (saída do modelo estático, ex.: PMT_K.tif)
    K=r"C:\Users\<voce>\dados\PMT_K.tif",
    # mapas de garimpo (0/1) das duas datas, como vêm do mapa anual (ex.:
    # MapBiomas, só a classe de garimpo): 1 = garimpo, 0 = não, sem suavizar
    MU_INICIAL=r"C:\Users\<voce>\dados\garimpo_2022.tif",
    MU_FINAL=r"C:\Users\<voce>\dados\garimpo_2024.tif",
    # onde a dinâmica roda: a área de estudo + uma faixa em volta (ex.: 5 km),
    # para que o entorno alimente o contágio na borda; None = grade inteira
    MASCARA=r"C:\Users\<voce>\dados\mascara_area_buffer5km.tif",
    # onde as métricas são medidas: só a área de estudo; None = MASCARA
    MASCARA_AVALIACAO=r"C:\Users\<voce>\dados\mascara_area.tif",
    # eventos: arquivo (.shp, .gpkg ou .gpkg|layername=nome) e campo de data
    ALERTAS=r"C:\Users\<voce>\dados\alertas.gpkg",
    CAMPO_DATA_ALERTAS="data",
    AUTOS=r"C:\Users\<voce>\dados\autos.gpkg",
    CAMPO_DATA_AUTOS="data",
    # período: datas das duas fotografias de garimpo (MU_INICIAL, MU_FINAL)
    DATA_INICIAL="2022-12-31",
    DATA_FINAL="2024-12-31",
    # resolução da simulação; verificar() mostra o tempo por simulação
    RESOLUCAO_M=150.0,
    # fora do OneDrive de propósito: o CSV é gravado a cada simulação e a
    # sincronização disputa o arquivo
    PASTA_SAIDA=r"C:\Users\<voce>\calibracao_noite",
    HORAS_MAXIMAS=11.0,
    LIMIAR_NOVO=0.02,
    RAIO_AUTOS_M=1500.0,
    BLOCO_M=1000.0,
    N_ALEATORIAS=400,
    SEMENTE=2026,
    MEMORIA_CAMPOS_GB=1.5,
    # etapa "estrutura": saturação por K mais frouxa (K × fator) e escalas de
    # contágio menores, nos dois modos; é dela que sai a escala sugerida.
    # Uma PASTA_SAIDA de antes da 0.3.x (sem a coluna k_fator) exige pasta nova
    ESTRUTURA=True,
    # combinações escolhidas em OUTRO período, rodadas tal como vieram (com o
    # r0 delas, sem recalibrar), nos dois modos: o teste honesto de uma
    # escolha. Ex.: [{"r0": 0.0007, "escala_contagio_m": 100.0,
    # "k_fator": 1.5}]. As chaves são as de Parametros, mais k_fator
    CONFIGURACOES_EXTRAS=[],
)


_MOTOR = []          # o motor desta sessão do console (com os dados lidos)


def _plugin():
    """O motor de calibração do plugin instalado (0.5.0 ou mais novo)."""
    try:
        from risco_garimpo.core import calibracao_dinamica
    except ImportError as erro:
        raise ImportError("o plugin Risco Garimpo 0.5.0 ou mais novo não está "
                          "instalado neste QGIS (%s)" % erro)
    return calibracao_dinamica


def _motor(novo=False):
    if novo or not _MOTOR or _MOTOR[0].config_original != CONFIG:
        modulo = _plugin()
        motor = modulo.CalibracaoDinamica(dict(CONFIG), registrar=print)
        motor.config_original = dict(CONFIG)
        _MOTOR[:] = [motor]
    return _MOTOR[0]


def verificar():
    """Confere os dados, roda uma simulação e estima o tempo. 1–5 minutos."""
    return _motor(novo=True).verificar()


def rodar_noite():
    """Roda o plano em ordem de prioridade, retomando o que já foi feito."""
    motor = _motor()
    if motor.ctx is None:
        motor.verificar()
    try:
        from qgis.core import QgsApplication
        motor.processar_eventos = QgsApplication.processEvents
    except ImportError:
        pass
    motor.log("A partir daqui o QGIS fica sem responder até acabar. "
              "Acompanhe: %s" % os.path.join(motor.pasta, "calibracao.log"))
    return motor.rodar()


def resumir(imprimir=True):
    """Escreve resumo.txt a partir do resultados.csv (pode rodar a qualquer hora)."""
    texto = _motor().resumir()
    if imprimir:
        print(texto)
    return texto


def fora_da_amostra(pasta_treino, pasta_teste, n=10, imprimir=True):
    """Escolhe parâmetros num período e mede no outro, sem simular nada."""
    texto = _plugin().fora_da_amostra(pasta_treino, pasta_teste, n=n)
    if imprimir:
        print(texto)
    return texto


if __name__ != "__main__" or "qgis" in sys.modules:
    if __name__ not in ("calibrar_noite", "calibrar_paralelo"):
        print("calibrar_noite carregado. Preencha CONFIG no arquivo e rode:")
        print("   verificar()      # confere os dados e estima o tempo")
        print("   rodar_noite()    # roda a noite toda")
        print("   resumir()        # refaz o resumo a partir do CSV")
        print("   fora_da_amostra(r'pasta_treino', r'pasta_teste')")

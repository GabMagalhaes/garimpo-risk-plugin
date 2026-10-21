# -*- coding: utf-8 -*-
"""Calibração e sensibilidade do modelo dinâmico contra o garimpo observado.

Feito para rodar sozinho por horas (a noite toda) no QGIS. Simula o período
entre duas datas com o mapa de garimpo da data inicial e compara a expansão
prevista com a observada na data final, para muitas combinações de
parâmetros. Responde, com os seus dados:

* o modelo dinâmico ordena melhor que alternativas simples (só K, só o
  remanescente K − μ, só a proximidade ao garimpo existente)?
* o platô da fiscalização de 180 dias ajusta melhor que 90, 270, 365 dias
  ou um efeito persistente? E qual alcance d₀ (400–1.500 m)?
* que valores de r₀, α e F_max reproduzem a área nova observada?

COMO USAR (Console Python do QGIS, com o plugin Risco Garimpo instalado)
--------------------------------------------------------------------------
1. Abra este arquivo no Bloco de Notas e preencha o bloco CONFIG abaixo.
2. No console:

       exec(open(r"C:\\caminho\\calibrar_noite.py", encoding="utf-8").read())
       verificar()          # 1–5 min: confere os dados e estima o tempo
       rodar_noite()        # roda até acabar ou até HORAS_MAXIMAS

   Enquanto roda, o QGIS fica sem responder — é esperado. O progresso vai
   para ``calibracao.log`` na PASTA_SAIDA (abra no Bloco de Notas).
3. De manhã: ``resumo.txt`` e ``resultados.csv`` na PASTA_SAIDA. Se o QGIS
   tiver sido fechado, rode ``exec(...)`` e ``resumir()`` para refazer o
   resumo a partir do CSV.

Robustez: cada simulação é gravada no CSV assim que termina; rodar de novo
retoma de onde parou, sem repetir. Um erro numa combinação é anotado e a
próxima segue. Para parar com segurança, crie um arquivo ``PARAR.txt`` na
PASTA_SAIDA (ou feche o QGIS: o que já foi gravado fica). No Windows, o
script impede que o computador entre em suspensão enquanto roda.

MODOS
-----
``reconstrucao``  usa os alertas e autos de todo o período. Mede se a
                  dinâmica reproduz o que aconteceu, dados os eventos; é o modo
                  que identifica platô e alcance da fiscalização.
``previsao``      usa só eventos até a data inicial — o que se saberia no dia
                  da previsão. Mede a capacidade honesta de antecipar.
"""

import csv
import datetime as _dt
import hashlib
import json
import math
import os
import random
import sys
import time
import traceback

import numpy as np

# =========================================================================
# CONFIG — preencha. Caminhos entre aspas, com r na frente: r"C:\pasta\x.tif"
# =========================================================================
CONFIG = dict(
    # capacidade de suporte (saída do modelo estático, ex.: PMT_K.tif)
    K=r"C:\Users\<voce>\dados\PMT_K.tif",
    # garimpo na data inicial e na data final, em fração 0–1 (1 = garimpo);
    # ex.: MapBiomas Mineração de dois anos, só a classe de garimpo
    MU_INICIAL=r"C:\Users\<voce>\dados\garimpo_2022.tif",
    MU_FINAL=r"C:\Users\<voce>\dados\garimpo_2024.tif",
    MASCARA=None,                  # opcional: raster > 0 = área de estudo
    # eventos: arquivo (.shp, .gpkg ou .gpkg|layername=nome) e campo de data
    ALERTAS=r"C:\Users\<voce>\dados\alertas_brasil_mais.gpkg",
    CAMPO_DATA_ALERTAS="data",
    AUTOS=r"C:\Users\<voce>\dados\autos_ibama_icmbio.shp",
    CAMPO_DATA_AUTOS="data",
    # período: datas das duas fotografias de garimpo (MU_INICIAL, MU_FINAL)
    DATA_INICIAL="2022-12-31",
    DATA_FINAL="2024-12-31",
    # resolução da simulação. 150 m é um bom compromisso para a província;
    # verificar() mostra o tempo por simulação — se passar de ~5 min, suba
    RESOLUCAO_M=150.0,
    PASTA_SAIDA=r"C:\Users\<voce>\calibracao_noite",
    HORAS_MAXIMAS=11.0,
    # o que conta como garimpo novo observado (fração do pixel)
    LIMIAR_NOVO=0.02,
    # raio em torno dos autos do período para as métricas locais
    RAIO_AUTOS_M=1500.0,
    # bloco para a correlação agregada (m)
    BLOCO_M=1000.0,
    # combinações aleatórias depois das etapas fixas (sorteio reprodutível)
    N_ALEATORIAS=400,
    SEMENTE=2026,
    # memória máxima para guardar campos de eventos entre simulações
    MEMORIA_CAMPOS_GB=1.5,
)

# faixas exploradas (edite à vontade; o padrão do plugin está em Parametros)
# 1º calibra-se r0 para a área nova prevista bater com a observada; as etapas
# seguintes usam esse r0 — senão, com tudo subestimado, o critério premiaria
# a fiscalização mais fraca só por deixar crescer mais
R0 = [0.001, 0.002, 0.003, 0.005, 0.008, 0.012, 0.018, 0.027, 0.04]
SENSIBILIDADE = {
    "alerta_peso": [0.0, 0.5, 2.0, 4.0],
    "alerta_semeia": [False],
    "alerta_tau_dias": [30.0, 180.0],
    "escala_contagio_m": [250.0, 500.0, 750.0],
    "fisc_max": [0.0, 0.4, 1.0],
    "fisc_plato_dias": [60.0, 120.0, 270.0, 365.0, 730.0, 3650.0],
    "fisc_escala_m": [250.0, 500.0, 1000.0, 1500.0],
}
FISCAL = {
    "fisc_plato_dias": [90.0, 180.0, 270.0, 365.0, 3650.0],
    "fisc_escala_m": [400.0, 750.0, 1000.0],
    "fisc_max": [0.5, 0.8, 1.0],
}
ALEATORIA = {
    "r0": (0.001, 0.02, "log"),
    "alerta_peso": (0.0, 4.0, "lin"),
    "escala_contagio_m": (250.0, 750.0, "lin"),
    "fisc_max": (0.0, 1.0, "lin"),
    "fisc_plato_dias": (60.0, 730.0, "log"),
    "fisc_escala_m": (250.0, 1500.0, "log"),
}

COLUNAS = ["id", "etapa", "modo", "r0", "alerta_peso", "alerta_escala_m",
           "alerta_tau_dias", "alerta_semeia", "escala_contagio_m", "fisc_max",
           "fisc_escala_m", "fisc_plato_dias", "fisc_k_queda", "passo_dias",
           "auc", "captura_1", "captura_5", "captura_10", "r_blocos",
           "pred_ha", "obs_ha", "razao_area", "perto_autos_pred_ha",
           "perto_autos_obs_ha", "perto_autos_razao", "perto_autos_auc",
           "segundos", "erro"]
PARAMETROS_CSV = COLUNAS[3:14]

_CONTEXTO = {}


# =========================================================================
# registro em arquivo (seguro mesmo com o QGIS sem responder)
# =========================================================================
def _pasta():
    return CONFIG["PASTA_SAIDA"]


def _log(mensagem, tambem_print=True):
    linha = "%s  %s" % (_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        mensagem)
    try:
        os.makedirs(_pasta(), exist_ok=True)
        with open(os.path.join(_pasta(), "calibracao.log"), "a",
                  encoding="utf-8") as arquivo:
            arquivo.write(linha + "\n")
    except OSError:
        pass
    if tambem_print:
        print(linha)


def _impedir_suspensao(ligar):
    """No Windows, impede a suspensão enquanto roda (sem efeito fora dele)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        continuo, sistema = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            continuo | sistema if ligar else continuo)
    except Exception:  # noqa: BLE001
        pass


def _processar_eventos():
    try:
        from qgis.core import QgsApplication
        QgsApplication.processEvents()
    except Exception:  # noqa: BLE001
        pass


# =========================================================================
# métricas (NumPy puro)
# =========================================================================
def auc(pontuacao, alvo):
    """Área sob a curva ROC (Mann-Whitney), com empates tratados por posto médio."""
    pontuacao = np.asarray(pontuacao, dtype=np.float64).ravel()
    alvo = np.asarray(alvo, dtype=bool).ravel()
    n1 = int(alvo.sum())
    n0 = alvo.size - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    _, inverso, contagens = np.unique(pontuacao, return_inverse=True,
                                      return_counts=True)
    fim = np.cumsum(contagens).astype(np.float64)
    posto_medio = fim - (contagens - 1) / 2.0
    soma = float(posto_medio[inverso.ravel()][alvo].sum())
    return (soma - n1 * (n1 + 1) / 2.0) / (float(n1) * n0)


def captura(pontuacao, observado, fracao_area):
    """Fração do garimpo novo observado nos ``fracao_area`` pixels de maior risco.

    Empates na fronteira entram em proporção (sem favorecer a ordem dos
    pixels): é o que torna a métrica justa com pontuações cheias de zeros.
    """
    pontuacao = np.asarray(pontuacao, dtype=np.float64).ravel()
    observado = np.asarray(observado, dtype=np.float64).ravel()
    total = observado.sum()
    if total <= 0 or pontuacao.size == 0:
        return float("nan")
    k = int(round(fracao_area * pontuacao.size))
    if k <= 0:
        return 0.0
    limite = np.partition(pontuacao, pontuacao.size - k)[pontuacao.size - k]
    acima = pontuacao > limite
    empatados = pontuacao == limite
    faltam = k - int(acima.sum())
    capturado = observado[acima].sum()
    if faltam > 0 and empatados.any():
        capturado += observado[empatados].sum() * faltam / float(empatados.sum())
    return float(capturado / total)


def _somar_blocos(arr, fator):
    ny, nx = arr.shape
    py, px = (-ny) % fator, (-nx) % fator
    arr = np.pad(arr, ((0, py), (0, px)))
    return arr.reshape(arr.shape[0] // fator, fator,
                       arr.shape[1] // fator, fator).sum(axis=(1, 3))


def correlacao_blocos(previsto, observado, fator):
    if fator <= 1:
        a, b = previsto.ravel(), observado.ravel()
    else:
        a = _somar_blocos(previsto, fator).ravel()
        b = _somar_blocos(observado, fator).ravel()
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def avaliar(pontuacao, ctx, magnitude=True):
    """Todas as métricas de uma pontuação (Δμ previsto ou linha de base)."""
    universo = ctx["universo"]
    obs = ctx["obs_novo"]
    pont = np.where(universo, pontuacao, 0.0).astype(np.float32)
    p_u, o_u = pont[universo], obs[universo]
    area_ha = ctx["area_ha"]
    m = {
        "auc": auc(p_u, o_u > CONFIG["LIMIAR_NOVO"]),
        "captura_1": captura(p_u, o_u, 0.01),
        "captura_5": captura(p_u, o_u, 0.05),
        "captura_10": captura(p_u, o_u, 0.10),
        "r_blocos": correlacao_blocos(pont, obs, ctx["fator_bloco"]),
        "obs_ha": float(o_u.sum() * area_ha),
    }
    perto = ctx["perto_autos"]
    if perto is not None and perto.any():
        m["perto_autos_obs_ha"] = float(obs[perto].sum() * area_ha)
        m["perto_autos_auc"] = auc(pont[perto],
                                   obs[perto] > CONFIG["LIMIAR_NOVO"])
    if magnitude:
        m["pred_ha"] = float(p_u.sum() * area_ha)
        m["razao_area"] = (m["pred_ha"] / m["obs_ha"]
                           if m["obs_ha"] > 0 else float("nan"))
        if perto is not None and perto.any():
            m["perto_autos_pred_ha"] = float(pont[perto].sum() * area_ha)
            m["perto_autos_razao"] = (
                m["perto_autos_pred_ha"] / m["perto_autos_obs_ha"]
                if m["perto_autos_obs_ha"] > 0 else float("nan"))
    return m


# =========================================================================
# preparação (thread principal: lê camadas do QGIS)
# =========================================================================
def _data(texto):
    return _dt.date.fromisoformat(str(texto))


def _camada(caminho, nome, campo):
    from qgis.core import QgsVectorLayer
    if not caminho:
        return None
    camada = QgsVectorLayer(caminho, nome, "ogr")
    if not camada.isValid():
        raise ValueError("não consegui abrir %s: %s" % (nome, caminho))
    if camada.fields().indexFromName(campo) < 0:
        raise ValueError("a camada %s não tem o campo de data %r. Campos: %s"
                         % (nome, campo,
                            ", ".join(f.name() for f in camada.fields())))
    return camada


def _plugin():
    try:
        from risco_garimpo.core import execucao, model, numerico, kernels
        from risco_garimpo.core import raster as _raster
        from risco_garimpo.core.eventos import CampoEventos
    except ImportError as erro:
        raise ImportError("o plugin Risco Garimpo não está instalado neste "
                          "QGIS (%s)" % erro)
    return execucao, model, numerico, kernels, _raster, CampoEventos


def _assinatura_config():
    return json.dumps(CONFIG, sort_keys=True, default=str)


def verificar():
    """Confere os dados, roda uma simulação e estima o tempo. 1–5 minutos."""
    inicio = time.time()
    execucao, model, numerico, kernels, _raster, CampoEventos = _plugin()
    os.makedirs(_pasta(), exist_ok=True)
    _log("=== verificar() ===")
    for chave in ("K", "MU_INICIAL", "MU_FINAL"):
        if not os.path.isfile(CONFIG[chave]):
            raise ValueError("%s não encontrado: %s" % (chave, CONFIG[chave]))
    if CONFIG["MASCARA"] and not os.path.isfile(CONFIG["MASCARA"]):
        raise ValueError("MASCARA não encontrada: %s" % CONFIG["MASCARA"])
    t0, t1 = _data(CONFIG["DATA_INICIAL"]), _data(CONFIG["DATA_FINAL"])
    if t1 <= t0:
        raise ValueError("DATA_FINAL deve ser posterior a DATA_INICIAL")

    alertas = _camada(CONFIG["ALERTAS"], "alertas",
                      CONFIG["CAMPO_DATA_ALERTAS"])
    autos = _camada(CONFIG["AUTOS"], "autos", CONFIG["CAMPO_DATA_AUTOS"])
    cfg = execucao.Configuracao(
        caminho_k=CONFIG["K"], caminho_mu0=CONFIG["MU_INICIAL"],
        caminho_mascara=CONFIG["MASCARA"], resolucao_m=CONFIG["RESOLUCAO_M"],
        camada_alertas=alertas,
        campo_data_alertas=CONFIG["CAMPO_DATA_ALERTAS"],
        camada_fiscalizacao=autos,
        campo_data_fiscalizacao=CONFIG["CAMPO_DATA_AUTOS"],
        data_inicio=t0, data_fim=t1, pasta_saida=_pasta(),
        prefixo="calibracao")
    preparo = execucao.preparar(cfg, log=lambda m: _log("   " + m))
    grade = preparo.grade
    pixels = grade.nx * grade.ny
    _log("grade: %d x %d = %.1f milhões de pixels de %.0f m"
         % (grade.nx, grade.ny, pixels / 1e6, grade.pixel_m))
    if pixels > 25e6:
        raise ValueError("grade grande demais (%.0f milhões de pixels) para "
                         "rodar centenas de simulações: aumente RESOLUCAO_M "
                         "ou use uma MASCARA menor" % (pixels / 1e6))

    K = _raster.ler_alinhado(CONFIG["K"], grade, "bilinear")
    mu0 = _raster.ler_alinhado(CONFIG["MU_INICIAL"], grade, "media")
    mu1 = _raster.ler_alinhado(CONFIG["MU_FINAL"], grade, "media")
    for nome, arr in (("MU_INICIAL", mu0), ("MU_FINAL", mu1), ("K", K)):
        maximo = float(np.nanmax(arr)) if arr.size else 0.0
        if maximo > 1.0001:
            raise ValueError("%s tem valores até %.3g; o esperado é fração "
                             "0–1 (1 = garimpo). Se for o raster de classes "
                             "do MapBiomas, extraia antes só a classe de "
                             "garimpo como 0/1." % (nome, maximo))
    K = np.nan_to_num(K).astype(np.float32)
    mu0 = np.clip(np.nan_to_num(mu0), 0, 1).astype(np.float32)
    mu1 = np.clip(np.nan_to_num(mu1), 0, 1).astype(np.float32)
    mascara = None
    if CONFIG["MASCARA"]:
        mascara = _raster.ler_alinhado(CONFIG["MASCARA"], grade,
                                       "vizinho") > 0
    universo = mascara if mascara is not None else np.ones(K.shape, bool)
    area_ha = grade.pixel_m ** 2 / 10000.0
    obs_novo = np.where(universo, np.clip(mu1 - mu0, 0.0, None), 0.0)
    _log("garimpo observado: %.0f ha em %s, %.0f ha em %s; novo: %.0f ha"
         % (mu0[universo].sum() * area_ha, t0, mu1[universo].sum() * area_ha,
            t1, obs_novo.sum() * area_ha))
    if obs_novo.sum() <= 0:
        raise ValueError("não há garimpo novo entre MU_INICIAL e MU_FINAL — "
                         "confira se os arquivos estão na ordem certa")
    novos_fora_k = obs_novo[(K <= 0) & universo].sum() / obs_novo.sum()
    if novos_fora_k > 0.05:
        _log("AVISO: %.0f%% do garimpo novo observado caiu onde K = 0 — o "
             "modelo não pode prever ali (limite do K, não da dinâmica)"
             % (100 * novos_fora_k))

    # zona dos autos do período, para as métricas locais da fiscalização
    perto = None
    autos_periodo = [(d, idx) for d, idx in preparo.janelas_fiscalizacao
                     if t0 <= d <= t1]
    if autos_periodo:
        marcados = np.zeros(K.shape, dtype=bool)
        for _d, (linhas, colunas) in autos_periodo:
            marcados[linhas, colunas] = True
        distancia = numerico.distancia_euclidiana(marcados, grade.pixel_m)
        perto = (distancia <= CONFIG["RAIO_AUTOS_M"]) & universo
        _log("autos no período: %d janelas; zona de %.0f m em volta: %.0f km²"
             % (len(autos_periodo), CONFIG["RAIO_AUTOS_M"],
                perto.sum() * grade.pixel_m ** 2 / 1e6))
    else:
        _log("AVISO: nenhum auto no período; o platô e o alcance da "
             "fiscalização não serão identificáveis")

    ctx = dict(execucao=execucao, model=model, kernels=kernels,
               numerico=numerico, CampoEventos=CampoEventos, preparo=preparo,
               grade=grade, K=K, mu0=mu0, mascara=mascara, universo=universo,
               obs_novo=obs_novo, perto_autos=perto, area_ha=area_ha,
               t0=t0, t1=t1, campos={},
               fator_bloco=max(int(round(CONFIG["BLOCO_M"] / grade.pixel_m)),
                               1),
               assinatura=_assinatura_config())
    _CONTEXTO.clear()
    _CONTEXTO.update(ctx)

    _linhas_de_base(ctx)
    padrao = model.Parametros()
    plano = (_plano_inicial(padrao)
             + _plano_restante(padrao, r0_calibrado(padrao.r0)))
    feitos = _ids_feitos()
    _log("simulação de teste com os parâmetros padrão do plugin…")
    linha = _rodar_uma(ctx, plano[0])
    if linha["erro"]:
        raise RuntimeError("a simulação de teste falhou:\n" + linha["erro"])
    if linha["id"] not in feitos:
        _gravar_linha(linha)
    memoria = sum(_bytes_do_campo(c) for c in ctx["campos"].values())
    _log("campos de eventos da simulação de teste: %.2f GB (limite do cache "
         "%.1f GB)" % (memoria / 1e9, CONFIG["MEMORIA_CAMPOS_GB"]))
    segundos = max(linha["segundos"], 0.1)
    cabem = int(CONFIG["HORAS_MAXIMAS"] * 3600 / segundos)
    _log("uma simulação: %.0f s (AUC %.3f, captura nos 5%% de maior risco "
         "%.0f%%, área prevista / observada %.2f)"
         % (segundos, linha["auc"], 100 * linha["captura_5"],
            linha["razao_area"]))
    etapas = {}
    for item in plano:
        etapas[item["etapa"]] = etapas.get(item["etapa"], 0) + 1
    _log("plano: %s — %d simulações; em %.0f h cabem ~%d"
         % (", ".join("%s %d" % kv for kv in etapas.items()), len(plano),
            CONFIG["HORAS_MAXIMAS"], cabem))
    if cabem < sum(n for e, n in etapas.items() if e != "aleatoria"):
        _log("AVISO: não cabem nem as etapas fixas. Considere RESOLUCAO_M "
             "maior ou uma MASCARA menor.")
    _log("verificar() levou %.0f s. Tudo certo: rode rodar_noite()."
         % (time.time() - inicio))
    return True


def _linhas_de_base(ctx):
    """Alternativas simples, sem dinâmica: o modelo precisa superá-las."""
    feitos = _ids_feitos()
    nucleo = ctx["kernels"].kernel_contagio(380.0, ctx["grade"].pixel_m)
    proximidade = ctx["numerico"].convolver(ctx["mu0"], nucleo)
    folga = np.clip(ctx["K"] - ctx["mu0"], 0.0, None)
    for nome, pontuacao in (("so_K", ctx["K"]),
                            ("remanescente", folga),
                            ("proximidade", proximidade),
                            ("proximidade_x_remanescente", proximidade * folga)):
        identificador = "base_" + nome
        if identificador in feitos:
            continue
        linha = dict.fromkeys(COLUNAS, "")
        linha.update(id=identificador, etapa="linha_de_base", modo=nome)
        linha.update(avaliar(pontuacao, ctx, magnitude=False))
        linha["segundos"] = 0.0
        _gravar_linha(linha)
        _log("linha de base %-28s AUC %.3f  captura 5%% %.0f%%"
             % (nome, linha["auc"], 100 * linha["captura_5"]))


# =========================================================================
# plano de simulações (ordem = prioridade)
# =========================================================================
def _plano_inicial(padrao):
    """Padrão do plugin e varredura de r0 (não dependem de nada)."""
    base = {c: getattr(padrao, c) for c in PARAMETROS_CSV}
    itens = []

    def item(etapa, modo, **mudancas):
        params = dict(base)
        params.update(mudancas)
        itens.append({"etapa": etapa, "modo": modo, "params": params})

    item("padrao", "reconstrucao")
    item("padrao", "previsao")
    for valor in R0:
        item("r0", "reconstrucao", r0=valor)
    return _com_ids(itens)


def r0_calibrado(padrao_r0=0.004):
    """r0 em que a área nova prevista iguala a observada (modo reconstrução).

    Interpola em escala log-log entre as duas varreduras que cercam a razão 1;
    sem cerco, fica com a mais próxima. Lê sempre do CSV, para que a
    retomada chegue ao mesmo valor.
    """
    pontos = []
    if os.path.isfile(_caminho_csv()):
        for l in _ler_csv():
            if (l["etapa"] == "r0" and not l.get("erro")
                    and l.get("razao_area", 0) > 0):
                pontos.append((l["r0"], l["razao_area"]))
    if not pontos:
        return padrao_r0
    pontos.sort()
    for (r_a, q_a), (r_b, q_b) in zip(pontos, pontos[1:]):
        if (q_a - 1.0) * (q_b - 1.0) <= 0 and q_a != q_b:
            t = (0.0 - math.log(q_a)) / (math.log(q_b) - math.log(q_a))
            valor = math.exp(math.log(r_a) + t * (math.log(r_b) - math.log(r_a)))
            return float("%.4g" % valor)
    return float("%.4g" % min(pontos, key=lambda p: abs(math.log(p[1])))[0])


def _plano_restante(padrao, r0):
    """Sensibilidade, fatorial da fiscalização e aleatórias, com r0 calibrado."""
    base = {c: getattr(padrao, c) for c in PARAMETROS_CSV}
    base["r0"] = r0
    itens = []

    def item(etapa, modo, **mudancas):
        params = dict(base)
        params.update(mudancas)
        itens.append({"etapa": etapa, "modo": modo, "params": params})

    item("calibrado", "reconstrucao")
    item("calibrado", "previsao")
    for nome, valores in SENSIBILIDADE.items():
        for valor in valores:
            item("sensibilidade", "reconstrucao", **{nome: valor})
    for plato in FISCAL["fisc_plato_dias"]:
        for alcance in FISCAL["fisc_escala_m"]:
            for fmax in FISCAL["fisc_max"]:
                item("fiscal", "reconstrucao", fisc_plato_dias=plato,
                     fisc_escala_m=alcance, fisc_max=fmax)
    sorteio = random.Random(CONFIG["SEMENTE"])
    for i in range(int(CONFIG["N_ALEATORIAS"])):
        mudancas = {}
        for nome, (minimo, maximo, escala) in ALEATORIA.items():
            u = sorteio.random()
            if escala == "log":
                valor = math.exp(math.log(minimo)
                                 + u * (math.log(maximo) - math.log(minimo)))
            else:
                valor = minimo + u * (maximo - minimo)
            mudancas[nome] = round(valor, 6)
        # um terço das aleatórias no modo previsão
        item("aleatoria", "previsao" if i % 3 == 2 else "reconstrucao",
             **mudancas)
    return _com_ids(itens)


def _com_ids(itens):
    for it in itens:
        it["id"] = _id(it["modo"], it["params"])
    vistos, unicos = set(), []
    for it in itens:
        if it["id"] not in vistos:
            vistos.add(it["id"])
            unicos.append(it)
    return unicos


def _id(modo, params):
    texto = json.dumps({"modo": modo, "params": params}, sort_keys=True)
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


# =========================================================================
# uma simulação
# =========================================================================
def _bytes_do_campo(campo):
    if campo is None:
        return 0
    return sum(sub.nbytes for _d, _sy, _sx, sub in campo.itens)


def _campo(ctx, tipo, modo, escala_m):
    """Campos de eventos em cache (o termo espacial só depende da escala).

    O cache descarta o menos usado quando passa de MEMORIA_CAMPOS_GB: numa
    província inteira, o retângulo de uma janela de autos pode cobrir quase
    a grade toda.
    """
    chave = (tipo, modo, round(float(escala_m), 3))
    campos = ctx["campos"]
    if chave in campos:
        campos[chave] = campos.pop(chave)       # vira o mais recente
        return campos[chave]
    preparo = ctx["preparo"]
    janelas = (preparo.janelas_alertas if tipo == "alertas"
               else preparo.janelas_fiscalizacao)
    limite = ctx["t0"] if modo == "previsao" else ctx["t1"]
    janelas = [(d, idx) for d, idx in janelas if d <= limite]
    campo = None
    if janelas:
        campo = ctx["CampoEventos"](ctx["grade"].shape, ctx["grade"].pixel_m,
                                    janelas, escala_m,
                                    guardar_posicoes=(tipo == "alertas"))
    limite = CONFIG["MEMORIA_CAMPOS_GB"] * 1e9
    while campos and (sum(_bytes_do_campo(c) for c in campos.values())
                      + _bytes_do_campo(campo) > limite):
        campos.pop(next(iter(campos)))           # o menos usado
    campos[chave] = campo
    return campo


def _rodar_uma(ctx, item):
    linha = dict.fromkeys(COLUNAS, "")
    linha.update(id=item["id"], etapa=item["etapa"], modo=item["modo"])
    linha.update(item["params"])
    inicio = time.time()
    try:
        params = ctx["model"].Parametros(**item["params"]).validar()
        alertas = _campo(ctx, "alertas", item["modo"], params.alerta_escala_m)
        autos = _campo(ctx, "autos", item["modo"], params.fisc_escala_m)
        resultado = ctx["model"].simular(
            ctx["K"], ctx["mu0"], ctx["grade"].pixel_m, ctx["t0"], ctx["t1"],
            params, campo_alertas=alertas, campo_fiscalizacao=autos,
            mascara=ctx["mascara"])
        linha.update(avaliar(resultado.delta_mu, ctx))
    except MemoryError:
        ctx["campos"].clear()
        linha["erro"] = "MemoryError (cache de campos esvaziado)"
    except Exception:  # noqa: BLE001 - anota e segue para a próxima
        linha["erro"] = traceback.format_exc(limit=4)
    linha["segundos"] = round(time.time() - inicio, 1)
    return linha


# =========================================================================
# CSV: gravação incremental e retomada
# =========================================================================
def _caminho_csv():
    return os.path.join(_pasta(), "resultados.csv")


def _ids_feitos():
    caminho = _caminho_csv()
    if not os.path.isfile(caminho):
        return set()
    with open(caminho, encoding="utf-8", newline="") as arquivo:
        return {l["id"] for l in csv.DictReader(arquivo, delimiter=";")
                if not l.get("erro")}


def _formatar(valor):
    if isinstance(valor, float):
        return "" if math.isnan(valor) else "%.6g" % valor
    return str(valor)


def _gravar_linha(linha):
    caminho = _caminho_csv()
    novo = not os.path.isfile(caminho)
    with open(caminho, "a", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS, delimiter=";",
                                  extrasaction="ignore")
        if novo:
            escritor.writeheader()
        escritor.writerow({c: _formatar(linha.get(c, "")) for c in COLUNAS})
        arquivo.flush()
        os.fsync(arquivo.fileno())


# =========================================================================
# laço da noite
# =========================================================================
def rodar_noite():
    """Roda o plano em ordem de prioridade, retomando o que já foi feito."""
    if _CONTEXTO.get("assinatura") != _assinatura_config():
        verificar()
    ctx = _CONTEXTO
    padrao = ctx["model"].Parametros()
    limite = time.time() + CONFIG["HORAS_MAXIMAS"] * 3600
    _log("=== rodar_noite(): para até %s ==="
         % _dt.datetime.fromtimestamp(limite).strftime("%d/%m %H:%M"))
    _log("A partir daqui o QGIS fica sem responder até acabar. Acompanhe "
         "este arquivo: %s" % os.path.join(_pasta(), "calibracao.log"))
    _processar_eventos()
    _impedir_suspensao(True)
    contagem = {"feitas": 0}
    try:
        if _rodar_lista(ctx, _plano_inicial(padrao), limite, contagem):
            r0 = r0_calibrado(padrao.r0)
            _log("r0 calibrado (área prevista = observada): %.4g" % r0, False)
            _rodar_lista(ctx, _plano_restante(padrao, r0), limite, contagem)
    finally:
        _impedir_suspensao(False)
        _resumir_seguro()
        _log("=== fim: %d simulações nesta sessão. Veja resumo.txt ==="
             % contagem["feitas"])


def _rodar_lista(ctx, plano, limite, contagem):
    """Roda o que falta do plano. Devolve False se mandaram parar."""
    feitos = _ids_feitos()
    pendentes = [it for it in plano if it["id"] not in feitos]
    parar = os.path.join(_pasta(), "PARAR.txt")
    _log("%d pendentes de %d nesta fase" % (len(pendentes), len(plano)), False)
    for n, item in enumerate(pendentes, 1):
        if os.path.exists(parar):
            _log("PARAR.txt encontrado: parando.", False)
            return False
        if time.time() > limite:
            _log("HORAS_MAXIMAS atingido: parando.", False)
            return False
        linha = _rodar_uma(ctx, item)
        _gravar_linha(linha)
        contagem["feitas"] += 1
        if linha["erro"]:
            _log("[%d/%d] %s %s ERRO: %s"
                 % (n, len(pendentes), item["etapa"], item["id"],
                    linha["erro"].strip().splitlines()[-1]), False)
        else:
            _log("[%d/%d] %-13s %-12s AUC %.3f  cap5 %.0f%%  área %.2f  "
                 "%.0f s" % (n, len(pendentes), item["etapa"], item["modo"],
                             linha["auc"], 100 * linha["captura_5"],
                             linha["razao_area"], linha["segundos"]), False)
        if contagem["feitas"] % 5 == 0:
            _resumir_seguro()
        _processar_eventos()
    return True


def _resumir_seguro():
    try:
        resumir(imprimir=False)
    except Exception:  # noqa: BLE001 - o resumo nunca derruba o laço
        _log("AVISO: falha ao escrever o resumo:\n" + traceback.format_exc(),
             False)


# =========================================================================
# resumo
# =========================================================================
def _ler_csv():
    with open(_caminho_csv(), encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo, delimiter=";"))
    for linha in linhas:
        for chave, valor in linha.items():
            if chave in ("id", "etapa", "modo", "erro", "alerta_semeia"):
                continue
            try:
                linha[chave] = float(valor) if valor != "" else float("nan")
            except ValueError:
                pass
    return linhas


def _mediana(valores):
    valores = [v for v in valores if isinstance(v, float) and not math.isnan(v)]
    return float(np.median(valores)) if valores else float("nan")


def resumir(imprimir=True):
    """Escreve resumo.txt a partir do resultados.csv (pode rodar a qualquer hora)."""
    linhas = _ler_csv()
    ok = [l for l in linhas if not l.get("erro")]
    erros = len(linhas) - len(ok)
    sims = [l for l in ok if l["etapa"] != "linha_de_base"]
    saida = []
    escrever = saida.append
    escrever("RESUMO DA CALIBRAÇÃO — %s" % _dt.datetime.now().strftime(
        "%d/%m/%Y %H:%M"))
    escrever("%d simulações válidas, %d com erro. Período %s a %s, "
             "resolução %.0f m." % (len(sims), erros, CONFIG["DATA_INICIAL"],
                                    CONFIG["DATA_FINAL"],
                                    CONFIG["RESOLUCAO_M"]))
    escrever("")
    escrever("Métricas: AUC (0,5 = acaso, 1 = perfeito) ordena o risco; "
             "cap5 = fração do garimpo novo observado nos 5%% da área de "
             "maior risco; área = área prevista / observada (1 = certo); "
             "r_blocos = correlação em blocos de %.0f m; perto_autos = o "
             "mesmo, só a até %.0f m dos autos do período."
             % (CONFIG["BLOCO_M"], CONFIG["RAIO_AUTOS_M"]))

    def tabela(titulo, grupo, chave_ordem, n=10, reverso=True):
        escrever("")
        escrever(titulo)
        escrever("  %-13s %-27s %6s %6s %6s %6s %6s  %s"
                 % ("etapa", "modo", "AUC", "cap5", "área", "r_bl", "p_aut",
                    "parâmetros"))
        validos = [l for l in grupo
                   if not math.isnan(l.get(chave_ordem, float("nan")))]
        validos.sort(key=lambda l: l[chave_ordem], reverse=reverso)
        for l in validos[:n]:
            params = ("" if l["etapa"] == "linha_de_base" else
                      "r0=%.4g α=%.2g Fmax=%.2g platô=%.0f d0=%.0f esc=%.0f"
                      % (l["r0"], l["alerta_peso"], l["fisc_max"],
                         l["fisc_plato_dias"], l["fisc_escala_m"],
                         l["escala_contagio_m"]))
            escrever("  %-13s %-27s %6.3f %6.3f %6s %6.3f %6s  %s"
                     % (l["etapa"], l["modo"], l["auc"], l["captura_5"],
                        _f(l.get("razao_area")), l["r_blocos"],
                        _f(l.get("perto_autos_razao")), params))

    escrever("")
    escrever("r0 calibrado (área prevista = observada, modo reconstrução): "
             "%.4g — usado como base nas etapas sensibilidade, fiscal e "
             "calibrado." % r0_calibrado())
    bases = [l for l in ok if l["etapa"] == "linha_de_base"]
    padrao = [l for l in ok if l["etapa"] in ("padrao", "calibrado")]
    tabela("1) LINHAS DE BASE × MODELO (padrão do plugin e com r0 calibrado; "
           "o modelo precisa superar as linhas de base)", bases + padrao,
           "auc", n=20)
    for modo in ("reconstrucao", "previsao"):
        tabela("2) MELHORES POR AUC — modo %s" % modo,
               [l for l in sims if l["modo"] == modo], "auc")
    tabela("3) MELHORES POR CAPTURA NOS 5% — modo reconstrucao",
           [l for l in sims if l["modo"] == "reconstrucao"], "captura_5")
    calibradas = [dict(l, erro_area=abs(math.log(l["razao_area"])))
                  for l in sims if l["modo"] == "reconstrucao"
                  and l.get("razao_area", 0) > 0]
    tabela("4) ÁREA NOVA MAIS PRÓXIMA DA OBSERVADA (calibra r0)",
           calibradas, "erro_area", reverso=False)

    rec = [dict(l, p_aut_rel=(l.get("perto_autos_razao", float("nan"))
                              / l["razao_area"]
                              if l.get("razao_area", 0) > 0 else float("nan")))
           for l in sims if l["modo"] == "reconstrucao"
           and l["etapa"] in ("fiscal", "aleatoria", "sensibilidade")]
    for chave, nome in (("fisc_plato_dias", "PLATÔ DA FISCALIZAÇÃO (dias)"),
                        ("fisc_escala_m", "ALCANCE d0 DA FISCALIZAÇÃO (m)"),
                        ("fisc_max", "F_max")):
        escrever("")
        escrever("5) EFEITO DE %s — medianas, etapa fiscal (fatorial) e, entre "
                 "parênteses, aleatória por faixa" % nome)
        escrever("  %-10s %4s %7s %7s %9s %9s %9s"
                 % ("valor", "n", "AUC", "cap5", "p_aut_AUC", "p_aut_área",
                    "p_aut_rel"))
        fiscal = [l for l in rec if l["etapa"] == "fiscal"]
        for valor in sorted({l[chave] for l in fiscal}):
            grupo = [l for l in fiscal if l[chave] == valor]
            escrever("  %-10s %4d %7.3f %7.3f %9.3f %9s %9s"
                     % ("%.4g" % valor, len(grupo),
                        _mediana([l["auc"] for l in grupo]),
                        _mediana([l["captura_5"] for l in grupo]),
                        _mediana([l.get("perto_autos_auc", float("nan"))
                                  for l in grupo]),
                        _f(_mediana([l.get("perto_autos_razao", float("nan"))
                                     for l in grupo])),
                        _f(_mediana([l["p_aut_rel"] for l in grupo]))))
        aleat = [l for l in rec if l["etapa"] == "aleatoria"
                 and not math.isnan(l.get(chave, float("nan")))]
        if len(aleat) >= 12:
            valores = sorted(l[chave] for l in aleat)
            cortes = [valores[int(len(valores) * q)] for q in (0.25, 0.5, 0.75)]
            faixas = [(-math.inf, cortes[0]), (cortes[0], cortes[1]),
                      (cortes[1], cortes[2]), (cortes[2], math.inf)]
            for a, b in faixas:
                grupo = [l for l in aleat if a < l[chave] <= b]
                rotulo = "(%s–%s)" % (_f(max(a, valores[0])), _f(min(b, valores[-1])))
                escrever("  %-10s %4d %7.3f %7.3f %9.3f"
                         % (rotulo, len(grupo),
                            _mediana([l["auc"] for l in grupo]),
                            _mediana([l["captura_5"] for l in grupo]),
                            _mediana([l.get("perto_autos_auc", float("nan"))
                                      for l in grupo])))
    escrever("")
    escrever("Leitura: compare primeiro (1). Em (5), diferenças de AUC "
             "menores que ~0,005 entre valores não distinguem nada; as colunas "
             "p_aut, perto dos autos, são as mais sensíveis à fiscalização. "
             "p_aut_rel = (área prevista/observada perto dos autos) ÷ (a "
             "mesma razão na área toda): perto de 1, a fiscalização simulada "
             "reproduz o entorno dos autos tão bem quanto o resto; abaixo de "
             "1, suprime demais; acima, de menos. Como r0 foi calibrado, "
             "p_aut_área perto de 1 indica o valor que melhor reproduz o "
             "entorno dos autos.")
    texto = "\n".join(saida)
    with open(os.path.join(_pasta(), "resumo.txt"), "w",
              encoding="utf-8") as arquivo:
        arquivo.write(texto + "\n")
    if imprimir:
        print(texto)
    return texto


def _f(valor):
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "—"
    return "%.3g" % valor


if __name__ != "__main__" or "qgis" in sys.modules:
    print("calibrar_noite carregado. Preencha CONFIG no arquivo e rode:")
    print("   verificar()      # confere os dados e estima o tempo")
    print("   rodar_noite()    # roda a noite toda")
    print("   resumir()        # refaz o resumo a partir do CSV")

# -*- coding: utf-8 -*-
"""Validação: expansão prevista × garimpo novo observado.

O modelo dinâmico entrega Δμ, a expansão prevista entre duas datas. Validar
é compará-la com o garimpo que de fato apareceu no mesmo período, num mapa
que o modelo não viu. Duas perguntas, com métricas distintas:

**Onde?** — ordenação. O modelo põe o risco nos lugares certos?

``auc``
    área sob a curva ROC: probabilidade de um pixel com garimpo novo ter
    risco maior que um pixel sem. 0,5 é o acaso; 1, a ordenação perfeita.
    Trata o alvo como binário (garimpo novo acima de ``limiar``).
``curva_captura`` / ``captura``
    fração da área nova observada que cai nos x% da área de maior risco
    (curva de taxa de sucesso). Pesa pelo tamanho do garimpo novo, ao
    contrário da AUC; é a leitura operacional: "fiscalizando os 5% de maior
    risco, alcança-se quanto do que aconteceu?".
``area_sob_captura``
    área sob a curva de captura: 0,5 é o acaso; perto de 1, todo o garimpo
    novo concentrado no topo do risco.

**Quanto?** — magnitude. ``razao_area`` = área nova prevista ÷ observada
(1 é o certo); é o que calibra r₀. A ordenação não depende dela.

**Em que escala?** — ``correlacao_blocos`` correlaciona as somas em blocos
(ex.: 1 km), tolerando erros de posição menores que o bloco;
``comparar_zonas`` faz o mesmo por sub-bacia, a unidade de priorização
do produto, com a correlação de postos (Spearman) e a coincidência entre
as sub-bacias de topo previstas e observadas.

Empates — inevitáveis, porque o risco é zero em boa parte da área — são
tratados por posto médio na AUC e por rateio proporcional na captura, de
modo que nenhuma métrica dependa da ordem dos pixels no raster.

Este módulo é NumPy puro: recebe arrays já alinhados na grade de trabalho.
"""

import numpy as np

from .agregacao import somar_por_zona
from .kernels import kernel_contagio
from .numerico import convolver

FRACOES_PADRAO = (0.01, 0.05, 0.10, 0.20)


def garimpo_novo(mu_inicial, mu_final, universo=None):
    """Garimpo novo observado: ``máx(μ_final − μ_inicial, 0)``, zero fora do universo."""
    novo = np.clip(np.asarray(mu_final, dtype=np.float64)
                   - np.asarray(mu_inicial, dtype=np.float64), 0.0, None)
    novo = np.nan_to_num(novo, nan=0.0, posinf=0.0, neginf=0.0)
    if universo is not None:
        novo = np.where(np.asarray(universo, dtype=bool), novo, 0.0)
    return novo


# ------------------------------------------------------------------- ordenação
def postos_medios(valores):
    """Postos de 1 a n, com empates recebendo a média dos postos que ocupam."""
    valores = np.asarray(valores, dtype=np.float64).ravel()
    _, inverso, contagens = np.unique(valores, return_inverse=True,
                                      return_counts=True)
    fim = np.cumsum(contagens).astype(np.float64)
    return (fim - (contagens - 1) / 2.0)[inverso.ravel()]


def auc(pontuacao, alvo):
    """AUC (estatística de Mann-Whitney), com empates por posto médio.

    ``nan`` quando o alvo é todo verdadeiro ou todo falso.
    """
    alvo = np.asarray(alvo, dtype=bool).ravel()
    n1 = int(alvo.sum())
    n0 = alvo.size - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    soma = float(postos_medios(pontuacao)[alvo].sum())
    return (soma - n1 * (n1 + 1) / 2.0) / (float(n1) * n0)


def _degraus_captura(pontuacao, observado):
    """Área acumulada e captura acumulada, por nível de risco decrescente."""
    pontuacao = np.asarray(pontuacao, dtype=np.float64).ravel()
    observado = np.asarray(observado, dtype=np.float64).ravel()
    total = observado.sum()
    if pontuacao.size == 0 or total <= 0:
        return None, None
    niveis, inverso, contagens = np.unique(-pontuacao, return_inverse=True,
                                           return_counts=True)
    por_nivel = np.bincount(inverso.ravel(), weights=observado,
                            minlength=niveis.size)
    area = np.concatenate([[0.0], np.cumsum(contagens) / float(pontuacao.size)])
    capturado = np.concatenate([[0.0], np.cumsum(por_nivel) / total])
    return area, capturado


def curva_captura(pontuacao, observado, n_pontos=101):
    """Curva de captura em ``n_pontos`` frações de área de 0 a 1.

    Devolve ``(fracao_da_area, fracao_capturada)``. Dentro de um grupo de
    pixels empatados a curva é reta: o grupo é rateado em proporção à área,
    sem favorecer nenhuma ordem.
    """
    area, capturado = _degraus_captura(pontuacao, observado)
    fracoes = np.linspace(0.0, 1.0, int(n_pontos))
    if area is None:
        return fracoes, np.full(fracoes.shape, np.nan)
    return fracoes, np.interp(fracoes, area, capturado)


def captura(pontuacao, observado, fracao_area):
    """Fração do garimpo novo observado nos ``fracao_area`` de maior risco."""
    area, capturado = _degraus_captura(pontuacao, observado)
    if area is None:
        return float("nan")
    return float(np.interp(float(fracao_area), area, capturado))


def area_sob_captura(pontuacao, observado):
    """Área sob a curva de captura (exata, pelos degraus): 0,5 é o acaso."""
    area, capturado = _degraus_captura(pontuacao, observado)
    if area is None:
        return float("nan")
    return float(np.sum(np.diff(area) * (capturado[1:] + capturado[:-1]) / 2.0))


# ----------------------------------------------------------------- escala
def _somar_blocos(arr, fator):
    arr = np.asarray(arr, dtype=np.float64)
    ny, nx = arr.shape
    arr = np.pad(arr, ((0, (-ny) % fator), (0, (-nx) % fator)))
    return arr.reshape(arr.shape[0] // fator, fator,
                       arr.shape[1] // fator, fator).sum(axis=(1, 3))


def _pearson(a, b):
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.size < 2 or a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def correlacao_blocos(previsto, observado, fator):
    """Correlação de Pearson entre as somas em blocos de ``fator`` × ``fator`` pixels."""
    fator = max(int(fator), 1)
    if fator == 1:
        return _pearson(previsto, observado)
    return _pearson(_somar_blocos(previsto, fator),
                    _somar_blocos(observado, fator))


def spearman(a, b):
    """Correlação de postos de Spearman (Pearson sobre postos médios)."""
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.size < 2:
        return float("nan")
    return _pearson(postos_medios(a), postos_medios(b))


def comparar_zonas(previsto, observado, zonas, n_zonas, area_ha,
                   fracao_topo=0.10):
    """Compara previsto e observado por sub-bacia (ou qualquer zoneamento).

    ``zonas`` traz o índice da zona de cada pixel (``-1`` fora de todas).

    Returns
    -------
    (list[dict], dict)
        Uma linha por zona — ``prev_ha``, ``obs_ha``, ``rank_prev``,
        ``rank_obs`` (1 = maior; empates na ordem das zonas) — e o resumo:
        ``spearman`` entre as áreas previstas e observadas; ``topo_comum``,
        a fração das ``fracao_topo`` zonas de maior expansão observada que
        também estão entre as de maior expansão prevista.
    """
    previsto_soma, _ = somar_por_zona(zonas, previsto, n_zonas)
    observado_soma, _ = somar_por_zona(zonas, observado, n_zonas)
    prev_ha = previsto_soma * float(area_ha)
    obs_ha = observado_soma * float(area_ha)

    def _rank(valores):
        ordem = np.argsort(-valores, kind="stable")
        rank = np.empty(n_zonas, dtype=np.int64)
        rank[ordem] = np.arange(1, n_zonas + 1)
        return rank

    rank_prev, rank_obs = _rank(prev_ha), _rank(obs_ha)
    linhas = [{"prev_ha": float(prev_ha[i]), "obs_ha": float(obs_ha[i]),
               "rank_prev": int(rank_prev[i]), "rank_obs": int(rank_obs[i])}
              for i in range(n_zonas)]

    k = max(int(round(fracao_topo * n_zonas)), 1) if n_zonas else 0
    topo_comum = float("nan")
    if k and (obs_ha > 0).any():
        topo_obs = set(np.nonzero(rank_obs <= k)[0].tolist())
        topo_prev = set(np.nonzero(rank_prev <= k)[0].tolist())
        topo_comum = len(topo_obs & topo_prev) / float(k)
    resumo = {"zonas": int(n_zonas),
              "zonas_com_garimpo_novo": int((obs_ha > 0).sum()),
              "spearman": spearman(prev_ha, obs_ha),
              "topo_comum": topo_comum, "topo_k": int(k)}
    return linhas, resumo


# --------------------------------------------------------------- avaliação
def avaliar(previsto, observado, universo=None, pixel_m=1.0, limiar=0.02,
            fracoes=FRACOES_PADRAO, bloco_m=1000.0, magnitude=True):
    """Todas as métricas de uma pontuação contra o garimpo novo observado.

    Parameters
    ----------
    previsto : ndarray
        Δμ previsto (ou qualquer pontuação de risco, como uma linha de base).
    observado : ndarray
        Garimpo novo observado, em fração do pixel (ver ``garimpo_novo``).
    universo : ndarray[bool] ou None
        Pixels avaliados — a área de estudo inteira, inclusive onde K = 0:
        garimpo novo fora de K é erro do modelo e deve contar.
    limiar : float
        Garimpo novo acima disto é o alvo positivo da AUC.
    magnitude : bool
        ``False`` para pontuações sem unidade de área (linhas de base):
        omite as razões de área.
    """
    previsto = np.nan_to_num(np.asarray(previsto, dtype=np.float64))
    observado = np.asarray(observado, dtype=np.float64)
    if previsto.shape != observado.shape:
        raise ValueError("previsto %s e observado %s em grades diferentes"
                         % (previsto.shape, observado.shape))
    if universo is None:
        universo = np.ones(previsto.shape, dtype=bool)
    universo = np.asarray(universo, dtype=bool)
    p = previsto[universo]
    o = observado[universo]
    area_ha = float(pixel_m) ** 2 / 10000.0

    metricas = {
        "pixels": int(universo.sum()),
        "pixels_com_garimpo_novo": int((o > limiar).sum()),
        "auc": auc(p, o > limiar),
        "area_sob_captura": area_sob_captura(p, o),
        "obs_ha": float(o.sum() * area_ha),
    }
    for fracao in fracoes:
        metricas["captura_%g" % (100 * fracao)] = captura(p, o, fracao)
    fator = max(int(round(float(bloco_m) / float(pixel_m))), 1)
    metricas["bloco_m"] = fator * float(pixel_m)
    metricas["r_blocos"] = correlacao_blocos(
        np.where(universo, previsto, 0.0), np.where(universo, observado, 0.0),
        fator)
    if magnitude:
        metricas["prev_ha"] = float(p.sum() * area_ha)
        metricas["razao_area"] = (metricas["prev_ha"] / metricas["obs_ha"]
                                  if metricas["obs_ha"] > 0 else float("nan"))
    return metricas


def linhas_de_base(K, mu_inicial, pixel_m, escala_m=380.0):
    """Pontuações sem dinâmica, que o modelo precisa superar para valer a pena.

    ``so_K``
        só o potencial do terreno.
    ``remanescente``
        o que resta do potencial, ``máx(K − μ, 0)``.
    ``proximidade``
        a vizinhança do garimpo existente (μ convoluído pelo núcleo de
        contágio), isto é, "o garimpo cresce perto de onde já está".
    ``proximidade_x_remanescente``
        a proximidade onde ainda há potencial: o modelo dinâmico num passo
        só, sem eventos.
    """
    K = np.nan_to_num(np.asarray(K, dtype=np.float32))
    mu = np.clip(np.nan_to_num(np.asarray(mu_inicial, dtype=np.float32)),
                 0.0, None)
    proximidade = convolver(mu, kernel_contagio(escala_m, pixel_m))
    remanescente = np.clip(K - mu, 0.0, None)
    return {"so_K": K, "remanescente": remanescente,
            "proximidade": proximidade,
            "proximidade_x_remanescente": proximidade * remanescente}

# -*- coding: utf-8 -*-
"""Potencial Aurífero Remanescente — o ramo "(−)" do fluxograma.

A favorabilidade diz quanto o terreno comporta; o garimpo pretérito diz
quanto já foi tomado. A diferença é o que resta:

    R(x) = máx(K(x) − μ(x), 0)

Tudo em **proporção da área do pixel**, de modo que a soma por sub-bacia sai
em hectares equivalentes e é lida diretamente.

Uma distinção que decide o método
---------------------------------
Agregar por sub-bacia é legítimo **aqui** e não era antes. A diferença não é
de gosto: é de tipo de grandeza.

- **Intensiva** (PGgeom, Est, Lt): a média por sub-bacia destrói o sinal.
  Bordas de baixão ocupam fração pequena de uma sub-bacia de mais de 100 km²;
  a média as dilui abaixo do ruído. Foi o que aconteceu com o PGgeom (ρ =
  +0,017 agregado, contra concentração de 90× por pixel) e com o Lt_local.
- **Extensiva** (K, μ, R em hectares): a **soma** conserva a grandeza. Somar
  o resultado depois de calculá-lo no pixel não perde nada — é a mesma regra
  do modelo dinâmico, em que a sub-bacia é unidade de saída e nunca de
  propagação.

Por isso a ordem é: combinar no pixel, subtrair no pixel, **somar** por
sub-bacia. Nunca o contrário.

Onde μ passa de K
-----------------
Não é erro a ser escondido: é sinal. Ou K está subestimado ali, ou μ inclui
coisa que o modelo de terreno não cobre — balsa no leito, rejeito,
retrabalho. O excedente é reportado em coluna própria, e a soma dele é uma
métrica de aderência do modelo estático aos dados.
"""

import numpy as np

from .agregacao import _escrever_gpkg, rasterizar_zonas, somar_por_zona


def remanescente(K, mu):
    """R = máx(K − μ, 0): a proporção da área que ainda cabe."""
    K = np.asarray(K, dtype=np.float32)
    mu = np.clip(np.asarray(mu, dtype=np.float32), 0.0, 1.0)
    return np.maximum(K - mu, 0.0).astype(np.float32)


def excedente(K, mu):
    """máx(μ − K, 0): já explorado além do que o modelo comporta."""
    K = np.asarray(K, dtype=np.float32)
    mu = np.clip(np.asarray(mu, dtype=np.float32), 0.0, 1.0)
    return np.maximum(mu - K, 0.0).astype(np.float32)


def grau_de_explotacao(K, mu):
    """μ/K, truncado em [0, 1]. É o que satura o termo logístico.

    Onde K = 0 o grau é 1 por convenção: não há o que explorar, portanto
    nada resta.
    """
    K = np.asarray(K, dtype=np.float64)
    mu = np.clip(np.asarray(mu, dtype=np.float64), 0.0, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        grau = np.where(K > 0, mu / np.maximum(K, 1e-12), 1.0)
    return np.clip(np.nan_to_num(grau, nan=1.0), 0.0, 1.0).astype(np.float32)


def resumo_global(K, mu, pixel_m, dentro=None):
    """Totais da área de estudo, em hectares equivalentes."""
    if dentro is None:
        dentro = np.ones(np.shape(K), dtype=bool)
    area_ha = (float(pixel_m) ** 2) / 10000.0
    k = np.asarray(K, dtype=np.float64)[dentro]
    m = np.clip(np.asarray(mu, dtype=np.float64)[dentro], 0.0, 1.0)
    r = np.maximum(k - m, 0.0)
    exc = np.maximum(m - k, 0.0)
    total_k = float(k.sum() * area_ha)
    return {
        "potencial_ha": total_k,
        "garimpado_ha": float(m.sum() * area_ha),
        "remanescente_ha": float(r.sum() * area_ha),
        "excedente_ha": float(exc.sum() * area_ha),
        "explotacao": float(m.sum() / k.sum()) if k.sum() > 0 else 1.0,
        "area_km2": float(dentro.sum() * (float(pixel_m) ** 2) / 1e6),
    }


def agregar_potencial(K, mu, grade, camada_zonas, mu_anterior=None,
                      campo_id=None, caminho_saida=None, dentro=None,
                      progresso=None):
    """Tabela de Potencial Aurífero Remanescente por sub-bacia.

    Colunas geradas:

    ``pa_k_ha``      potencial total do terreno (Σ K), hectares equivalentes
    ``pa_mu_ha``     área total garimpada na sub-bacia (Σ μ)
    ``pa_rem_ha``    **potencial remanescente** (Σ máx(K−μ, 0))
    ``pa_expl``      grau de explotação garimpeira: μ/K agregado, em [0, 1]
    ``pa_exc_ha``    excedente (Σ máx(μ−K, 0)) — diagnóstico de aderência
    ``pa_var_ha``    variação no último ano (Σ Δμ), se μ anterior for dado
    ``pa_anos``      anos até esgotar o remanescente no ritmo do último ano
                     (``-1`` quando não houve avanço, isto é, sem ritmo a
                     projetar)
    ``pa_px``        pixels da sub-bacia dentro da grade
    ``pa_rank``      posição por potencial remanescente (1 = maior)
    """
    zonas, feicoes = rasterizar_zonas(camada_zonas, grade, progresso)
    n = len(feicoes)
    if n == 0:
        raise ValueError("a camada de sub-bacias não tem feições válidas")

    zonas = np.asarray(zonas, dtype=np.int64)
    if dentro is not None:
        zonas = np.where(np.asarray(dentro, dtype=bool), zonas, -1)

    area_ha = (grade.pixel_m ** 2) / 10000.0
    mu = np.clip(np.asarray(mu, dtype=np.float64), 0.0, 1.0)
    K = np.asarray(K, dtype=np.float64)

    k_soma, contagem = somar_por_zona(zonas, K, n)
    mu_soma, _ = somar_por_zona(zonas, mu, n)
    rem_soma, _ = somar_por_zona(zonas, np.maximum(K - mu, 0.0), n)
    exc_soma, _ = somar_por_zona(zonas, np.maximum(mu - K, 0.0), n)

    if mu_anterior is not None:
        anterior = np.clip(np.asarray(mu_anterior, dtype=np.float64), 0.0, 1.0)
        var_soma, _ = somar_por_zona(zonas, mu - anterior, n)
    else:
        var_soma = np.full(n, np.nan)

    k_ha = k_soma * area_ha
    mu_ha = mu_soma * area_ha
    rem_ha = rem_soma * area_ha
    exc_ha = exc_soma * area_ha
    var_ha = var_soma * area_ha

    with np.errstate(divide="ignore", invalid="ignore"):
        explotacao = np.where(k_ha > 0, np.clip(mu_ha / k_ha, 0.0, 1.0), 1.0)
        anos = np.where(np.isfinite(var_ha) & (var_ha > 0),
                        rem_ha / np.maximum(var_ha, 1e-12), -1.0)

    ordem = np.argsort(-rem_ha, kind="stable")
    rank = np.empty(n, dtype=np.int64)
    rank[ordem] = np.arange(1, n + 1)

    nomes_campos = None
    linhas = []
    for i, feicao in enumerate(feicoes):
        if nomes_campos is None:
            nomes_campos = [f.name() for f in feicao.fields()]
        identificador = (feicao[campo_id]
                         if campo_id and campo_id in nomes_campos
                         else feicao.id())
        linha = {
            "id": identificador,
            "pa_k_ha": float(k_ha[i]),
            "pa_mu_ha": float(mu_ha[i]),
            "pa_rem_ha": float(rem_ha[i]),
            "pa_expl": float(explotacao[i]),
            "pa_exc_ha": float(exc_ha[i]),
            "pa_px": int(contagem[i]),
            "pa_rank": int(rank[i]),
        }
        if mu_anterior is not None:
            linha["pa_var_ha"] = float(var_ha[i])
            linha["pa_anos"] = float(anos[i])
        linhas.append(linha)

    if caminho_saida:
        _escrever_gpkg(caminho_saida, camada_zonas, feicoes, linhas)
    return linhas

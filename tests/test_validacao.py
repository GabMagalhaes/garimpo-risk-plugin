# -*- coding: utf-8 -*-
"""Testes das métricas de validação (NumPy puro)."""

import itertools
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risco_garimpo.core import validacao as v  # noqa: E402


def _auc_forca_bruta(pontuacao, alvo):
    pos = [p for p, a in zip(pontuacao, alvo) if a]
    neg = [p for p, a in zip(pontuacao, alvo) if not a]
    pares = [1.0 if p > n else 0.5 if p == n else 0.0
             for p, n in itertools.product(pos, neg)]
    return sum(pares) / len(pares)


# ------------------------------------------------------------------- AUC
def test_auc_bate_com_a_definicao_inclusive_com_empates():
    rng = np.random.default_rng(3)
    for _ in range(20):
        pontuacao = rng.integers(0, 4, 50).astype(float)   # muitos empates
        alvo = rng.random(50) < 0.3
        if alvo.all() or not alvo.any():
            continue
        assert v.auc(pontuacao, alvo) == \
            pytest.approx(_auc_forca_bruta(pontuacao, alvo))


def test_auc_casos_limite():
    alvo = np.array([True, False, True, False])
    assert v.auc([4, 1, 3, 2], alvo) == 1.0
    assert v.auc([1, 4, 2, 3], alvo) == 0.0
    assert v.auc([0, 0, 0, 0], alvo) == 0.5            # risco constante
    assert np.isnan(v.auc([1, 2, 3, 4], np.zeros(4, bool)))


# --------------------------------------------------------------- captura
def test_captura_reparte_empates_na_fronteira():
    pontuacao = np.array([3.0, 2.0] + [0.0] * 8)
    observado = np.array([1.0, 0.0, 1.0, 1.0, 0, 0, 0, 0, 0, 1.0])
    # 30% da área: os dois primeiros e 1 dos 8 empatados em zero,
    # que levam 3 de garimpo: 1 + 3/8 de um total de 4
    assert v.captura(pontuacao, observado, 0.3) == \
        pytest.approx((1.0 + 3.0 / 8.0) / 4.0)
    assert v.captura(pontuacao, observado, 0.0) == 0.0
    assert v.captura(pontuacao, observado, 1.0) == pytest.approx(1.0)


def test_captura_nao_depende_da_ordem_dos_pixels():
    rng = np.random.default_rng(5)
    pontuacao = rng.integers(0, 3, 200).astype(float)
    observado = rng.random(200) * (rng.random(200) < 0.2)
    ordem = rng.permutation(200)
    for fracao in (0.05, 0.1, 0.37):
        assert v.captura(pontuacao, observado, fracao) == pytest.approx(
            v.captura(pontuacao[ordem], observado[ordem], fracao))


def test_curva_vai_de_zero_a_um_e_nunca_desce():
    rng = np.random.default_rng(7)
    pontuacao = rng.random(500)
    observado = rng.random(500) * (rng.random(500) < 0.1)
    area, capturado = v.curva_captura(pontuacao, observado, n_pontos=51)
    assert area[0] == 0.0 and area[-1] == 1.0
    assert capturado[0] == 0.0 and capturado[-1] == pytest.approx(1.0)
    assert (np.diff(capturado) >= -1e-12).all()
    assert capturado[10] == pytest.approx(v.captura(pontuacao, observado,
                                                    area[10]))


def test_area_sob_captura_acaso_e_ordem_perfeita():
    rng = np.random.default_rng(11)
    n = 20000
    observado = (rng.random(n) < 0.05).astype(float)
    acaso = v.area_sob_captura(rng.random(n), observado)
    assert acaso == pytest.approx(0.5, abs=0.02)
    # ordem perfeita: tudo capturado na fração p da área → 1 − p/2
    p = observado.mean()
    assert v.area_sob_captura(observado, observado) == \
        pytest.approx(1.0 - p / 2.0)
    assert v.area_sob_captura(np.zeros(n), observado) == pytest.approx(0.5)


def test_sem_garimpo_novo_as_metricas_de_captura_ficam_indefinidas():
    zeros = np.zeros(10)
    assert np.isnan(v.captura(np.arange(10.0), zeros, 0.1))
    assert np.isnan(v.area_sob_captura(np.arange(10.0), zeros))
    _, capturado = v.curva_captura(np.arange(10.0), zeros)
    assert np.isnan(capturado).all()


# ------------------------------------------------------- escala e zonas
def test_spearman_com_empates():
    a = [1, 2, 2, 3, 5, 5, 5, 8]
    b = [2, 1, 4, 4, 6, 7, 7, 9]
    stats = pytest.importorskip("scipy.stats")
    assert v.spearman(a, b) == pytest.approx(stats.spearmanr(a, b)[0])
    assert v.spearman(a, a) == pytest.approx(1.0)


def test_correlacao_em_blocos_tolera_deslocamento_menor_que_o_bloco():
    observado = np.zeros((40, 40))
    previsto = np.zeros((40, 40))
    for (i, j) in ((3, 3), (12, 25), (30, 8), (35, 35)):
        observado[i, j] = 1.0
        previsto[i + 1, j + 1] = 1.0             # um pixel ao lado
    assert v.correlacao_blocos(previsto, observado, 1) < 0.0
    assert v.correlacao_blocos(previsto, observado, 10) > 0.9


def test_comparar_zonas():
    zonas = np.array([[0, 0, 1, 1],
                      [2, 2, -1, -1]])
    previsto = np.array([[1.0, 1.0, 0.5, 0.0],
                         [0.0, 0.1, 9.0, 9.0]])          # fora: ignorado
    observado = np.array([[0.8, 0.8, 0.0, 0.2],
                          [0.0, 0.0, 9.0, 9.0]])
    linhas, resumo = v.comparar_zonas(previsto, observado, zonas, 3,
                                      area_ha=1.0, fracao_topo=0.34)
    assert [l["prev_ha"] for l in linhas] == pytest.approx([2.0, 0.5, 0.1])
    assert [l["obs_ha"] for l in linhas] == pytest.approx([1.6, 0.2, 0.0])
    assert [l["rank_prev"] for l in linhas] == [1, 2, 3]
    assert [l["rank_obs"] for l in linhas] == [1, 2, 3]
    assert resumo["spearman"] == pytest.approx(1.0)
    assert resumo["topo_k"] == 1 and resumo["topo_comum"] == 1.0
    assert resumo["zonas_com_garimpo_novo"] == 2


# --------------------------------------------------------------- avaliar
def test_avaliar_respeita_o_universo_e_as_unidades():
    observado = np.zeros((10, 10))
    observado[2, 2] = 0.5
    observado[9, 9] = 1.0                       # fora do universo
    previsto = np.zeros((10, 10))
    previsto[2, 2] = 0.25
    universo = np.ones((10, 10), dtype=bool)
    universo[9, 9] = False
    m = v.avaliar(previsto, observado, universo, pixel_m=100.0, limiar=0.1)
    assert m["pixels"] == 99
    assert m["pixels_com_garimpo_novo"] == 1
    assert m["obs_ha"] == pytest.approx(0.5)       # 0,5 × 1 ha
    assert m["prev_ha"] == pytest.approx(0.25)
    assert m["razao_area"] == pytest.approx(0.5)
    assert m["auc"] == 1.0
    # 1% de 99 pixels é 0,99 pixel: captura 0,99 do único pixel com garimpo
    assert m["captura_1"] == pytest.approx(0.99)
    assert m["captura_5"] == pytest.approx(1.0)
    assert set(m) >= {"captura_5", "captura_10", "captura_20", "r_blocos",
                      "area_sob_captura", "bloco_m"}
    base = v.avaliar(previsto, observado, universo, 100.0, magnitude=False)
    assert "razao_area" not in base and "prev_ha" not in base


def test_avaliar_recusa_grades_diferentes():
    with pytest.raises(ValueError):
        v.avaliar(np.zeros((3, 3)), np.zeros((3, 4)))


def test_garimpo_novo_ignora_recuo_e_fora_do_universo():
    mu0 = np.array([[0.5, 0.2], [0.0, np.nan]])
    mu1 = np.array([[0.3, 0.6], [0.4, 0.9]])
    universo = np.array([[True, True], [False, True]])
    np.testing.assert_allclose(v.garimpo_novo(mu0, mu1, universo),
                               [[0.0, 0.4], [0.0, 0.0]])


def test_linhas_de_base():
    K = np.full((30, 30), 0.6, dtype=np.float32)
    mu = np.zeros((30, 30), dtype=np.float32)
    mu[15, 15] = 0.6
    bases = v.linhas_de_base(K, mu, pixel_m=100.0)
    assert set(bases) == {"so_K", "remanescente", "proximidade",
                          "proximidade_x_remanescente"}
    assert bases["remanescente"][15, 15] == 0.0
    proximidade = bases["proximidade"]
    assert proximidade[15, 16] > proximidade[15, 25] > proximidade[0, 0]
    assert bases["proximidade_x_remanescente"][15, 15] == 0.0

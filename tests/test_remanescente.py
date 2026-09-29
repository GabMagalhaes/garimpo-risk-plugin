# -*- coding: utf-8 -*-
"""Testes do Potencial Aurífero Remanescente (o ramo "(−)" do fluxograma)."""

import os
import sys
import types

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risco_garimpo.core.remanescente import (  # noqa: E402
    excedente, grau_de_explotacao, remanescente, resumo_global)


# ------------------------------------------------------------------ subtração
def test_remanescente_e_a_diferenca():
    K = np.array([0.50, 0.30, 0.10])
    mu = np.array([0.20, 0.30, 0.00])
    assert np.allclose(remanescente(K, mu), [0.30, 0.00, 0.10])


def test_remanescente_nunca_e_negativo():
    """Onde já se garimpou além do que o terreno comporta, resta zero."""
    assert np.allclose(remanescente(np.array([0.2]), np.array([0.9])), [0.0])


def test_excedente_registra_o_que_passou_de_k():
    """Não é erro a esconder: é sinal de K subestimado ou de μ que o
    terreno não explica (balsa, rejeito, retrabalho)."""
    K = np.array([0.20, 0.80])
    mu = np.array([0.90, 0.10])
    assert np.allclose(excedente(K, mu), [0.70, 0.00])


def test_remanescente_e_excedente_sao_complementares():
    rng = np.random.default_rng(0)
    K = rng.random(1000)
    mu = rng.random(1000)
    assert np.allclose(remanescente(K, mu) - excedente(K, mu), K - mu,
                       atol=1e-6)


def test_mu_acima_de_um_e_truncado():
    assert remanescente(np.array([0.5]), np.array([3.0]))[0] == 0.0


# --------------------------------------------------------------- explotação
def test_grau_de_explotacao_e_a_fracao_tomada():
    grau = grau_de_explotacao(np.array([0.4, 0.4]), np.array([0.1, 0.4]))
    assert grau[0] == pytest.approx(0.25)
    assert grau[1] == pytest.approx(1.0)


def test_onde_k_e_zero_nada_resta():
    """Convenção: sem capacidade não há remanescente, logo grau 1."""
    assert grau_de_explotacao(np.array([0.0]), np.array([0.0]))[0] == \
        pytest.approx(1.0)


def test_grau_e_truncado_em_um():
    assert grau_de_explotacao(np.array([0.1]), np.array([0.9]))[0] == \
        pytest.approx(1.0)


# ------------------------------------------------------------------ totais
def test_resumo_converte_para_hectares():
    K = np.full((10, 10), 0.5)
    mu = np.full((10, 10), 0.2)
    r = resumo_global(K, mu, pixel_m=100.0)
    # 100 px de 1 ha; K soma 50 ha, mu 20 ha, resta 30 ha
    assert r["potencial_ha"] == pytest.approx(50.0)
    assert r["garimpado_ha"] == pytest.approx(20.0)
    assert r["remanescente_ha"] == pytest.approx(30.0)
    assert r["explotacao"] == pytest.approx(0.4)
    assert r["area_km2"] == pytest.approx(1.0)


def test_resumo_respeita_a_mascara():
    K = np.full((10, 10), 0.5)
    mu = np.zeros((10, 10))
    dentro = np.zeros((10, 10), dtype=bool)
    dentro[:5, :] = True
    r = resumo_global(K, mu, pixel_m=100.0, dentro=dentro)
    assert r["potencial_ha"] == pytest.approx(25.0)
    assert r["area_km2"] == pytest.approx(0.5)


def test_resumo_soma_o_excedente_separado():
    K = np.array([[0.2, 0.8]])
    mu = np.array([[0.9, 0.1]])
    r = resumo_global(K, mu, pixel_m=100.0)
    assert r["excedente_ha"] == pytest.approx(0.7)
    assert r["remanescente_ha"] == pytest.approx(0.7)


# --------------------------------------------------------------- agregação
class _Campo(object):
    def __init__(self, nome):
        self._n = nome

    def name(self):
        return self._n


class _Feicao(object):
    def __init__(self, identificador, cod):
        self._id = identificador
        self._cod = cod

    def id(self):
        return self._id

    def fields(self):
        return [_Campo("HYBAS_ID")]

    def __getitem__(self, chave):
        if chave == "HYBAS_ID":
            return self._cod
        raise KeyError(chave)


class _Grade(object):
    def __init__(self, pixel_m=100.0):
        self.pixel_m = pixel_m


def _aparelhar_zonas(monkeypatch, zonas, feicoes):
    import risco_garimpo.core.remanescente as mod
    monkeypatch.setattr(mod, "rasterizar_zonas",
                        lambda camada, grade, progresso=None:
                        (zonas, feicoes))
    gravados = {}
    monkeypatch.setattr(mod, "_escrever_gpkg",
                        lambda caminho, camada, feicoes_, linhas:
                        gravados.update(caminho=caminho, linhas=linhas))
    return gravados


def test_agregacao_soma_por_subbacia(monkeypatch):
    from risco_garimpo.core.remanescente import agregar_potencial

    zonas = np.array([[0, 0, 1, 1],
                      [0, 0, 1, 1]], dtype=np.int64)
    feicoes = [_Feicao(1, "A"), _Feicao(2, "B")]
    _aparelhar_zonas(monkeypatch, zonas, feicoes)

    K = np.full((2, 4), 0.5)
    mu = np.zeros((2, 4))
    mu[:, :2] = 0.5                      # zona A totalmente explotada

    linhas = agregar_potencial(K, mu, _Grade(), camada_zonas=object(),
                               campo_id="HYBAS_ID")
    por_id = {linha["id"]: linha for linha in linhas}
    # 4 px de 1 ha em cada zona, K = 0.5 -> 2 ha de potencial por zona
    assert por_id["A"]["pa_k_ha"] == pytest.approx(2.0)
    assert por_id["A"]["pa_mu_ha"] == pytest.approx(2.0)
    assert por_id["A"]["pa_rem_ha"] == pytest.approx(0.0)
    assert por_id["A"]["pa_expl"] == pytest.approx(1.0)
    assert por_id["B"]["pa_rem_ha"] == pytest.approx(2.0)
    assert por_id["B"]["pa_expl"] == pytest.approx(0.0)


def test_ranking_e_por_remanescente(monkeypatch):
    from risco_garimpo.core.remanescente import agregar_potencial

    zonas = np.array([[0, 1]], dtype=np.int64)
    feicoes = [_Feicao(1, "pobre"), _Feicao(2, "rica")]
    _aparelhar_zonas(monkeypatch, zonas, feicoes)
    linhas = agregar_potencial(np.array([[0.1, 0.9]]), np.zeros((1, 2)),
                               _Grade(), object(), campo_id="HYBAS_ID")
    por_id = {linha["id"]: linha for linha in linhas}
    assert por_id["rica"]["pa_rank"] == 1
    assert por_id["pobre"]["pa_rank"] == 2


def test_variacao_e_anos_ate_esgotar(monkeypatch):
    from risco_garimpo.core.remanescente import agregar_potencial

    zonas = np.zeros((1, 10), dtype=np.int64)
    feicoes = [_Feicao(1, "A")]
    _aparelhar_zonas(monkeypatch, zonas, feicoes)

    K = np.full((1, 10), 0.5)            # 5 ha de potencial
    mu = np.full((1, 10), 0.3)           # 3 ha tomados
    anterior = np.full((1, 10), 0.2)     # avançou 1 ha no último ano

    linha = agregar_potencial(K, mu, _Grade(), object(), mu_anterior=anterior,
                              campo_id="HYBAS_ID")[0]
    assert linha["pa_rem_ha"] == pytest.approx(2.0)
    assert linha["pa_var_ha"] == pytest.approx(1.0)
    assert linha["pa_anos"] == pytest.approx(2.0)


def test_sem_avanco_nao_ha_ritmo_a_projetar(monkeypatch):
    from risco_garimpo.core.remanescente import agregar_potencial

    zonas = np.zeros((1, 4), dtype=np.int64)
    _aparelhar_zonas(monkeypatch, zonas, [_Feicao(1, "A")])
    linha = agregar_potencial(np.full((1, 4), 0.5), np.full((1, 4), 0.1),
                              _Grade(), object(),
                              mu_anterior=np.full((1, 4), 0.1),
                              campo_id="HYBAS_ID")[0]
    assert linha["pa_var_ha"] == pytest.approx(0.0)
    assert linha["pa_anos"] == -1.0


def test_mascara_tira_pixel_da_subbacia(monkeypatch):
    """Furo de corpo d'água não conta área nem potencial."""
    from risco_garimpo.core.remanescente import agregar_potencial

    zonas = np.zeros((1, 4), dtype=np.int64)
    _aparelhar_zonas(monkeypatch, zonas, [_Feicao(1, "A")])
    dentro = np.array([[True, True, False, False]])
    linha = agregar_potencial(np.full((1, 4), 0.5), np.zeros((1, 4)),
                              _Grade(), object(), campo_id="HYBAS_ID",
                              dentro=dentro)[0]
    assert linha["pa_px"] == 2
    assert linha["pa_k_ha"] == pytest.approx(1.0)


def test_excedente_aparece_na_tabela(monkeypatch):
    from risco_garimpo.core.remanescente import agregar_potencial

    zonas = np.zeros((1, 2), dtype=np.int64)
    _aparelhar_zonas(monkeypatch, zonas, [_Feicao(1, "A")])
    linha = agregar_potencial(np.array([[0.2, 0.2]]), np.array([[0.9, 0.9]]),
                              _Grade(), object(), campo_id="HYBAS_ID")[0]
    assert linha["pa_exc_ha"] == pytest.approx(1.4)
    assert linha["pa_rem_ha"] == pytest.approx(0.0)
    assert linha["pa_expl"] == pytest.approx(1.0)


def test_sem_feicao_a_agregacao_recusa(monkeypatch):
    from risco_garimpo.core.remanescente import agregar_potencial

    _aparelhar_zonas(monkeypatch, np.zeros((1, 2), dtype=np.int64), [])
    with pytest.raises(ValueError):
        agregar_potencial(np.zeros((1, 2)), np.zeros((1, 2)), _Grade(),
                          object())


def test_grava_o_gpkg_quando_pedido(monkeypatch):
    from risco_garimpo.core.remanescente import agregar_potencial

    gravados = _aparelhar_zonas(monkeypatch, np.zeros((1, 2), dtype=np.int64),
                                [_Feicao(1, "A")])
    agregar_potencial(np.full((1, 2), 0.4), np.zeros((1, 2)), _Grade(),
                      object(), caminho_saida="/saida/x.gpkg")
    assert gravados["caminho"] == "/saida/x.gpkg"
    assert len(gravados["linhas"]) == 1


def test_agregar_somando_bate_com_o_total_global(monkeypatch):
    """A soma por sub-bacia conserva a grandeza — é essa a razão de agregar
    o resultado, e não as variáveis."""
    from risco_garimpo.core.remanescente import agregar_potencial

    rng = np.random.default_rng(3)
    forma = (40, 40)
    K = rng.random(forma) * 0.6
    mu = rng.random(forma) * 0.4
    zonas = rng.integers(0, 5, forma).astype(np.int64)
    feicoes = [_Feicao(i, "Z%d" % i) for i in range(5)]
    _aparelhar_zonas(monkeypatch, zonas, feicoes)

    linhas = agregar_potencial(K, mu, _Grade(), object(), campo_id="HYBAS_ID")
    total = resumo_global(K, mu, 100.0)
    assert sum(l["pa_rem_ha"] for l in linhas) == \
        pytest.approx(total["remanescente_ha"], rel=1e-9)
    assert sum(l["pa_k_ha"] for l in linhas) == \
        pytest.approx(total["potencial_ha"], rel=1e-9)

# -*- coding: utf-8 -*-
"""Testes do modelo estático: ordenação, veto e estimativa do nível."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risco_garimpo.core.estatico import (  # noqa: E402
    aplicar_nivel, curva_de_nivel, diagnosticar_veto, fuzzy_gamma, gerar_k,
    isotonica, normalizar, ocupacao_local, saturacao_por_faixa)


# ------------------------------------------------------------- normalização
def test_normalizacao_respeita_o_piso():
    x = normalizar(np.array([0.0, 5.0, 10.0]), piso=0.01)
    assert x[0] == pytest.approx(0.01)
    assert x[-1] == pytest.approx(1.0)
    assert x[1] == pytest.approx(0.505)


def test_normalizacao_de_variavel_constante_nao_explode():
    x = normalizar(np.full(5, 3.0), piso=0.02)
    assert np.allclose(x, 0.02)


def test_piso_zero_deixa_o_minimo_em_zero():
    """Sem piso o veto continua possível — é uma escolha, não um acidente."""
    x = normalizar(np.array([2.0, 4.0]), piso=0.0)
    assert x[0] == pytest.approx(0.0)


# ------------------------------------------------------------- fuzzy gamma
def test_zero_veta_qualquer_que_seja_o_peso():
    """A propriedade que obriga o piso a existir."""
    for peso in (0.001, 0.075, 0.9):
        escore = fuzzy_gamma([np.array([0.0]), np.array([1.0])],
                             [peso, 1.0 - peso], gamma=0.0)
        assert escore[0] == pytest.approx(0.0)


def test_piso_pequeno_nao_veta_mas_penaliza():
    escore = fuzzy_gamma([np.array([0.001]), np.array([1.0])],
                         [0.075, 0.925], gamma=0.4)
    assert 0.0 < escore[0] < 1.0


def test_gamma_zero_e_produto_e_gamma_um_e_soma():
    a, b = np.array([0.6]), np.array([0.4])
    produto = fuzzy_gamma([a, b], [0.5, 0.5], gamma=0.0)
    soma = fuzzy_gamma([a, b], [0.5, 0.5], gamma=1.0)
    assert produto[0] == pytest.approx(np.sqrt(0.6 * 0.4), abs=1e-6)
    assert soma[0] == pytest.approx(1.0 - np.sqrt(0.4 * 0.6), abs=1e-6)
    assert produto[0] < soma[0]


def test_escore_e_monotono_em_cada_variavel():
    base = np.linspace(0.05, 1.0, 20)
    outra = np.full(20, 0.5)
    escore = fuzzy_gamma([base, outra], [0.6, 0.4], gamma=0.4)
    assert np.all(np.diff(escore) > 0)


def test_pesos_sao_normalizados():
    a, b = np.array([0.3]), np.array([0.8])
    assert fuzzy_gamma([a, b], [1.0, 3.0], 0.4)[0] == \
        pytest.approx(fuzzy_gamma([a, b], [0.25, 0.75], 0.4)[0], abs=1e-6)


def test_pesos_invalidos_sao_recusados():
    a = np.array([0.5])
    with pytest.raises(ValueError):
        fuzzy_gamma([a, a], [0.0, 0.0], 0.4)
    with pytest.raises(ValueError):
        fuzzy_gamma([a, a], [0.5, 0.5], 1.5)
    with pytest.raises(ValueError):
        fuzzy_gamma([a, a], [1.0], 0.4)


def test_diagnostico_de_veto_conta_a_area():
    rel = diagnosticar_veto([np.array([0.0, 0.0, 1.0, 2.0])], ["Est"])
    nome, zeros, fracao = rel[0]
    assert (nome, zeros) == ("Est", 2)
    assert fracao == pytest.approx(0.5)


# ------------------------------------------------------------ ocupação local
def test_ocupacao_local_mede_a_vizinhanca():
    mu = np.zeros((41, 41), dtype=np.float32)
    mu[20, 20] = 1.0
    ocup = ocupacao_local(mu, pixel_m=30.0, janela_m=300.0)
    assert 0.0 < ocup[20, 20] < 1.0
    assert ocup[20, 21] > 0.0
    assert ocup[0, 0] == pytest.approx(0.0, abs=1e-6)


def test_bloco_saturado_tem_ocupacao_um_no_centro():
    mu = np.zeros((61, 61), dtype=np.float32)
    mu[10:50, 10:50] = 1.0
    ocup = ocupacao_local(mu, pixel_m=30.0, janela_m=300.0)
    assert ocup[30, 30] == pytest.approx(1.0, abs=1e-5)


# ------------------------------------------------------------------ isotônica
def test_isotonica_preserva_sequencia_ja_crescente():
    y = np.array([0.1, 0.2, 0.5, 0.9])
    assert np.allclose(isotonica(y), y)


def test_isotonica_corrige_inversao_pela_media_ponderada():
    """(0, 1, 0) vira (0, 0.5, 0.5), não três terços.

    Só os dois últimos violam a ordem, então só eles são agrupados. Juntar
    os três daria erro quadrático 0,667 contra 0,5 — o PAVA agrupa o mínimo
    necessário, e é isso que preserva o sinal das faixas que já estavam
    certas.
    """
    y = np.array([0.0, 1.0, 0.0])
    ajuste = isotonica(y, peso=np.array([1.0, 1.0, 1.0]))
    assert np.all(np.diff(ajuste) >= -1e-12)
    assert np.allclose(ajuste, [0.0, 0.5, 0.5])
    erro = float(((y - ajuste) ** 2).sum())
    erro_tres_tercos = float(((y - y.mean()) ** 2).sum())
    assert erro < erro_tres_tercos


def test_isotonica_respeita_os_pesos():
    y = np.array([1.0, 0.0])
    leve = isotonica(y, peso=np.array([1.0, 99.0]))
    assert leve[0] == pytest.approx(0.01, abs=1e-9)


# --------------------------------------------------------------- saturação
def _mundo(semente=0, teto_verdadeiro=0.45):
    """Escore uniforme; ocupação satura em teto·escore nos maduros."""
    rng = np.random.default_rng(semente)
    n = 200000
    escore = rng.random(n)
    maduro = rng.random(n) < 0.3
    limite = teto_verdadeiro * escore
    # ocupação é uma fração do que cabe; alguns pixels chegam perto do limite
    ocupacao = limite * rng.random(n) ** 0.25
    return escore, ocupacao, maduro


def test_saturacao_recupera_o_teto_verdadeiro():
    escore, ocupacao, maduro = _mundo(teto_verdadeiro=0.45)
    centros, sat, cont = saturacao_por_faixa(escore, ocupacao, maduro,
                                             n_faixas=25, quantil=0.99,
                                             minimo_por_faixa=100)
    x, y = curva_de_nivel(centros, sat, cont)
    assert y[-1] == pytest.approx(0.45, rel=0.10)
    assert np.all(np.diff(y) >= -1e-12)


def test_o_nivel_estimado_e_limite_inferior():
    """Nenhum quantil de ocupação observada pode passar do teto real."""
    escore, ocupacao, maduro = _mundo(teto_verdadeiro=0.45)
    _, sat, _ = saturacao_por_faixa(escore, ocupacao, maduro, n_faixas=25,
                                    quantil=0.99, minimo_por_faixa=100)
    assert np.nanmax(sat) <= 0.45 + 1e-9


def test_sem_pixel_maduro_nao_ha_saturacao_observavel():
    escore, ocupacao, _ = _mundo()
    with pytest.raises(ValueError):
        saturacao_por_faixa(escore, ocupacao, np.zeros(escore.size, bool))


def test_faixa_rala_vira_nan_e_nao_entra_na_curva():
    """Três pixels não estimam um quantil de saturação — e não podem fixar
    o teto de K por serem os de escore mais alto."""
    escore = np.concatenate([np.full(5000, 0.2), np.full(3, 0.93)])
    ocupacao = np.concatenate([np.full(5000, 0.1), np.full(3, 0.99)])
    maduro = np.ones(escore.size, bool)
    centros, sat, cont = saturacao_por_faixa(escore, ocupacao, maduro,
                                             n_faixas=20, quantil=0.99,
                                             minimo_por_faixa=100)
    assert cont.sum() == escore.size
    rala = cont == 3
    assert rala.sum() == 1
    assert np.isnan(sat[rala][0])
    assert np.nanmax(sat) == pytest.approx(0.1)


def test_curva_precisa_de_faixas_suficientes():
    with pytest.raises(ValueError):
        curva_de_nivel(np.array([0.5]), np.array([0.3]), np.array([10]))


def test_aplicar_nivel_extrapola_constante():
    x = np.array([0.2, 0.8])
    y = np.array([0.1, 0.5])
    K = aplicar_nivel(np.array([0.0, 0.5, 1.0]), x, y)
    assert K[0] == pytest.approx(0.1)
    assert K[-1] == pytest.approx(0.5)
    assert 0.1 < K[1] < 0.5


# ------------------------------------------------------------------ gerar_k
def _grade_sintetica(semente=1, teto=0.5):
    rng = np.random.default_rng(semente)
    forma = (200, 200)
    ibx = rng.random(forma).astype(np.float32)
    est = rng.random(forma).astype(np.float32)
    escore_real = np.sqrt(ibx * est)
    mu = (rng.random(forma) < teto * escore_real).astype(np.float32)
    maduro = np.ones(forma, bool)
    return ibx, est, mu, maduro


def test_gerar_k_devolve_proporcao_entre_zero_e_um():
    ibx, est, mu, maduro = _grade_sintetica()
    r = gerar_k([ibx, est], ["IBx", "Est"], [0.6, 0.4], 0.4, mu,
                pixel_m=30.0, maduro=maduro, n_faixas=20,
                minimo_por_faixa=100)
    assert r.K.min() >= 0.0
    assert r.K.max() <= 1.0
    assert 0.0 < r.teto_estimado <= 1.0


def test_k_cresce_com_o_escore():
    ibx, est, mu, maduro = _grade_sintetica()
    r = gerar_k([ibx, est], ["IBx", "Est"], [0.6, 0.4], 0.4, mu,
                pixel_m=30.0, maduro=maduro, n_faixas=20,
                minimo_por_faixa=100)
    ordem = np.argsort(r.escore.ravel())
    k_ordenado = r.K.ravel()[ordem]
    assert np.all(np.diff(k_ordenado) >= -1e-6)


def test_teto_decretado_ignora_os_dados():
    ibx, est, mu, maduro = _grade_sintetica()
    r = gerar_k([ibx, est], ["IBx", "Est"], [0.6, 0.4], 0.4, mu,
                pixel_m=30.0, maduro=maduro, teto=0.70)
    assert r.teto_estimado == pytest.approx(0.70)
    assert r.K.max() == pytest.approx(0.70 * r.escore.max(), abs=1e-5)
    assert r.saturacao is None


def test_captura_e_cega_ao_nivel():
    """O motivo de o nível precisar de método próprio.

    Multiplicar K por uma constante não muda nenhuma métrica de ordenação.
    """
    ibx, est, mu, maduro = _grade_sintetica()
    r = gerar_k([ibx, est], ["IBx", "Est"], [0.6, 0.4], 0.4, mu,
                pixel_m=30.0, maduro=maduro, n_faixas=20,
                minimo_por_faixa=100)

    def captura(k, alvo, fracao=0.10):
        corte = np.quantile(k, 1.0 - fracao)
        return alvo[k >= corte].sum() / alvo.sum()

    cheio = captura(r.K.ravel(), mu.ravel())
    metade = captura(0.5 * r.K.ravel(), mu.ravel())
    assert cheio == pytest.approx(metade)


def test_estratos_recebem_niveis_proprios():
    """Terra e leito de rio não têm a mesma mecânica nem o mesmo teto."""
    rng = np.random.default_rng(5)
    forma = (200, 200)
    ibx = rng.random(forma).astype(np.float32)
    est = rng.random(forma).astype(np.float32)
    agua = np.zeros(forma, bool)
    agua[:60, :] = True
    escore_real = np.sqrt(ibx * est)
    mu = np.where(agua,
                  (rng.random(forma) < 0.9 * escore_real),
                  (rng.random(forma) < 0.2 * escore_real)).astype(np.float32)
    r = gerar_k([ibx, est], ["IBx", "Est"], [0.5, 0.5], 0.4, mu,
                pixel_m=30.0, maduro=np.ones(forma, bool), n_faixas=20,
                minimo_por_faixa=100,
                estratos={"agua": agua, "terra": ~agua})
    assert set(r.por_estrato) == {"agua", "terra"}
    assert r.K[agua].max() > r.K[~agua].max()

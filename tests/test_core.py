# -*- coding: utf-8 -*-
"""Testes do núcleo numérico — rodam sem QGIS e sem GDAL.

    python -m pytest tests -q
"""

import datetime as _dt
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risco_garimpo.core import numerico, temporal  # noqa: E402
from risco_garimpo.core.eventos import CampoEventos, janelas_temporais  # noqa: E402
from risco_garimpo.core.frentes import agrupar_frentes, resumir_frentes  # noqa: E402
from risco_garimpo.core.kernels import kernel_contagio  # noqa: E402
from risco_garimpo.core.model import Parametros, simular  # noqa: E402


# ------------------------------------------------------------------ numérico
def test_convolucao_preserva_massa_no_interior():
    arr = np.zeros((41, 41), dtype=np.float32)
    arr[20, 20] = 1.0
    k = kernel_contagio(380.0, 100.0, "gaussiano")
    saida = numerico.convolver(arr, k)
    assert saida.sum() == pytest.approx(1.0, rel=1e-3)
    assert np.argmax(saida) == np.argmax(arr)


def test_convolucao_numpy_bate_com_scipy():
    rng = np.random.default_rng(42)
    arr = rng.random((37, 53)).astype(np.float32)
    k = kernel_contagio(200.0, 100.0, "exponencial")
    referencia = numerico.convolver(arr, k)
    manual = numerico._fftconvolve_numpy(arr, k)
    assert np.allclose(referencia, manual, atol=1e-5)


def test_edt_numpy_bate_com_scipy():
    mascara = np.zeros((25, 31), dtype=bool)
    mascara[5, 7] = True
    mascara[20, 25] = True
    pela_scipy = numerico.distancia_euclidiana(mascara, 30.0)
    pelo_numpy = np.sqrt(numerico._edt2_quadrada_numpy(~mascara)) * 30.0
    assert np.allclose(pela_scipy, pelo_numpy, atol=1e-4)


def test_distancia_sem_eventos_e_infinita():
    d = numerico.distancia_euclidiana(np.zeros((5, 5), dtype=bool), 10.0)
    assert np.isinf(d).all()


# ------------------------------------------------------------------- kernels
def test_kernel_normalizado_e_simetrico():
    k = kernel_contagio(380.0, 100.0)
    assert k.sum() == pytest.approx(1.0, rel=1e-6)
    assert np.allclose(k, k[::-1, :])
    assert np.allclose(k, k[:, ::-1])


def test_kernel_sem_centro_zera_o_proprio_pixel():
    k = kernel_contagio(380.0, 100.0, excluir_centro=True)
    meio = k.shape[0] // 2
    assert k[meio, meio] == 0.0
    assert k.sum() == pytest.approx(1.0, rel=1e-6)


# ------------------------------------------------------------------ temporal
def test_plato_da_fiscalizacao_tem_forma_esperada():
    """Sobe quase imediato, fica estável no platô e cai depois dele."""
    assert temporal.plato_logistico(-1.0) == 0.0
    inicio = float(temporal.plato_logistico(3.0))
    meio = float(temporal.plato_logistico(90.0))
    fim_plato = float(temporal.plato_logistico(180.0))
    depois = float(temporal.plato_logistico(400.0))

    assert inicio > 0.9              # efeito praticamente instantâneo
    assert meio == pytest.approx(1.0, abs=0.05)
    assert 0.3 < fim_plato < 0.8     # já em transição ao fim do platô
    assert depois < 0.02             # dissipado


def test_plato_normalizado_nunca_passa_de_um():
    dias = np.linspace(0, 1000, 5000)
    assert float(temporal.plato_logistico(dias).max()) <= 1.0 + 1e-9


def test_alerta_decai_do_dia_zero():
    assert float(temporal.decaimento_exponencial(0.0, 90.0)) == pytest.approx(1.0)
    assert float(temporal.decaimento_exponencial(90.0, 90.0)) == pytest.approx(
        np.exp(-1.0), rel=1e-6)
    assert float(temporal.decaimento_exponencial(-5.0, 90.0)) == 0.0


def test_decaimento_espacial_trata_infinito():
    s = temporal.decaimento_espacial(np.array([0.0, 750.0, np.inf]), 750.0)
    assert s[0] == pytest.approx(1.0)
    assert s[1] == pytest.approx(np.exp(-1.0), rel=1e-5)
    assert s[2] == 0.0


# ------------------------------------------------------------------- frentes
def test_agrupamento_em_frentes_e_transitivo():
    # três pontos em cadeia, cada par a 80 m: uma única frente
    x = np.array([0.0, 80.0, 160.0, 5000.0])
    y = np.array([0.0, 0.0, 0.0, 5000.0])
    rotulos = agrupar_frentes(x, y, 100.0)
    assert rotulos[0] == rotulos[1] == rotulos[2]
    assert rotulos[3] != rotulos[0]
    assert len(set(rotulos.tolist())) == 2


def test_frente_guarda_a_data_de_chegada():
    x = np.array([0.0, 50.0])
    y = np.array([0.0, 0.0])
    datas = [_dt.date(2024, 5, 10), _dt.date(2024, 3, 1)]
    rotulos = agrupar_frentes(x, y, 100.0)
    xc, yc, chegada, quantos = resumir_frentes(x, y, datas, rotulos)
    assert len(chegada) == 1
    assert chegada[0] == _dt.date(2024, 3, 1)   # a mais antiga
    assert xc[0] == pytest.approx(25.0)
    assert quantos[0] == 2


def test_alertas_densos_colapsam_em_poucas_frentes():
    """Reproduz o padrão real: fragmentos vizinhos de uma mesma detecção."""
    rng = np.random.default_rng(7)
    nucleos = np.array([[0.0, 0.0], [10000.0, 0.0], [0.0, 10000.0]])
    pontos = []
    for cx, cy in nucleos:
        pontos.append(rng.normal([cx, cy], 25.0, size=(40, 2)))
    pontos = np.vstack(pontos)
    rotulos = agrupar_frentes(pontos[:, 0], pontos[:, 1], 100.0)
    assert len(set(rotulos.tolist())) == 3


# ------------------------------------------------------------------- eventos
def _campo_simples(shape=(41, 41), pixel=100.0, escala=750.0):
    janelas = janelas_temporais([_dt.date(2025, 1, 1)], [20], [20], 30)
    return CampoEventos(shape, pixel, janelas, escala)


def test_campo_de_eventos_decai_com_a_distancia():
    campo = _campo_simples()
    valores = campo.valor(_dt.date(2025, 1, 20),
                          lambda d: temporal.plato_logistico(d))
    assert valores[20, 20] > valores[20, 25] > valores[20, 35]


def test_campo_e_nulo_no_instante_do_evento():
    """A resposta da fiscalização parte de zero e sobe em poucos dias."""
    campo = _campo_simples()
    no_dia = campo.valor(_dt.date(2025, 1, 1),
                         lambda d: temporal.plato_logistico(d))
    tres_dias = campo.valor(_dt.date(2025, 1, 4),
                            lambda d: temporal.plato_logistico(d))
    assert float(no_dia.max()) == 0.0
    assert float(tres_dias.max()) > 0.9


def test_campo_e_nulo_antes_do_evento():
    campo = _campo_simples()
    valores = campo.valor(_dt.date(2024, 12, 1),
                          lambda d: temporal.plato_logistico(d))
    assert float(valores.max()) == 0.0


def test_janelas_usam_data_media():
    datas = [_dt.date(2025, 1, 1), _dt.date(2025, 1, 11)]
    janelas = janelas_temporais(datas, [1, 2], [1, 2], 30)
    assert len(janelas) == 1
    assert janelas[0][0] == _dt.date(2025, 1, 6)


# --------------------------------------------------------------------- modelo
def _cenario(n=61, pixel=100.0):
    K = np.ones((n, n), dtype=np.float32)
    mu0 = np.zeros((n, n), dtype=np.float32)
    mu0[n // 2, n // 2] = 1.0          # um foco central saturado
    return K, mu0, pixel


def test_sem_foco_nao_ha_crescimento():
    """Sem vizinho ocupado não há chegada: o contágio é a única porta."""
    K, mu0, pixel = _cenario()
    mu0[:] = 0.0
    r = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2025, 7, 1),
                Parametros())
    assert float(r.delta_mu.max()) == 0.0


def test_crescimento_parte_do_foco_e_e_local():
    K, mu0, pixel = _cenario()
    r = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2025, 7, 1),
                Parametros(r0=0.02))
    meio = K.shape[0] // 2
    assert r.delta_mu[meio, meio + 1] > 0
    assert r.delta_mu[meio, meio + 1] > r.delta_mu[meio, meio + 8]
    # a frente avança com o tempo, então o canto não é exatamente zero —
    # mas deve ser ordens de grandeza menor que a vizinhança do foco
    assert r.delta_mu[0, 0] < r.delta_mu[meio, meio + 1] * 1e-3


def test_capacidade_de_suporte_limita_o_estado():
    K, mu0, pixel = _cenario()
    K *= 0.3
    mu0[:] = 0.0
    mu0[30, 30] = 0.3
    r = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2027, 1, 1),
                Parametros(r0=0.5))
    assert float(r.mu_final.max()) <= 0.3 + 1e-6


def test_k_zero_impede_qualquer_expansao():
    K, mu0, pixel = _cenario()
    K[:, 31:] = 0.0
    r = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2026, 1, 1),
                Parametros(r0=0.05))
    assert float(r.delta_mu[:, 31:].max()) == 0.0
    assert float(r.delta_mu[:, :31].max()) > 0.0


def test_fiscalizacao_reduz_a_expansao():
    K, mu0, pixel = _cenario()
    meio = K.shape[0] // 2
    params = Parametros(r0=0.02, fisc_max=1.0, fisc_escala_m=750.0)

    sem = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2025, 7, 1),
                  params)
    janelas = janelas_temporais([_dt.date(2025, 1, 2)], [meio], [meio], 30)
    campo = CampoEventos(K.shape, pixel, janelas, params.fisc_escala_m)
    com = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2025, 7, 1),
                  params, campo_fiscalizacao=campo)

    assert float(com.delta_mu.sum()) < float(sem.delta_mu.sum())


def test_campo_de_supressao_dissipa_apos_o_plato():
    """O *campo* repressivo volta a zero; é ele que tem prazo de validade."""
    K, _, pixel = _cenario()
    meio = K.shape[0] // 2
    params = Parametros(fisc_plato_dias=180.0)
    janelas = janelas_temporais([_dt.date(2025, 1, 1)], [meio], [meio], 30)
    campo = CampoEventos(K.shape, pixel, janelas, params.fisc_escala_m)

    def pico(data):
        return float(campo.valor(
            data, lambda d: temporal.plato_logistico(
                d, params.fisc_k_subida, params.fisc_plato_dias,
                params.fisc_k_queda)).max())

    assert pico(_dt.date(2025, 3, 1)) > 0.9     # dentro do platô
    assert pico(_dt.date(2025, 7, 1)) < 0.9     # fim do platô, em transição
    assert pico(_dt.date(2026, 3, 1)) < 0.01    # dissipado


def test_supressao_temporaria_desloca_a_trajetoria_permanentemente():
    """Resultado do modelo, não da implementação — e vale registrar.

    Num sistema logístico com contágio, um pulso repressivo temporário não
    devolve o sistema à trajetória original depois de dissipado: ele atrasa a
    frente, e o atraso é amplificado pelo crescimento posterior. A diferença
    acumulada entre cenários continua aumentando mesmo quando o campo
    repressivo já é nulo.
    """
    K, mu0, pixel = _cenario()
    meio = K.shape[0] // 2
    params = Parametros(r0=0.02, fisc_max=1.0, passo_dias=5.0)
    janelas = janelas_temporais([_dt.date(2025, 1, 2)], [meio], [meio], 30)
    campo = CampoEventos(K.shape, pixel, janelas, params.fisc_escala_m)

    def diferenca(data_fim):
        sem = simular(K, mu0, pixel, _dt.date(2025, 1, 1), data_fim, params)
        com = simular(K, mu0, pixel, _dt.date(2025, 1, 1), data_fim, params,
                      campo_fiscalizacao=campo)
        return float(sem.delta_mu.sum() - com.delta_mu.sum())

    curto = diferenca(_dt.date(2025, 7, 1))     # ~180 dias: dentro do platô
    longo = diferenca(_dt.date(2026, 7, 1))     # ~545 dias: bem depois
    assert curto > 0
    assert longo > curto


def test_alertas_semeiam_area_virgem():
    K, mu0, pixel = _cenario()
    mu0[:] = 0.0
    params = Parametros(r0=0.02, alerta_semeia=True, alerta_semente=0.1)
    janelas = janelas_temporais([_dt.date(2025, 2, 1)], [10], [10], 30)
    campo = CampoEventos(K.shape, pixel, janelas, params.alerta_escala_m,
                         guardar_posicoes=True)
    r = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2025, 12, 1),
                params, campo_alertas=campo)
    assert r.mu_final[10, 10] >= 0.1
    assert float(r.delta_mu.sum()) > 0.1


def test_alertas_aceleram_o_crescimento():
    K, mu0, pixel = _cenario()
    meio = K.shape[0] // 2
    params_base = Parametros(r0=0.02, alerta_semeia=False)
    sem = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2025, 7, 1),
                  params_base)

    janelas = janelas_temporais([_dt.date(2025, 1, 2)], [meio], [meio], 30)
    campo = CampoEventos(K.shape, pixel, janelas, params_base.alerta_escala_m)
    com = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2025, 7, 1),
                  params_base, campo_alertas=campo)
    assert float(com.delta_mu.sum()) > float(sem.delta_mu.sum())


def test_mascara_restringe_a_area():
    K, mu0, pixel = _cenario()
    mascara = np.zeros(K.shape, dtype=bool)
    mascara[:, :35] = True
    r = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2026, 1, 1),
                Parametros(r0=0.05), mascara=mascara)
    assert float(r.delta_mu[:, 35:].max()) == 0.0


def test_parametros_invalidos_sao_recusados():
    with pytest.raises(ValueError):
        Parametros(fisc_max=1.5).validar()
    with pytest.raises(ValueError):
        Parametros(passo_dias=0).validar()
    with pytest.raises(ValueError):
        Parametros(combinacao="media").validar()


def test_cancelamento_interrompe():
    from risco_garimpo.core.model import SimulacaoCancelada

    K, mu0, pixel = _cenario()
    with pytest.raises(SimulacaoCancelada):
        simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2026, 1, 1),
                Parametros(), progresso=lambda f, m: False)


def test_conservacao_do_diagnostico():
    K, mu0, pixel = _cenario()
    r = simular(K, mu0, pixel, _dt.date(2025, 1, 1), _dt.date(2025, 7, 1),
                Parametros(r0=0.02))
    d = r.diagnostico
    assert d["mu_final_total"] >= d["mu_inicial_total"]
    assert d["expansao_total_px_equivalente"] == pytest.approx(
        d["mu_final_total"] - d["mu_inicial_total"], rel=1e-4)

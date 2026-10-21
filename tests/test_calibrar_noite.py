# -*- coding: utf-8 -*-
"""Métricas e calibração de r0 do roteiro tools/calibrar_noite.py (sem QGIS)."""

import itertools
import os
import sys

import numpy as np
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from risco_garimpo.core.model import Parametros  # noqa: E402


@pytest.fixture
def roteiro(tmp_path):
    caminho = os.path.join(RAIZ, "tools", "calibrar_noite.py")
    espaco = {"__name__": "__console__"}
    exec(compile(open(caminho, encoding="utf-8").read(), caminho, "exec"),
         espaco)
    espaco["CONFIG"]["PASTA_SAIDA"] = str(tmp_path)
    return espaco


def _auc_forca_bruta(pontuacao, alvo):
    pos = [p for p, a in zip(pontuacao, alvo) if a]
    neg = [p for p, a in zip(pontuacao, alvo) if not a]
    pares = [(1.0 if p > n else 0.5 if p == n else 0.0)
             for p, n in itertools.product(pos, neg)]
    return sum(pares) / len(pares)


def test_auc_bate_com_a_definicao_inclusive_com_empates(roteiro):
    rng = np.random.default_rng(3)
    pontuacao = rng.integers(0, 4, 60).astype(float)     # muitos empates
    alvo = rng.random(60) < 0.3
    assert roteiro["auc"](pontuacao, alvo) == \
        pytest.approx(_auc_forca_bruta(pontuacao, alvo))
    assert np.isnan(roteiro["auc"](pontuacao, np.zeros(60, bool)))


def test_captura_reparte_empates_na_fronteira(roteiro):
    pontuacao = np.array([3.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    observado = np.array([1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    # 30% da área: os dois primeiros e 1 de 8 empatados em zero (3/8 com garimpo)
    esperado = (1.0 + 3.0 / 8.0) / 4.0
    assert roteiro["captura"](pontuacao, observado, 0.3) == \
        pytest.approx(esperado)
    assert roteiro["captura"](pontuacao, observado, 1.0) == pytest.approx(1.0)


def test_r0_interpolado_onde_a_area_prevista_iguala_a_observada(roteiro):
    gravar = roteiro["_gravar_linha"]
    for r0, razao in ((0.002, 0.25), (0.004, 0.5), (0.008, 2.0)):
        linha = dict.fromkeys(roteiro["COLUNAS"], "")
        linha.update(id="r%s" % r0, etapa="r0", modo="reconstrucao", r0=r0,
                     razao_area=razao)
        gravar(linha)
    # entre 0,004 (0,5) e 0,008 (2,0), em log-log: raiz geométrica
    assert roteiro["r0_calibrado"]() == pytest.approx(0.005657, rel=1e-3)


def test_plano_nao_repete_combinacoes_e_tem_ids_estaveis(roteiro):
    plano = (roteiro["_plano_inicial"](Parametros())
             + roteiro["_plano_restante"](Parametros(), 0.01))
    ids = [it["id"] for it in plano]
    assert len(ids) == len(set(ids))
    de_novo = roteiro["_plano_restante"](Parametros(), 0.01)
    assert [it["id"] for it in de_novo] == \
        [it["id"] for it in plano if it["etapa"] != "r0"
         and it["etapa"] != "padrao"]
    fiscal = [it for it in plano if it["etapa"] == "fiscal"]
    assert all(it["params"]["r0"] == 0.01 for it in fiscal)

# -*- coding: utf-8 -*-
"""Testes da geometria da grade de trabalho (sem GDAL)."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risco_garimpo.core.raster import (Grade, grade_reamostrada,  # noqa: E402
                                       rasterizar_pontos)


def _grade(xmin=600000.0, ymax=9400000.0, res=100.0, nx=50, ny=40):
    return Grade(gt=(xmin, res, 0.0, ymax, 0.0, -res), wkt="", nx=nx, ny=ny)


def test_limites_da_grade():
    g = _grade()
    assert g.xmin == 600000.0
    assert g.ymax == 9400000.0
    assert g.xmax == 605000.0
    assert g.ymin == 9396000.0
    assert g.shape == (40, 50)
    assert g.pixel_m == 100.0


def test_canto_superior_esquerdo_e_o_pixel_zero():
    g = _grade()
    linhas, colunas, dentro = rasterizar_pontos([g.xmin + 1.0],
                                                [g.ymax - 1.0], g)
    assert (linhas[0], colunas[0]) == (0, 0)
    assert dentro[0]


def test_indices_de_pixel_conhecidos():
    g = _grade()
    # centro do pixel (linha 3, coluna 7)
    x = g.xmin + 7 * 100.0 + 50.0
    y = g.ymax - 3 * 100.0 - 50.0
    linhas, colunas, dentro = rasterizar_pontos([x], [y], g)
    assert (int(linhas[0]), int(colunas[0])) == (3, 7)
    assert dentro.all()


def test_pontos_fora_sao_marcados():
    g = _grade()
    x = [g.xmin - 500.0, g.xmax + 500.0, g.xmin + 10.0]
    y = [g.ymax - 10.0, g.ymax - 10.0, g.ymin - 500.0]
    _, _, dentro = rasterizar_pontos(x, y, g)
    assert list(dentro) == [False, False, False]


def test_borda_direita_e_inferior_ficam_fora():
    """Meio pixel de tolerância não existe: a borda pertence ao vizinho."""
    g = _grade()
    _, _, dentro = rasterizar_pontos([g.xmax], [g.ymin], g)
    assert not dentro[0]


def test_reamostragem_preserva_a_extensao_e_muda_o_pixel():
    g = _grade()
    nova = grade_reamostrada(g, resolucao_m=250.0)
    assert nova.xres == 250.0
    assert nova.xmin == g.xmin
    assert nova.nx == int(np.ceil(5000.0 / 250.0))
    assert nova.ny == int(np.ceil(4000.0 / 250.0))
    # a grade cresce para cobrir a extensão inteira, nunca encolhe
    assert nova.xmax >= g.xmax - 1e-9
    assert nova.ymin <= g.ymin + 1e-9


def test_reamostragem_para_pixel_nao_divisor_arredonda_para_cima():
    g = _grade(res=30.0, nx=101, ny=51)          # 3030 x 1530 m
    nova = grade_reamostrada(g, resolucao_m=400.0)
    assert nova.nx == 8                           # ceil(3030/400)
    assert nova.ny == 4                           # ceil(1530/400)
    assert nova.xmax >= g.xmax


def test_sem_mudanca_devolve_a_mesma_grade():
    g = _grade()
    assert grade_reamostrada(g) is g


def test_grade_sem_crs_nao_e_tratada_como_geografica():
    assert _grade().geografica is False


def test_memoria_estimada_e_coerente():
    g = _grade(nx=2000, ny=2000)
    mb = g.nx * g.ny * 4 / (1024.0 ** 2)
    assert mb == pytest.approx(15.26, abs=0.05)


# ------------------------------------------------- caminhos de saída previstos
def _config_falsa(**extra):
    import datetime as dt
    import types

    cfg = types.SimpleNamespace(
        pasta_saida="/saida", prefixo="risco",
        data_fim=dt.date(2026, 12, 31), salvar_mu_final=True,
        salvar_taxa=True, salvar_campos=False, camada_subbacias=None)
    for chave, valor in extra.items():
        setattr(cfg, chave, valor)
    return cfg


def test_caminhos_previstos_batem_com_as_opcoes():
    """A interface precisa saber o que será gravado ANTES de gravar."""
    import sys as _sys

    _sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    from risco_garimpo.core.execucao import caminhos_de_saida

    caminhos = caminhos_de_saida(_config_falsa())
    nomes = [os.path.basename(c) for c in caminhos]
    assert nomes == ["risco_expansao_20261231.tif",
                     "risco_mu_20261231.tif",
                     "risco_taxa_20261231.tif"]


def test_caminhos_previstos_incluem_os_opcionais():
    from risco_garimpo.core.execucao import caminhos_de_saida

    caminhos = caminhos_de_saida(_config_falsa(salvar_campos=True,
                                               camada_subbacias=object()))
    nomes = [os.path.basename(c) for c in caminhos]
    assert "risco_ativacao_20261231.tif" in nomes
    assert "risco_supressao_20261231.tif" in nomes
    assert "risco_subbacias_20261231.gpkg" in nomes


def test_caminho_de_saida_usa_o_prefixo():
    from risco_garimpo.core.execucao import caminho_de_saida

    cfg = _config_falsa(prefixo="PMT")
    assert os.path.basename(caminho_de_saida(cfg, "expansao")) == \
        "PMT_expansao_20261231.tif"


def test_expansao_sai_sempre():
    """É a camada que responde à pergunta do modelo; nunca é opcional."""
    from risco_garimpo.core.execucao import caminhos_de_saida

    cfg = _config_falsa(salvar_mu_final=False, salvar_taxa=False)
    nomes = [os.path.basename(c) for c in caminhos_de_saida(cfg)]
    assert nomes == ["risco_expansao_20261231.tif"]

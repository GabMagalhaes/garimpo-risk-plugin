# -*- coding: utf-8 -*-
"""Testes da orquestração do modelo estático (sem QGIS, sem GDAL)."""

import os
import sys
import types

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risco_garimpo.core.execucao_estatica import (  # noqa: E402
    ConfiguracaoEstatica, SimulacaoCancelada, caminho_de_saida,
    caminhos_de_saida, executar, preparar)


def _config(**extra):
    base = dict(caminho_ibx="/dados/pggeom.tif", caminho_mu="/dados/mu.tif",
                pasta_saida="/saida", prefixo="PMT")
    base.update(extra)
    return ConfiguracaoEstatica(**base)


# ------------------------------------------------------- caminhos previstos
def test_k_sai_sempre():
    cfg = _config(salvar_escore=False, salvar_tabela=False,
                  salvar_remanescente=False)
    assert [os.path.basename(c) for c in caminhos_de_saida(cfg)] == ["PMT_K.tif"]


def test_caminhos_previstos_incluem_os_opcionais():
    nomes = [os.path.basename(c) for c in caminhos_de_saida(_config())]
    assert nomes == ["PMT_K.tif", "PMT_escore.tif", "PMT_remanescente.tif",
                     "PMT_saturacao.csv"]


def test_subbacias_entram_nos_caminhos_previstos():
    """A interface precisa liberar o .gpkg antes de reescrevê-lo."""
    nomes = [os.path.basename(c)
             for c in caminhos_de_saida(_config(camada_subbacias=object()))]
    assert "PMT_subbacias.gpkg" in nomes


def test_tabela_nao_sai_com_teto_fixo():
    """Sem estimativa não há saturação a tabelar."""
    cfg = _config(estimar_nivel=False)
    nomes = [os.path.basename(c) for c in caminhos_de_saida(cfg)]
    assert "PMT_saturacao.csv" not in nomes


def test_prefixo_manda_no_nome():
    assert os.path.basename(caminho_de_saida(_config(prefixo="X"), "K")) == \
        "X_K.tif"


# -------------------------------------------------------------- preparar
class _GradeFalsa(object):
    def __init__(self, nx=60, ny=50, pixel_m=30.0, geografica=False):
        self.nx, self.ny = nx, ny
        self.pixel_m = pixel_m
        self.geografica = geografica
        self.shape = (ny, nx)


def _aparelhar(monkeypatch, modulo, grade=None, leituras=None):
    grade = grade or _GradeFalsa()
    monkeypatch.setattr(modulo, "grade_de_raster", lambda c: grade)
    monkeypatch.setattr(modulo, "grade_reamostrada",
                        lambda g, resolucao_m=None, wkt_destino=None: g)
    leituras = leituras or {}

    def ler(caminho, g, reamostragem="bilinear", **kw):
        if caminho in leituras:
            return leituras[caminho]
        return np.full(g.shape, 0.5, dtype=np.float32)

    monkeypatch.setattr(modulo, "ler_alinhado", ler)
    return grade


def test_preparar_exige_crs_projetado(monkeypatch):
    import risco_garimpo.core.execucao_estatica as mod
    _aparelhar(monkeypatch, mod, grade=_GradeFalsa(geografica=True))
    with pytest.raises(ValueError) as erro:
        preparar(_config())
    assert "projetado" in str(erro.value)


def test_preparar_exige_variavel_de_terreno():
    with pytest.raises(ValueError):
        preparar(ConfiguracaoEstatica(caminho_ibx="", caminho_mu="/m.tif",
                                      pasta_saida="/s"))


def test_preparar_exige_mu_para_estimar_nivel():
    cfg = ConfiguracaoEstatica(caminho_ibx="/i.tif", caminho_mu="",
                               pasta_saida="/s", estimar_nivel=True)
    with pytest.raises(ValueError) as erro:
        preparar(cfg)
    assert "μ" in str(erro.value)


def test_preparar_so_conta_as_variaveis_informadas(monkeypatch):
    import risco_garimpo.core.execucao_estatica as mod
    _aparelhar(monkeypatch, mod)
    p = preparar(_config(caminho_est="/e.tif"))
    assert p["nomes"] == ["PGgeom", "Est"]
    assert p["pesos"] == [0.591, 0.075]


def test_preparar_recusa_pesos_todos_zerados(monkeypatch):
    import risco_garimpo.core.execucao_estatica as mod
    _aparelhar(monkeypatch, mod)
    with pytest.raises(ValueError):
        preparar(_config(peso_ibx=0.0))


# --------------------------------------------------------------- executar
def _mundo(grade, semente=0):
    rng = np.random.default_rng(semente)
    pggeom = rng.random(grade.shape).astype(np.float32)
    mu = (rng.random(grade.shape) < 0.35 * pggeom).astype(np.float32)
    return pggeom, mu


def test_executar_devolve_k_em_zero_um(monkeypatch):
    import risco_garimpo.core.execucao_estatica as mod
    grade = _GradeFalsa(nx=200, ny=200)
    pggeom, mu = _mundo(grade)
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu})
    saida = executar(preparar(_config(n_faixas=20, minimo_por_faixa=100)))
    K = saida["resultado"].K
    assert K.shape == grade.shape
    assert K.min() >= 0.0 and K.max() <= 1.0


def test_mascara_zera_o_que_esta_fora(monkeypatch):
    import risco_garimpo.core.execucao_estatica as mod
    grade = _GradeFalsa(nx=200, ny=200)
    pggeom, mu = _mundo(grade)
    mascara = np.zeros(grade.shape, dtype=np.float32)
    mascara[50:150, 50:150] = 1.0
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu,
                "/dados/mascara.tif": mascara})
    saida = executar(preparar(_config(caminho_mascara="/dados/mascara.tif",
                                      n_faixas=20, minimo_por_faixa=100)))
    K = saida["resultado"].K
    assert np.all(K[:50, :] == 0.0)
    assert K[50:150, 50:150].max() > 0.0


def test_furos_na_mascara_saem_do_universo(monkeypatch):
    """Corpos d'água excluídos não entram em área nem em estatística."""
    import risco_garimpo.core.execucao_estatica as mod
    grade = _GradeFalsa(nx=200, ny=200)
    pggeom, mu = _mundo(grade)
    mascara = np.ones(grade.shape, dtype=np.float32)
    mascara[90:110, :] = 0.0          # o furo, atravessando a área
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu,
                "/dados/mascara.tif": mascara})
    saida = executar(preparar(_config(caminho_mascara="/dados/mascara.tif",
                                      n_faixas=20, minimo_por_faixa=100)))
    assert np.all(saida["resultado"].K[90:110, :] == 0.0)
    assert saida["dentro"].sum() == grade.nx * grade.ny - 20 * grade.nx


def test_teto_fixo_e_respeitado(monkeypatch):
    import risco_garimpo.core.execucao_estatica as mod
    grade = _GradeFalsa(nx=100, ny=100)
    pggeom, mu = _mundo(grade)
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu})
    saida = executar(preparar(_config(estimar_nivel=False, teto=0.70)))
    assert saida["resultado"].teto_estimado == pytest.approx(0.70)
    assert saida["resultado"].K.max() <= 0.70 + 1e-6


def test_cancelamento_interrompe(monkeypatch):
    import risco_garimpo.core.execucao_estatica as mod
    grade = _GradeFalsa(nx=100, ny=100)
    pggeom, mu = _mundo(grade)
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu})
    with pytest.raises(SimulacaoCancelada):
        executar(preparar(_config()), cancelado=lambda: True)


def test_progresso_e_monotono(monkeypatch):
    import risco_garimpo.core.execucao_estatica as mod
    grade = _GradeFalsa(nx=100, ny=100)
    pggeom, mu = _mundo(grade)
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu})
    visto = []
    executar(preparar(_config(n_faixas=20, minimo_por_faixa=50)),
             progresso=visto.append)
    assert visto == sorted(visto)
    assert visto[-1] <= 100


def test_estrato_de_agua_recebe_nivel_proprio(monkeypatch):
    """Balsa no leito não obedece à geomorfologia de barranco."""
    import risco_garimpo.core.execucao_estatica as mod
    grade = _GradeFalsa(nx=200, ny=200)
    rng = np.random.default_rng(4)
    pggeom = rng.random(grade.shape).astype(np.float32)
    agua = np.zeros(grade.shape, dtype=np.float32)
    agua[:60, :] = 1.0
    mu = np.where(agua > 0,
                  rng.random(grade.shape) < 0.9 * pggeom,
                  rng.random(grade.shape) < 0.15 * pggeom).astype(np.float32)
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu,
                "/dados/agua.tif": agua})
    saida = executar(preparar(_config(caminho_agua="/dados/agua.tif",
                                      n_faixas=20, minimo_por_faixa=100)))
    K = saida["resultado"].K
    assert set(saida["resultado"].por_estrato) == {"agua", "terra"}
    assert K[:60, :].max() > K[60:, :].max()


def test_maturidade_restringe_a_amostra_do_nivel(monkeypatch):
    """Sem vizinhança garimpada antiga, o pixel não testemunha saturação."""
    import risco_garimpo.core.execucao_estatica as mod
    grade = _GradeFalsa(nx=200, ny=200)
    pggeom, mu = _mundo(grade, semente=7)
    antigo = np.zeros(grade.shape, dtype=np.float32)
    antigo[:100, :] = mu[:100, :]
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu,
                "/dados/antigo.tif": antigo})
    saida = executar(preparar(_config(caminho_mu_antigo="/dados/antigo.tif",
                                      n_faixas=15, minimo_por_faixa=50)))
    assert saida["resultado"].K.max() <= 1.0
    assert saida["resultado"].teto_estimado > 0.0

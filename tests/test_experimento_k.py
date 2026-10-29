# -*- coding: utf-8 -*-
"""Experimento K × K sem Est (tools/experimento_k.py)."""

import csv
import importlib.util
import json
import os
import sys

import numpy as np
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from risco_garimpo.core import validacao  # noqa: E402
from risco_garimpo.core.estatico import ocupacao_local  # noqa: E402
from risco_garimpo.core.kernels import kernel_contagio  # noqa: E402
from risco_garimpo.core.numerico import convolver  # noqa: E402


def _modulo():
    caminho = os.path.join(RAIZ, "tools", "experimento_k.py")
    spec = importlib.util.spec_from_file_location("experimento_k", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


ek = _modulo()


def _dados(n=1000, semente=0):
    rng = np.random.default_rng(semente)
    pontuacao = np.round(rng.random(n), 2)          # muitos empates
    pontuacao[rng.random(n) < 0.3] = 0.0            # e um bloco de zeros
    observado = np.where(rng.random(n) < 0.2 + 0.5 * pontuacao,
                         rng.random(n), 0.0)
    return pontuacao, observado


# --------------------------------------------------------------- métricas
def test_captura_sem_peso_e_a_da_validacao():
    pontuacao, observado = _dados()
    ordem = ek.Ordem(pontuacao)
    fracoes = (0.01, 0.05, 0.10, 0.37)
    obtido = ek.capturas(ordem, np.ones(pontuacao.size), observado, fracoes)
    for f, valor in zip(fracoes, obtido):
        assert valor == pytest.approx(validacao.captura(pontuacao, observado,
                                                        f))


def test_peso_inteiro_equivale_a_repetir_o_pixel():
    pontuacao, observado = _dados(semente=1)
    peso = np.random.default_rng(2).integers(0, 4, pontuacao.size)
    rep_p, rep_o = np.repeat(pontuacao, peso), np.repeat(observado, peso)
    fracoes = (0.01, 0.05, 0.10)
    np.testing.assert_allclose(
        ek.capturas(ek.Ordem(pontuacao), peso, observado, fracoes),
        ek.capturas(ek.Ordem(rep_p), np.ones(rep_p.size), rep_o, fracoes))
    positivo = observado > 0.02
    assert ek.auc(ek.Ordem(pontuacao), peso, positivo) == pytest.approx(
        validacao.auc(rep_p, np.repeat(positivo, peso)))


def test_auc_sem_peso_e_a_da_validacao():
    pontuacao, observado = _dados(semente=3)
    positivo = observado > 0.02
    assert ek.auc(ek.Ordem(pontuacao), np.ones(pontuacao.size), positivo) == \
        pytest.approx(validacao.auc(pontuacao, positivo))


def test_pearson_com_pesos_equivale_a_repetir():
    rng = np.random.default_rng(4)
    a, b = rng.random(50), rng.random(50)
    peso = rng.integers(0, 3, 50)
    assert ek.pearson(a, b, peso) == pytest.approx(
        np.corrcoef(np.repeat(a, peso), np.repeat(b, peso))[0, 1])
    assert ek.pearson(a, b) == pytest.approx(np.corrcoef(a, b)[0, 1])
    assert np.isnan(ek.pearson(np.ones(5), np.arange(5.0)))


def test_menos_infinito_e_um_grupo_de_empate():
    chave = ek.log_seguro(np.array([0.0, 0.5, 0.0, 0.25, 0.0]))
    ordem = ek.Ordem(chave)
    assert ordem.grupos == 3
    observado = np.array([1.0, 1.0, 1.0, 0.0, 1.0])
    # 60% da área: 0,5 e 0,25 (40%) e um terço do grupo de zeros (20%)
    captura = ek.capturas(ordem, np.ones(5), observado, (0.6,))[0]
    assert captura == pytest.approx((1.0 + 3.0 / 3.0) / 4.0)


def test_mesma_ordenacao():
    a = np.array([0.0, 0.1, 0.1, 0.5, 0.0])
    assert ek.mesma_ordenacao(a, a ** 2 * 3)
    assert not ek.mesma_ordenacao(a, np.array([0.0, 0.1, 0.2, 0.5, 0.0]))
    assert not ek.mesma_ordenacao(a, -a)


# ------------------------------------------------------- suporte e P exato
def test_media_em_janela_e_a_ocupacao_local_sem_ruido():
    rng = np.random.default_rng(5)
    mapa = (rng.random((80, 90)) < 0.05).astype(np.float32)
    exata = ek.media_em_janela(mapa, 13)
    np.testing.assert_allclose(exata, ocupacao_local(mapa, 30.0, 380.0),
                               atol=1e-6)
    contagem = exata * 169
    np.testing.assert_allclose(contagem, np.round(contagem), atol=1e-9)


def test_proximidade_exata_e_zero_fora_do_alcance():
    mu = np.zeros((60, 60), dtype=np.float32)
    mu[30, 30] = 0.8
    nucleo = kernel_contagio(200.0, 150.0)
    exata = ek.proximidade_exata(mu, nucleo)
    fft = convolver(mu, nucleo)
    np.testing.assert_allclose(exata, fft, atol=1e-6)
    alcance = nucleo.shape[0] // 2
    assert exata[30 - alcance, 30 - alcance] > 0
    assert exata[30 - alcance - 1, 30] == 0.0      # sem ruído: zero exato
    assert (exata == 0).sum() > 0.9 * exata.size


def test_multiplicidades_e_intervalo():
    rng = np.random.default_rng(6)
    m = ek.multiplicidades(rng, 40)
    assert m.sum() == 40 and m.size == 40
    inf, sup, n = ek.intervalo([1.0, np.nan, 2.0, 3.0])
    assert n == 3 and inf < sup


# ============================================================ ponta a ponta
def _gdal():
    if os.environ.get("EXIGIR_QGIS"):          # no CI com QGIS, não pular
        from osgeo import gdal
        return gdal
    return pytest.importorskip("osgeo.gdal")


def _gravar(caminho, arr, gt, wkt):
    g = _gdal()
    ds = g.GetDriverByName("GTiff").Create(str(caminho), arr.shape[1],
                                           arr.shape[0], 1, g.GDT_Float32)
    ds.SetGeoTransform(gt)
    ds.SetProjection(wkt)
    ds.GetRasterBand(1).WriteArray(arr.astype(np.float32))
    ds = None
    return str(caminho)


def _subbacias(caminho, x0, y0, lado_m, n, wkt):
    from osgeo import ogr, osr
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    fonte = ogr.GetDriverByName("GPKG").CreateDataSource(str(caminho))
    camada = fonte.CreateLayer("subbacias", srs, ogr.wkbPolygon)
    passo = lado_m / n
    for i in range(n):
        for j in range(n):
            anel = ogr.Geometry(ogr.wkbLinearRing)
            xa, ya = x0 + j * passo, y0 - i * passo
            for x, y in ((xa, ya), (xa + passo, ya), (xa + passo, ya - passo),
                         (xa, ya - passo), (xa, ya)):
                anel.AddPoint_2D(x, y)
            poligono = ogr.Geometry(ogr.wkbPolygon)
            poligono.AddGeometry(anel)
            feicao = ogr.Feature(camada.GetLayerDefn())
            feicao.SetGeometry(poligono)
            camada.CreateFeature(feicao)
    fonte = None
    return str(caminho)


def _mundo(pasta):
    """30 m, 18 × 18 km: terreno, garimpo de três anos, máscaras, bacias."""
    from osgeo import osr
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(31981)
    wkt = srs.ExportToWkt()
    n = 600
    x0, y0 = 500000.0, 9000000.0
    gt = (x0, 30.0, 0.0, y0, 0.0, -30.0)
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:n, 0:n]
    vale = np.exp(-((yy - 200) ** 2 + (xx - 200) ** 2) / (2 * 120.0 ** 2))
    pggeom = np.clip(vale + 0.2 * rng.random((n, n)), 0, 1)
    est = rng.random((n, n))
    lito = np.clip(0.5 * vale + 0.5 * rng.random((n, n)), 0, 1)
    anos = {}
    garimpo = np.zeros((n, n), dtype=bool)
    for ano, raio in ((2020, 60), (2022, 75), (2024, 90)):
        perto = (yy - 200) ** 2 + (xx - 200) ** 2 < raio ** 2
        garimpo = garimpo | (perto & (rng.random((n, n)) < 0.35 * pggeom))
        if ano == 2024:     # chegada longe do garimpo antigo
            garimpo[520:540, 520:540] = True
        anos[ano] = _gravar(os.path.join(pasta, "g%d.tif" % ano),
                            garimpo.astype(np.float32), gt, wkt)
    mascara = np.ones((n, n))
    apa = np.zeros((n, n))
    apa[30:570, 30:570] = 1
    caminhos = dict(
        pggeom=_gravar(os.path.join(pasta, "pggeom.tif"), pggeom, gt, wkt),
        est=_gravar(os.path.join(pasta, "est.tif"), est, gt, wkt),
        lito=_gravar(os.path.join(pasta, "lito.tif"), lito, gt, wkt),
        mascara=_gravar(os.path.join(pasta, "mascara.tif"), mascara, gt, wkt),
        apa=_gravar(os.path.join(pasta, "apa.tif"), apa, gt, wkt),
        subbacias=_subbacias(os.path.join(pasta, "subbacias.gpkg"), x0, y0,
                             n * 30.0, 6, wkt))
    caminhos.update({"g%d" % a: c for a, c in anos.items()})
    return caminhos


def _k_atual(pasta, c):
    from risco_garimpo.core import execucao_estatica as ee
    config = ee.ConfiguracaoEstatica(
        caminho_ibx=c["pggeom"], caminho_est=c["est"], caminho_lito=c["lito"],
        caminho_mu=c["g2024"], caminho_mu_antigo=c["g2020"],
        caminho_mascara=c["mascara"], pasta_saida=os.path.join(pasta, "K"),
        prefixo="K", n_faixas=10, minimo_por_faixa=20, salvar_escore=False,
        salvar_remanescente=False, carregar=False)
    calculado = ee.executar(ee.preparar(config))
    ee.escrever(calculado)
    return os.path.join(pasta, "K", "K_registro.json")


def test_experimento_de_ponta_a_ponta(tmp_path, monkeypatch):
    _gdal()
    pasta = str(tmp_path)
    c = _mundo(pasta)
    registro_k = _k_atual(pasta, c)
    saida = os.path.join(pasta, "experimento")
    config = dict(ek.CONFIG)
    config.update(
        REGISTRO_K=registro_k, K_SEM_EST=None, CONFERIR_K_ATUAL=True,
        PERIODOS=[dict(nome="2020-2022", MU_INICIAL=c["g2020"],
                       MU_FINAL=c["g2022"], CALIBRACAO_CSV=None),
                  dict(nome="2022-2024", MU_INICIAL=c["g2022"],
                       MU_FINAL=c["g2024"], CALIBRACAO_CSV=None)],
        MASCARA=c["mascara"], MASCARA_AVALIACAO=c["apa"],
        SUBBACIAS=c["subbacias"], N_REPLICAS=40, PASTA_SAIDA=saida)
    monkeypatch.setattr(ek, "CONFIG", config)
    dados = ek.principal("completo")

    assert dados["conferencia_k_atual"] == 0.0
    sem_est = json.load(open(os.path.join(saida, "k_sem_est",
                                          "K_semEst_registro.json"),
                             encoding="utf-8"))
    assert set(sem_est["resultados"]["variaveis"]) == {"PGgeom", "Lt"}

    metricas = list(csv.DictReader(open(os.path.join(
        saida, "experimento_metricas.csv"), encoding="utf-8"),
        delimiter=";"))
    assert len(metricas) == 2 * 4 * 4          # períodos × recortes × cand.
    ok = [m for m in metricas if m["situacao"] == "ok"]
    assert {m["recorte"] for m in ok} >= {"geral", "ate_1km"}
    for m in ok:
        for chave in ("captura_1", "captura_5", "captura_10", "auc"):
            assert 0.0 <= float(m[chave]) <= 1.0
    # longe do garimpo de t₀, P = 0: a comparação é redundante e dispensada
    longe = [m for m in metricas if m["recorte"] == "mais_5km"]
    assert longe and all(m["situacao"].startswith("redundante")
                         for m in longe)
    # a chegada em 2024 longe do garimpo antigo aparece como área convertida
    chegada = [m for m in longe if m["periodo"] == "2022-2024"]
    assert float(chegada[0]["convertida_ha"]) > 0

    diferencas = list(csv.DictReader(open(os.path.join(
        saida, "experimento_diferencas.csv"), encoding="utf-8"),
        delimiter=";"))
    pedidas = {"K_atual - P", "K_sem_Est - P", "K_atual - K_sem_Est"}
    assert pedidas <= {d["comparacao"] for d in diferencas}
    for d in diferencas:
        if d["situacao"] == "ok":
            assert float(d["ic_inf"]) <= float(d["ic_sup"])
            assert int(d["replicas_validas"]) >= 38

    reproducao = list(csv.DictReader(open(os.path.join(
        saida, "experimento_reproducao.csv"), encoding="utf-8"),
        delimiter=";"))
    assert {r["linha"] for r in reproducao} == {
        "base_proximidade_200m", "base_proximidade_x_remanescente_200m"}
    texto = open(os.path.join(saida, "experimento_verificacoes.txt"),
                 encoding="utf-8").read()
    assert "subbacias_no_universo" in texto and "P_fft_ruido_max" in texto
    registro = json.load(open(os.path.join(saida,
                                           "experimento_registro.json"),
                              encoding="utf-8"))
    assert set(registro["decisoes"]) >= {"D1", "D2", "D3", "hectares"}

    # a segunda rodada reaproveita o K sem Est e a conferência do K atual
    ek._LOG.clear()
    ek.principal("piloto")
    texto = "\n".join(ek._LOG)
    assert "K sem Est reaproveitado" in texto
    assert "K atual já conferido" in texto


def test_reproduz_a_linha_de_base_da_calibracao(tmp_path, monkeypatch):
    """Com o CSV da calibração, a reprodução compara número a número."""
    _gdal()
    pasta = str(tmp_path)
    c = _mundo(pasta)
    registro_k = _k_atual(pasta, c)
    k = json.load(open(registro_k, encoding="utf-8"))["saidas"]["K"]
    # uma "calibração" de mentira: as linhas de base que a calibração
    # gravaria, calculadas pelas funções da validação
    from risco_garimpo.core import raster
    grade = raster.grade_reamostrada(raster.grade_de_raster(k), 150.0)
    mu0 = np.clip(raster.ler_alinhado(c["g2020"], grade, "media"), 0, 1)
    mu1 = np.clip(raster.ler_alinhado(c["g2022"], grade, "media"), 0, 1)
    universo = (raster.ler_alinhado(c["mascara"], grade, "vizinho") > 0) & \
        (raster.ler_alinhado(c["apa"], grade, "vizinho") > 0)
    obs = np.where(universo, np.clip(mu1 - mu0, 0, None), 0)
    P = convolver(mu0, kernel_contagio(200.0, 150.0))
    m = validacao.avaliar(P, obs, universo, 150.0, 0.02, (0.01, 0.05, 0.1),
                          1000.0, magnitude=False)
    csv_cal = os.path.join(pasta, "resultados.csv")
    with open(csv_cal, "w", encoding="utf-8") as arquivo:
        arquivo.write("id;auc;captura_1;captura_5;captura_10;r_blocos\n")
        arquivo.write("base_proximidade_200m;%r;%r;%r;%r;%r\n"
                      % (m["auc"], m["captura_1"], m["captura_5"],
                         m["captura_10"], m["r_blocos"]))
    config = dict(ek.CONFIG)
    config.update(
        REGISTRO_K=registro_k, CONFERIR_K_ATUAL=False,
        PERIODOS=[dict(nome="2020-2022", MU_INICIAL=c["g2020"],
                       MU_FINAL=c["g2022"], CALIBRACAO_CSV=csv_cal)],
        MASCARA=c["mascara"], MASCARA_AVALIACAO=c["apa"],
        SUBBACIAS=c["subbacias"], PILOTO_REPLICAS=5,
        PASTA_SAIDA=os.path.join(pasta, "exp"))
    monkeypatch.setattr(ek, "CONFIG", config)
    ek.principal("piloto")
    linhas = [r for r in csv.DictReader(open(os.path.join(
        pasta, "exp", "experimento_reproducao.csv"), encoding="utf-8"),
        delimiter=";") if r["linha"] == "base_proximidade_200m"]
    assert len(linhas) == 5
    for r in linhas:
        assert abs(float(r["diferenca"])) < 1e-6, r


def _periodo_falso(n=3000, semente=8):
    rng = np.random.default_rng(semente)
    per = ek.Periodo()
    per.obs = np.where(rng.random(n) < 0.1, rng.random(n), 0.0)
    per.positivo = per.obs > 0.02
    per.P = np.where(rng.random(n) < 0.4, rng.random(n), 0.0)
    per.S = {"P": per.P}
    per.chave = {"P": ek.log_seguro(per.P)}
    for nome in ek.CANDIDATOS[1:]:
        r = np.clip(rng.random(n) - 0.1, 0, None)
        per.S[nome] = per.P * r
        per.chave[nome] = ek.log_seguro(per.S[nome])
    per.unidade = rng.integers(0, 30, n)
    per.bloco = rng.integers(0, 200, n)
    per.unidade_do_bloco = {b: int(rng.integers(0, 30)) for b in range(200)}
    return per


def test_recorte_rapido_e_igual_ao_caminho_direto():
    per = _periodo_falso()
    sel = np.ones(per.obs.size, dtype=bool)
    sel[::7] = False
    recorte = ek.Recorte(per, sel)
    mult = ek.multiplicidades(np.random.default_rng(9), 30)
    rapido = recorte.avaliar(mult)
    peso = mult[per.unidade[sel]]
    for nome in ek.CANDIDATOS:
        ordem = ek.Ordem(per.chave[nome][sel])
        np.testing.assert_allclose(
            [rapido[nome]["captura_1"], rapido[nome]["captura_5"],
             rapido[nome]["captura_10"]],
            ek.capturas(ordem, peso, per.obs[sel], (0.01, 0.05, 0.10)))
        assert rapido[nome]["auc"] == pytest.approx(
            ek.auc(ordem, peso, per.positivo[sel]))

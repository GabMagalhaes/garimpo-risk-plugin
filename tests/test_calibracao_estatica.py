# -*- coding: utf-8 -*-
"""Calibração do modelo estático: grade de pesos e γ (core/calibracao_estatica.py)."""

import json
import os
import sys

import numpy as np
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from risco_garimpo.core import calibracao_estatica as ce  # noqa: E402
from risco_garimpo.core import estatico  # noqa: E402


# ------------------------------------------------------------------- plano
def test_composicoes_cobrem_o_simplex_em_passos_de_0_1():
    tres = ce.composicoes(3, 0.1)
    assert len(tres) == 66 and len(set(tres)) == 66
    assert all(abs(sum(c) - 1.0) < 1e-9 for c in tres)
    assert (1.0, 0.0, 0.0) in tres and (0.6, 0.1, 0.3) in tres
    assert len(ce.composicoes(2, 0.1)) == 11
    assert ce.composicoes(1, 0.1) == [(1.0,)]
    with pytest.raises(ValueError):
        ce.composicoes(3, 0.3)


def test_plano_das_tres_evidencias():
    plano = ce.plano(list(ce.EVIDENCIAS), dict(ce.PADROES))
    referencias = [it for it in plano if it["etapa"] == "referencia"]
    grade = [it for it in plano if it["etapa"] == "grade"]
    assert [it["nome"] for it in referencias] == ["P", "K_1", "K_atual"]
    # 3 com uma evidência (sem γ) + (27 com duas + 36 com três) × 8 γ
    assert len(grade) == 3 + (27 + 36) * 8
    assert len({it["id"] for it in plano}) == len(plano)
    assert [it["n"] for it in grade] == sorted(it["n"] for it in grade)
    assert all(it["gama"] is None for it in grade if it["n"] == 1)
    assert all(it["gama"] is not None for it in grade if it["n"] > 1)
    # o K atual não se confunde com uma combinação da grade
    atual = referencias[2]
    assert atual["pesos"] == {"PGgeom": 0.591, "Est": 0.075, "Lt": 0.334}
    assert atual["id"].startswith("ref_")


def test_plano_sem_est_usa_so_as_evidencias_informadas():
    plano = ce.plano(["PGgeom", "Lt"], dict(ce.PADROES))
    atual = [it for it in plano if it["nome"] == "K_atual"][0]
    assert atual["pesos"] == {"PGgeom": 0.591, "Lt": 0.334}
    grade = [it for it in plano if it["etapa"] == "grade"]
    assert len(grade) == 2 + 9 * 8
    assert all(set(it["pesos"]) == {"PGgeom", "Lt"} for it in grade)


def test_com_uma_evidencia_gama_nao_muda_o_escore():
    """É o que permite rodar as combinações de uma evidência sem γ."""
    rng = np.random.default_rng(0)
    camadas = [rng.random((20, 20)), rng.random((20, 20))]
    a = estatico.fuzzy_gamma(camadas, [1.0, 0.0], 0.2)
    b = estatico.fuzzy_gamma(camadas, [1.0, 0.0], 0.9)
    np.testing.assert_allclose(a, b, rtol=1e-6)
    np.testing.assert_allclose(a, camadas[0], rtol=1e-6)


def test_ids_estaveis():
    assert ce.identificador({"PGgeom": 0.6, "Lt": 0.4}, 0.4) == \
        ce.identificador({"Lt": 0.4, "PGgeom": 0.6}, 0.4)
    assert ce.identificador({"PGgeom": 0.6, "Lt": 0.4}, 0.4) != \
        ce.identificador({"PGgeom": 0.6, "Lt": 0.4}, 0.5)


# ----------------------------------------------------------------- sugestão
def _linha(etapa, cap5, pesos=None, gama=None, nome="", auc=0.9):
    linha = {"id": "%s%s%s" % (nome, pesos, gama), "etapa": etapa,
             "nome": nome, "erro": "", "auc": auc, "captura_1": cap5 / 3,
             "captura_5": cap5, "captura_10": cap5 * 1.4, "r_blocos": 0.5,
             "teto": 0.8, "gama": float("nan") if gama is None else gama}
    pesos = pesos or {}
    linha["n_evidencias"] = float(sum(1 for p in pesos.values() if p > 0))
    for e in ce.EVIDENCIAS:
        linha["peso_%s" % e] = float(pesos.get(e, float("nan")))
    return linha


def test_sugere_a_mais_simples_dentro_da_tolerancia():
    linhas = [
        _linha("referencia", 0.40, nome="P"),
        _linha("referencia", 0.43, nome="K_1"),
        _linha("referencia", 0.47, {"PGgeom": 0.591, "Est": 0.075,
                                    "Lt": 0.334}, 0.4, nome="K_atual"),
        _linha("grade", 0.500, {"PGgeom": 0.6, "Est": 0.1, "Lt": 0.3}, 0.4),
        _linha("grade", 0.493, {"PGgeom": 0.7, "Est": 0.0, "Lt": 0.3}, 0.4),
        _linha("grade", 0.495, {"PGgeom": 0.6, "Est": 0.0, "Lt": 0.4}, 0.5),
        _linha("grade", 0.480, {"PGgeom": 1.0, "Est": 0.0, "Lt": 0.0}),
    ]
    sugestao = ce.sugerir(linhas, tolerancia_pp=1.0)
    # 0,495 e 0,493 estão a menos de 1 pp de 0,500; das duas, a de 0,495
    assert sugestao["pesos"] == {"PGgeom": 0.6, "Est": 0.0, "Lt": 0.4}
    assert sugestao["gama"] == 0.5 and sugestao["n_evidencias"] == 2
    assert sugestao["empatadas"] == 3
    assert sugestao["melhor"]["captura_5"] == 0.5
    assert sugestao["evidencias"]["Est"] == {"com": 0.5, "sem": 0.495}
    assert sugestao["referencias"]["K_atual"]["captura_5"] == 0.47
    # com tolerância maior, PGgeom sozinho basta (e não tem γ)
    larga = ce.sugerir(linhas, tolerancia_pp=2.5)
    assert larga["pesos"] == {"PGgeom": 1.0, "Est": 0.0, "Lt": 0.0}
    assert larga["gama"] is None
    texto = "\n".join(ce.texto_da_sugestao(sugestao))
    assert "PGgeom 0.6, Est 0.0, Lt 0.4; γ = 0.5" in texto
    assert "K com os pesos atuais" in texto and "+2.5 pp" in texto
    assert "Est     melhor com 0.500, melhor sem 0.495 (+0.5 pp)" in texto


def test_sem_grade_nao_ha_sugestao():
    assert ce.sugerir([_linha("referencia", 0.4, nome="P")]) is None
    assert "ainda não há" in ce.texto_da_sugestao(None)[1]


# ------------------------------------------------------ de ponta a ponta
def _gdal():
    return pytest.importorskip("osgeo.gdal")


def _suave(rng, forma, passos=6):
    arr = rng.random(forma)
    for _ in range(passos):
        arr = (arr + np.roll(arr, 1, 0) + np.roll(arr, -1, 0)
               + np.roll(arr, 1, 1) + np.roll(arr, -1, 1)) / 5.0
    arr -= arr.min()
    return arr / arr.max()


def _gravar(caminho, arr):
    gdal = _gdal()
    from osgeo import osr
    ny, nx = arr.shape
    destino = gdal.GetDriverByName("GTiff").Create(str(caminho), nx, ny, 1,
                                                   gdal.GDT_Float32)
    destino.SetGeoTransform((600000.0, 100.0, 0.0, 9400000.0, 0.0, -100.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(31981)
    destino.SetProjection(srs.ExportToWkt())
    destino.GetRasterBand(1).WriteArray(arr.astype(np.float32))
    destino = None
    return str(caminho)


def _dados(pasta):
    """PGgeom e Lt explicam onde há garimpo; Est é ruído sem relação."""
    pasta.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    forma = (120, 120)
    pg, est, lt = _suave(rng, forma), _suave(rng, forma), _suave(rng, forma)
    favor = 0.7 * pg + 0.3 * lt
    sorteio = rng.random(forma)
    antigo = (favor > np.quantile(favor, 0.93)) & (sorteio < 0.5)
    mu0 = antigo | ((favor > np.quantile(favor, 0.90)) & (sorteio < 0.6))
    # o garimpo novo nasce perto do existente, onde o terreno é favorável
    perto = estatico.ocupacao_local(mu0.astype(np.float32), 100.0, 600.0) > 0
    mu1 = mu0 | (perto & (favor > np.quantile(favor, 0.80))
                 & (rng.random(forma) < 0.7))
    mascara = np.ones(forma)
    mascara[:, :5] = 0.0
    nomes = {"PGGEOM": pg, "EST": est, "LT": lt,
             "MU_ANTIGO": antigo, "MU_INICIAL": mu0, "MU_FINAL": mu1,
             "MASCARA": mascara}
    return {chave: _gravar(pasta / ("%s.tif" % chave.lower()),
                           arr.astype(np.float32))
            for chave, arr in nomes.items()}


def _config(dados, pasta, **extra):
    config = dict(dados, PASTA_SAIDA=str(pasta), RESOLUCAO_M=100.0,
                  PASSO_PESOS=0.25, GAMAS=(0.3, 0.7), N_FAIXAS=11,
                  MINIMO_POR_FAIXA=5)
    config.update(extra)
    return config


def test_busca_completa_sugere_e_retoma_sem_repetir(tmp_path):
    dados = _dados(tmp_path / "dados")
    pasta = tmp_path / "saida"
    mensagens = []
    motor = ce.CalibracaoEstatica(_config(dados, pasta),
                                  registrar=mensagens.append)
    motor.carregar()
    progresso = []
    feitas = motor.rodar(progresso=lambda a, b: progresso.append((a, b)))
    plano = motor.plano()
    # 3 referências + 3 com uma evidência + (9 com duas + 3 com três) × 2 γ
    assert feitas == len(plano) == 3 + 3 + 12 * 2
    assert progresso[-1] == (len(plano), len(plano))
    linhas = motor.ler()
    assert not [l for l in linhas if l["erro"]]
    assert {l["id"] for l in linhas} == {it["id"] for it in plano}
    assert any("uma combinação:" in m for m in mensagens)

    por_nome = {l["nome"]: l for l in linhas if l["etapa"] == "referencia"}
    assert set(por_nome) == {"P", "K_1", "K_atual"}
    assert np.isnan(por_nome["P"]["teto"]) and por_nome["K_1"]["teto"] == 1.0

    sugestao = json.loads((pasta / "sugestao.json").read_text("utf-8"))
    melhor = max(l["captura_5"] for l in linhas if l["etapa"] == "grade")
    assert sugestao["metricas"]["captura_5"] >= melhor - 0.01 - 1e-9
    assert sugestao["n_evidencias"] <= 3
    resumo = (pasta / "resumo.txt").read_text("utf-8")
    assert "0) SUGESTÃO PARA O MODELO ESTÁTICO" in resumo
    assert "1) REFERÊNCIAS" in resumo and "atual: PGgeom 0.591" in resumo

    outro = ce.CalibracaoEstatica(_config(dados, pasta))
    outro.carregar()
    assert outro.rodar() == 0


def test_o_mapa_final_nao_entra_no_k(tmp_path):
    """Sem vazamento: K de uma combinação só depende do garimpo até t₀."""
    dados = _dados(tmp_path / "dados")
    a = ce.CalibracaoEstatica(_config(dados, tmp_path / "a"))
    a.carregar()
    outro_final = _gravar(tmp_path / "dados" / "outro_final.tif",
                          np.ones((120, 120), np.float32))
    b = ce.CalibracaoEstatica(_config(dados, tmp_path / "b",
                                      MU_FINAL=outro_final))
    b.carregar()
    pesos = {"PGgeom": 0.5, "Est": 0.25, "Lt": 0.25}
    np.testing.assert_array_equal(a.gerar_k(pesos, 0.3),
                                  b.gerar_k(pesos, 0.3))
    assert not np.array_equal(a.dados["obs"], b.dados["obs"])


def test_cancelar_para_entre_combinacoes(tmp_path):
    dados = _dados(tmp_path / "dados")
    motor = ce.CalibracaoEstatica(_config(dados, tmp_path / "saida"))
    motor.carregar()
    with pytest.raises(ce.Cancelada):
        motor.rodar(cancelado=lambda: len(motor.ids_feitos()) >= 4)
    assert len(motor.ids_feitos()) == 4
    assert (tmp_path / "saida" / "resumo.txt").is_file()


def test_erros_de_configuracao(tmp_path):
    dados = _dados(tmp_path / "dados")
    with pytest.raises(ValueError, match="mesmo arquivo"):
        ce.CalibracaoEstatica(_config(dados, tmp_path / "s",
                                      MU_FINAL=dados["MU_INICIAL"])).validar()
    with pytest.raises(ValueError, match="ao menos uma evidência"):
        ce.CalibracaoEstatica(_config(dados, tmp_path / "s", PGGEOM=None,
                                      EST=None, LT=None)).validar()
    # uma pasta com a calibração do modelo dinâmico não serve
    (tmp_path / "din").mkdir()
    (tmp_path / "din" / "configuracao.json").write_text(
        json.dumps({"modelo": "dinamico", "dados": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="modelo dinâmico"):
        ce.CalibracaoEstatica(_config(dados, tmp_path / "din")
                              ).conferir_pasta()
    # nem uma com outra busca (outra janela do nível)
    ce.CalibracaoEstatica(_config(dados, tmp_path / "e")).conferir_pasta()
    with pytest.raises(ValueError, match="JANELA_M"):
        ce.CalibracaoEstatica(_config(dados, tmp_path / "e", JANELA_M=500.0)
                              ).conferir_pasta()

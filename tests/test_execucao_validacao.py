# -*- coding: utf-8 -*-
"""Testes da orquestração da validação (sem QGIS, sem GDAL)."""

import csv
import datetime as dt
import importlib
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risco_garimpo.core import execucao_validacao as mod  # noqa: E402
from risco_garimpo.core import registro  # noqa: E402
from risco_garimpo.core.raster import Grade  # noqa: E402


def _grade(n=60, res=100.0):
    return Grade(gt=(600000.0, res, 0.0, 9400000.0, 0.0, -res), wkt="",
                 nx=n, ny=n)


def _mundo(n=60, semente=0):
    """K, μ₀, expansão prevista e μ final = μ₀ + previsto (acerto perfeito)."""
    rng = np.random.default_rng(semente)
    K = rng.uniform(0.3, 0.9, (n, n)).astype(np.float32)
    mu0 = np.zeros((n, n), dtype=np.float32)
    mu0[20:24, 20:24] = 0.3
    previsto = np.zeros((n, n), dtype=np.float32)
    previsto[18:27, 18:27] = 0.1
    previsto[20:24, 20:24] = 0.0
    return K, mu0, previsto, mu0 + previsto


def _arquivos(tmp_path, nomes):
    caminhos = {}
    for nome in nomes:
        caminho = tmp_path / ("%s.tif" % nome)
        caminho.write_bytes(("conteudo de %s" % nome).encode())
        caminhos[nome] = str(caminho)
    return caminhos


def _aparelhar(monkeypatch, tmp_path, arrays, grade=None):
    grade = grade or _grade()
    caminhos = _arquivos(tmp_path, arrays)
    por_caminho = {caminhos[nome]: arr for nome, arr in arrays.items()}
    monkeypatch.setattr(mod, "grade_de_raster", lambda c: grade)
    monkeypatch.setattr(mod, "ler_alinhado",
                        lambda caminho, g, reamostragem="bilinear", **kw:
                        por_caminho[caminho].copy())
    return caminhos


def _config(caminhos, tmp_path, **extra):
    base = dict(caminho_previsto=caminhos["previsto"],
                caminho_mu_inicial=caminhos["mu0"],
                caminho_mu_final=caminhos["mu1"],
                caminho_k=caminhos.get("K"),
                pasta_saida=str(tmp_path / "saida"), salvar_figura=False)
    base.update(extra)
    return mod.ConfiguracaoValidacao(**base)


# ------------------------------------------------------------------ preparar
def test_preparar_exige_os_tres_rasters(tmp_path):
    with pytest.raises(ValueError):
        mod.preparar(mod.ConfiguracaoValidacao(None, "a", "b", str(tmp_path)))
    with pytest.raises(ValueError) as erro:
        mod.preparar(mod.ConfiguracaoValidacao(
            str(tmp_path / "nao.tif"), "a", "b", str(tmp_path)))
    assert "não encontrado" in str(erro.value)


def test_preparar_recusa_o_mesmo_mapa_nas_duas_datas(monkeypatch, tmp_path):
    K, mu0, previsto, _ = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path,
                          {"previsto": previsto, "mu0": mu0})
    with pytest.raises(ValueError) as erro:
        mod.preparar(mod.ConfiguracaoValidacao(
            caminhos["previsto"], caminhos["mu0"], caminhos["mu0"],
            str(tmp_path)))
    assert "mesmo arquivo" in str(erro.value)


# ------------------------------------------------------------------ executar
def test_acerto_perfeito_da_auc_um_e_area_certa(monkeypatch, tmp_path):
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0, "mu1": mu1, "K": K})
    calculado = mod.executar(mod.preparar(_config(caminhos, tmp_path)))
    modelo = calculado["metricas"][0]
    assert modelo["pontuacao"] == "modelo"
    assert modelo["auc"] == pytest.approx(1.0)
    assert modelo["razao_area"] == pytest.approx(1.0, rel=1e-5)
    nomes = [m["pontuacao"] for m in calculado["metricas"]]
    assert "proximidade_200m" in nomes and "so_K" in nomes
    assert "proximidade_x_remanescente_380m" in nomes
    assert all("razao_area" not in m for m in calculado["metricas"][1:])
    curva = calculado["curvas"]["modelo"]
    assert curva[0] == 0.0 and curva[-1] == pytest.approx(1.0)


def test_sem_k_so_entram_as_linhas_de_base_de_proximidade(monkeypatch,
                                                          tmp_path):
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0, "mu1": mu1})
    calculado = mod.executar(mod.preparar(_config(caminhos, tmp_path)))
    nomes = [m["pontuacao"] for m in calculado["metricas"]]
    assert nomes == ["modelo"] + ["proximidade_%dm" % e
                                  for e in mod.ESCALAS_BASE_M]


def test_mascara_de_avaliacao_restringe_o_universo(monkeypatch, tmp_path):
    K, mu0, previsto, mu1 = _mundo()
    avaliacao = np.zeros(K.shape, dtype=np.float32)
    avaliacao[:, :22] = 1.0               # metade esquerda do garimpo novo
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0, "mu1": mu1, "aval": avaliacao})
    tudo = mod.executar(mod.preparar(_config(caminhos, tmp_path)))
    parte = mod.executar(mod.preparar(_config(
        caminhos, tmp_path, caminho_mascara_avaliacao=caminhos["aval"])))
    assert parte["metricas"][0]["obs_ha"] < tudo["metricas"][0]["obs_ha"]
    assert parte["universo"].sum() == int(avaliacao.sum())


def test_raster_de_classes_e_recusado(monkeypatch, tmp_path):
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0 * 30, "mu1": mu1})
    with pytest.raises(ValueError) as erro:
        mod.executar(mod.preparar(_config(caminhos, tmp_path)))
    assert "classe" in str(erro.value)


def test_sem_garimpo_novo_falha_com_explicacao(monkeypatch, tmp_path):
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu1, "mu1": mu0})   # ordem trocada
    with pytest.raises(ValueError) as erro:
        mod.executar(mod.preparar(_config(caminhos, tmp_path)))
    assert "ordem" in str(erro.value)


def test_cancelamento(monkeypatch, tmp_path):
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0, "mu1": mu1})
    with pytest.raises(mod.ValidacaoCancelada):
        mod.executar(mod.preparar(_config(caminhos, tmp_path)),
                     cancelado=lambda: True)


# ------------------------------------------------------------------ escrever
def test_escrever_grava_tabelas_e_registro(monkeypatch, tmp_path):
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0, "mu1": mu1, "K": K})
    config = _config(caminhos, tmp_path, prefixo="PMT_val")
    mensagens = []
    gravados = mod.escrever(mod.executar(mod.preparar(config)),
                            registrar=mensagens.append)
    nomes = sorted(os.path.basename(c) for c in gravados)
    assert nomes == ["PMT_val_curva_captura.csv", "PMT_val_metricas.csv",
                     "PMT_val_registro.json"]
    assert nomes == sorted(os.path.basename(c)
                           for c in mod.caminhos_de_saida(config))
    with open(mod.caminho_de_saida(config, "metricas", "csv"),
              encoding="utf-8") as arquivo:
        linhas = list(csv.DictReader(arquivo, delimiter=";"))
    assert linhas[0]["pontuacao"] == "modelo"
    assert float(linhas[0]["auc"]) == pytest.approx(1.0)
    with open(mod.caminho_de_saida(config, "curva_captura", "csv"),
              encoding="utf-8") as arquivo:
        curva = list(csv.reader(arquivo, delimiter=";"))
    assert curva[0][:2] == ["fracao_area", "modelo"] and len(curva) == 102
    dados = registro.ler(mod.caminho_de_saida(config, "registro", "json"))
    assert dados["ferramenta"] == "validacao"
    papeis = {i["papel"] for i in dados["insumos"]}
    assert {"previsto", "mu_inicial", "mu_final", "K"} <= papeis
    assert dados["resultados"]["metricas"][0]["pontuacao"] == "modelo"


def test_figura_quando_ha_matplotlib(monkeypatch, tmp_path):
    try:
        importlib.import_module("matplotlib.backends.backend_agg")
    except ImportError:
        pytest.skip("matplotlib indisponível neste Python")
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0, "mu1": mu1, "K": K})
    config = _config(caminhos, tmp_path, salvar_figura=True)
    gravados = mod.escrever(mod.executar(mod.preparar(config)))
    figura = mod.caminho_de_saida(config, "curva_captura", "png")
    assert figura in gravados
    assert open(figura, "rb").read(8) == b"\x89PNG\r\n\x1a\n"


# ------------------------------------------------------- a partir do registro
def _registro_dinamico(tmp_path, caminhos, ultima_alerta="2025-03-01"):
    insumos = [registro.descrever_arquivo(caminhos[nome], papel)
               for nome, papel in (("K", "K"), ("mu0", "mu0"))]
    dados = registro.montar(
        "modelo_dinamico",
        {"data_inicio": dt.date(2025, 1, 1), "data_fim": dt.date(2026, 12, 31),
         "prefixo": "PMT"},
        _grade(), insumos, {"expansao": caminhos["previsto"]},
        eventos={"alertas": {"ultima_data": ultima_alerta},
                 "fiscalizacao": {"ultima_data": "2024-12-01"}})
    return registro.gravar(str(tmp_path / "PMT_registro_20261231.json"),
                           dados)


def test_de_registro_monta_a_configuracao(monkeypatch, tmp_path):
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0, "mu1": mu1, "K": K})
    caminho = _registro_dinamico(tmp_path, caminhos)
    config = mod.de_registro(caminho, caminhos["mu1"], salvar_figura=False)
    assert config.caminho_previsto == caminhos["previsto"]
    assert config.caminho_mu_inicial == caminhos["mu0"]
    assert config.caminho_k == caminhos["K"]
    assert config.data_inicial == "2025-01-01"
    assert config.prefixo == "PMT_validacao"
    assert config.pasta_saida == str(tmp_path)
    calculado = mod.executar(mod.preparar(config))
    assert calculado["alterados"] == []


def test_alertas_depois_da_data_inicial_viram_aviso(monkeypatch, tmp_path):
    """Com os alertas do período, a validação mede reconstrução."""
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0, "mu1": mu1, "K": K})
    config = mod.de_registro(_registro_dinamico(tmp_path, caminhos),
                             caminhos["mu1"], salvar_figura=False)
    mensagens = []
    mod.preparar(config, registrar=mensagens.append)
    avisos = [m for m in mensagens if m.startswith("AVISO")]
    assert len(avisos) == 1 and "reconstrução" in avisos[0]
    assert "alertas até 2025-03-01" in avisos[0]

    honesto = mod.de_registro(
        _registro_dinamico(tmp_path, caminhos, ultima_alerta="2024-12-30"),
        caminhos["mu1"])
    assert mod.avisos_de_honestidade(honesto) == []


def test_insumo_alterado_desde_a_execucao_vira_aviso(monkeypatch, tmp_path):
    K, mu0, previsto, mu1 = _mundo()
    caminhos = _aparelhar(monkeypatch, tmp_path, {
        "previsto": previsto, "mu0": mu0, "mu1": mu1, "K": K})
    config = mod.de_registro(_registro_dinamico(tmp_path, caminhos),
                             caminhos["mu1"], salvar_figura=False)
    with open(caminhos["K"], "ab") as arquivo:
        arquivo.write(b" (refeito depois)")
    mensagens = []
    calculado = mod.executar(mod.preparar(config), registrar=mensagens.append)
    assert calculado["alterados"] == [caminhos["K"]]
    assert any("mudou desde a execução" in m for m in mensagens)
    mod.escrever(calculado)
    with open(mod.caminho_de_saida(config, "registro", "json"),
              encoding="utf-8") as arquivo:
        dados = json.load(arquivo)
    assert dados["resultados"]["insumos_alterados"] == [caminhos["K"]]


def test_de_registro_recusa_outro_tipo_de_registro(tmp_path):
    caminho = registro.gravar(str(tmp_path / "r.json"), registro.montar(
        "modelo_estatico", {}, _grade(), [], {}))
    with pytest.raises(ValueError):
        mod.de_registro(caminho, "mu1.tif")


def test_rotulos_da_figura():
    assert mod._rotulo("proximidade_x_remanescente_200m") == \
        "proximidade × remanescente, 200 m"
    assert mod._rotulo("so_K") == "so K"
    assert mod._data_br("2025-01-01") == "01/01/2025"
    assert mod._data_br(None) == "None"

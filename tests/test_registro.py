# -*- coding: utf-8 -*-
"""Testes do registro de execução (sem QGIS, sem GDAL)."""

import dataclasses
import datetime as dt
import hashlib
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risco_garimpo.core import registro  # noqa: E402
from risco_garimpo.core.model import Parametros  # noqa: E402
from risco_garimpo.core.raster import Grade  # noqa: E402


class _Crs(object):
    def __init__(self, authid):
        self._authid = authid

    def authid(self):
        return self._authid


class _CamadaFalsa(object):
    """O mínimo da API de QgsVectorLayer que o registro consulta."""

    def __init__(self, fonte, nome="camada", feicoes=3, crs="EPSG:31981",
                 publica=None, editada=False):
        self._fonte, self._nome = fonte, nome
        self._feicoes, self._crs = feicoes, crs
        self._publica = publica
        self._editada = editada

    def source(self):
        return self._fonte

    def publicSource(self):  # noqa: N802 - API do QGIS
        return self._fonte if self._publica is None else self._publica

    def isEditable(self):  # noqa: N802 - API do QGIS
        return self._editada

    def isModified(self):  # noqa: N802 - API do QGIS
        return self._editada

    def name(self):
        return self._nome

    def featureCount(self):  # noqa: N802 - API do QGIS
        return self._feicoes

    def crs(self):
        return _Crs(self._crs)


def _grade(nx=40, ny=30, res=100.0):
    return Grade(gt=(600000.0, res, 0.0, 9400000.0, 0.0, -res), wkt="",
                 nx=nx, ny=ny)


# ------------------------------------------------------- identidade
def test_sha256_confere_com_hashlib(tmp_path):
    arquivo = tmp_path / "k.tif"
    conteudo = os.urandom(3 * (1 << 20) + 17)    # mais de um bloco
    arquivo.write_bytes(conteudo)
    insumo = registro.descrever_arquivo(str(arquivo), "K")
    assert insumo["papel"] == "K"
    assert insumo["existe"] is True
    assert insumo["tamanho_bytes"] == len(conteudo)
    assert insumo["sha256"] == hashlib.sha256(conteudo).hexdigest()
    dt.datetime.fromisoformat(insumo["modificado_em"])


def test_arquivo_inexistente_nao_derruba(tmp_path):
    insumo = registro.descrever_arquivo(str(tmp_path / "nada.tif"), "K")
    assert insumo["existe"] is False
    assert "sha256" not in insumo


def test_shapefile_leva_os_complementos(tmp_path):
    """A data dos eventos está no .dbf: sem ele o registro não identifica o dado."""
    for extensao in (".shp", ".shx", ".dbf", ".prj"):
        (tmp_path / ("autos" + extensao)).write_bytes(extensao.encode())
    insumo = registro.identificar({"papel": "fiscalizacao",
                                   "caminho": str(tmp_path / "autos.shp")})
    nomes = sorted(os.path.basename(c["caminho"])
                   for c in insumo["complementos"])
    assert nomes == ["autos.dbf", "autos.prj", "autos.shx"]
    dbf = [c for c in insumo["complementos"]
           if c["caminho"].endswith(".dbf")][0]
    assert dbf["sha256"] == hashlib.sha256(b".dbf").hexdigest()


def test_camada_de_geopackage_aponta_para_o_arquivo(tmp_path):
    gpkg = tmp_path / "alertas.gpkg"
    gpkg.write_bytes(b"gpkg")
    camada = _CamadaFalsa(str(gpkg) + "|layername=alertas", nome="alertas",
                          feicoes=150)
    descricao = registro.descrever_camada(camada, "alertas",
                                          campo_data="data")
    assert descricao["caminho"] == str(gpkg)
    assert descricao["feicoes"] == 150
    assert descricao["crs"] == "EPSG:31981"
    assert descricao["campo_data"] == "data"
    assert "sha256" not in descricao              # fica para o segundo plano
    assert registro.identificar(descricao)["sha256"] == \
        hashlib.sha256(b"gpkg").hexdigest()


def test_fonte_gravada_nao_leva_a_senha():
    """O registro acompanha os mapas: senha de banco não pode ir junto."""
    fonte = ("dbname='ibama' host=10.0.0.1 user='analista' "
             "password='segredo' table=\"public\".\"autos\" (geom)")
    camada = _CamadaFalsa(fonte, publica=fonte.replace(
        " password='segredo'", ""))
    descricao = registro.descrever_camada(camada, "fiscalizacao")
    assert "segredo" not in json.dumps(descricao)
    assert "dbname='ibama'" in descricao["fonte"]


def test_texto_delimitado_e_spatialite_apontam_para_o_arquivo(tmp_path):
    csv = tmp_path / "autos de infração.csv"
    csv.write_text("x;y;data\n", encoding="utf-8")
    fonte = csv.as_uri() + "?type=csv&delimiter=;&xField=x&yField=y"
    descricao = registro.descrever_camada(_CamadaFalsa(fonte), "fiscalizacao")
    assert descricao["caminho"] == str(csv)

    banco = tmp_path / "eventos.sqlite"
    banco.write_bytes(b"sqlite")
    descricao = registro.descrever_camada(
        _CamadaFalsa("dbname='%s' table=\"alertas\" (geom)" % banco),
        "alertas")
    assert descricao["caminho"] == str(banco)


def test_edicoes_nao_salvas_ficam_marcadas():
    assert registro.descrever_camada(
        _CamadaFalsa("x", editada=True), "alertas")["alteracoes_nao_salvas"]
    assert not registro.descrever_camada(
        _CamadaFalsa("x"), "alertas")["alteracoes_nao_salvas"]


def test_camada_em_memoria_nao_tem_arquivo():
    camada = _CamadaFalsa("Point?crs=EPSG:31981", nome="tmp")
    descricao = registro.descrever_camada(camada, "alertas")
    assert descricao["caminho"] is None
    assert registro.identificar(descricao)["existe"] is False


# ------------------------------------------------------- montagem e JSON
def test_configuracao_simples_omite_camadas_e_objetos():
    class Config(object):
        pass

    cfg = Config()
    cfg.caminho_k = "/k.tif"
    cfg.data_fim = dt.date(2026, 12, 31)
    cfg.janela_dias = 30
    cfg.camada_alertas = _CamadaFalsa("x")
    cfg.parametros = Parametros()
    simples = registro.configuracao_simples(cfg)
    assert simples == {"caminho_k": "/k.tif", "data_fim": dt.date(2026, 12, 31),
                       "janela_dias": 30}


def test_json_aceita_numpy_datas_e_nao_finitos(tmp_path):
    dados = registro.montar(
        "modelo_dinamico", {"data_fim": dt.date(2026, 12, 31)}, _grade(),
        [], {"expansao": "/s/e.tif"}, parametros=Parametros(),
        eventos={"alertas": {"lidos": np.int64(150),
                             "primeira_data": dt.date(2025, 1, 3)}},
        resultados={"nan": float("nan"), "inf": np.float32(np.inf),
                    "vetor": np.array([1.5, 2.5]), "par": (1, 2)})
    caminho = registro.gravar(str(tmp_path / "r.json"), dados)
    lido = json.loads(open(caminho, encoding="utf-8").read())
    assert lido["configuracao"]["data_fim"] == "2026-12-31"
    assert lido["eventos"]["alertas"]["lidos"] == 150
    assert lido["eventos"]["alertas"]["primeira_data"] == "2025-01-03"
    assert lido["resultados"]["nan"] is None
    assert lido["resultados"]["inf"] is None
    assert lido["resultados"]["vetor"] == [1.5, 2.5]
    assert lido["resultados"]["par"] == [1, 2]
    assert lido["parametros"] == dataclasses.asdict(Parametros())
    assert lido["grade"]["nx"] == 40 and lido["grade"]["pixel_m"] == 100.0
    assert not os.path.exists(caminho + ".tmp")


def test_falha_na_troca_nao_deixa_temporario(monkeypatch, tmp_path):
    def negar(origem, destino):
        raise PermissionError("arquivo em uso")

    monkeypatch.setattr(registro.os, "replace", negar)
    caminho = str(tmp_path / "r.json")
    with pytest.raises(PermissionError):
        registro.gravar(caminho, {"a": 1})
    assert os.listdir(str(tmp_path)) == []


def test_versao_vem_do_metadata():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(raiz, "risco_garimpo", "metadata.txt"),
              encoding="utf-8") as arquivo:
        versao = [l.split("=", 1)[1].strip() for l in arquivo
                  if l.startswith("version=")][0]
    assert registro.versao_plugin() == versao


def test_acrescentar_preserva_o_que_havia(tmp_path):
    caminho = registro.gravar(str(tmp_path / "r.json"), registro.montar(
        "modelo_dinamico", {}, _grade(), [], {"expansao": "/s/e.tif"},
        resultados={"diagnostico": {"n_passos": 3}}))
    registro.acrescentar(caminho, {"subbacias": "/s/sb.gpkg"},
                         {"subbacias": 12})
    lido = registro.ler(caminho)
    assert lido["saidas"] == {"expansao": "/s/e.tif",
                              "subbacias": "/s/sb.gpkg"}
    assert lido["resultados"] == {"diagnostico": {"n_passos": 3},
                                  "subbacias": 12}


# ------------------------------------------------------- modelo dinâmico
def _aparelhar_dinamico(monkeypatch, grade):
    from risco_garimpo.core import raster as _raster

    rng = np.random.default_rng(0)
    K = rng.uniform(0.2, 0.8, grade.shape).astype(np.float32)
    mu0 = np.zeros(grade.shape, dtype=np.float32)
    mu0[10:13, 10:13] = 0.1
    leituras = {"K": K, "mu0": mu0}
    monkeypatch.setattr(_raster, "ler_alinhado",
                        lambda caminho, g, reamostragem="bilinear", **kw:
                        leituras["K" if "k" in os.path.basename(caminho)
                                 else "mu0"])
    gravados = []

    def escrever(caminho, arr, g, **kw):
        gravados.append(caminho)
        return caminho

    monkeypatch.setattr(_raster, "escrever_raster", escrever)
    return gravados


def _config_dinamica(tmp_path, **extra):
    from risco_garimpo.core.execucao import Configuracao

    (tmp_path / "k.tif").write_bytes(b"conteudo de K")
    (tmp_path / "mu0.tif").write_bytes(b"conteudo de mu0")
    base = dict(caminho_k=str(tmp_path / "k.tif"),
                caminho_mu0=str(tmp_path / "mu0.tif"),
                pasta_saida=str(tmp_path / "saida"), prefixo="PMT",
                data_inicio=dt.date(2025, 1, 1),
                data_fim=dt.date(2025, 3, 1),
                parametros=Parametros(r0=0.01))
    base.update(extra)
    return Configuracao(**base)


def test_execucao_dinamica_grava_o_registro(monkeypatch, tmp_path):
    from risco_garimpo.core.execucao import (Preparo, caminho_de_registro,
                                             executar)

    grade = _grade()
    _aparelhar_dinamico(monkeypatch, grade)
    cfg = _config_dinamica(tmp_path)
    mensagens = []
    saida = executar(cfg, Preparo(grade, inicio="2026-10-21T10:00:00-03:00"),
                     log=mensagens.append)

    assert saida["registro"] == caminho_de_registro(cfg)
    assert os.path.basename(saida["registro"]) == "PMT_registro_20250301.json"
    lido = registro.ler(saida["registro"])
    assert lido["ferramenta"] == "modelo_dinamico"
    assert lido["inicio"] == "2026-10-21T10:00:00-03:00"
    assert lido["parametros"]["r0"] == 0.01
    assert lido["configuracao"]["data_fim"] == "2025-03-01"
    assert lido["configuracao"]["prefixo"] == "PMT"
    papeis = {i["papel"]: i for i in lido["insumos"]}
    assert set(papeis) == {"K", "mu0"}
    assert papeis["K"]["sha256"] == \
        hashlib.sha256(b"conteudo de K").hexdigest()
    assert lido["saidas"]["expansao"].endswith("PMT_expansao_20250301.tif")
    assert lido["resultados"]["diagnostico"]["n_passos"] == \
        saida["diagnostico"]["n_passos"]
    assert any("Registro da execução" in m for m in mensagens)


def test_falha_no_registro_nao_derruba_a_execucao(monkeypatch, tmp_path):
    from risco_garimpo.core.execucao import Preparo, executar

    grade = _grade()
    gravados = _aparelhar_dinamico(monkeypatch, grade)

    def falhar(caminho, dados):
        raise OSError("disco cheio")

    monkeypatch.setattr(registro, "gravar", falhar)
    mensagens = []
    saida = executar(_config_dinamica(tmp_path), Preparo(grade),
                     log=mensagens.append)
    assert saida["registro"] is None
    assert gravados                               # as saídas foram gravadas
    assert any("AVISO" in m and "disco cheio" in m for m in mensagens)


def test_preparar_registra_camadas_e_eventos(monkeypatch, tmp_path):
    import risco_garimpo.core.execucao as mod
    from risco_garimpo.core import raster as _raster

    grade = _grade()
    monkeypatch.setattr(_raster, "grade_de_raster", lambda c: grade)
    monkeypatch.setattr(_raster, "grade_reamostrada", lambda g, r, w: g)
    # 4 alertas: três colados (uma frente) e um isolado; 1 auto fora da grade
    pontos = {
        "alertas": ([600550.0, 600560.0, 600570.0, 602050.0],
                    [9398550.0, 9398550.0, 9398550.0, 9398050.0],
                    [dt.date(2025, 2, 1), dt.date(2025, 1, 10),
                     dt.date(2025, 3, 1), dt.date(2025, 4, 1)], 2),
        "autos": ([600550.0, 700000.0], [9398550.0, 9398550.0],
                  [dt.date(2025, 5, 1), dt.date(2025, 6, 1)], 0),
    }

    def extrair(camada, campo, g):
        x, y, datas, ignorados = pontos[camada.name()]
        return np.array(x), np.array(y), list(datas), ignorados

    monkeypatch.setattr(mod, "extrair_eventos", extrair)
    gpkg = tmp_path / "eventos.gpkg"
    gpkg.write_bytes(b"x")
    cfg = _config_dinamica(
        tmp_path,
        camada_alertas=_CamadaFalsa(str(gpkg) + "|layername=a",
                                    nome="alertas", feicoes=6, editada=True),
        campo_data_alertas="data_det",
        camada_fiscalizacao=_CamadaFalsa("Point?crs=EPSG:31981",
                                         nome="autos", feicoes=2),
        campo_data_fiscalizacao="data_auto")
    mensagens = []
    preparo = mod.preparar(cfg, log=mensagens.append)

    assert preparo.inicio is not None
    assert any("AVISO" in m and "edições não salvas" in m and "alertas" in m
               for m in mensagens)
    papeis = {i["papel"]: i for i in preparo.insumos}
    assert papeis["alertas"]["caminho"] == str(gpkg)
    assert papeis["alertas"]["campo_data"] == "data_det"
    assert papeis["fiscalizacao"]["caminho"] is None
    alertas = preparo.eventos["alertas"]
    assert alertas["lidos"] == 4 and alertas["descartados"] == 2
    assert alertas["frentes"] == 2
    assert alertas["primeira_data"] == dt.date(2025, 1, 10)
    autos = preparo.eventos["fiscalizacao"]
    assert autos["lidos"] == 2 and autos["fora_da_grade"] == 1


def test_agregar_completa_o_registro(monkeypatch, tmp_path):
    import risco_garimpo.core.agregacao as agregacao
    from risco_garimpo.core.execucao import (Preparo, agregar,
                                             caminho_de_registro, executar)

    grade = _grade()
    _aparelhar_dinamico(monkeypatch, grade)
    cfg = _config_dinamica(tmp_path)
    saida = executar(cfg, Preparo(grade))
    monkeypatch.setattr(agregacao, "agregar_resultado",
                        lambda *a, **k: [{"id": 1}, {"id": 2}, {"id": 3}])
    cfg.camada_subbacias = _CamadaFalsa("Polygon?crs=EPSG:31981")
    caminho, tabela = agregar(cfg, grade, saida["resultado"],
                              registro=saida["registro"])

    lido = registro.ler(caminho_de_registro(cfg))
    assert lido["saidas"]["subbacias"] == caminho
    assert lido["saidas"]["expansao"]                 # o resto continua lá
    assert lido["resultados"]["subbacias"] == 3


def test_agregar_nao_completa_registro_de_outra_execucao(monkeypatch,
                                                         tmp_path):
    """Sem registro desta execução, o JSON que estiver na pasta é de outra."""
    import risco_garimpo.core.agregacao as agregacao
    from risco_garimpo.core.execucao import agregar, caminho_de_registro

    cfg = _config_dinamica(tmp_path)
    os.makedirs(cfg.pasta_saida)
    antigo = registro.gravar(caminho_de_registro(cfg), {"inicio": "OUTRA"})
    monkeypatch.setattr(agregacao, "agregar_resultado",
                        lambda *a, **k: [{"id": 1}])
    cfg.camada_subbacias = _CamadaFalsa("Polygon?crs=EPSG:31981")
    agregar(cfg, _grade(), None, registro=None)
    assert registro.ler(antigo) == {"inicio": "OUTRA"}


def test_falha_no_meio_nao_deixa_registro_antigo(monkeypatch, tmp_path):
    """Saídas novas ao lado do registro de outra execução enganam o leitor."""
    from risco_garimpo.core import raster as _raster
    from risco_garimpo.core.execucao import (Preparo, caminho_de_registro,
                                             executar)

    grade = _grade()
    _aparelhar_dinamico(monkeypatch, grade)
    cfg = _config_dinamica(tmp_path, salvar_mu_final=True)
    os.makedirs(cfg.pasta_saida)
    registro.gravar(caminho_de_registro(cfg), {"inicio": "OUTRA"})
    chamadas = []

    def escrever(caminho, arr, g, **kw):
        chamadas.append(caminho)
        if len(chamadas) == 2:
            raise PermissionError("arquivo em uso: %s" % caminho)
        return caminho

    monkeypatch.setattr(_raster, "escrever_raster", escrever)
    with pytest.raises(PermissionError):
        executar(cfg, Preparo(grade))
    assert not os.path.exists(caminho_de_registro(cfg))


# ------------------------------------------------------- modelo estático
def test_escrita_estatica_grava_o_registro(monkeypatch, tmp_path):
    import risco_garimpo.core.execucao_estatica as mod

    from test_execucao_estatica import _GradeFalsa, _aparelhar, _mundo

    grade = _GradeFalsa(nx=120, ny=120)
    pggeom, mu = _mundo(grade)
    ibx = tmp_path / "pggeom.tif"
    ibx.write_bytes(b"pggeom")
    _aparelhar(monkeypatch, mod, grade, {str(ibx): pggeom, "/dados/mu.tif": mu})
    monkeypatch.setattr(mod, "escrever_raster",
                        lambda caminho, arr, g, **kw: caminho)
    cfg = mod.ConfiguracaoEstatica(
        caminho_ibx=str(ibx), caminho_mu="/dados/mu.tif",
        pasta_saida=str(tmp_path / "saida"), prefixo="PMT", n_faixas=20,
        minimo_por_faixa=100, salvar_tabela=False)
    gravados = mod.escrever(mod.executar(mod.preparar(cfg)))

    caminho = mod.caminho_de_saida(cfg, "registro", "json")
    assert caminho in gravados
    lido = registro.ler(caminho)
    assert lido["ferramenta"] == "modelo_estatico"
    assert lido["inicio"] is not None
    assert lido["configuracao"]["gamma"] == cfg.gamma
    assert lido["configuracao"]["piso"] == cfg.piso
    papeis = {i["papel"]: i for i in lido["insumos"]}
    assert papeis["PGgeom"]["sha256"] == hashlib.sha256(b"pggeom").hexdigest()
    assert papeis["mu"]["existe"] is False
    assert set(lido["saidas"]) == {"K", "escore", "remanescente"}
    assert 0.0 < lido["resultados"]["teto_estimado"] <= 1.0
    assert lido["resultados"]["variaveis"] == {"PGgeom": cfg.peso_ibx}
    assert lido["grade"]["geotransform"] is None   # grade falsa, sem gt


def test_falha_no_meio_da_escrita_estatica_nao_deixa_registro_antigo(
        monkeypatch, tmp_path):
    import risco_garimpo.core.execucao_estatica as mod

    from test_execucao_estatica import _GradeFalsa, _aparelhar, _mundo

    grade = _GradeFalsa(nx=80, ny=80)
    pggeom, mu = _mundo(grade)
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu})
    cfg = mod.ConfiguracaoEstatica(
        caminho_ibx="/dados/pggeom.tif", caminho_mu="/dados/mu.tif",
        pasta_saida=str(tmp_path / "saida"), prefixo="PMT", n_faixas=20,
        minimo_por_faixa=50, salvar_tabela=False)
    os.makedirs(cfg.pasta_saida)
    antigo = registro.gravar(mod.caminho_de_saida(cfg, "registro", "json"),
                             {"inicio": "OUTRA"})
    chamadas = []

    def escrever(caminho, arr, g, **kw):
        chamadas.append(caminho)
        if len(chamadas) == 2:
            raise PermissionError("arquivo em uso: %s" % caminho)
        return caminho

    monkeypatch.setattr(mod, "escrever_raster", escrever)
    with pytest.raises(PermissionError):
        mod.escrever(mod.executar(mod.preparar(cfg)))
    assert not os.path.exists(antigo)


def test_cancelar_durante_o_hash_interrompe(monkeypatch, tmp_path):
    """Sem isso, cancelar na fase do SHA-256 ainda gravaria as saídas."""
    import risco_garimpo.core.execucao_estatica as mod

    from test_execucao_estatica import _GradeFalsa, _aparelhar, _mundo

    grade = _GradeFalsa(nx=80, ny=80)
    pggeom, mu = _mundo(grade)
    ibx = tmp_path / "pggeom.tif"
    ibx.write_bytes(b"x" * 1024)
    _aparelhar(monkeypatch, mod, grade, {str(ibx): pggeom, "/dados/mu.tif": mu})
    estado = {"cancelar": False}
    original = registro.sha256

    def sha256(caminho, interromper=None):
        estado["cancelar"] = True                 # o usuário clica Cancelar
        return original(caminho, interromper)

    monkeypatch.setattr(registro, "sha256", sha256)
    cfg = mod.ConfiguracaoEstatica(
        caminho_ibx=str(ibx), caminho_mu="/dados/mu.tif",
        pasta_saida=str(tmp_path / "saida"), n_faixas=20, minimo_por_faixa=50)
    with pytest.raises(mod.SimulacaoCancelada):
        mod.executar(mod.preparar(cfg), cancelado=lambda: estado["cancelar"])


def test_falha_no_registro_estatico_nao_derruba_a_escrita(monkeypatch,
                                                         tmp_path):
    import risco_garimpo.core.execucao_estatica as mod

    from test_execucao_estatica import _GradeFalsa, _aparelhar, _mundo

    grade = _GradeFalsa(nx=80, ny=80)
    pggeom, mu = _mundo(grade)
    _aparelhar(monkeypatch, mod, grade,
               {"/dados/pggeom.tif": pggeom, "/dados/mu.tif": mu})
    monkeypatch.setattr(mod, "escrever_raster",
                        lambda caminho, arr, g, **kw: caminho)
    monkeypatch.setattr(registro, "gravar",
                        lambda c, d: (_ for _ in ()).throw(OSError("negado")))
    cfg = mod.ConfiguracaoEstatica(
        caminho_ibx="/dados/pggeom.tif", caminho_mu="/dados/mu.tif",
        pasta_saida=str(tmp_path / "saida"), prefixo="PMT", n_faixas=20,
        minimo_por_faixa=50, salvar_tabela=False)
    mensagens = []
    gravados = mod.escrever(mod.executar(mod.preparar(cfg)),
                            registrar=mensagens.append)
    assert gravados and all(not g.endswith(".json") for g in gravados)
    assert any("AVISO" in m and "negado" in m for m in mensagens)

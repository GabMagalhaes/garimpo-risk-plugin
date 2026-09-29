# -*- coding: utf-8 -*-
"""Testa o gerador de insumos sintéticos com uma GDAL de mentira.

Não há GDAL no ambiente de teste, então aqui se substitui ``osgeo`` por um
dublê que apenas registra o que teria sido gravado. O que se verifica é a
parte que importa e que poderia estar errada em silêncio: os valores dos
rasters, a coerência entre μ₀ e K, e — principalmente — que os alertas
sintéticos realmente colapsam nas 6 frentes que o roteiro de teste promete.
"""

import datetime as _dt
import os
import sys
import types

import numpy as np
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from risco_garimpo.core.frentes import agrupar_frentes  # noqa: E402


# ----------------------------------------------------------------- dublê GDAL
class _Banda:
    def __init__(self, dono):
        self.dono = dono

    def SetNoDataValue(self, valor):  # noqa: N802
        self.dono.nodata = valor

    def WriteArray(self, arr):  # noqa: N802
        self.dono.arr = np.array(arr)

    def FlushCache(self):  # noqa: N802
        pass


class _Raster:
    def __init__(self, caminho):
        self.caminho = caminho
        self.arr = None
        self.gt = None
        self.proj = None
        self.nodata = None

    def SetGeoTransform(self, gt):  # noqa: N802
        self.gt = gt

    def SetProjection(self, proj):  # noqa: N802
        self.proj = proj

    def GetRasterBand(self, i):  # noqa: N802
        return _Banda(self)


class _Camada:
    def __init__(self, nome):
        self.nome = nome
        self.pontos = []
        self.datas = []
        self.campos = []

    def CreateField(self, campo):  # noqa: N802
        self.campos.append(campo)

    def GetLayerDefn(self):  # noqa: N802
        return self

    def CreateFeature(self, feicao):  # noqa: N802
        self.pontos.append(feicao.geometria.ponto)
        self.datas.append(feicao.data)


class _Fonte:
    def __init__(self):
        self.camadas = {}

    def CreateLayer(self, nome, srs, tipo):  # noqa: N802
        self.camadas[nome] = _Camada(nome)
        return self.camadas[nome]


class _Geometria:
    def __init__(self, tipo):
        self.ponto = None

    def AddPoint(self, x, y):  # noqa: N802
        self.ponto = (x, y)


class _Feicao:
    def __init__(self, definicao):
        self.geometria = None
        self.data = None

    def SetGeometry(self, geometria):  # noqa: N802
        self.geometria = geometria

    def SetField(self, nome, *valores):  # noqa: N802
        if nome == "data" and len(valores) >= 3:
            self.data = _dt.date(valores[0], valores[1], valores[2])


def _montar_osgeo(registro):
    gdal = types.ModuleType("osgeo.gdal")
    gdal.GDT_Float32 = 6
    gdal.UseExceptions = lambda: None

    class _DriverRaster:
        def Create(self, caminho, nx, ny, nb, tipo, options=None):  # noqa: N802
            ds = _Raster(caminho)
            registro["rasters"][os.path.basename(caminho)] = ds
            return ds

    gdal.GetDriverByName = lambda nome: _DriverRaster()

    ogr = types.ModuleType("osgeo.ogr")
    ogr.wkbPoint = 1
    ogr.OFTDate = 9
    ogr.OFTString = 4
    ogr.FieldDefn = lambda nome, tipo: (nome, tipo)
    ogr.Geometry = _Geometria
    ogr.Feature = _Feicao

    class _DriverVetor:
        def CreateDataSource(self, caminho):  # noqa: N802
            fonte = _Fonte()
            registro["vetor"] = fonte
            return fonte

    ogr.GetDriverByName = lambda nome: _DriverVetor()

    osr = types.ModuleType("osgeo.osr")
    osr.OAMS_TRADITIONAL_GIS_ORDER = 1

    class _SRS:
        def ImportFromEPSG(self, codigo):  # noqa: N802
            self.codigo = codigo

        def SetAxisMappingStrategy(self, e):  # noqa: N802
            pass

        def ExportToWkt(self):  # noqa: N802
            return "WKT_FALSO"

    osr.SpatialReference = _SRS

    pacote = types.ModuleType("osgeo")
    pacote.gdal = gdal
    pacote.ogr = ogr
    pacote.osr = osr
    return {"osgeo": pacote, "osgeo.gdal": gdal, "osgeo.ogr": ogr,
            "osgeo.osr": osr}


@pytest.fixture
def gerado(tmp_path, monkeypatch):
    registro = {"rasters": {}, "vetor": None}
    for nome, modulo in _montar_osgeo(registro).items():
        monkeypatch.setitem(sys.modules, nome, modulo)
    monkeypatch.delitem(sys.modules, "gerar_dados_teste", raising=False)
    sys.path.insert(0, os.path.join(RAIZ, "tools"))
    import gerar_dados_teste  # noqa: PLC0415

    saidas = gerar_dados_teste.gerar(str(tmp_path))
    return registro, saidas


# -------------------------------------------------------------------- rasters
def test_k_fica_entre_zero_e_um(gerado):
    registro, _ = gerado
    K = registro["rasters"]["teste_K.tif"].arr
    assert K.shape == (200, 200)
    assert K.min() >= 0.0
    assert K.max() <= 1.0
    assert K.max() > 0.5          # existe território favorável
    assert (K == 0).any()         # e território sem aptidão


def test_mascara_zera_a_borda_de_k(gerado):
    registro, _ = gerado
    K = registro["rasters"]["teste_K.tif"].arr
    mascara = registro["rasters"]["teste_mascara.tif"].arr
    assert (mascara[:, :10] == 0).all()
    assert (mascara[:10, :] == 0).all()
    assert (K[:, :10] == 0).all()


def test_mu_inicial_nunca_excede_k(gerado):
    registro, _ = gerado
    K = registro["rasters"]["teste_K.tif"].arr
    mu0 = registro["rasters"]["teste_mu0.tif"].arr
    assert (mu0 <= K + 1e-6).all()
    assert mu0.max() > 0.0        # há foco de onde o contágio possa partir


def test_georreferenciamento_dos_rasters(gerado):
    registro, _ = gerado
    ds = registro["rasters"]["teste_K.tif"]
    assert ds.gt == (600000.0, 100.0, 0.0, 9400000.0, 0.0, -100.0)
    assert ds.proj == "WKT_FALSO"
    assert ds.nodata == -9999.0


# -------------------------------------------------------------------- eventos
def test_alertas_colapsam_em_seis_frentes(gerado):
    """É a promessa do roteiro de teste: 150 fragmentos, 6 frentes."""
    registro, _ = gerado
    alertas = registro["vetor"].camadas["alertas"]
    assert len(alertas.pontos) == 150
    pontos = np.array(alertas.pontos)
    rotulos = agrupar_frentes(pontos[:, 0], pontos[:, 1], 100.0)
    assert len(set(rotulos.tolist())) == 6


def test_autos_tem_doze_pontos_datados(gerado):
    registro, _ = gerado
    autos = registro["vetor"].camadas["autos"]
    assert len(autos.pontos) == 12
    assert all(isinstance(d, _dt.date) for d in autos.datas)


def test_eventos_caem_dentro_da_grade(gerado):
    registro, _ = gerado
    for nome in ("alertas", "autos"):
        pontos = np.array(registro["vetor"].camadas[nome].pontos)
        assert (pontos[:, 0] > 600000.0).all()
        assert (pontos[:, 0] < 620000.0).all()
        assert (pontos[:, 1] < 9400000.0).all()
        assert (pontos[:, 1] > 9380000.0).all()


def test_datas_cobrem_o_periodo_sugerido(gerado):
    registro, _ = gerado
    datas = (registro["vetor"].camadas["alertas"].datas
             + registro["vetor"].camadas["autos"].datas)
    assert min(datas) >= _dt.date(2025, 1, 1)
    assert max(datas) <= _dt.date(2026, 12, 31)


def test_caminhos_devolvidos(gerado):
    _, saidas = gerado
    assert set(saidas) == {"K", "mu0", "mascara", "eventos"}
    assert saidas["eventos"].endswith("teste_eventos.gpkg")

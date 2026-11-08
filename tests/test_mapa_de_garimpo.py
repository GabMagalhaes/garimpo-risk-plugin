# -*- coding: utf-8 -*-
"""O aviso de mapa de garimpo suavizado (raster.aviso_de_mapa)."""

import numpy as np
import pytest

gdal = pytest.importorskip("osgeo.gdal")

from risco_garimpo.core import raster  # noqa: E402
from risco_garimpo.core.estatico import ocupacao_local  # noqa: E402


def _gravar(caminho, arr, nodata=None):
    ds = gdal.GetDriverByName("GTiff").Create(
        str(caminho), arr.shape[1], arr.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((0.0, 30.0, 0.0, 30.0 * arr.shape[0], 0.0, -30.0))
    banda = ds.GetRasterBand(1)
    if nodata is not None:
        banda.SetNoDataValue(nodata)
    banda.WriteArray(arr)
    ds = None
    return str(caminho)


def _mapa():
    rng = np.random.default_rng(3)
    mapa = np.zeros((300, 400), dtype=np.float32)
    for _ in range(25):
        y, x = rng.integers(10, 280), rng.integers(10, 380)
        mapa[y:y + 12, x:x + 15] = rng.random((12, 15)) < 0.7
    return mapa


def test_mapa_0_1_nao_avisa(tmp_path):
    caminho = _gravar(tmp_path / "binario.tif", _mapa())
    assert raster.fracao_intermediaria(caminho) == 0.0
    assert raster.aviso_de_mapa(caminho, "mapa") is None


def test_mapa_suavizado_avisa(tmp_path):
    suavizado = ocupacao_local(_mapa(), 30.0, 380.0)
    caminho = _gravar(tmp_path / "suavizado.tif", suavizado)
    assert raster.fracao_intermediaria(caminho) > 0.9
    aviso = raster.aviso_de_mapa(caminho, "mapa de garimpo do ano X")
    assert aviso and "mapa de garimpo do ano X" in aviso and "0/1" in aviso


def test_nodata_e_mapa_vazio(tmp_path):
    mapa = _mapa()
    mapa[:, :50] = -9999.0
    caminho = _gravar(tmp_path / "nodata.tif", mapa, nodata=-9999.0)
    assert raster.fracao_intermediaria(caminho) == 0.0
    vazio = _gravar(tmp_path / "vazio.tif", np.zeros((50, 50), np.float32))
    assert raster.fracao_intermediaria(vazio) is None
    assert raster.aviso_de_mapa(vazio, "mapa") is None


def test_arquivo_ilegivel_nao_derruba(tmp_path):
    assert raster.aviso_de_mapa(str(tmp_path / "nao_existe.tif"),
                                "mapa") is None

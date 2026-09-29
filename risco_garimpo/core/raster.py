# -*- coding: utf-8 -*-
"""Leitura, alinhamento e escrita de rasters (GDAL).

Todas as camadas são levadas a uma **grade de trabalho** única antes de
qualquer cálculo: mesma projeção, mesma origem, mesma resolução. É isso que
elimina de uma vez o problema recorrente de misturar SIRGAS 2000 / UTM 21S
com coordenadas geográficas EPSG:4674.

A grade de trabalho é, por padrão, a do raster de capacidade de suporte (K),
mas pode ser reprojetada e reamostrada para uma resolução mais grossa — o
que é frequentemente necessário para que a simulação caiba em memória.
"""

from dataclasses import dataclass

import numpy as np

try:  # pragma: no cover - ambiente QGIS
    from osgeo import gdal, osr

    gdal.UseExceptions()
    osr.UseExceptions()
except Exception:  # pragma: no cover
    gdal = None
    osr = None

REAMOSTRAGENS = {
    "vizinho": "near",
    "bilinear": "bilinear",
    "cubica": "cubic",
    "media": "average",
    "maximo": "max",
    "soma": "sum",
}


def _exigir_gdal():
    if gdal is None:  # pragma: no cover
        raise RuntimeError(
            "GDAL não disponível. Execute dentro do QGIS ou instale a GDAL "
            "para Python."
        )


@dataclass
class Grade:
    """Grade de trabalho: geotransform, projeção e tamanho."""

    gt: tuple
    wkt: str
    nx: int
    ny: int

    @property
    def xres(self):
        return abs(self.gt[1])

    @property
    def yres(self):
        return abs(self.gt[5])

    @property
    def xmin(self):
        return self.gt[0]

    @property
    def ymax(self):
        return self.gt[3]

    @property
    def xmax(self):
        return self.xmin + self.nx * self.xres

    @property
    def ymin(self):
        return self.ymax - self.ny * self.yres

    @property
    def bounds(self):
        return (self.xmin, self.ymin, self.xmax, self.ymax)

    @property
    def shape(self):
        return (int(self.ny), int(self.nx))

    @property
    def geografica(self):
        if osr is None or not self.wkt:  # pragma: no cover
            return False
        srs = osr.SpatialReference()
        srs.ImportFromWkt(self.wkt)
        return bool(srs.IsGeographic())

    @property
    def pixel_m(self):
        """Tamanho do pixel em metros.

        Em CRS projetado, é a própria resolução (convertida pela unidade
        linear). Em CRS geográfico, é uma aproximação na latitude central —
        aceitável para diagnóstico, **não** para rodar o modelo: a interface
        exige grade projetada.
        """
        if not self.wkt or osr is None:  # pragma: no cover
            return self.xres
        srs = osr.SpatialReference()
        srs.ImportFromWkt(self.wkt)
        if srs.IsGeographic():
            lat = np.deg2rad((self.ymin + self.ymax) / 2.0)
            graus_m = 111320.0
            dx = self.xres * graus_m * float(np.cos(lat))
            dy = self.yres * graus_m
            return float((dx + dy) / 2.0)
        unidade = srs.GetLinearUnits() or 1.0
        return float((self.xres + self.yres) / 2.0 * unidade)

    def descricao(self):
        return ("%d x %d px | pixel %.4g (%.1f m) | %s"
                % (self.nx, self.ny, self.xres, self.pixel_m,
                   _nome_crs(self.wkt)))


def _nome_crs(wkt):
    if not wkt or osr is None:  # pragma: no cover
        return "CRS indefinido"
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    nome = srs.GetName() or "CRS sem nome"
    codigo = srs.GetAuthorityCode(None)
    return "%s (EPSG:%s)" % (nome, codigo) if codigo else nome


# ----------------------------------------------------------------- leitura
def grade_de_raster(caminho):
    """Lê a grade de um raster no disco."""
    _exigir_gdal()
    ds = gdal.Open(caminho, gdal.GA_ReadOnly)
    if ds is None:
        raise IOError("não foi possível abrir o raster: %s" % caminho)
    grade = Grade(gt=tuple(ds.GetGeoTransform()), wkt=ds.GetProjection(),
                  nx=ds.RasterXSize, ny=ds.RasterYSize)
    ds = None
    return grade


def grade_reamostrada(grade, resolucao_m=None, wkt_destino=None):
    """Deriva uma grade de trabalho com outra resolução e/ou projeção."""
    if resolucao_m is None and not wkt_destino:
        return grade

    alvo_wkt = wkt_destino or grade.wkt
    if wkt_destino and wkt_destino != grade.wkt:
        _exigir_gdal()
        xmin, ymin, xmax, ymax = _reprojetar_bbox(grade.bounds, grade.wkt,
                                                  wkt_destino)
    else:
        xmin, ymin, xmax, ymax = grade.bounds

    if resolucao_m is None:
        res = grade.xres
    else:
        res = float(resolucao_m)

    nx = max(1, int(np.ceil((xmax - xmin) / res)))
    ny = max(1, int(np.ceil((ymax - ymin) / res)))
    gt = (xmin, res, 0.0, ymin + ny * res, 0.0, -res)
    return Grade(gt=gt, wkt=alvo_wkt, nx=nx, ny=ny)


def _reprojetar_bbox(bounds, wkt_origem, wkt_destino):
    origem = osr.SpatialReference()
    origem.ImportFromWkt(wkt_origem)
    destino = osr.SpatialReference()
    destino.ImportFromWkt(wkt_destino)
    try:
        origem.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        destino.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:  # pragma: no cover - GDAL 2
        pass
    tr = osr.CoordinateTransformation(origem, destino)
    xmin, ymin, xmax, ymax = bounds
    cantos = [(xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)]
    pontos = [tr.TransformPoint(float(cx), float(cy))[:2] for cx, cy in cantos]
    xs = [p[0] for p in pontos]
    ys = [p[1] for p in pontos]
    return (min(xs), min(ys), max(xs), max(ys))


def ler_alinhado(caminho, grade, reamostragem="bilinear", banda=1,
                 nodata_para=0.0):
    """Lê um raster já reprojetado/reamostrado para a grade de trabalho."""
    _exigir_gdal()
    alg = REAMOSTRAGENS.get(reamostragem, reamostragem)
    opcoes = gdal.WarpOptions(
        format="MEM",
        outputBounds=grade.bounds,
        width=grade.nx,
        height=grade.ny,
        dstSRS=grade.wkt,
        resampleAlg=alg,
        outputType=gdal.GDT_Float32,
        multithread=True,
    )
    ds = gdal.Warp("", caminho, options=opcoes)
    if ds is None:
        raise IOError("falha ao alinhar o raster: %s" % caminho)
    banda_ds = ds.GetRasterBand(int(banda))
    arr = banda_ds.ReadAsArray().astype(np.float32)
    nodata = banda_ds.GetNoDataValue()
    ds = None
    if nodata is not None:
        arr = np.where(np.isclose(arr, nodata), np.float32(nodata_para), arr)
    return np.nan_to_num(arr, nan=nodata_para, posinf=nodata_para,
                         neginf=nodata_para).astype(np.float32)


# ------------------------------------------------------------------ escrita
def escrever_raster(caminho, arr, grade, nodata=-9999.0, mascara=None,
                    comprimir=True):
    """Grava um GeoTIFF float32 na grade de trabalho."""
    _exigir_gdal()
    arr = np.asarray(arr, dtype=np.float32)
    if arr.shape != grade.shape:
        raise ValueError("array %s incompatível com a grade %s"
                         % (arr.shape, grade.shape))
    saida = arr.copy()
    if mascara is not None:
        saida = np.where(np.asarray(mascara, dtype=bool), saida,
                         np.float32(nodata))

    opcoes = ["TILED=YES", "BIGTIFF=IF_SAFER"]
    if comprimir:
        opcoes += ["COMPRESS=DEFLATE", "PREDICTOR=2", "ZLEVEL=6"]

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(caminho, grade.nx, grade.ny, 1, gdal.GDT_Float32,
                       options=opcoes)
    if ds is None:
        raise IOError("não foi possível criar o raster: %s" % caminho)
    ds.SetGeoTransform(grade.gt)
    if grade.wkt:
        ds.SetProjection(grade.wkt)
    banda = ds.GetRasterBand(1)
    banda.SetNoDataValue(float(nodata))
    banda.WriteArray(saida)
    banda.FlushCache()
    ds = None
    return caminho


def rasterizar_pontos(x, y, grade):
    """Converte coordenadas da grade de trabalho em índices (linha, coluna)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    colunas = np.floor((x - grade.xmin) / grade.xres).astype(np.int64)
    linhas = np.floor((grade.ymax - y) / grade.yres).astype(np.int64)
    dentro = ((colunas >= 0) & (colunas < grade.nx)
              & (linhas >= 0) & (linhas < grade.ny))
    return linhas, colunas, dentro

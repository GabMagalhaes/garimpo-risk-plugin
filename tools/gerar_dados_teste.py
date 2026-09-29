# -*- coding: utf-8 -*-
"""Gera um conjunto sintético de insumos para testar o plugin.

Serve para verificar que o plugin roda ponta a ponta **antes** de ligar os
dados reais: se falhar aqui, o problema é o plugin; se funcionar aqui e
falhar com os seus dados, o problema é projeção, campo de data ou extensão.

Como usar, dentro do QGIS (Complementos → Console Python), duas linhas:

    exec(open(r"C:\\Plugin\\tools\\gerar_dados_teste.py", encoding="utf-8").read())
    gerar_e_carregar(r"C:\\Plugin\\teste")

A primeira linha define as funções; a segunda gera os arquivos e já carrega
as camadas no projeto aberto.

Cria, na pasta indicada:

    teste_K.tif        favorabilidade sintética (0–1), com áreas de K = 0
    teste_mu0.tif      três focos de garimpo já instalados
    teste_mascara.tif  área de estudo
    teste_eventos.gpkg camadas 'alertas' (6 frentes) e 'autos' (12 pontos)

Área de 20 × 20 km em SIRGAS 2000 / UTM 21S (EPSG:31981), pixel de 100 m —
pequena de propósito: roda em segundos.
"""

import datetime as _dt
import os

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()

EPSG = 31981
XMIN = 600000.0
YMAX = 9400000.0
RES = 100.0
NX = 200
NY = 200
SEMENTE = 20250921


def _referencia():
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    try:
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:
        pass
    return srs


def _gravar(caminho, arr, nodata=-9999.0):
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(caminho, NX, NY, 1, gdal.GDT_Float32,
                       options=["COMPRESS=DEFLATE", "TILED=YES"])
    ds.SetGeoTransform((XMIN, RES, 0.0, YMAX, 0.0, -RES))
    ds.SetProjection(_referencia().ExportToWkt())
    banda = ds.GetRasterBand(1)
    banda.SetNoDataValue(float(nodata))
    banda.WriteArray(np.asarray(arr, dtype=np.float32))
    banda.FlushCache()
    ds = None
    return caminho


def _suavizar(arr, passos=6, raio=3):
    """Média móvel repetida — aproxima um borrão gaussiano sem SciPy."""
    saida = np.asarray(arr, dtype=np.float64)
    for _ in range(passos):
        acumulado = np.zeros_like(saida)
        contagem = 0
        for dy in range(-raio, raio + 1):
            for dx in range(-raio, raio + 1):
                acumulado += np.roll(np.roll(saida, dy, axis=0), dx, axis=1)
                contagem += 1
        saida = acumulado / contagem
    return saida


def _normalizar(arr):
    minimo, maximo = float(arr.min()), float(arr.max())
    if maximo <= minimo:
        return np.zeros_like(arr)
    return (arr - minimo) / (maximo - minimo)


def _pixel_para_coord(linha, coluna):
    return (XMIN + (coluna + 0.5) * RES, YMAX - (linha + 0.5) * RES)


def _camada_pontos(fonte, nome, pontos, datas):
    camada = fonte.CreateLayer(nome, _referencia(), ogr.wkbPoint)
    camada.CreateField(ogr.FieldDefn("data", ogr.OFTDate))
    camada.CreateField(ogr.FieldDefn("obs", ogr.OFTString))
    definicao = camada.GetLayerDefn()
    for (x, y), data in zip(pontos, datas):
        feicao = ogr.Feature(definicao)
        geometria = ogr.Geometry(ogr.wkbPoint)
        geometria.AddPoint(float(x), float(y))
        feicao.SetGeometry(geometria)
        feicao.SetField("data", data.year, data.month, data.day, 0, 0, 0, 0)
        feicao.SetField("obs", "%s sintético" % nome)
        camada.CreateFeature(feicao)
        feicao = None
    return camada


def gerar(pasta=None, prefixo="teste"):
    pasta = pasta or os.getcwd()
    os.makedirs(pasta, exist_ok=True)
    rng = np.random.default_rng(SEMENTE)

    # ------------------------------------------------------ K e área válida
    K = _normalizar(_suavizar(rng.random((NY, NX)), passos=8, raio=4))
    K = np.clip((K - 0.25) / 0.75, 0.0, 1.0)      # parte do território sem aptidão

    mascara = np.ones((NY, NX), dtype=np.float32)
    mascara[:, :10] = 0.0                          # borda fora da área de estudo
    mascara[:10, :] = 0.0
    K = K * mascara

    # ------------------------------------------------------------ focos de μ
    mu0 = np.zeros((NY, NX), dtype=np.float32)
    focos = [(60, 70), (130, 150), (95, 45)]
    for linha, coluna in focos:
        mu0[linha - 1:linha + 2, coluna - 1:coluna + 2] = 0.9
    mu0 = np.minimum(mu0, K)                       # μ nunca excede K

    caminho_k = _gravar(os.path.join(pasta, "%s_K.tif" % prefixo), K)
    caminho_mu = _gravar(os.path.join(pasta, "%s_mu0.tif" % prefixo), mu0)
    caminho_mascara = _gravar(os.path.join(pasta, "%s_mascara.tif" % prefixo),
                              mascara)

    # ---------------------------------------------------------------- eventos
    caminho_gpkg = os.path.join(pasta, "%s_eventos.gpkg" % prefixo)
    if os.path.exists(caminho_gpkg):
        os.remove(caminho_gpkg)
    fonte = ogr.GetDriverByName("GPKG").CreateDataSource(caminho_gpkg)

    # seis frentes de alerta, cada uma um aglomerado de fragmentos a < 100 m
    pontos_alerta, datas_alerta = [], []
    centros = [(55, 75), (65, 62), (128, 158), (140, 145), (100, 40), (88, 52)]
    base = _dt.date(2025, 1, 15)
    for i, (linha, coluna) in enumerate(centros):
        cx, cy = _pixel_para_coord(linha, coluna)
        chegada = base + _dt.timedelta(days=45 * i)
        for _ in range(25):                        # fragmentos da mesma detecção
            pontos_alerta.append((cx + rng.normal(0, 30.0),
                                  cy + rng.normal(0, 30.0)))
            datas_alerta.append(chegada
                                + _dt.timedelta(days=int(rng.integers(0, 20))))
    _camada_pontos(fonte, "alertas", pontos_alerta, datas_alerta)

    # autos de infração: alguns sobre os focos, outros dispersos
    pontos_auto, datas_auto = [], []
    for i, (linha, coluna) in enumerate(focos + centros[:3]):
        cx, cy = _pixel_para_coord(linha, coluna)
        for j in range(2):
            pontos_auto.append((cx + rng.normal(0, 200.0),
                                cy + rng.normal(0, 200.0)))
            datas_auto.append(_dt.date(2025, 3, 1)
                              + _dt.timedelta(days=60 * i + 30 * j))
    _camada_pontos(fonte, "autos", pontos_auto, datas_auto)
    fonte = None

    print("Insumos sintéticos gravados em:", pasta)
    print("  K            ", caminho_k)
    print("  μ₀           ", caminho_mu)
    print("  máscara      ", caminho_mascara)
    print("  eventos      ", caminho_gpkg, "(camadas 'alertas' e 'autos')")
    print("")
    print("Alertas: %d fragmentos em 6 frentes — o plugin deve reportar "
          "6 frentes ao agrupar." % len(pontos_alerta))
    print("Autos:   %d pontos." % len(pontos_auto))
    print("")
    print("Período sugerido na aba Parâmetros: 01/01/2025 a 31/12/2026.")
    return {"K": caminho_k, "mu0": caminho_mu, "mascara": caminho_mascara,
            "eventos": caminho_gpkg}


def carregar_no_qgis(saidas):
    """Carrega os arquivos gerados no projeto aberto (só dentro do QGIS)."""
    from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

    projeto = QgsProject.instance()
    for rotulo, chave in (("teste K", "K"), ("teste μ₀", "mu0"),
                          ("teste máscara", "mascara")):
        camada = QgsRasterLayer(saidas[chave], rotulo)
        if camada.isValid():
            projeto.addMapLayer(camada)
    for nome in ("alertas", "autos"):
        camada = QgsVectorLayer("%s|layername=%s" % (saidas["eventos"], nome),
                                "teste %s" % nome, "ogr")
        if camada.isValid():
            projeto.addMapLayer(camada)
    print("Camadas carregadas no projeto.")


def gerar_e_carregar(pasta=None, prefixo="teste"):
    """Atalho para o console do QGIS: gera os arquivos e já os carrega."""
    saidas = gerar(pasta, prefixo)
    carregar_no_qgis(saidas)
    return saidas


if __name__ == "__main__":
    import sys

    gerar(sys.argv[1] if len(sys.argv) > 1 else None)
else:
    # Mesmo motivo do gerar_K: o editor do QGIS executa o arquivo com
    # __name__ != "__main__", e sem este aviso o script devolve o prompt em
    # silêncio, parecendo que falhou.
    print("gerar_dados_teste carregado — nada foi gerado ainda.")
    print("  Rode:  gerar_e_carregar(r'C:\\Users\\<voce>\\Output\\teste')")

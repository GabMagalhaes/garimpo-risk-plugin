# -*- coding: utf-8 -*-
"""Agregação por sub-bacia — unidade de **saída**, nunca de propagação.

A dinâmica é resolvida no pixel. Só depois de propagar é que os resultados
são somados por sub-bacia HydroBASINS nível 12, para leitura operacional e
priorização. A ordem importa: agregar antes de propagar dilui o efeito
repressivo (raio de 0,5–1 km sobre sub-bacias de mediana > 100 km²) abaixo do
ruído e destrói as escalas de 380 m e 3.500 m.
"""

import os
import tempfile

import numpy as np

from .raster import _exigir_gdal, gdal

CAMPO_INDICE = "rg_idx"


def rasterizar_zonas(camada, grade, progresso=None):
    """Rasteriza polígonos na grade de trabalho, devolvendo índices de feição.

    Returns
    -------
    (ndarray[int32], list)
        Raster de índices (``-1`` fora de qualquer polígono) e a lista de
        ``QgsFeature`` na mesma ordem dos índices.
    """
    _exigir_gdal()
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                           QgsFeature, QgsField, QgsProject, QgsVectorLayer,
                           QgsVectorFileWriter, QgsWkbTypes)
    from qgis.PyQt.QtCore import QVariant

    destino = QgsCoordinateReferenceSystem()
    destino.createFromWkt(grade.wkt)
    transformacao = QgsCoordinateTransform(camada.crs(), destino,
                                           QgsProject.instance())

    tipo = QgsWkbTypes.displayString(camada.wkbType()) or "Polygon"
    temporaria = QgsVectorLayer("%s?crs=%s" % (tipo, destino.authid() or destino.toWkt()),
                                "zonas_tmp", "memory")
    provedor = temporaria.dataProvider()
    provedor.addAttributes([QgsField(CAMPO_INDICE, QVariant.Int)])
    temporaria.updateFields()

    originais = []
    novas = []
    for i, feicao in enumerate(camada.getFeatures()):
        geom = feicao.geometry()
        if geom is None or geom.isEmpty():
            continue
        geom = type(geom)(geom)
        if geom.transform(transformacao) != 0:
            continue
        nova = QgsFeature(temporaria.fields())
        nova.setGeometry(geom)
        nova[CAMPO_INDICE] = len(originais)
        novas.append(nova)
        originais.append(feicao)
        if progresso is not None and i % 500 == 0:
            progresso(0.0, "Preparando zonas (%d feições)" % i)
    provedor.addFeatures(novas)
    temporaria.updateExtents()

    caminho = os.path.join(tempfile.mkdtemp(prefix="risco_garimpo_"),
                           "zonas.gpkg")
    opcoes = QgsVectorFileWriter.SaveVectorOptions()
    opcoes.driverName = "GPKG"
    erro = QgsVectorFileWriter.writeAsVectorFormatV3(
        temporaria, caminho, QgsProject.instance().transformContext(), opcoes)
    if isinstance(erro, (list, tuple)) and erro[0] != QgsVectorFileWriter.NoError:
        raise IOError("falha ao preparar as zonas: %s" % (erro,))

    alvo = gdal.GetDriverByName("MEM").Create("", grade.nx, grade.ny, 1,
                                              gdal.GDT_Int32)
    alvo.SetGeoTransform(grade.gt)
    alvo.SetProjection(grade.wkt)
    banda = alvo.GetRasterBand(1)
    banda.SetNoDataValue(-1)
    banda.Fill(-1)
    gdal.Rasterize(alvo, caminho, options=gdal.RasterizeOptions(
        attribute=CAMPO_INDICE, allTouched=False))
    zonas = banda.ReadAsArray().astype(np.int32)
    alvo = None
    return zonas, originais


def somar_por_zona(zonas, valores, n_zonas):
    """Soma de ``valores`` por índice de zona (``-1`` é descartado)."""
    zonas = np.asarray(zonas, dtype=np.int64)
    valores = np.asarray(valores, dtype=np.float64)
    validos = zonas >= 0
    if not validos.any():
        return np.zeros(n_zonas, dtype=np.float64), np.zeros(n_zonas, dtype=np.int64)
    soma = np.bincount(zonas[validos], weights=valores[validos],
                       minlength=n_zonas)[:n_zonas]
    contagem = np.bincount(zonas[validos], minlength=n_zonas)[:n_zonas]
    return soma, contagem


def agregar_resultado(resultado, grade, camada_zonas, campo_id=None,
                      caminho_saida=None, progresso=None):
    """Tabela por sub-bacia com expansão prevista, estoque e ranking.

    Colunas geradas:

    ``rg_exp_ha``
        expansão prevista no horizonte, em hectares equivalentes
        (Σ Δμ × área do pixel).
    ``rg_mu0_ha`` / ``rg_muf_ha``
        estoque inicial e final, em hectares equivalentes.
    ``rg_exp_rel``
        expansão relativa ao estoque inicial (adimensional; ``-1`` quando não
        havia estoque, isto é, chegada em área virgem).
    ``rg_k_ha``
        capacidade de suporte total da sub-bacia.
    ``rg_ocup``
        fração da capacidade já ocupada ao fim do horizonte.
    ``rg_rank``
        posição no ranking por expansão prevista (1 = maior).
    """
    zonas, feicoes = rasterizar_zonas(camada_zonas, grade, progresso)
    n = len(feicoes)
    if n == 0:
        raise ValueError("a camada de zonas não tem feições válidas")

    area_ha = (grade.pixel_m ** 2) / 10000.0
    exp, _ = somar_por_zona(zonas, resultado.delta_mu, n)
    mu0, _ = somar_por_zona(zonas, resultado.mu_inicial, n)
    muf, contagem = somar_por_zona(zonas, resultado.mu_final, n)

    exp_ha = exp * area_ha
    mu0_ha = mu0 * area_ha
    muf_ha = muf * area_ha

    with np.errstate(divide="ignore", invalid="ignore"):
        relativa = np.where(mu0_ha > 0, exp_ha / mu0_ha, -1.0)

    ordem = np.argsort(-exp_ha, kind="stable")
    rank = np.empty(n, dtype=np.int64)
    rank[ordem] = np.arange(1, n + 1)

    linhas = []
    for i, feicao in enumerate(feicoes):
        identificador = (feicao[campo_id] if campo_id
                         and campo_id in [f.name() for f in feicao.fields()]
                         else feicao.id())
        linhas.append({
            "id": identificador,
            "rg_exp_ha": float(exp_ha[i]),
            "rg_mu0_ha": float(mu0_ha[i]),
            "rg_muf_ha": float(muf_ha[i]),
            "rg_exp_rel": float(relativa[i]),
            "rg_px": int(contagem[i]),
            "rg_rank": int(rank[i]),
        })

    if caminho_saida:
        _escrever_gpkg(caminho_saida, camada_zonas, feicoes, linhas)
    return linhas


def _escrever_gpkg(caminho, camada_original, feicoes, linhas):
    from qgis.core import (QgsFeature, QgsField, QgsProject, QgsVectorLayer,
                           QgsVectorFileWriter, QgsWkbTypes)
    from qgis.PyQt.QtCore import QVariant

    tipo = QgsWkbTypes.displayString(camada_original.wkbType()) or "Polygon"
    saida = QgsVectorLayer("%s?crs=%s" % (tipo, camada_original.crs().authid()),
                           "risco_subbacias", "memory")
    provedor = saida.dataProvider()
    campos = list(camada_original.fields())
    novos = [QgsField("rg_exp_ha", QVariant.Double),
             QgsField("rg_mu0_ha", QVariant.Double),
             QgsField("rg_muf_ha", QVariant.Double),
             QgsField("rg_exp_rel", QVariant.Double),
             QgsField("rg_px", QVariant.Int),
             QgsField("rg_rank", QVariant.Int)]
    provedor.addAttributes(campos + novos)
    saida.updateFields()

    lote = []
    for feicao, linha in zip(feicoes, linhas):
        nova = QgsFeature(saida.fields())
        nova.setGeometry(feicao.geometry())
        for campo in campos:
            nova[campo.name()] = feicao[campo.name()]
        for chave in ("rg_exp_ha", "rg_mu0_ha", "rg_muf_ha", "rg_exp_rel",
                      "rg_px", "rg_rank"):
            nova[chave] = linha[chave]
        lote.append(nova)
    provedor.addFeatures(lote)
    saida.updateExtents()

    opcoes = QgsVectorFileWriter.SaveVectorOptions()
    opcoes.driverName = "GPKG"
    opcoes.layerName = "risco_subbacias"
    erro = QgsVectorFileWriter.writeAsVectorFormatV3(
        saida, caminho, QgsProject.instance().transformContext(), opcoes)
    if isinstance(erro, (list, tuple)) and erro[0] != QgsVectorFileWriter.NoError:
        raise IOError("falha ao gravar %s: %s" % (caminho, erro))
    return caminho

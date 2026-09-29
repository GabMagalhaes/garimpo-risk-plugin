# -*- coding: utf-8 -*-
"""Extração de eventos datados a partir de camadas vetoriais do QGIS.

Alertas e autos de infração chegam como camadas de pontos ou polígonos com
um campo de data. Aqui eles são reprojetados para a grade de trabalho,
reduzidos a coordenadas e convertidos em índices de pixel.
"""

import datetime as _dt

import numpy as np


def _importar_qgis():
    from qgis.core import (  # noqa: F401
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsProject,
        QgsWkbTypes,
    )

    return (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject,
            QgsWkbTypes)


def extrair_eventos(camada, campo_data, grade, filtro_data=None,
                    apenas_selecionados=False):
    """Lê uma camada vetorial e devolve ``(x, y, datas, ignorados)``.

    ``x`` e ``y`` estão no CRS da grade de trabalho. Geometrias não pontuais
    são reduzidas ao centroide (para polígonos de alerta, ``pointOnSurface``,
    que garante ponto interno). Feições sem data válida são contadas em
    ``ignorados`` e descartadas — silenciar isso seria esconder perda de dado.
    """
    (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject,
     QgsWkbTypes) = _importar_qgis()

    destino = QgsCoordinateReferenceSystem()
    destino.createFromWkt(grade.wkt)
    if not destino.isValid():
        raise ValueError("a grade de trabalho não tem CRS válido")

    transformacao = QgsCoordinateTransform(camada.crs(), destino,
                                           QgsProject.instance())

    idx = camada.fields().indexFromName(campo_data)
    if idx < 0:
        raise ValueError("campo de data inexistente na camada: %r"
                         % (campo_data,))

    feicoes = (camada.getSelectedFeatures() if apenas_selecionados
               else camada.getFeatures())

    xs, ys, datas = [], [], []
    ignorados = 0
    for feicao in feicoes:
        data = converter_data(feicao.attributes()[idx])
        if data is None:
            ignorados += 1
            continue
        if filtro_data is not None and not filtro_data(data):
            continue
        geom = feicao.geometry()
        if geom is None or geom.isEmpty():
            ignorados += 1
            continue
        if QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.PointGeometry:
            ponto = geom.asPoint() if not geom.isMultipart() else geom.asMultiPoint()[0]
        else:
            interno = geom.pointOnSurface()
            if interno is None or interno.isEmpty():
                ignorados += 1
                continue
            ponto = interno.asPoint()
        try:
            ponto = transformacao.transform(ponto)
        except Exception:
            ignorados += 1
            continue
        xs.append(ponto.x())
        ys.append(ponto.y())
        datas.append(data)

    return (np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64),
            datas, ignorados)


def converter_data(valor):
    """Converte QDate/QDateTime/str/date em ``datetime.date`` ou ``None``."""
    if valor is None:
        return None
    if isinstance(valor, _dt.datetime):
        return valor.date()
    if isinstance(valor, _dt.date):
        return valor

    # Tipos do Qt, sem importar PyQt aqui
    for metodo in ("toPyDate", "toPyDateTime"):
        if hasattr(valor, metodo):
            try:
                convertido = getattr(valor, metodo)()
                if isinstance(convertido, _dt.datetime):
                    return convertido.date()
                if isinstance(convertido, _dt.date):
                    return convertido
            except Exception:
                pass
    if hasattr(valor, "date") and not isinstance(valor, str):
        try:
            return converter_data(valor.date())
        except Exception:
            pass

    texto = str(valor).strip()
    if not texto or texto.upper() in ("NULL", "NONE", "NAN"):
        return None
    formatos = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y %H:%M:%S", "%Y%m%d")
    for fmt in formatos:
        try:
            return _dt.datetime.strptime(texto[:len(fmt) + 8], fmt).date()
        except ValueError:
            continue
    for fmt in formatos:
        try:
            return _dt.datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None


def intervalo_datas(datas):
    """(mínima, máxima) de uma lista de datas, ou ``(None, None)``."""
    if not datas:
        return (None, None)
    return (min(datas), max(datas))

# -*- coding: utf-8 -*-
"""Núcleo numérico do modelo dinâmico.

Este subpacote **não importa QGIS nem GDAL** (salvo em ``raster.py`` e
``vetor.py``, onde a importação é tardia), de modo que o modelo possa ser
testado e executado fora do QGIS, como script.
"""

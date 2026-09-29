# -*- coding: utf-8 -*-
"""Risco Garimpo — Modelo Dinâmico.

Ponto de entrada do plugin QGIS.
"""


def classFactory(iface):  # noqa: N802 (nome exigido pelo QGIS)
    from .plugin import RiscoGarimpoPlugin

    return RiscoGarimpoPlugin(iface)

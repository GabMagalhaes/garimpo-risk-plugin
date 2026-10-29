# -*- coding: utf-8 -*-
"""Compatibilidade entre QGIS 3 (Qt5) e QGIS 4 (Qt6).

O plugin roda nos dois. As diferenças que o tocam são três:

* o PyQt6 só aceita membros de enum com o escopo
  (``QMessageBox.StandardButton.Yes``); o PyQt5 aceita os dois nomes;
* o QGIS 4 removeu os enums antigos que o 3.30 já tinha substituído por
  ``Qgis.*`` (``QgsWkbTypes.PolygonGeometry`` → ``Qgis.GeometryType.Polygon``)
  e o ``QVariant`` como tipo de campo (``QMetaType.Type``, desde o 3.38);
* o ``QAction`` passou de ``QtWidgets`` para ``QtGui``.

Nada aqui importa o QGIS no topo: os módulos do núcleo continuam
importáveis sem ele.
"""

_AUSENTE = object()


def membro(classe, escopo, nome):
    """``classe.escopo.nome`` quando existe; senão ``classe.nome``.

    Ex.: ``membro(QMessageBox, "StandardButton", "Yes")``.
    """
    tipo = getattr(classe, escopo, None)
    if tipo is not None:
        valor = getattr(tipo, nome, _AUSENTE)
        if valor is not _AUSENTE:
            return valor
    return getattr(classe, nome)


def tipo_geometria(nome):
    """Tipo de geometria (``"Point"``, ``"Line"``, ``"Polygon"``).

    ``Qgis.GeometryType`` desde o QGIS 3.30; antes, ``QgsWkbTypes.*Geometry``.
    """
    from qgis.core import Qgis, QgsWkbTypes
    tipo = getattr(Qgis, "GeometryType", None)
    if tipo is not None and hasattr(tipo, nome):
        return getattr(tipo, nome)
    return getattr(QgsWkbTypes, nome + "Geometry")


def tipo_de_campo(inteiro):
    """Tipo de um ``QgsField`` numérico: inteiro ou real.

    ``QMetaType.Type`` desde o QGIS 3.38 (o único aceito no QGIS 4);
    antes, ``QVariant``.
    """
    from qgis.core import Qgis
    if Qgis.QGIS_VERSION_INT >= 33800:
        from qgis.PyQt.QtCore import QMetaType
        return membro(QMetaType, "Type", "Int" if inteiro else "Double")
    from qgis.PyQt.QtCore import QVariant
    return QVariant.Int if inteiro else QVariant.Double


def interpolacao_linear():
    """Rampa de cores interpolada: ``Qgis.ShaderInterpolationMethod.Linear``
    desde o QGIS 3.30; antes, ``QgsColorRampShader.Interpolated``."""
    from qgis.core import Qgis, QgsColorRampShader
    tipo = getattr(Qgis, "ShaderInterpolationMethod", None)
    if tipo is not None and hasattr(tipo, "Linear"):
        return tipo.Linear
    return QgsColorRampShader.Interpolated


def nivel_sucesso():
    """Nível de mensagem de sucesso da barra do QGIS."""
    from qgis.core import Qgis
    return membro(Qgis, "MessageLevel", "Success")


def tarefa_cancelavel():
    """Opção ``CanCancel`` de um ``QgsTask``."""
    from qgis.core import QgsTask
    return membro(QgsTask, "Flag", "CanCancel")

# -*- coding: utf-8 -*-
"""Registro de execução: o que foi rodado, com quê e o que saiu.

Cada execução grava, ao lado das saídas, um JSON com a configuração, os
parâmetros, a grade de trabalho, a identificação dos insumos (caminho,
tamanho, data de modificação e SHA-256) e os arquivos gravados. É o que
permite dizer, meses depois, de que versão das bases saiu um mapa — e
repetir a execução.

O registro é sobrescrito a cada execução, como as próprias saídas: o JSON
de uma pasta descreve sempre os arquivos que estão nela.

Este módulo não importa QGIS nem GDAL. Camadas vetoriais são descritas por
``descrever_camada``, que só chama métodos da própria camada e por isso
deve rodar na thread principal; o SHA-256, que lê os arquivos inteiros, é
calculado depois, em segundo plano, por ``identificar``.
"""

import dataclasses
import datetime as _dt
import hashlib
import json
import math
import os
import platform

import numpy as np

VERSAO_REGISTRO = 1
_BLOCO = 1 << 20

# arquivos que acompanham um shapefile e fazem parte do dado (a data dos
# eventos, por exemplo, está no .dbf)
_COMPLEMENTOS_SHP = (".dbf", ".shx", ".prj", ".cpg")


def agora():
    """Instante atual, com fuso, em ISO 8601."""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(caminho):
    """SHA-256 do arquivo, lido em blocos (rasters podem ter gigabytes)."""
    resumo = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(_BLOCO), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def _identidade(caminho):
    if not caminho or not os.path.isfile(caminho):
        return {"existe": False}
    estado = os.stat(caminho)
    modificado = _dt.datetime.fromtimestamp(estado.st_mtime).astimezone()
    return {"existe": True,
            "tamanho_bytes": int(estado.st_size),
            "modificado_em": modificado.isoformat(timespec="seconds"),
            "sha256": sha256(caminho)}


def identificar(insumo):
    """Completa um insumo com a identidade do arquivo em ``insumo["caminho"]``.

    Não falha quando o caminho não é um arquivo local (camada em memória,
    fonte remota): marca ``existe = False`` e segue.
    """
    insumo = dict(insumo)
    caminho = insumo.get("caminho")
    insumo.update(_identidade(caminho))
    if caminho and os.path.splitext(caminho)[1].lower() == ".shp":
        base = os.path.splitext(caminho)[0]
        complementos = []
        for extensao in _COMPLEMENTOS_SHP:
            for candidato in (base + extensao, base + extensao.upper()):
                if os.path.isfile(candidato):
                    complementos.append(dict(caminho=candidato,
                                             **_identidade(candidato)))
                    break
        insumo["complementos"] = complementos
    return insumo


def descrever_arquivo(caminho, papel):
    """Insumo raster (ou qualquer arquivo) identificado pelo conteúdo."""
    return identificar({"papel": papel, "caminho": caminho})


def descrever_camada(camada, papel, **extra):
    """Descrição de uma camada vetorial do projeto. Thread principal.

    ``caminho`` é o arquivo por trás da camada, quando há um; a identidade
    (tamanho, SHA-256) fica para ``identificar``, fora da thread principal.
    """
    fonte = str(camada.source())
    arquivo = fonte.split("|")[0]
    descricao = {"papel": papel,
                 "nome": str(camada.name()),
                 "fonte": fonte,
                 "caminho": arquivo if os.path.isfile(arquivo) else None,
                 "crs": str(camada.crs().authid()),
                 "feicoes": int(camada.featureCount())}
    descricao.update(extra)
    return descricao


def configuracao_simples(objeto, excluir=()):
    """Atributos de uma configuração que cabem em JSON.

    Objetos do QGIS (camadas) e outros valores compostos são omitidos: as
    camadas entram no registro pela lista de insumos.
    """
    simples = {}
    for chave, valor in sorted(vars(objeto).items()):
        if chave.startswith("_") or chave in excluir:
            continue
        if valor is None or isinstance(valor, (bool, int, float, str,
                                               _dt.date, np.generic)):
            simples[chave] = valor
    return simples


def descrever_grade(grade):
    """Geometria da grade de trabalho."""
    gt = getattr(grade, "gt", None)
    return {"nx": int(grade.nx), "ny": int(grade.ny),
            "pixel_m": float(grade.pixel_m),
            "geotransform": list(gt) if gt is not None else None,
            "crs_wkt": getattr(grade, "wkt", None)}


def versao_plugin():
    """Versão declarada em ``metadata.txt``."""
    caminho = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "metadata.txt")
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            for linha in arquivo:
                if linha.startswith("version="):
                    return linha.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def ambiente():
    """Versões do ambiente que podem mudar o resultado."""
    info = {"python": platform.python_version(),
            "sistema": platform.platform(),
            "numpy": np.__version__}
    try:
        import scipy
        info["scipy"] = scipy.__version__
    except Exception:  # noqa: BLE001 - SciPy é opcional
        info["scipy"] = None
    try:
        from osgeo import gdal
        info["gdal"] = getattr(gdal, "__version__", None)
    except Exception:  # noqa: BLE001 - fora do QGIS
        info["gdal"] = None
    try:
        from qgis.core import Qgis
        info["qgis"] = Qgis.QGIS_VERSION
    except Exception:  # noqa: BLE001 - fora do QGIS
        info["qgis"] = None
    return info


def montar(ferramenta, configuracao, grade, insumos, saidas, parametros=None,
           eventos=None, resultados=None, inicio=None):
    """Monta o registro de uma execução."""
    registro = {
        "registro_versao": VERSAO_REGISTRO,
        "ferramenta": ferramenta,
        "versao_plugin": versao_plugin(),
        "inicio": inicio,
        "gravado_em": agora(),
        "ambiente": ambiente(),
        "configuracao": configuracao,
        "parametros": (dataclasses.asdict(parametros)
                       if dataclasses.is_dataclass(parametros)
                       else parametros),
        "grade": descrever_grade(grade),
        "insumos": list(insumos),
        "eventos": eventos or {},
        "saidas": dict(saidas),
        "resultados": resultados or {},
    }
    return _limpar(registro)


def gravar(caminho, registro):
    """Grava o registro em JSON (UTF-8). A troca do arquivo é atômica."""
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump(_limpar(registro), arquivo, ensure_ascii=False, indent=2,
                  allow_nan=False)
        arquivo.write("\n")
    os.replace(temporario, caminho)
    return caminho


def ler(caminho):
    with open(caminho, encoding="utf-8") as arquivo:
        return json.load(arquivo)


def acrescentar(caminho, saidas=None, resultados=None):
    """Acrescenta saídas e resultados a um registro já gravado.

    Serve à agregação por sub-bacia do modelo dinâmico, que roda depois da
    simulação, na thread principal.
    """
    registro = ler(caminho)
    registro.setdefault("saidas", {}).update(saidas or {})
    registro.setdefault("resultados", {}).update(resultados or {})
    registro["gravado_em"] = agora()
    return gravar(caminho, registro)


def _limpar(valor):
    """Converte para tipos de JSON: NumPy, datas, tuplas; NaN e ±inf viram null."""
    if isinstance(valor, dict):
        return {str(k): _limpar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple, set)):
        return [_limpar(v) for v in valor]
    if isinstance(valor, np.ndarray):
        return [_limpar(v) for v in valor.tolist()]
    if isinstance(valor, np.generic):
        valor = valor.item()
    if isinstance(valor, float):
        return valor if math.isfinite(valor) else None
    if isinstance(valor, (_dt.date, _dt.datetime)):
        return valor.isoformat()
    if valor is None or isinstance(valor, (bool, int, str)):
        return valor
    return str(valor)

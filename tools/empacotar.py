# -*- coding: utf-8 -*-
"""Empacota o plugin em um .zip instalável pelo QGIS.

    python tools/empacotar.py

Gera ``dist/risco_garimpo-<versão>.zip`` com a pasta ``risco_garimpo/`` na
raiz, que é o formato aceito em *Complementos → Instalar a partir do ZIP*.
"""

import os
import re
import zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACOTE = "risco_garimpo"
IGNORAR_DIR = {"__pycache__", ".git", ".pytest_cache"}
IGNORAR_EXT = {".pyc", ".pyo", ".orig", ".rej"}


def versao():
    caminho = os.path.join(RAIZ, PACOTE, "metadata.txt")
    with open(caminho, encoding="utf-8") as arquivo:
        achado = re.search(r"^version=(.+)$", arquivo.read(), re.MULTILINE)
    return achado.group(1).strip() if achado else "0.0.0"


def arquivos():
    base = os.path.join(RAIZ, PACOTE)
    for pasta, subpastas, nomes in os.walk(base):
        subpastas[:] = [s for s in subpastas if s not in IGNORAR_DIR]
        for nome in sorted(nomes):
            if os.path.splitext(nome)[1] in IGNORAR_EXT:
                continue
            completo = os.path.join(pasta, nome)
            yield completo, os.path.relpath(completo, RAIZ)


def main():
    destino_dir = os.path.join(RAIZ, "dist")
    os.makedirs(destino_dir, exist_ok=True)
    destino = os.path.join(destino_dir, "%s-%s.zip" % (PACOTE, versao()))

    total = 0
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zip_saida:
        for completo, relativo in arquivos():
            zip_saida.write(completo, relativo)
            total += 1
    print("%s (%d arquivos, %.1f KB)"
          % (destino, total, os.path.getsize(destino) / 1024.0))
    return destino


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Agrupamento de alertas em frentes.

Cerca de 85% dos alertas do Brasil+ têm vizinho a menos de 100 m: o alerta
individual é fragmento de detecção, não evento independente. Tratar cada
alerta como um evento infla a contagem e enviesa qualquer análise espacial.

Aqui os alertas são agrupados por **ligação simples** (single linkage) com
raio de 100 m — dois alertas a menos de 100 m pertencem à mesma frente, e a
relação é transitiva. Cada frente é representada pelo seu centroide e pela
**data mais antiga** do grupo, que é a data de chegada da frente.

A implementação usa uma grade de indexação com célula igual ao raio, de modo
que cada ponto só é comparado com os vizinhos das 9 células adjacentes.
"""

import datetime as _dt
from collections import defaultdict

import numpy as np


class _UnionFind:
    def __init__(self, n):
        self.pai = list(range(n))
        self.posto = [0] * n

    def achar(self, a):
        while self.pai[a] != a:
            self.pai[a] = self.pai[self.pai[a]]
            a = self.pai[a]
        return a

    def unir(self, a, b):
        ra, rb = self.achar(a), self.achar(b)
        if ra == rb:
            return
        if self.posto[ra] < self.posto[rb]:
            ra, rb = rb, ra
        self.pai[rb] = ra
        if self.posto[ra] == self.posto[rb]:
            self.posto[ra] += 1


def agrupar_frentes(x, y, raio_m=100.0):
    """Rótulo de frente para cada alerta.

    Parameters
    ----------
    x, y : array-like
        Coordenadas **projetadas**, em metros.
    raio_m : float
        Raio de ligação simples (padrão 100 m).

    Returns
    -------
    ndarray[int]
        Rótulo da frente de cada ponto, em 0..n_frentes-1.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = x.size
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    if x.shape != y.shape:
        raise ValueError("x e y devem ter o mesmo tamanho")

    raio = float(raio_m)
    celula = max(raio, 1e-6)
    cx = np.floor(x / celula).astype(np.int64)
    cy = np.floor(y / celula).astype(np.int64)

    balde = defaultdict(list)
    for i in range(n):
        balde[(int(cx[i]), int(cy[i]))].append(i)

    uf = _UnionFind(n)
    raio2 = raio * raio
    for (bx, by), indices in balde.items():
        vizinhos = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                vizinhos.extend(balde.get((bx + dx, by + dy), ()))
        if not vizinhos:
            continue
        viz = np.array(vizinhos, dtype=np.int64)
        vx, vy = x[viz], y[viz]
        for i in indices:
            d2 = (vx - x[i]) ** 2 + (vy - y[i]) ** 2
            proximos = viz[d2 <= raio2]
            for j in proximos:
                if j != i:
                    uf.unir(i, int(j))

    raizes = np.array([uf.achar(i) for i in range(n)], dtype=np.int64)
    _, rotulos = np.unique(raizes, return_inverse=True)
    return rotulos.astype(np.int64)


def resumir_frentes(x, y, datas, rotulos):
    """Centroide e data de chegada (mais antiga) de cada frente.

    Returns
    -------
    (ndarray, ndarray, list[datetime.date], ndarray)
        ``xc``, ``yc``, ``data_chegada``, ``n_alertas``.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    rotulos = np.asarray(rotulos, dtype=np.int64)
    ordinais = np.array([_ordinal(d) for d in datas], dtype=np.int64)

    n_frentes = int(rotulos.max()) + 1 if rotulos.size else 0
    xc = np.zeros(n_frentes)
    yc = np.zeros(n_frentes)
    quantos = np.zeros(n_frentes, dtype=np.int64)
    primeiro = np.full(n_frentes, np.iinfo(np.int64).max, dtype=np.int64)

    np.add.at(xc, rotulos, x)
    np.add.at(yc, rotulos, y)
    np.add.at(quantos, rotulos, 1)
    np.minimum.at(primeiro, rotulos, ordinais)

    xc /= np.maximum(quantos, 1)
    yc /= np.maximum(quantos, 1)
    chegada = [_dt.date.fromordinal(int(o)) for o in primeiro]
    return xc, yc, chegada, quantos


def _ordinal(d):
    if isinstance(d, _dt.datetime):
        return d.date().toordinal()
    if isinstance(d, _dt.date):
        return d.toordinal()
    raise TypeError("data inválida: %r" % (d,))

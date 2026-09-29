# -*- coding: utf-8 -*-
"""Núcleos de contágio espacial.

A escala de contágio local estimada empiricamente (~380 m) é a distância
característica em que a presença de garimpo eleva a probabilidade de novo
garimpo. O núcleo é normalizado para somar 1, de modo que a pressão de
contágio ``C`` resultante da convolução esteja na mesma escala de ``mu``
(0–1) e possa ser lida como "fração local ocupada, ponderada pela
proximidade".
"""

import numpy as np

TIPOS = ("exponencial", "gaussiano")


def kernel_contagio(escala_m, pixel_m, tipo="exponencial", raio_escalas=3.0,
                    excluir_centro=False):
    """Constrói o núcleo de contágio.

    Parameters
    ----------
    escala_m : float
        Distância característica do contágio, em metros (padrão do modelo: 380).
    pixel_m : float
        Tamanho do pixel em metros.
    tipo : {'exponencial', 'gaussiano'}
        ``exponencial`` → ``exp(-d/escala)``; ``gaussiano`` → ``exp(-d²/2σ²)``.
    raio_escalas : float
        Raio do núcleo em múltiplos da escala. 3 cobre ~95% da massa
        exponencial e praticamente toda a gaussiana.
    excluir_centro : bool
        Se ``True``, o pixel central não contribui para si mesmo. Útil para
        separar "contágio vindo da vizinhança" de "persistência local".
    """
    if tipo not in TIPOS:
        raise ValueError("tipo de núcleo desconhecido: %r" % (tipo,))
    if escala_m <= 0 or pixel_m <= 0:
        raise ValueError("escala_m e pixel_m devem ser positivos")

    raio_px = max(1, int(np.ceil(raio_escalas * escala_m / pixel_m)))
    eixo = np.arange(-raio_px, raio_px + 1, dtype=np.float64) * pixel_m
    dx, dy = np.meshgrid(eixo, eixo)
    d = np.hypot(dx, dy)

    if tipo == "exponencial":
        k = np.exp(-d / float(escala_m))
    else:
        k = np.exp(-(d ** 2) / (2.0 * float(escala_m) ** 2))

    if excluir_centro:
        k[raio_px, raio_px] = 0.0

    soma = k.sum()
    if soma <= 0:
        raise ValueError("núcleo degenerado (soma nula)")
    return np.asarray(k / soma, dtype=np.float32)


def raio_efetivo_px(escala_m, pixel_m, raio_escalas=3.0):
    """Raio do núcleo em pixels — útil para estimar custo e borda perdida."""
    return max(1, int(np.ceil(raio_escalas * escala_m / pixel_m)))

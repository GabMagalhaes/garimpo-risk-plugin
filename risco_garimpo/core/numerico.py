# -*- coding: utf-8 -*-
"""Primitivas numéricas: convolução e transformada de distância.

Usa SciPy quando disponível (o QGIS oficial já o distribui) e cai para
implementações em NumPy puro caso contrário, sem alterar o resultado.
"""

import numpy as np

try:  # pragma: no cover - depende do ambiente
    from scipy.ndimage import distance_transform_edt as _scipy_edt

    TEM_SCIPY_EDT = True
except Exception:  # pragma: no cover
    _scipy_edt = None
    TEM_SCIPY_EDT = False

try:  # pragma: no cover
    from scipy.signal import fftconvolve as _scipy_fftconvolve

    TEM_SCIPY_FFT = True
except Exception:  # pragma: no cover
    _scipy_fftconvolve = None
    TEM_SCIPY_FFT = False


# --------------------------------------------------------------------- convolução
def convolver(arr, kernel):
    """Convolução 2D 'same', com bordas tratadas como zero (fora da área).

    Tratar a borda como zero é conservador: o contágio não é inventado onde
    não há informação.
    """
    arr = np.asarray(arr, dtype=np.float32)
    kernel = np.asarray(kernel, dtype=np.float32)
    if kernel.size == 1:
        return arr * float(kernel.ravel()[0])

    if TEM_SCIPY_FFT:  # pragma: no cover - caminho preferencial
        out = _scipy_fftconvolve(arr, kernel, mode="same")
        return np.asarray(out, dtype=np.float32)

    return _fftconvolve_numpy(arr, kernel)


def _fftconvolve_numpy(arr, kernel):
    """Convolução 'same' via FFT do NumPy (fallback sem SciPy)."""
    ah, aw = arr.shape
    kh, kw = kernel.shape
    sh, sw = ah + kh - 1, aw + kw - 1
    fh, fw = _proxima_potencia_agradavel(sh), _proxima_potencia_agradavel(sw)

    fa = np.fft.rfft2(arr, s=(fh, fw))
    fk = np.fft.rfft2(kernel, s=(fh, fw))
    full = np.fft.irfft2(fa * fk, s=(fh, fw))[:sh, :sw]

    y0 = (kh - 1) // 2
    x0 = (kw - 1) // 2
    return np.asarray(full[y0 : y0 + ah, x0 : x0 + aw], dtype=np.float32)


def _proxima_potencia_agradavel(n):
    """Menor inteiro >= n cujos fatores primos são apenas 2, 3 e 5 (FFT rápida)."""
    if n <= 1:
        return 1
    limite = 2 ** int(np.ceil(np.log2(n)))  # sempre um candidato válido
    melhor = limite
    p5 = 1
    while p5 <= limite:
        p3 = p5
        while p3 <= limite:
            p2 = p3
            while p2 < n:
                p2 *= 2
            if p2 <= melhor:
                melhor = p2
            p3 *= 3
        p5 *= 5
    return int(melhor)


# ---------------------------------------------------------- distância euclidiana
def distancia_euclidiana(mascara, pixel_m):
    """Distância euclidiana (em metros) de cada pixel à feição mais próxima.

    ``mascara`` é booleana: ``True`` marca a feição (evento). O resultado em
    pixels sem nenhuma feição na cena é ``+inf``.
    """
    mascara = np.asarray(mascara, dtype=bool)
    if not mascara.any():
        return np.full(mascara.shape, np.inf, dtype=np.float32)

    if TEM_SCIPY_EDT:  # pragma: no cover - caminho preferencial
        d = _scipy_edt(~mascara)
    else:  # pragma: no cover
        d = np.sqrt(_edt2_quadrada_numpy(~mascara))
    return np.asarray(d, dtype=np.float32) * float(pixel_m)


def _edt2_quadrada_numpy(fundo):
    """EDT exata ao quadrado (Felzenszwalb & Huttenlocher, 2012), sem SciPy."""
    grande = 1e12
    f = np.where(fundo, grande, 0.0).astype(np.float64)
    saida = np.empty_like(f)
    for i in range(f.shape[0]):
        saida[i, :] = _edt1_quadrada(f[i, :])
    for j in range(f.shape[1]):
        saida[:, j] = _edt1_quadrada(saida[:, j])
    return saida


def _edt1_quadrada(f):
    n = f.shape[0]
    d = np.empty(n, dtype=np.float64)
    v = np.zeros(n, dtype=np.int64)
    z = np.empty(n + 1, dtype=np.float64)
    k = 0
    v[0] = 0
    z[0] = -np.inf
    z[1] = np.inf
    for q in range(1, n):
        s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
        while k > 0 and s <= z[k]:
            k -= 1
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = np.inf
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d[q] = (q - v[k]) ** 2 + f[v[k]]
    return d

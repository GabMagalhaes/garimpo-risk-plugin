# -*- coding: utf-8 -*-
"""Campos de eventos datados (alertas e autos de infração).

Cada evento tem data e posição. O efeito de um evento sobre um pixel é o
produto de um termo espacial (decaimento com a distância, fixo no tempo) por
um termo temporal (escalar, função dos dias decorridos).

Como o termo espacial não muda, ele é pré-calculado uma única vez por
*janela temporal* de eventos e guardado apenas no retângulo em que é
significativo — é isso que torna viável simular centenas de eventos sobre
um raster grande. A janela (``janela_dias``) é o único compromisso de
precisão: eventos da mesma janela compartilham a mesma data de referência.
"""

import datetime as _dt

import numpy as np

from .numerico import distancia_euclidiana
from .temporal import decaimento_espacial

COMBINACOES = ("maximo", "soma_saturada")


class CampoEventos:
    """Campo espaço-temporal gerado por um conjunto de eventos datados."""

    def __init__(self, shape, pixel_m, janelas, escala_m, limiar=1e-3,
                 guardar_posicoes=False, progresso=None):
        """
        Parameters
        ----------
        shape : (int, int)
            Formato do raster de trabalho.
        pixel_m : float
            Tamanho do pixel, em metros.
        janelas : list[(datetime.date, (ndarray[int], ndarray[int]))]
            Índices ``(linhas, colunas)`` dos eventos de cada janela temporal,
            com sua data de referência. Guardar índices em vez de máscaras
            evita manter dezenas de rasters booleanos em memória.
        escala_m : float
            Distância característica do decaimento espacial.
        limiar : float
            Valor abaixo do qual o efeito espacial é considerado nulo.
        guardar_posicoes : bool
            Guarda os índices dos pixels de evento (usado para semeadura).
        """
        self.shape = tuple(shape)
        self.pixel_m = float(pixel_m)
        self.escala_m = float(escala_m)
        self.itens = []
        self.posicoes = []
        self.n_janelas = 0

        total = max(len(janelas), 1)
        for i, (data, indices) in enumerate(janelas):
            if progresso is not None:
                progresso(i / total, "Pré-calculando campo de eventos (%d/%d)"
                          % (i + 1, len(janelas)))
            mascara = _mascara_de_indices(indices, self.shape)
            if not mascara.any():
                continue
            self.n_janelas += 1
            if guardar_posicoes:
                self.posicoes.append((data, np.nonzero(mascara)))

            dist = distancia_euclidiana(mascara, self.pixel_m)
            espacial = decaimento_espacial(dist, self.escala_m)
            significativo = espacial > limiar
            linhas = np.any(significativo, axis=1)
            colunas = np.any(significativo, axis=0)
            if not linhas.any():
                continue
            y0, y1 = int(np.argmax(linhas)), int(len(linhas) - np.argmax(linhas[::-1]))
            x0, x1 = int(np.argmax(colunas)), int(len(colunas) - np.argmax(colunas[::-1]))
            sub = espacial[y0:y1, x0:x1].copy()
            sub[sub <= limiar] = 0.0
            self.itens.append((data, slice(y0, y1), slice(x0, x1),
                               sub.astype(np.float32)))

    # ------------------------------------------------------------------ uso
    def valor(self, data, func_temporal, combinacao="maximo", corte=1e-4):
        """Campo no instante ``data``.

        ``func_temporal`` recebe os dias decorridos (escalar) e devolve o
        peso temporal. A combinação entre eventos é o máximo (padrão, evita
        que a mera quantidade de autos sature a supressão) ou a soma saturada
        em 1.
        """
        if combinacao not in COMBINACOES:
            raise ValueError("combinação desconhecida: %r" % (combinacao,))
        saida = np.zeros(self.shape, dtype=np.float32)
        for ref, sy, sx, sub in self.itens:
            dias = (data - ref).days
            if dias < 0:
                continue
            peso = float(np.asarray(func_temporal(dias)).ravel()[0])
            if peso <= corte:
                continue
            trecho = saida[sy, sx]
            contribuicao = sub * np.float32(peso)
            if combinacao == "maximo":
                np.maximum(trecho, contribuicao, out=trecho)
            else:
                trecho += contribuicao
        if combinacao == "soma_saturada":
            np.clip(saida, 0.0, 1.0, out=saida)
        return saida

    def posicoes_ate(self, data_inicial, data_final):
        """Índices de eventos com data em ``(data_inicial, data_final]``."""
        for ref, idx in self.posicoes:
            if data_inicial < ref <= data_final:
                yield idx

    def __len__(self):
        return len(self.itens)


# ----------------------------------------------------------------- agrupamento
def _mascara_de_indices(indices, shape):
    """Converte ``(linhas, colunas)`` — ou já uma máscara — em booleano."""
    if isinstance(indices, np.ndarray) and indices.dtype == bool:
        return indices
    linhas, colunas = indices
    mascara = np.zeros(shape, dtype=bool)
    linhas = np.asarray(linhas, dtype=np.int64)
    colunas = np.asarray(colunas, dtype=np.int64)
    dentro = ((linhas >= 0) & (linhas < shape[0])
              & (colunas >= 0) & (colunas < shape[1]))
    mascara[linhas[dentro], colunas[dentro]] = True
    return mascara


def janelas_temporais(datas, linhas, colunas, janela_dias=30):
    """Agrupa eventos pontuais em janelas temporais.

    Devolve ``[(data_de_referência, (linhas, colunas)), …]``. A data de
    referência de cada janela é a média das datas dos eventos que caem nela —
    não o início da janela — para não deslocar sistematicamente o efeito no
    tempo.
    """
    datas = list(datas)
    if not datas:
        return []
    linhas = np.asarray(linhas, dtype=np.int64)
    colunas = np.asarray(colunas, dtype=np.int64)
    if len(linhas) != len(datas) or len(colunas) != len(datas):
        raise ValueError("datas, linhas e colunas devem ter o mesmo tamanho")

    ordinais = np.array([_ordinal(d) for d in datas], dtype=np.int64)
    base = ordinais.min()
    grupo = (ordinais - base) // int(max(janela_dias, 1))

    janelas = []
    for g in np.unique(grupo):
        sel = grupo == g
        media = int(round(float(ordinais[sel].mean())))
        janelas.append((_dt.date.fromordinal(media),
                        (linhas[sel].copy(), colunas[sel].copy())))
    janelas.sort(key=lambda item: item[0])
    return janelas


def _ordinal(d):
    if isinstance(d, _dt.datetime):
        return d.date().toordinal()
    if isinstance(d, _dt.date):
        return d.toordinal()
    raise TypeError("data inválida: %r" % (d,))

# -*- coding: utf-8 -*-
"""Funções temporais do modelo dinâmico.

Duas formas distintas, sustentadas pelo event-study:

* **Fiscalização** — o efeito repressivo *não* é um decaimento exponencial a
  partir de um pico. Ele sobe quase imediatamente após o auto de infração,
  mantém-se em patamar estável por cerca de 180 dias e então se dissipa.
  A especificação é, portanto, um **platô logístico**:

  ``T(Δt) = (1 - exp(-k_subida·Δt)) · σ(-k_queda·(Δt - platô))``

  com ``Δt`` em dias desde o evento e ``T(Δt) = 0`` para ``Δt < 0``.
  A curva é normalizada para atingir máximo 1, de modo que o parâmetro
  ``F_max`` do modelo permaneça interpretável como "supressão máxima".

* **Alertas** — a ativação é contínua desde o dia zero e decai
  exponencialmente: ``A(Δt) = exp(-Δt/τ)``.
"""

import numpy as np


def plato_logistico(dias, k_subida=1.0, plato_dias=180.0, k_queda=0.05,
                    normalizar=True):
    """Resposta temporal da fiscalização, ``0`` antes do evento."""
    dias = np.asarray(dias, dtype=np.float64)
    subida = 1.0 - np.exp(-float(k_subida) * np.maximum(dias, 0.0))
    queda = 1.0 / (1.0 + np.exp(float(k_queda) * (dias - float(plato_dias))))
    resposta = subida * queda
    resposta = np.where(dias < 0.0, 0.0, resposta)
    if normalizar:
        pico = _pico_plato(k_subida, plato_dias, k_queda)
        if pico > 0:
            resposta = resposta / pico
    return np.clip(resposta, 0.0, 1.0)


def _pico_plato(k_subida, plato_dias, k_queda):
    """Máximo da curva, avaliado em grade densa no intervalo relevante."""
    fim = float(plato_dias) + 10.0 / max(float(k_queda), 1e-6)
    grade = np.linspace(0.0, max(fim, 1.0), 2000)
    subida = 1.0 - np.exp(-float(k_subida) * grade)
    queda = 1.0 / (1.0 + np.exp(float(k_queda) * (grade - float(plato_dias))))
    return float(np.max(subida * queda))


def decaimento_exponencial(dias, tau_dias=90.0):
    """Ativação por alerta: contínua desde o dia zero, ``0`` antes do evento."""
    dias = np.asarray(dias, dtype=np.float64)
    resposta = np.exp(-np.maximum(dias, 0.0) / float(tau_dias))
    return np.where(dias < 0.0, 0.0, resposta)


def decaimento_espacial(distancia_m, escala_m):
    """Decaimento espacial exponencial ``exp(-d/d0)``, robusto a ``inf``."""
    d = np.asarray(distancia_m, dtype=np.float32)
    with np.errstate(over="ignore", invalid="ignore"):
        s = np.exp(-d / np.float32(escala_m))
    return np.nan_to_num(s, nan=0.0, posinf=0.0, neginf=0.0)


def meia_vida_para_tau(meia_vida_dias):
    """Converte meia-vida em constante de tempo ``τ`` do decaimento exponencial."""
    return float(meia_vida_dias) / np.log(2.0)

# -*- coding: utf-8 -*-
"""Modelo dinâmico de risco de expansão do garimpo.

Equação resolvida, em superfície contínua (raster), por Euler explícito::

    dμ/dt = r₀ · C(μ) · (1 − μ/K) · (1 + α·A) · (1 − F)

onde

``μ(x,t)``
    variável de estado — grau de ocupação por garimpo no pixel, em [0, K].
``K(x)``
    capacidade de suporte — favorabilidade do modelo estático. **K exclui μ**:
    o garimpo pretérito entra na variável de estado e no termo de contágio,
    nunca em K, sob pena de dupla contagem e de K endógeno.
``C(μ)``
    pressão de contágio local — convolução de μ por um núcleo de escala
    ~380 m. É ele que implementa a propagação: sem vizinho ocupado não há
    crescimento, o que é coerente com o achado de que a geomorfologia prediz
    taxa de crescimento condicionada à presença, e não chegada em área virgem.
``(1 − μ/K)``
    saturação logística — reproduz a queda do crescimento relativo à medida
    que o estoque existente aumenta.
``A(x,t)``
    ativação por alertas — decaimento exponencial no espaço e no tempo,
    contínuo desde o dia zero.
``F(x,t)``
    supressão por fiscalização — platô logístico no tempo (~180 dias),
    decaimento exponencial no espaço (0,5–1 km).

A propagação é euclidiana, não hidrológica, e ocorre **no pixel**. A
sub-bacia é unidade de saída e reporte; agregar antes de propagar dilui o
efeito repressivo abaixo do ruído e destrói as escalas de 380 m e 3.500 m.
"""

import datetime as _dt
from dataclasses import dataclass, field

import numpy as np

from .eventos import COMBINACOES
from .kernels import kernel_contagio
from .numerico import convolver
from .temporal import decaimento_exponencial, plato_logistico


@dataclass
class Parametros:
    """Parâmetros do modelo dinâmico (unidades: metros e dias)."""

    # crescimento e contágio
    r0: float = 0.004
    escala_contagio_m: float = 380.0
    tipo_kernel: str = "exponencial"
    excluir_centro: bool = False

    # alertas (ativação)
    alerta_peso: float = 1.0
    alerta_escala_m: float = 1000.0
    alerta_tau_dias: float = 90.0
    alerta_semeia: bool = True
    alerta_semente: float = 0.05

    # fiscalização (supressão)
    fisc_max: float = 0.8
    fisc_escala_m: float = 750.0
    fisc_k_subida: float = 1.0
    fisc_plato_dias: float = 180.0
    fisc_k_queda: float = 0.05

    # combinação entre eventos e integração
    combinacao: str = "maximo"
    passo_dias: float = 7.0

    def validar(self):
        if self.r0 < 0:
            raise ValueError("r0 não pode ser negativo")
        if self.escala_contagio_m <= 0:
            raise ValueError("a escala de contágio deve ser positiva")
        if not 0.0 <= self.fisc_max <= 1.0:
            raise ValueError("fisc_max deve estar em [0, 1]")
        if self.passo_dias <= 0:
            raise ValueError("o passo de integração deve ser positivo")
        if self.combinacao not in COMBINACOES:
            raise ValueError("combinação desconhecida: %r" % (self.combinacao,))
        return self


@dataclass
class Resultado:
    """Saídas da simulação."""

    mu_inicial: np.ndarray
    mu_final: np.ndarray
    delta_mu: np.ndarray
    taxa_final: np.ndarray
    ativacao_final: np.ndarray
    supressao_final: np.ndarray
    datas: list
    serie: list = field(default_factory=list)
    diagnostico: dict = field(default_factory=dict)


class SimulacaoCancelada(RuntimeError):
    """Levantada quando a função de progresso pede cancelamento."""


def simular(K, mu0, pixel_m, data_inicio, data_fim, params,
            campo_alertas=None, campo_fiscalizacao=None, mascara=None,
            progresso=None, salvar_serie=False, passo_serie=1):
    """Integra o modelo dinâmico de ``data_inicio`` a ``data_fim``.

    Parameters
    ----------
    K : ndarray
        Capacidade de suporte (favorabilidade estática), ≥ 0. Pixels com
        ``K = 0`` nunca crescem.
    mu0 : ndarray
        Estado inicial, mesma grade de ``K``, em [0, K].
    pixel_m : float
        Tamanho do pixel em metros.
    data_inicio, data_fim : datetime.date
    params : Parametros
    campo_alertas, campo_fiscalizacao : CampoEventos ou None
    mascara : ndarray[bool] ou None
        Área de estudo. Fora dela nada cresce e nada é reportado.
    progresso : callable(fracao, mensagem) -> bool ou None
        Devolver ``False`` cancela a simulação.
    salvar_serie : bool
        Guarda μ a cada ``passo_serie`` passos (custo de memória).
    """
    params.validar()
    K = np.asarray(K, dtype=np.float32)
    mu = np.array(mu0, dtype=np.float32, copy=True)
    if mu.shape != K.shape:
        raise ValueError("mu0 e K devem ter o mesmo formato")
    if not isinstance(data_inicio, _dt.date) or not isinstance(data_fim, _dt.date):
        raise TypeError("data_inicio e data_fim devem ser datetime.date")
    if data_fim <= data_inicio:
        raise ValueError("data_fim deve ser posterior a data_inicio")

    K = np.nan_to_num(K, nan=0.0, posinf=0.0, neginf=0.0)
    mu = np.nan_to_num(mu, nan=0.0, posinf=0.0, neginf=0.0)
    np.clip(K, 0.0, None, out=K)

    if mascara is not None:
        mascara = np.asarray(mascara, dtype=bool)
        K = np.where(mascara, K, 0.0).astype(np.float32)

    valido = K > 0
    np.clip(mu, 0.0, None, out=mu)
    mu = np.where(valido, np.minimum(mu, K), 0.0).astype(np.float32)
    mu_inicial = mu.copy()

    nucleo = kernel_contagio(params.escala_contagio_m, pixel_m,
                             tipo=params.tipo_kernel,
                             excluir_centro=params.excluir_centro)

    dt = float(params.passo_dias)
    n_passos = int(np.ceil((data_fim - data_inicio).days / dt))
    if n_passos <= 0:
        raise ValueError("intervalo de simulação menor que um passo")

    datas = []
    serie = []
    ativacao = np.zeros_like(K)
    supressao = np.zeros_like(K)
    taxa = np.zeros_like(K)
    data = data_inicio

    def _temporal_alerta(dias):
        return decaimento_exponencial(dias, params.alerta_tau_dias)

    def _temporal_fisc(dias):
        return plato_logistico(dias, params.fisc_k_subida,
                               params.fisc_plato_dias, params.fisc_k_queda)

    for passo in range(n_passos):
        if progresso is not None:
            if progresso(passo / float(n_passos),
                         "Simulando %s" % data.isoformat()) is False:
                raise SimulacaoCancelada("simulação cancelada pelo usuário")

        data_seguinte = data + _dt.timedelta(days=dt)

        # semeadura: um alerta é, ele próprio, detecção de garimpo novo
        if campo_alertas is not None and params.alerta_semeia:
            for idx in campo_alertas.posicoes_ate(data, data_seguinte):
                semente = np.float32(params.alerta_semente)
                alvo = np.minimum(np.maximum(mu[idx], semente), K[idx])
                mu[idx] = np.where(K[idx] > 0, alvo, 0.0)

        contagio = convolver(mu, nucleo)

        if campo_alertas is not None:
            ativacao = campo_alertas.valor(data, _temporal_alerta,
                                           params.combinacao)
        if campo_fiscalizacao is not None:
            supressao = campo_fiscalizacao.valor(data, _temporal_fisc,
                                                 params.combinacao)

        with np.errstate(divide="ignore", invalid="ignore"):
            folga = np.where(valido, 1.0 - mu / np.maximum(K, 1e-12), 0.0)
        np.clip(folga, 0.0, 1.0, out=folga)

        taxa = (np.float32(params.r0) * contagio * folga
                * (1.0 + np.float32(params.alerta_peso) * ativacao)
                * (1.0 - np.float32(params.fisc_max) * supressao))
        np.clip(taxa, 0.0, None, out=taxa)
        taxa = np.where(valido, taxa, 0.0).astype(np.float32)

        mu = np.clip(mu + np.float32(dt) * taxa, 0.0, K).astype(np.float32)

        datas.append(data_seguinte)
        if salvar_serie and (passo % max(int(passo_serie), 1) == 0):
            serie.append((data_seguinte, mu.copy()))
        data = data_seguinte

    delta = (mu - mu_inicial).astype(np.float32)
    diagnostico = {
        "n_passos": n_passos,
        "passo_dias": dt,
        "pixel_m": float(pixel_m),
        "raio_kernel_px": int((nucleo.shape[0] - 1) // 2),
        "area_valida_px": int(valido.sum()),
        "expansao_total_px_equivalente": float(delta.sum()),
        "mu_inicial_total": float(mu_inicial.sum()),
        "mu_final_total": float(mu.sum()),
        "janelas_alertas": len(campo_alertas) if campo_alertas else 0,
        "janelas_fiscalizacao": (len(campo_fiscalizacao)
                                 if campo_fiscalizacao else 0),
    }
    if progresso is not None:
        progresso(1.0, "Simulação concluída")

    return Resultado(mu_inicial=mu_inicial, mu_final=mu, delta_mu=delta,
                     taxa_final=taxa, ativacao_final=ativacao,
                     supressao_final=supressao, datas=datas, serie=serie,
                     diagnostico=diagnostico)

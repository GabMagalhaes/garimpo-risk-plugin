# -*- coding: utf-8 -*-
"""Orquestração: da configuração da interface às camadas de saída.

A execução é deliberadamente partida em duas fases, por causa das regras de
*thread* do QGIS:

``preparar``
    roda na **thread principal**, porque toca camadas vetoriais do projeto
    (alertas, autos de infração). Lê as feições, agrupa alertas em frentes,
    converte para índices de pixel e devolve apenas dados simples — NumPy e
    datas.

``executar``
    roda em **segundo plano** (``QgsTask``), porque a partir daí só existem
    NumPy e GDAL: leitura alinhada dos rasters, campos de eventos, simulação
    e escrita.

A agregação por sub-bacia volta à thread principal, pois escreve GeoPackage
com a API vetorial do QGIS.
"""

import datetime as _dt
import os

import numpy as np

from . import raster as _raster
from .eventos import CampoEventos, janelas_temporais
from .frentes import agrupar_frentes, resumir_frentes
from .model import Parametros, simular
from .vetor import extrair_eventos


class ConfiguracaoInvalida(ValueError):
    """Configuração incompleta ou incoerente vinda da interface."""


class Configuracao(object):
    """Recipiente simples de configuração (sem dependência de Qt)."""

    def __init__(self, **kwargs):
        # insumos
        self.caminho_k = None
        self.caminho_mu0 = None
        self.caminho_mascara = None
        self.reamostragem_k = "bilinear"
        self.reamostragem_mu0 = "media"
        # grade de trabalho
        self.wkt_trabalho = None
        self.resolucao_m = None
        # eventos
        self.camada_alertas = None
        self.campo_data_alertas = None
        self.agrupar_em_frentes = True
        self.raio_frente_m = 100.0
        self.camada_fiscalizacao = None
        self.campo_data_fiscalizacao = None
        self.janela_dias = 30
        # período
        self.data_inicio = None
        self.data_fim = None
        # modelo
        self.parametros = Parametros()
        # saídas
        self.pasta_saida = None
        self.prefixo = "risco"
        self.salvar_mu_final = True
        self.salvar_taxa = True
        self.salvar_campos = False
        self.salvar_serie = False
        self.passo_serie = 4
        self.camada_subbacias = None
        self.campo_id_subbacias = None
        for chave, valor in kwargs.items():
            setattr(self, chave, valor)

    def validar(self):
        if not self.caminho_k:
            raise ConfiguracaoInvalida(
                "informe o raster de capacidade de suporte K (favorabilidade).")
        if not self.caminho_mu0:
            raise ConfiguracaoInvalida(
                "informe o raster de estado inicial μ₀ (garimpo existente).")
        if not self.pasta_saida:
            raise ConfiguracaoInvalida("informe a pasta de saída.")
        if not isinstance(self.data_inicio, _dt.date) or \
                not isinstance(self.data_fim, _dt.date):
            raise ConfiguracaoInvalida("informe o período de simulação.")
        if self.data_fim <= self.data_inicio:
            raise ConfiguracaoInvalida(
                "a data final deve ser posterior à inicial.")
        if self.camada_alertas is not None and not self.campo_data_alertas:
            raise ConfiguracaoInvalida(
                "escolha o campo de data da camada de alertas.")
        if self.camada_fiscalizacao is not None and \
                not self.campo_data_fiscalizacao:
            raise ConfiguracaoInvalida(
                "escolha o campo de data da camada de fiscalização.")
        self.parametros.validar()
        return self


class Preparo(object):
    """Dados simples produzidos na thread principal."""

    def __init__(self, grade, janelas_alertas=None, janelas_fiscalizacao=None,
                 resumo=None):
        self.grade = grade
        self.janelas_alertas = janelas_alertas or []
        self.janelas_fiscalizacao = janelas_fiscalizacao or []
        self.resumo = resumo or {}


# --------------------------------------------------- fase 1 (thread principal)
def preparar(config, log=None):
    """Define a grade de trabalho e extrai os eventos das camadas vetoriais."""
    config.validar()

    def _log(msg):
        if log is not None:
            log(msg)

    grade_base = _raster.grade_de_raster(config.caminho_k)
    grade = _raster.grade_reamostrada(grade_base, config.resolucao_m,
                                      config.wkt_trabalho)
    if grade.geografica:
        raise ConfiguracaoInvalida(
            "a grade de trabalho está em coordenadas geográficas. O modelo "
            "opera em metros (contágio de ~380 m, repressão de 0,5–1 km): "
            "escolha um CRS projetado, por exemplo SIRGAS 2000 / UTM 21S "
            "(EPSG:31981).")
    _log("Grade de trabalho: %s" % grade.descricao())

    pixel_m = grade.pixel_m
    if pixel_m > config.parametros.escala_contagio_m / 2.0:
        _log("AVISO: pixel de %.0f m é grosso demais para uma escala de "
             "contágio de %.0f m — o núcleo fica com 1–2 pixels de raio e a "
             "propagação perde forma. Reduza a resolução de trabalho."
             % (pixel_m, config.parametros.escala_contagio_m))

    resumo = {}
    janelas_alertas = []
    janelas_fisc = []

    if config.camada_alertas is not None:
        x, y, datas, ignorados = extrair_eventos(
            config.camada_alertas, config.campo_data_alertas, grade)
        _log("Alertas lidos: %d (descartados por data ou geometria inválida: "
             "%d)" % (len(datas), ignorados))
        if len(datas):
            if config.agrupar_em_frentes:
                rotulos = agrupar_frentes(x, y, config.raio_frente_m)
                x, y, datas, quantos = resumir_frentes(x, y, datas, rotulos)
                _log("Agrupados em %d frentes (raio de %.0f m; mediana de %.0f "
                     "alertas por frente)."
                     % (len(datas), config.raio_frente_m,
                        float(np.median(quantos))))
                resumo["frentes"] = int(len(datas))
            else:
                _log("ATENÇÃO: alertas usados sem agrupamento em frentes. "
                     "Alertas vizinhos são fragmentos da mesma detecção; sem "
                     "agrupar, o peso dos eventos fica inflado.")
            janelas_alertas, fora = _para_janelas(x, y, datas, grade,
                                                  config.janela_dias)
            if fora:
                _log("Alertas fora da grade de trabalho: %d" % fora)
            resumo["janelas_alertas"] = len(janelas_alertas)
            primeira, ultima = (min(datas), max(datas))
            _log("Período dos alertas: %s a %s"
                 % (primeira.isoformat(), ultima.isoformat()))

    if config.camada_fiscalizacao is not None:
        x, y, datas, ignorados = extrair_eventos(
            config.camada_fiscalizacao, config.campo_data_fiscalizacao, grade)
        _log("Eventos de fiscalização lidos: %d (descartados: %d)"
             % (len(datas), ignorados))
        if len(datas):
            janelas_fisc, fora = _para_janelas(x, y, datas, grade,
                                               config.janela_dias)
            if fora:
                _log("Autos fora da grade de trabalho: %d" % fora)
            resumo["janelas_fiscalizacao"] = len(janelas_fisc)
            primeira, ultima = (min(datas), max(datas))
            _log("Período dos autos: %s a %s"
                 % (primeira.isoformat(), ultima.isoformat()))

    return Preparo(grade, janelas_alertas, janelas_fisc, resumo)


def _para_janelas(x, y, datas, grade, janela_dias):
    linhas, colunas, dentro = _raster.rasterizar_pontos(x, y, grade)
    datas_dentro = [d for d, ok in zip(datas, dentro) if ok]
    janelas = janelas_temporais(datas_dentro, linhas[dentro], colunas[dentro],
                                janela_dias)
    return janelas, int((~dentro).sum())


# ------------------------------------------------ fase 2 (segundo plano, GDAL)
def executar(config, preparo, progresso=None, log=None):
    """Lê rasters, simula e grava as saídas. Sem dependência da API vetorial."""

    def _log(msg):
        if log is not None:
            log(msg)

    def _passo(fracao, mensagem):
        if progresso is not None:
            return progresso(fracao, mensagem)
        return True

    grade = preparo.grade
    pixel_m = grade.pixel_m

    _passo(0.02, "Lendo K (capacidade de suporte)")
    K = _raster.ler_alinhado(config.caminho_k, grade, config.reamostragem_k)
    _passo(0.08, "Lendo μ₀ (estado inicial)")
    mu0 = _raster.ler_alinhado(config.caminho_mu0, grade,
                               config.reamostragem_mu0)

    mascara = None
    if config.caminho_mascara:
        _passo(0.11, "Lendo máscara da área de estudo")
        mascara = _raster.ler_alinhado(config.caminho_mascara, grade,
                                       "vizinho") > 0

    k_max = float(np.nanmax(K)) if K.size else 0.0
    mu_max = float(np.nanmax(mu0)) if mu0.size else 0.0
    if k_max <= 0:
        raise ConfiguracaoInvalida(
            "o raster K é todo zero ou nodata na grade de trabalho — "
            "verifique a projeção e a extensão.")
    if k_max > 1.0001:
        _log("AVISO: K tem máximo %.3f (acima de 1). μ é lido na mesma "
             "unidade de K." % k_max)
    if mu_max > k_max + 1e-6:
        _log("AVISO: μ₀ excede K em parte da área; os excessos são truncados "
             "em K (saturação).")

    campo_alertas = None
    campo_fisc = None
    if preparo.janelas_alertas:
        _passo(0.15, "Campo de ativação por alertas")
        campo_alertas = CampoEventos(
            grade.shape, pixel_m, preparo.janelas_alertas,
            config.parametros.alerta_escala_m,
            guardar_posicoes=config.parametros.alerta_semeia,
            progresso=lambda f, m: _passo(0.15 + 0.12 * f, m))
    if preparo.janelas_fiscalizacao:
        _passo(0.28, "Campo de supressão por fiscalização")
        campo_fisc = CampoEventos(
            grade.shape, pixel_m, preparo.janelas_fiscalizacao,
            config.parametros.fisc_escala_m,
            progresso=lambda f, m: _passo(0.28 + 0.12 * f, m))

    resultado = simular(
        K, mu0, pixel_m, config.data_inicio, config.data_fim,
        config.parametros, campo_alertas=campo_alertas,
        campo_fiscalizacao=campo_fisc, mascara=mascara,
        progresso=lambda f, m: _passo(0.42 + 0.45 * f, m),
        salvar_serie=config.salvar_serie, passo_serie=config.passo_serie)
    resultado.diagnostico.update(preparo.resumo)

    _passo(0.90, "Gravando rasters")
    saidas = _gravar(config, grade, resultado, K, mascara)
    _passo(1.0, "Concluído")
    return {"resultado": resultado, "grade": grade, "saidas": saidas,
            "diagnostico": resultado.diagnostico}


def caminho_de_saida(config, nome):
    """Caminho de uma saída. Único lugar que decide o nome dos arquivos."""
    return os.path.join(config.pasta_saida,
                        "%s_%s_%s.tif" % (config.prefixo or "risco", nome,
                                          config.data_fim.strftime("%Y%m%d")))


def caminhos_de_saida(config):
    """Todos os arquivos que esta configuração vai gravar.

    A interface precisa conhecê-los ANTES de rodar, para soltar do projeto as
    camadas que os estejam segurando: no Windows o QGIS mantém o arquivo
    aberto e a gravação falha com "Permission denied" na segunda execução.
    """
    nomes = ["expansao"]
    if config.salvar_mu_final:
        nomes.append("mu")
    if config.salvar_taxa:
        nomes.append("taxa")
    if config.salvar_campos:
        nomes += ["ativacao", "supressao"]
    caminhos = [caminho_de_saida(config, nome) for nome in nomes]
    if config.camada_subbacias is not None:
        caminhos.append(os.path.join(
            config.pasta_saida,
            "%s_subbacias_%s.gpkg" % (config.prefixo or "risco",
                                      config.data_fim.strftime("%Y%m%d"))))
    return caminhos


def _gravar(config, grade, resultado, K, mascara):
    os.makedirs(config.pasta_saida, exist_ok=True)
    validos = (K > 0) if mascara is None else ((K > 0) & mascara)
    prefixo = config.prefixo or "risco"
    saidas = {}

    def _caminho(nome):
        return caminho_de_saida(config, nome)

    saidas["expansao"] = _raster.escrever_raster(
        _caminho("expansao"), resultado.delta_mu, grade, mascara=validos)

    if config.salvar_mu_final:
        saidas["mu_final"] = _raster.escrever_raster(
            _caminho("mu"), resultado.mu_final, grade, mascara=validos)
    if config.salvar_taxa:
        saidas["taxa"] = _raster.escrever_raster(
            _caminho("taxa"), resultado.taxa_final, grade, mascara=validos)
    if config.salvar_campos:
        saidas["ativacao"] = _raster.escrever_raster(
            _caminho("ativacao"), resultado.ativacao_final, grade,
            mascara=validos)
        saidas["supressao"] = _raster.escrever_raster(
            _caminho("supressao"), resultado.supressao_final, grade,
            mascara=validos)
    if config.salvar_serie and resultado.serie:
        pasta = os.path.join(config.pasta_saida, "%s_serie" % prefixo)
        os.makedirs(pasta, exist_ok=True)
        for data, arr in resultado.serie:
            _raster.escrever_raster(
                os.path.join(pasta, "%s_mu_%s.tif"
                             % (prefixo, data.strftime("%Y%m%d"))),
                arr, grade, mascara=validos)
        saidas["serie"] = pasta
    return saidas


# --------------------------------------------------- fase 3 (thread principal)
def agregar(config, grade, resultado, progresso=None):
    """Agrega por sub-bacia e grava o GeoPackage. Thread principal."""
    if config.camada_subbacias is None:
        return None, None
    from .agregacao import agregar_resultado

    caminho = os.path.join(
        config.pasta_saida,
        "%s_subbacias_%s.gpkg" % (config.prefixo or "risco",
                                  config.data_fim.strftime("%Y%m%d")))
    tabela = agregar_resultado(resultado, grade, config.camada_subbacias,
                               config.campo_id_subbacias, caminho, progresso)
    return caminho, tabela

# -*- coding: utf-8 -*-
"""Orquestração do modelo estático, em três fases.

Mesma razão do modelo dinâmico: QgsTask não pode tocar a API do QGIS, então
tudo que lê camadas do projeto acontece na thread principal (`preparar`), o
cálculo roda em segundo plano (`executar`) e a escrita volta para a thread
principal (`escrever`).

A fronteira com o modelo dinâmico é de arquivo, não de chamada: aqui se
grava `<prefixo>_K.tif`, e o modelo dinâmico o lê como qualquer outro
raster. Nenhum dos dois importa o código do outro.
"""

import os

import numpy as np

from . import estatico
from .raster import (escrever_raster, grade_de_raster, grade_reamostrada,
                     ler_alinhado)
from .remanescente import agregar_potencial, remanescente, resumo_global


class SimulacaoCancelada(Exception):
    """Levantada quando o usuário cancela no meio do cálculo."""


class ConfiguracaoEstatica(object):
    """Tudo que a geração de K precisa saber."""

    def __init__(self, caminho_ibx, caminho_mu, pasta_saida,
                 caminho_est=None, caminho_lito=None, caminho_mu_antigo=None,
                 caminho_mascara=None, caminho_agua=None,
                 caminho_mu_anterior=None, camada_subbacias=None,
                 campo_id_subbacia=None,
                 resolucao_m=30.0, wkt_trabalho=None,
                 peso_ibx=0.591, peso_est=0.075, peso_lito=0.334,
                 gamma=0.4, piso=0.008,
                 estimar_nivel=True, janela_m=380.0, quantil=0.99,
                 n_faixas=51, minimo_por_faixa=200, teto=0.70,
                 prefixo="PMT", salvar_escore=True, salvar_tabela=True,
                 salvar_remanescente=True, carregar=True):
        self.caminho_ibx = caminho_ibx
        self.caminho_est = caminho_est
        self.caminho_lito = caminho_lito
        self.caminho_mu = caminho_mu
        self.caminho_mu_antigo = caminho_mu_antigo
        self.caminho_mu_anterior = caminho_mu_anterior
        self.camada_subbacias = camada_subbacias
        self.campo_id_subbacia = campo_id_subbacia
        self.caminho_mascara = caminho_mascara
        self.caminho_agua = caminho_agua
        self.pasta_saida = pasta_saida
        self.resolucao_m = resolucao_m
        self.wkt_trabalho = wkt_trabalho
        self.peso_ibx = peso_ibx
        self.peso_est = peso_est
        self.peso_lito = peso_lito
        self.gamma = gamma
        self.piso = piso
        self.estimar_nivel = estimar_nivel
        self.janela_m = janela_m
        self.quantil = quantil
        self.n_faixas = n_faixas
        self.minimo_por_faixa = minimo_por_faixa
        self.teto = teto
        self.prefixo = prefixo
        self.salvar_escore = salvar_escore
        self.salvar_tabela = salvar_tabela
        self.salvar_remanescente = salvar_remanescente
        self.carregar = carregar


def caminho_de_saida(config, nome, extensao="tif"):
    return os.path.join(config.pasta_saida,
                        "%s_%s.%s" % (config.prefixo, nome, extensao))


def caminhos_de_saida(config):
    """O que será gravado, ANTES de gravar.

    A interface precisa disto para liberar arquivos que o QGIS mantenha
    abertos — no Windows, um raster carregado no projeto impede a
    sobrescrita.
    """
    caminhos = [caminho_de_saida(config, "K")]
    if config.salvar_escore:
        caminhos.append(caminho_de_saida(config, "escore"))
    if config.salvar_remanescente:
        caminhos.append(caminho_de_saida(config, "remanescente"))
    if config.estimar_nivel and config.salvar_tabela:
        caminhos.append(caminho_de_saida(config, "saturacao", "csv"))
    if config.camada_subbacias is not None:
        caminhos.append(caminho_de_saida(config, "subbacias", "gpkg"))
    return caminhos


def grade_de_trabalho(config):
    grade = grade_de_raster(config.caminho_ibx)
    return grade_reamostrada(grade, resolucao_m=config.resolucao_m,
                             wkt_destino=config.wkt_trabalho)


# ----------------------------------------------------------------- fase 1
def preparar(config, registrar=None):
    """Valida a configuração e monta a grade. Thread principal."""
    def log(msg):
        if registrar:
            registrar(msg)

    if not config.caminho_ibx:
        raise ValueError("informe ao menos a camada de PGgeom/IBx")
    if not config.caminho_mu and config.estimar_nivel:
        raise ValueError("estimar o nível exige a camada de garimpo "
                         "observado (μ)")
    if not config.pasta_saida:
        raise ValueError("escolha a pasta de saída")

    grade = grade_de_trabalho(config)
    if grade.geografica:
        raise ValueError("a grade de trabalho está em coordenadas "
                         "geográficas. O modelo precisa de CRS projetado, em "
                         "metros (ex.: EPSG:31981).")
    log("Grade: %d x %d px de %.0f m (%.0f km²)"
        % (grade.nx, grade.ny, grade.pixel_m,
           grade.nx * grade.ny * grade.pixel_m ** 2 / 1e6))

    pesos = []
    nomes = []
    caminhos = []
    for caminho, peso, nome in (
            (config.caminho_ibx, config.peso_ibx, "PGgeom"),
            (config.caminho_est, config.peso_est, "Est"),
            (config.caminho_lito, config.peso_lito, "Lt")):
        if caminho:
            caminhos.append(caminho)
            pesos.append(float(peso))
            nomes.append(nome)
    if not caminhos:
        raise ValueError("nenhuma variável de terreno informada")
    if sum(pesos) <= 0:
        raise ValueError("os pesos das variáveis informadas somam zero")
    log("Variáveis: %s" % ", ".join(
        "%s (peso %.3f)" % (n, p) for n, p in zip(nomes, pesos)))

    return {"grade": grade, "caminhos": caminhos, "pesos": pesos,
            "nomes": nomes, "config": config}


# ----------------------------------------------------------------- fase 2
def executar(preparado, progresso=None, cancelado=None, registrar=None):
    """Calcula escore, nível e K. Pode rodar em segundo plano."""
    config = preparado["config"]
    grade = preparado["grade"]

    def log(msg):
        if registrar:
            registrar(msg)

    def passo(pct):
        if cancelado is not None and cancelado():
            raise SimulacaoCancelada()
        if progresso:
            progresso(pct)

    passo(2)
    camadas = []
    for caminho, nome in zip(preparado["caminhos"], preparado["nomes"]):
        camadas.append(ler_alinhado(caminho, grade, "bilinear"))
        log("lido: %s" % nome)
        passo(2 + 8 * len(camadas))

    dentro = np.ones(grade.shape, dtype=bool)
    if config.caminho_mascara:
        mascara = ler_alinhado(config.caminho_mascara, grade, "near")
        dentro = np.isfinite(mascara) & (mascara > 0)
        log("máscara: %.1f%% da grade (%.0f km²)"
            % (100.0 * dentro.mean(),
               dentro.sum() * grade.pixel_m ** 2 / 1e6))
    passo(35)

    mu = np.zeros(grade.shape, dtype=np.float32)
    if config.caminho_mu:
        mu = np.clip(ler_alinhado(config.caminho_mu, grade, "average"),
                     0.0, 1.0)
    passo(45)

    maduro = dentro.copy()
    if config.caminho_mu_antigo:
        antigo = np.clip(ler_alinhado(config.caminho_mu_antigo, grade,
                                      "average"), 0.0, 1.0)
        vizinhanca = estatico.ocupacao_local(antigo, grade.pixel_m,
                                             config.janela_m)
        maduro = dentro & (vizinhanca > 0)
        log("pixels maduros (exposição longa): %.1f%% da área válida"
            % (100.0 * maduro.sum() / max(dentro.sum(), 1)))
    passo(55)

    estratos = None
    if config.caminho_agua:
        agua = ler_alinhado(config.caminho_agua, grade, "near")
        e_agua = dentro & np.isfinite(agua) & (agua > 0)
        estratos = {"agua": e_agua, "terra": dentro & ~e_agua}
        log("estrato água: %.2f%% da área válida"
            % (100.0 * e_agua.sum() / max(dentro.sum(), 1)))
    passo(60)

    pisos = [config.piso] * len(camadas)
    resultado = estatico.gerar_k(
        camadas, preparado["nomes"], preparado["pesos"], config.gamma,
        mu, grade.pixel_m, maduro, pisos=pisos, janela_m=config.janela_m,
        n_faixas=config.n_faixas, quantil=config.quantil,
        minimo_por_faixa=config.minimo_por_faixa,
        teto=None if config.estimar_nivel else config.teto,
        estratos=estratos)
    passo(90)

    for nome, zeros, fracao in resultado.veto:
        if fracao > 0:
            log("veto: %s zeraria %.1f%% da grade sem o piso"
                % (nome, 100.0 * fracao))

    resultado.K = np.where(dentro, resultado.K, 0.0).astype(np.float32)
    resultado.escore = np.where(dentro, resultado.escore,
                                0.0).astype(np.float32)

    mu_anterior = None
    if config.caminho_mu_anterior:
        mu_anterior = np.clip(
            ler_alinhado(config.caminho_mu_anterior, grade, "average"),
            0.0, 1.0)

    R = np.where(dentro, remanescente(resultado.K, mu), 0.0).astype(np.float32)
    totais = resumo_global(resultado.K, mu, grade.pixel_m, dentro)
    log("potencial total:      %12.0f ha" % totais["potencial_ha"])
    log("já garimpado:         %12.0f ha  (%.1f%% do potencial)"
        % (totais["garimpado_ha"], 100.0 * totais["explotacao"]))
    log("REMANESCENTE:         %12.0f ha" % totais["remanescente_ha"])
    if totais["excedente_ha"] > 0:
        log("excedente (μ > K):    %12.0f ha  (%.1f%% do já garimpado) — "
            "K subestimado ali, ou μ inclui o que o terreno não explica"
            % (totais["excedente_ha"],
               100.0 * totais["excedente_ha"] / max(totais["garimpado_ha"], 1)))

    validos = resultado.K[dentro]
    if validos.size:
        log("K: mínimo %.4f, mediana %.4f, máximo %.4f"
            % (validos.min(), float(np.median(validos)), validos.max()))
        log("teto de K: %.4f  (%s)"
            % (resultado.teto_estimado,
               "estimado da saturação observada" if config.estimar_nivel
               else "fixado na configuração"))
    passo(95)
    return {"resultado": resultado, "grade": grade, "dentro": dentro,
            "config": config, "mu": mu, "mu_anterior": mu_anterior,
            "remanescente": R, "totais": totais}


# ----------------------------------------------------------------- fase 3
def escrever(calculado, registrar=None):
    """Grava K e os diagnósticos. Thread principal."""
    config = calculado["config"]
    grade = calculado["grade"]
    resultado = calculado["resultado"]
    dentro = calculado["dentro"]

    def log(msg):
        if registrar:
            registrar(msg)

    os.makedirs(config.pasta_saida, exist_ok=True)
    gravados = []

    caminho_k = caminho_de_saida(config, "K")
    escrever_raster(caminho_k, resultado.K, grade, mascara=dentro)
    gravados.append(caminho_k)
    log("gravado: %s" % caminho_k)

    if config.salvar_escore:
        caminho = caminho_de_saida(config, "escore")
        escrever_raster(caminho, resultado.escore, grade, mascara=dentro)
        gravados.append(caminho)
        log("gravado: %s" % caminho)

    if config.salvar_remanescente:
        caminho = caminho_de_saida(config, "remanescente")
        escrever_raster(caminho, calculado["remanescente"], grade,
                        mascara=dentro)
        gravados.append(caminho)
        log("gravado: %s" % caminho)

    if config.estimar_nivel and config.salvar_tabela \
            and resultado.saturacao is not None:
        caminho = caminho_de_saida(config, "saturacao", "csv")
        x, y = resultado.curva
        with open(caminho, "w", encoding="utf-8") as arquivo:
            arquivo.write("escore;n_pixels;saturacao_observada;nivel_ajustado\n")
            for centro, sat, cont in zip(resultado.centros,
                                         resultado.saturacao,
                                         resultado.contagem):
                ajustado = float(np.interp(centro, x, y, left=y[0],
                                           right=y[-1]))
                arquivo.write("%.6f;%d;%s;%.6f\n"
                              % (centro, int(cont),
                                 "" if not np.isfinite(sat) else "%.6f" % sat,
                                 ajustado))
        gravados.append(caminho)
        log("gravado: %s" % caminho)

    if config.camada_subbacias is not None:
        caminho = caminho_de_saida(config, "subbacias", "gpkg")
        linhas = agregar_potencial(
            resultado.K, calculado["mu"], grade, config.camada_subbacias,
            mu_anterior=calculado.get("mu_anterior"),
            campo_id=config.campo_id_subbacia, caminho_saida=caminho,
            dentro=dentro)
        gravados.append(caminho)
        log("gravado: %s  (%d sub-bacias)" % (caminho, len(linhas)))
        melhores = sorted(linhas, key=lambda l: -l["pa_rem_ha"])[:5]
        log("maior potencial remanescente:")
        for linha in melhores:
            log("   #%-3d  %-18s  %10.0f ha remanescentes, %4.1f%% explotado"
                % (linha["pa_rank"], str(linha["id"])[:18],
                   linha["pa_rem_ha"], 100.0 * linha["pa_expl"]))

    return gravados

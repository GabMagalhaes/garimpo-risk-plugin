# -*- coding: utf-8 -*-
"""Orquestração da validação: expansão prevista × garimpo novo observado.

Três fases, como nos modelos: ``preparar`` (thread principal: confere a
configuração, monta a grade e descreve a camada de sub-bacias),
``executar`` (segundo plano: lê os rasters alinhados, calcula as métricas,
as linhas de base e as curvas de captura) e ``escrever`` (thread principal:
grava as tabelas, a figura, o GeoPackage por sub-bacia e o registro).

A grade é a do raster de expansão prevista (``*_expansao_*.tif``); os demais
rasters são lidos alinhados a ela. O ponto de partida mais simples é o
registro de uma execução do modelo dinâmico (``de_registro``): dele saem a
expansão prevista, μ₀, K, a máscara, as datas e o SHA-256 de cada insumo,
conferido de novo aqui — se um arquivo mudou desde a execução, a validação
avisa.

Duas perguntas que a validação responde, com honestidade diferente:

* com eventos só até a data inicial, mede a **antecipação** — o que se
  saberia no dia da previsão;
* com eventos do período inteiro (o uso normal do plugin, com a camada de
  alertas completa), mede a **reconstrução**: os alertas do período já
  detectam boa parte do garimpo que o mapa final registra. A validação
  avisa quando é esse o caso.
"""

import csv
import os
import re

import numpy as np

from . import registro as _registro
from . import validacao as _v
from .raster import aviso_de_mapa, grade_de_raster, ler_alinhado

# a calibração com dados reais (150 m) teve o melhor resultado com contágio de
# 100 m; a linha de base mais fina precisa estar à altura
ESCALAS_BASE_M = (100.0, 150.0, 200.0, 300.0, 380.0, 500.0)
CAMPOS_ZONA = ("va_prev_ha", "va_obs_ha", "va_rank_prev", "va_rank_obs")


class ValidacaoCancelada(Exception):
    """Levantada quando o usuário cancela no meio do cálculo."""


class ConfiguracaoValidacao(object):
    """O que a validação precisa saber.

    ``caminho_previsto`` é a expansão prevista (Δμ) de uma execução;
    ``caminho_mu_inicial`` e ``caminho_mu_final``, o garimpo observado nas
    datas inicial e final da mesma execução, em fração 0–1. ``caminho_k``
    habilita as linhas de base que usam o potencial. A avaliação usa
    ``caminho_mascara_avaliacao`` ou, sem ela, ``caminho_mascara``.
    """

    def __init__(self, caminho_previsto, caminho_mu_inicial, caminho_mu_final,
                 pasta_saida, caminho_k=None, caminho_mascara=None,
                 caminho_mascara_avaliacao=None, camada_subbacias=None,
                 campo_id_subbacia=None, limiar=0.02, bloco_m=1000.0,
                 fracao_topo=0.10, escalas_base_m=ESCALAS_BASE_M,
                 data_inicial=None, data_final=None, prefixo="validacao",
                 caminho_registro_execucao=None, salvar_figura=True):
        self.caminho_previsto = caminho_previsto
        self.caminho_mu_inicial = caminho_mu_inicial
        self.caminho_mu_final = caminho_mu_final
        self.pasta_saida = pasta_saida
        self.caminho_k = caminho_k
        self.caminho_mascara = caminho_mascara
        self.caminho_mascara_avaliacao = caminho_mascara_avaliacao
        self.camada_subbacias = camada_subbacias
        self.campo_id_subbacia = campo_id_subbacia
        self.limiar = limiar
        self.bloco_m = bloco_m
        self.fracao_topo = fracao_topo
        self.escalas_base_m = tuple(escalas_base_m)
        self.data_inicial = data_inicial
        self.data_final = data_final
        self.prefixo = prefixo
        self.caminho_registro_execucao = caminho_registro_execucao
        self.salvar_figura = salvar_figura
        # preenchidos por de_registro: SHA-256 esperados e eventos usados
        self._sha_esperado = {}
        self._eventos_execucao = {}


def de_registro(caminho_registro, caminho_mu_final, pasta_saida=None,
                **extra):
    """Configuração a partir do registro de uma execução do modelo dinâmico.

    O garimpo observado na data final é o único insumo novo; o resto —
    expansão prevista, μ₀, K, máscara e datas — vem do registro.
    """
    dados = _registro.ler(caminho_registro)
    if dados.get("ferramenta") != "modelo_dinamico":
        raise ValueError("%s não é o registro de uma execução do modelo "
                         "dinâmico" % caminho_registro)
    insumos = {i.get("papel"): i for i in dados.get("insumos", [])}
    config_execucao = dados.get("configuracao", {})
    previsto = dados.get("saidas", {}).get("expansao")
    if not previsto:
        raise ValueError("o registro não lista a expansão prevista")

    def caminho(papel):
        return (insumos.get(papel) or {}).get("caminho")

    parametros = dict(
        caminho_previsto=previsto,
        caminho_mu_inicial=caminho("mu0"),
        caminho_mu_final=caminho_mu_final,
        pasta_saida=pasta_saida or os.path.dirname(
            os.path.abspath(caminho_registro)),
        caminho_k=caminho("K"),
        caminho_mascara=caminho("mascara"),
        data_inicial=config_execucao.get("data_inicio"),
        data_final=config_execucao.get("data_fim"),
        prefixo="%s_validacao" % (config_execucao.get("prefixo") or "risco"),
        caminho_registro_execucao=caminho_registro)
    parametros.update(extra)
    config = ConfiguracaoValidacao(**parametros)
    config._sha_esperado = {i["caminho"]: i["sha256"]
                            for i in insumos.values()
                            if i.get("caminho") and i.get("sha256")}
    config._eventos_execucao = dados.get("eventos", {})
    return config


def caminho_de_saida(config, nome, extensao):
    return os.path.join(config.pasta_saida,
                        "%s_%s.%s" % (config.prefixo, nome, extensao))


def caminhos_de_saida(config):
    """O que será gravado, ANTES de gravar (para liberar camadas abertas)."""
    caminhos = [caminho_de_saida(config, "metricas", "csv"),
                caminho_de_saida(config, "curva_captura", "csv")]
    if config.salvar_figura:
        caminhos.append(caminho_de_saida(config, "curva_captura", "png"))
    if config.camada_subbacias is not None:
        caminhos.append(caminho_de_saida(config, "subbacias", "gpkg"))
    caminhos.append(caminho_de_saida(config, "registro", "json"))
    return caminhos


# ----------------------------------------------------------------- fase 1
def preparar(config, registrar=None):
    """Confere a configuração e monta a grade. Thread principal."""
    inicio = _registro.agora()

    def log(msg):
        if registrar:
            registrar(msg)

    obrigatorios = (("expansão prevista", config.caminho_previsto),
                    ("garimpo na data inicial", config.caminho_mu_inicial),
                    ("garimpo na data final", config.caminho_mu_final))
    for nome, caminho in obrigatorios:
        if not caminho:
            raise ValueError("informe o raster de %s" % nome)
        if not os.path.isfile(caminho):
            raise ValueError("raster de %s não encontrado: %s"
                             % (nome, caminho))
    if not config.pasta_saida:
        raise ValueError("escolha a pasta de saída")
    if os.path.abspath(config.caminho_mu_inicial) == \
            os.path.abspath(config.caminho_mu_final):
        raise ValueError("o garimpo inicial e o final são o mesmo arquivo")

    grade = grade_de_raster(config.caminho_previsto)
    log("Grade da expansão prevista: %d x %d px de %.0f m"
        % (grade.nx, grade.ny, grade.pixel_m))
    if config.data_inicial and config.data_final:
        log("Período validado: %s a %s"
            % (config.data_inicial, config.data_final))
    for aviso in avisos_de_honestidade(config):
        log("AVISO: " + aviso)

    vetoriais = []
    if config.camada_subbacias is not None:
        vetoriais.append(_registro.descrever_camada(
            config.camada_subbacias, "subbacias",
            campo_id=config.campo_id_subbacia))
    return {"config": config, "grade": grade, "vetoriais": vetoriais,
            "inicio": inicio}


def avisos_de_honestidade(config):
    """O que a validação mede, dado o que a execução usou.

    Eventos posteriores à data inicial fazem da validação uma medida de
    reconstrução, não de antecipação.
    """
    avisos = []
    inicial = str(config.data_inicial or "")
    for tipo, nome in (("alertas", "alertas"), ("fiscalizacao", "autos")):
        ultima = str((config._eventos_execucao.get(tipo) or {})
                     .get("ultima_data") or "")
        if inicial and ultima > inicial:
            avisos.append(
                "a execução usou %s até %s, depois da data inicial (%s): a "
                "validação mede a reconstrução do período, não a "
                "antecipação. Para medir antecipação, rode o modelo com "
                "eventos só até a data inicial." % (nome, ultima, inicial))
    return avisos


# ----------------------------------------------------------------- fase 2
def executar(preparado, progresso=None, cancelado=None, registrar=None):
    """Lê os rasters e calcula as métricas. Pode rodar em segundo plano."""
    config = preparado["config"]
    grade = preparado["grade"]

    def log(msg):
        if registrar:
            registrar(msg)

    def passo(pct):
        if cancelado is not None and cancelado():
            raise ValidacaoCancelada()
        if progresso:
            progresso(pct)

    passo(2)
    previsto = np.nan_to_num(ler_alinhado(config.caminho_previsto, grade,
                                          "media")).astype(np.float32)
    previsto = np.clip(previsto, 0.0, None)
    mu0 = _fracao(ler_alinhado(config.caminho_mu_inicial, grade, "media"),
                  "garimpo na data inicial")
    passo(15)
    mu1 = _fracao(ler_alinhado(config.caminho_mu_final, grade, "media"),
                  "garimpo na data final")
    for caminho, data in ((config.caminho_mu_inicial, "inicial"),
                          (config.caminho_mu_final, "final")):
        aviso = aviso_de_mapa(caminho, "mapa de garimpo da data %s" % data)
        if aviso:
            log(aviso)
    passo(30)
    K = None
    if config.caminho_k:
        K = np.clip(np.nan_to_num(ler_alinhado(config.caminho_k, grade,
                                               "bilinear")), 0.0, 1.0)
    universo = np.ones(grade.shape, dtype=bool)
    for caminho in (config.caminho_mascara, config.caminho_mascara_avaliacao):
        if caminho:
            universo &= ler_alinhado(caminho, grade, "vizinho") > 0
    passo(40)

    observado = _v.garimpo_novo(mu0, mu1, universo)
    area_ha = grade.pixel_m ** 2 / 10000.0
    log("Universo avaliado: %.0f km²; garimpo novo observado: %.0f ha"
        % (universo.sum() * area_ha / 100.0, observado.sum() * area_ha))
    if observado.sum() <= 0:
        raise ValueError("não há garimpo novo entre os dois mapas na área "
                         "avaliada — confira a ordem dos arquivos")
    if K is not None:
        fora = observado[(K <= 0) & universo].sum() / observado.sum()
        if fora > 0.05:
            log("AVISO: %.0f%% do garimpo novo observado caiu onde K = 0; o "
                "modelo não pode prever ali (limite do K, não da dinâmica)."
                % (100 * fora))

    pontuacoes = [("modelo", previsto, True)]
    for escala in config.escalas_base_m:
        bases = _v.linhas_de_base(K if K is not None else np.ones_like(mu0),
                                  mu0, grade.pixel_m, escala_m=escala)
        pontuacoes.append(("proximidade_%dm" % escala,
                           bases["proximidade"], False))
        if K is not None:
            pontuacoes.append(("proximidade_x_remanescente_%dm" % escala,
                               bases["proximidade_x_remanescente"], False))
    if K is not None:
        pontuacoes += [("so_K", K, False),
                       ("remanescente", np.clip(K - mu0, 0.0, None), False)]

    metricas, curvas = [], {}
    for i, (nome, pontuacao, magnitude) in enumerate(pontuacoes):
        linha = {"pontuacao": nome}
        linha.update(_v.avaliar(pontuacao, observado, universo,
                                grade.pixel_m, limiar=config.limiar,
                                bloco_m=config.bloco_m, magnitude=magnitude))
        metricas.append(linha)
        fracao, capturado = _v.curva_captura(pontuacao[universo],
                                             observado[universo])
        curvas["fracao_area"] = fracao
        curvas[nome] = capturado
        passo(40 + 45 * (i + 1) / len(pontuacoes))

    modelo = metricas[0]
    bases = [m for m in metricas[1:]]
    melhor = max(bases, key=lambda m: m["auc"]) if bases else None
    log("Modelo: AUC %.3f, captura nos 5%% de maior risco %.0f%%, área "
        "prevista / observada %.2f"
        % (modelo["auc"], 100 * modelo["captura_5"], modelo["razao_area"]))
    if melhor is not None:
        log("Melhor linha de base: %s, AUC %.3f, captura 5%% %.0f%% — o "
            "modelo %s" % (melhor["pontuacao"], melhor["auc"],
                           100 * melhor["captura_5"],
                           "a supera" if modelo["auc"] > melhor["auc"]
                           and modelo["captura_5"] > melhor["captura_5"]
                           else "NÃO a supera"))

    insumos, alterados = _identificar(config, preparado.get("vetoriais", []),
                                      cancelado)
    for caminho in alterados:
        log("AVISO: %s mudou desde a execução validada (SHA-256 diferente "
            "do registro da execução)." % caminho)
    passo(98)
    return {"config": config, "grade": grade, "previsto": previsto,
            "observado": observado, "universo": universo,
            "metricas": metricas, "curvas": curvas, "melhor_base": melhor,
            "insumos": insumos, "alterados": alterados,
            "inicio": preparado.get("inicio"),
            "avisos": avisos_de_honestidade(config)}


def _fracao(arr, nome):
    arr = np.nan_to_num(np.asarray(arr, dtype=np.float32))
    maximo = float(arr.max()) if arr.size else 0.0
    if maximo > 1.0001:
        raise ValueError("o raster de %s vai até %.3g; o esperado é fração "
                         "0–1 (1 = garimpo). Se for o raster de classes do "
                         "MapBiomas, extraia antes só a classe de garimpo."
                         % (nome, maximo))
    return np.clip(arr, 0.0, 1.0)


def _identificar(config, vetoriais, cancelado):
    """SHA-256 dos insumos e os que mudaram desde a execução validada."""
    papeis = (("previsto", config.caminho_previsto),
              ("mu_inicial", config.caminho_mu_inicial),
              ("mu_final", config.caminho_mu_final),
              ("K", config.caminho_k), ("mascara", config.caminho_mascara),
              ("mascara_avaliacao", config.caminho_mascara_avaliacao),
              ("registro_execucao", config.caminho_registro_execucao))
    try:
        insumos = [_registro.descrever_arquivo(caminho, papel, cancelado)
                   for papel, caminho in papeis if caminho]
        insumos += [_registro.identificar(v, cancelado) for v in vetoriais]
    except _registro.Interrompido:
        raise ValidacaoCancelada()
    alterados = sorted(
        i["caminho"] for i in insumos
        if i.get("caminho") in config._sha_esperado and i.get("sha256")
        and i["sha256"] != config._sha_esperado[i["caminho"]])
    return insumos, alterados


# ----------------------------------------------------------------- fase 3
def escrever(calculado, registrar=None):
    """Grava tabelas, figura, sub-bacias e registro. Thread principal."""
    config = calculado["config"]

    def log(msg):
        if registrar:
            registrar(msg)

    os.makedirs(config.pasta_saida, exist_ok=True)
    caminho_registro = caminho_de_saida(config, "registro", "json")
    _registro.remover(caminho_registro)
    gravados, saidas = [], {}

    caminho = caminho_de_saida(config, "metricas", "csv")
    _gravar_metricas(caminho, calculado["metricas"])
    gravados.append(caminho)
    saidas["metricas"] = caminho
    log("gravado: %s" % caminho)

    caminho = caminho_de_saida(config, "curva_captura", "csv")
    _gravar_curvas(caminho, calculado["curvas"])
    gravados.append(caminho)
    saidas["curva_captura"] = caminho
    log("gravado: %s" % caminho)

    if config.salvar_figura:
        caminho = caminho_de_saida(config, "curva_captura", "png")
        if _gravar_figura(caminho, calculado, log):
            gravados.append(caminho)
            saidas["figura"] = caminho
            log("gravado: %s" % caminho)

    resumo_zonas = None
    if config.camada_subbacias is not None:
        caminho = caminho_de_saida(config, "subbacias", "gpkg")
        resumo_zonas = _gravar_subbacias(caminho, calculado)
        gravados.append(caminho)
        saidas["subbacias"] = caminho
        log("gravado: %s  (%d sub-bacias; Spearman %.2f; %.0f%% das %d de "
            "maior expansão observada também no topo previsto)"
            % (caminho, resumo_zonas["zonas"], resumo_zonas["spearman"],
               100 * resumo_zonas["topo_comum"], resumo_zonas["topo_k"]))

    try:
        dados = _registro.montar(
            "validacao", _registro.configuracao_simples(config),
            calculado["grade"], calculado["insumos"], saidas,
            resultados={"metricas": calculado["metricas"],
                        "melhor_linha_de_base": (calculado["melhor_base"] or
                                                 {}).get("pontuacao"),
                        "subbacias": resumo_zonas,
                        "avisos": calculado["avisos"],
                        "insumos_alterados": calculado["alterados"]},
            inicio=calculado.get("inicio"))
        gravados.append(_registro.gravar(caminho_registro, dados))
        log("gravado: %s" % caminho_registro)
    except Exception as erro:  # noqa: BLE001 - registro não desfaz o resto
        log("AVISO: não foi possível gravar o registro da validação: %s"
            % erro)
    return gravados


def _formatar(valor):
    if isinstance(valor, float):
        return "" if not np.isfinite(valor) else "%.6g" % valor
    return str(valor)


def _gravar_metricas(caminho, metricas):
    colunas = []
    for linha in metricas:
        colunas += [c for c in linha if c not in colunas]
    with open(caminho, "w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(colunas)
        for linha in metricas:
            escritor.writerow([_formatar(linha.get(c, "")) for c in colunas])


def _gravar_curvas(caminho, curvas):
    nomes = ["fracao_area"] + [n for n in curvas if n != "fracao_area"]
    with open(caminho, "w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(nomes)
        for i in range(len(curvas["fracao_area"])):
            escritor.writerow([_formatar(float(curvas[n][i])) for n in nomes])


def _gravar_figura(caminho, calculado, log):
    """Curva de captura do modelo, da melhor linha de base e do acaso."""
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError:
        log("AVISO: matplotlib indisponível; a figura não foi gerada (a "
            "curva está no CSV).")
        return False
    curvas = calculado["curvas"]
    fracao = 100 * curvas["fracao_area"]
    figura = Figure(figsize=(6.0, 4.5), dpi=150)
    FigureCanvasAgg(figura)
    eixo = figura.add_subplot(1, 1, 1)
    eixo.plot(fracao, fracao, color="0.6", linestyle=":", label="Acaso")
    melhor = calculado["melhor_base"]
    if melhor is not None:
        eixo.plot(fracao, 100 * curvas[melhor["pontuacao"]], color="0.3",
                  linestyle="--", label="Linha de base: %s"
                  % _rotulo(melhor["pontuacao"]))
    eixo.plot(fracao, 100 * curvas["modelo"], color="#b2182b", linewidth=2,
              label="Modelo dinâmico")
    eixo.set_xlim(0, 100)
    eixo.set_ylim(0, 100)
    eixo.set_xlabel("Área de maior risco (%)")
    eixo.set_ylabel("Garimpo novo observado capturado (%)")
    config = calculado["config"]
    if config.data_inicial and config.data_final:
        eixo.set_title("Curva de captura, %s a %s"
                       % (_data_br(config.data_inicial),
                          _data_br(config.data_final)))
    eixo.grid(True, color="0.9")
    eixo.legend(loc="lower right", fontsize=8)
    figura.tight_layout()
    figura.savefig(caminho)
    return True


def _rotulo(nome):
    """'proximidade_x_remanescente_200m' → 'proximidade × remanescente, 200 m'."""
    nome = re.sub(r"_(\d+)m$", r", \1 m", nome)
    return nome.replace("_x_", " × ").replace("_", " ")


def _data_br(data):
    texto = str(data)
    achado = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", texto)
    return "%s/%s/%s" % achado.group(3, 2, 1) if achado else texto


def _gravar_subbacias(caminho, calculado):
    from .agregacao import _escrever_gpkg, rasterizar_zonas

    config = calculado["config"]
    grade = calculado["grade"]
    zonas, feicoes = rasterizar_zonas(config.camada_subbacias, grade)
    if not feicoes:
        raise ValueError("a camada de sub-bacias não tem feições válidas")
    zonas = np.where(calculado["universo"], zonas, -1)
    area_ha = grade.pixel_m ** 2 / 10000.0
    linhas_zona, resumo = _v.comparar_zonas(
        calculado["previsto"], calculado["observado"], zonas, len(feicoes),
        area_ha, fracao_topo=config.fracao_topo)
    nomes = None
    linhas = []
    for feicao, valores in zip(feicoes, linhas_zona):
        if nomes is None:
            nomes = [f.name() for f in feicao.fields()]
        identificador = (feicao[config.campo_id_subbacia]
                         if config.campo_id_subbacia
                         and config.campo_id_subbacia in nomes
                         else feicao.id())
        linhas.append({"id": identificador,
                       "va_prev_ha": valores["prev_ha"],
                       "va_obs_ha": valores["obs_ha"],
                       "va_rank_prev": valores["rank_prev"],
                       "va_rank_obs": valores["rank_obs"]})
    _escrever_gpkg(caminho, config.camada_subbacias, feicoes, linhas,
                   nome_camada="validacao_subbacias")
    return resumo


def validar(config, registrar=print):
    """As três fases em sequência, para o Console Python ou um script."""
    preparado = preparar(config, registrar=registrar)
    calculado = executar(preparado, registrar=registrar)
    return escrever(calculado, registrar=registrar)

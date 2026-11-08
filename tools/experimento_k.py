# -*- coding: utf-8 -*-
"""Experimento K × K sem Est: K acrescenta informação para localizar a expansão?

Compara, em cada período e em cada recorte de distância ao garimpo de t₀,
a proximidade ao garimpo existente (P, a referência) com escores que a
combinam com o remanescente de cada K:

    S = P × máx(K − O₀, 0)

Candidatos: P; K atual; K sem Est; K = 1 (só o remanescente 1 − O₀, que
separa o efeito de K do simples "ainda não ocupado"). Métricas: captura
nos 1, 5 e 10% da área, AUC (complemento, sem veto) e r_blocos. Incerteza:
bootstrap pareado por sub-bacia (as mesmas réplicas para todos os
candidatos e recortes de um período), com IC de 95% das diferenças.

DECISÕES (aprovadas antes da implementação)
-------------------------------------------
D1  K sem Est é RECALIBRADO: o modelo estático roda com os mesmos insumos
    e parâmetros do K atual, sem Est; os pesos se renormalizam e a curva
    escore → nível é estimada de novo.
D2  O₀ é medido no MESMO SUPORTE da calibração de K: a ocupação na janela
    de ocupação (390 m a 30 m). R = máx(K − Ō₀, 0) é calculado a 30 m e
    levado a 150 m pela média dos pixels de 30 m.
D3  Usa-se o K final (nível calibrado com o garimpo de 2024): o resultado
    é RETROSPECTIVO; o nível de K conhece o fim dos dois períodos.

Outras definições, documentadas na saída:
* P = μ₀ ⊛ núcleo exp(−d/200 m) do plugin (truncado em 3 escalas), μ₀ a
  fração de garimpo do pixel de 150 m; calculado por soma direta, sem o
  ruído da FFT: P = 0 exatamente fora do alcance do núcleo.
* Ordenação por log S = log P + log R (−∞ onde S = 0, sem piso); S linear
  só no r_blocos. Empates rateados nos cortes por área.
* r_blocos: Pearson das somas em blocos de 7 × 7 pixels (1.050 m), a
  definição da validação; nos recortes, só os blocos com pixel do recorte.
* Bootstrap: sub-bacias da camada informada (nível 12), sorteadas com
  reposição; cada bloco de 1 km pertence à sub-bacia do seu centroide.
* Hectares: não testados — não há conversão de S em área ajustada num
  período e aplicada no outro.

COMO USAR (OSGeo4W Shell; QGIS 4: python-qgis; QGIS 3: python-qgis-ltr)
------------------------------------------------------------------------
1. Preencha o CONFIG. O K atual entra pelo REGISTRO dele: o registro dá o
   K e os insumos para gerar o K sem Est.
2. Piloto (um período, poucas réplicas; mede tempo e memória):

       python-qgis "C:\\caminho\\experimento_k.py" piloto

3. Completo:

       python-qgis "C:\\caminho\\experimento_k.py" completo

Saídas na PASTA_SAIDA: experimento_metricas.csv, experimento_diferencas.csv,
experimento_curvas.csv (e PNG), experimento_reproducao.csv,
experimento_verificacoes.txt, experimento_registro.json e experimento.log.
"""

import csv
import datetime as _dt
import inspect
import math
import os
import sys
import time
import tracemalloc

import numpy as np

# =========================================================================
# CONFIG — preencha. Caminhos entre aspas, com r na frente.
# =========================================================================
CONFIG = dict(
    # registro do K atual (o JSON gravado pelo modelo estático ao gerar K)
    REGISTRO_K=r"C:\Users\<voce>\Resultados\PMT_K037_registro.json",
    # K sem Est já gerado; None = gerar agora a partir do registro (D1)
    K_SEM_EST=None,
    # regera o K atual pelo mesmo procedimento e confere que reproduz o
    # arquivo do registro (se não reproduzir, o experimento para)
    CONFERIR_K_ATUAL=True,
    # mapas de garimpo (0/1) do início e do fim de cada período
    PERIODOS=[
        dict(nome="2020-2022",
             MU_INICIAL=r"C:\Users\<voce>\Garimpos\2020.tif",
             MU_FINAL=r"C:\Users\<voce>\Garimpos\2022.tif",
             # resultados.csv da calibração do mesmo período, para
             # reproduzir as linhas de base; None = não conferir
             CALIBRACAO_CSV=None),
        dict(nome="2022-2024",
             MU_INICIAL=r"C:\Users\<voce>\Garimpos\2022.tif",
             MU_FINAL=r"C:\Users\<voce>\Garimpos\2024.tif",
             CALIBRACAO_CSV=None),
    ],
    # os mesmos recortes da calibração: simulação e avaliação
    MASCARA=r"C:\Users\<voce>\dados\mascara_apa_buffer5km.tif",
    MASCARA_AVALIACAO=r"C:\Users\<voce>\dados\mascara_apa.tif",
    # sub-bacias nível 12 (unidades do bootstrap)
    SUBBACIAS=r"C:\Users\<voce>\dados\subbacias.gpkg",
    SUBBACIAS_CAMADA=None,           # None = a primeira camada do arquivo
    RESOLUCAO_M=150.0,
    ESCALA_P_M=200.0,
    LIMIAR_NOVO=0.02,
    BLOCO_M=1000.0,
    # recortes pela distância ao garimpo de t₀ (m): (nome, mínimo, máximo];
    # None = sem limite
    RECORTES=[("geral", None, None), ("ate_1km", None, 1000.0),
              ("1_a_5km", 1000.0, 5000.0), ("mais_5km", 5000.0, None)],
    FRACOES=(0.01, 0.05, 0.10),
    N_REPLICAS=1000,
    PILOTO_REPLICAS=50,
    SEMENTE=2026,
    # fora do OneDrive: o K sem Est tem centenas de MB
    PASTA_SAIDA=r"C:\Users\<voce>\experimento_k",
)

CANDIDATOS = ("P", "K_atual", "K_sem_Est", "K_1")
# diferenças com IC: as três pedidas e duas complementares (K = 1)
COMPARACOES = (("K_atual", "P"), ("K_sem_Est", "P"), ("K_atual", "K_sem_Est"),
               ("K_1", "P"), ("K_atual", "K_1"))
METRICAS_ORDEM = ("captura_1", "captura_5", "captura_10", "auc")
METRICAS = METRICAS_ORDEM + ("r_blocos",)

DECISOES = {
    "D1": "K sem Est recalibrado: mesmos insumos e parâmetros do K atual, "
          "sem Est; pesos renormalizados e curva escore → nível reestimada.",
    "D2": "O₀ na janela de ocupação (390 m a 30 m); R = máx(K − Ō₀, 0) a "
          "30 m, média para 150 m.",
    "D3": "K final (nível calibrado com o garimpo de 2024): resultado "
          "retrospectivo.",
    "hectares": "não testado: não há conversão de S em área ajustada num "
                "período e aplicada no outro sem reajuste.",
}


# =========================================================================
# métricas com pesos (as réplicas do bootstrap só trocam os pesos)
# =========================================================================
class Ordem(object):
    """Ordem decrescente de uma pontuação e os seus grupos de empate.

    Calculada uma vez por candidato e recorte. ``-inf`` é aceito (S = 0) e
    forma um grupo de empate como qualquer outro valor.
    """

    def __init__(self, chave):
        chave = np.asarray(chave, dtype=np.float64)
        if chave.size == 0:
            raise ValueError("pontuação vazia")
        self.idx = np.argsort(-chave, kind="stable")
        ordenada = chave[self.idx]
        novo = np.empty(ordenada.size, dtype=bool)
        novo[0] = True
        novo[1:] = ordenada[1:] != ordenada[:-1]
        self.inicios = np.flatnonzero(novo)

    @property
    def grupos(self):
        return int(self.inicios.size)


def capturas(ordem, peso, observado, fracoes):
    """Fração do garimpo novo nos ``fracoes`` da área de maior risco.

    ``peso`` é a área relativa de cada pixel (1, ou a multiplicidade da
    sub-bacia na réplica). O corte cai na fração exata da área; o grupo de
    empate que o atravessa entra em proporção.
    """
    w = np.asarray(peso, dtype=np.float64)[ordem.idx]
    o = np.asarray(observado, dtype=np.float64)[ordem.idx] * w
    return _capturas_ordenadas(w, o, ordem.inicios, fracoes)


def _capturas_ordenadas(w, o, inicios, fracoes):
    """``capturas`` com peso e garimpo novo já na ordem da pontuação."""
    area = np.add.reduceat(w, inicios)
    novo = np.add.reduceat(o, inicios)
    area_acum = np.cumsum(area)
    novo_acum = np.cumsum(novo)
    total_area, total_novo = area_acum[-1], novo_acum[-1]
    if total_novo <= 0 or total_area <= 0:
        return [float("nan")] * len(fracoes)
    saida = []
    for fracao in fracoes:
        alvo = fracao * total_area
        g = min(int(np.searchsorted(area_acum, alvo, side="left")),
                area.size - 1)
        antes_area = area_acum[g] - area[g]
        antes_novo = novo_acum[g] - novo[g]
        parte = (alvo - antes_area) / area[g] if area[g] > 0 else 0.0
        saida.append(float((antes_novo + novo[g] * min(max(parte, 0.0), 1.0))
                           / total_novo))
    return saida


def auc(ordem, peso, positivo):
    """Mann–Whitney com pesos: P(positivo acima do negativo) + ½ P(empate)."""
    return _auc_ordenada(np.asarray(peso, dtype=np.float64)[ordem.idx],
                         np.asarray(positivo, dtype=bool)[ordem.idx],
                         ordem.inicios)


def _auc_ordenada(w, p, inicios):
    """``auc`` com peso e alvo já na ordem da pontuação."""
    pos = np.add.reduceat(w * p, inicios)
    neg = np.add.reduceat(w * ~p, inicios)
    total_pos, total_neg = pos.sum(), neg.sum()
    if total_pos <= 0 or total_neg <= 0:
        return float("nan")
    abaixo = total_neg - np.cumsum(neg)      # negativos dos grupos seguintes
    return float(np.sum(pos * (abaixo + 0.5 * neg)) / (total_pos * total_neg))


def pearson(a, b, peso=None):
    """Correlação de Pearson, com pesos (multiplicidades) opcionais."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    w = np.ones_like(a) if peso is None else np.asarray(peso, np.float64)
    total = w.sum()
    if a.size < 2 or total <= 0:
        return float("nan")
    da = a - (w * a).sum() / total
    db = b - (w * b).sum() / total
    va, vb = (w * da * da).sum(), (w * db * db).sum()
    if va <= 0 or vb <= 0:
        return float("nan")
    return float((w * da * db).sum() / math.sqrt(va * vb))


def mesma_ordenacao(a, b):
    """``a`` e ``b`` induzem a mesma ordem fraca (com os mesmos empates)?"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    o = np.lexsort((b, a))
    da, db = np.diff(a[o]), np.diff(b[o])
    return bool(np.all(db >= 0) and np.array_equal(da == 0, db == 0))


def media_em_janela(arr, lado):
    """Média em janela ``lado`` × ``lado`` (borda zero), por soma acumulada.

    Exata (sem o ruído da FFT): para um mapa 0/1, é k/lado² com k inteiro.
    """
    arr = np.asarray(arr, dtype=np.float64)
    r = lado // 2
    acum = np.pad(np.pad(arr, r).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    ny, nx = arr.shape
    soma = (acum[lado:lado + ny, lado:lado + nx] - acum[:ny, lado:lado + nx]
            - acum[lado:lado + ny, :nx] + acum[:ny, :nx])
    return soma / float(lado * lado)


def proximidade_exata(mu, nucleo):
    """P = μ ⊛ núcleo por soma direta (float64): zero fora do alcance."""
    mu = np.asarray(mu, dtype=np.float64)
    nucleo = np.asarray(nucleo, dtype=np.float64)
    r = nucleo.shape[0] // 2
    ny, nx = mu.shape
    pad = np.pad(mu, r)
    saida = np.zeros_like(mu)
    for i in range(nucleo.shape[0]):
        for j in range(nucleo.shape[1]):
            if nucleo[i, j]:
                saida += nucleo[i, j] * pad[i:i + ny, j:j + nx]
    return saida


def log_seguro(x):
    """log(x), com −∞ em x = 0 (sem piso)."""
    with np.errstate(divide="ignore"):
        return np.log(np.asarray(x, dtype=np.float64))


def multiplicidades(rng, n_unidades):
    """Uma réplica: quantas vezes cada unidade saiu no sorteio."""
    return np.bincount(rng.integers(0, n_unidades, n_unidades),
                       minlength=n_unidades).astype(np.float64)


def intervalo(valores):
    """IC percentil de 95% e quantas réplicas o estimaram."""
    v = np.asarray(valores, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return float("nan"), float("nan"), 0
    return (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)),
            int(v.size))


# =========================================================================
# leitura (GDAL, pelo plugin)
# =========================================================================
_LOG = []


def _pasta():
    return CONFIG["PASTA_SAIDA"]


def log(mensagem):
    linha = "%s  %s" % (_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        mensagem)
    print(linha)
    _LOG.append(linha)
    try:
        os.makedirs(_pasta(), exist_ok=True)
        with open(os.path.join(_pasta(), "experimento.log"), "a",
                  encoding="utf-8") as arquivo:
            arquivo.write(linha + "\n")
    except OSError:
        pass


def _plugin():
    try:
        from risco_garimpo.core import (estatico, execucao_estatica, kernels,
                                        numerico, raster, registro, validacao)
    except ImportError as erro:
        raise ImportError("o plugin Risco Garimpo não foi encontrado (%s)"
                          % erro)
    return dict(estatico=estatico, execucao_estatica=execucao_estatica,
                kernels=kernels, numerico=numerico, raster=raster,
                registro=registro, validacao=validacao)


def _pastas_de_plugins():
    perfis = os.path.join(os.environ.get("APPDATA", ""), "QGIS")
    try:
        from qgis.core import Qgis
        atual = "QGIS%d" % (Qgis.QGIS_VERSION_INT // 10000)
    except Exception:  # noqa: BLE001 - sem QGIS, procura nos dois perfis
        atual = "QGIS4"
    nomes = [atual] + [p for p in ("QGIS4", "QGIS3") if p != atual]
    return [os.path.join(perfis, p, "profiles", "default", "python",
                         "plugins") for p in nomes]


def grade_fina(grade, fator, linha0, linha1, coluna0, coluna1, margem):
    """Grade de 30 m aninhada na de 150 m, numa janela com margem."""
    from risco_garimpo.core.raster import Grade
    passo = grade.xres / fator
    x0 = grade.xmin + (coluna0 * fator - margem) * passo
    y0 = grade.ymax - (linha0 * fator - margem) * passo
    return Grade(gt=(x0, passo, 0.0, y0, 0.0, -passo), wkt=grade.wkt,
                 nx=(coluna1 - coluna0) * fator + 2 * margem,
                 ny=(linha1 - linha0) * fator + 2 * margem)


def remanescente_grosso(fonte_k, caminho_mu0, grade, fator, lado, janela,
                        faixa=100):
    """R na grade de 150 m: média dos pixels finos de máx(K − Ō₀, 0).

    ``fonte_k`` é o caminho de um K ou um número (1.0 para K = 1).
    ``janela`` = (linha0, linha1, coluna0, coluna1) da grade de 150 m que
    interessa (o universo); fora dela, R fica 0. Lê em faixas de ``faixa``
    linhas, com a margem da janela de ocupação.
    """
    from risco_garimpo.core.raster import ler_alinhado
    linha0, linha1, coluna0, coluna1 = janela
    margem = lado // 2
    R = np.zeros(grade.shape, dtype=np.float64)
    for a in range(linha0, linha1, faixa):
        b = min(a + faixa, linha1)
        fina = grade_fina(grade, fator, a, b, coluna0, coluna1, margem)
        mu = np.clip(ler_alinhado(caminho_mu0, fina, "media"), 0.0, 1.0)
        ocupacao = media_em_janela(mu, lado)[margem:-margem, margem:-margem]
        if isinstance(fonte_k, str):
            K = ler_alinhado(fonte_k, fina, "vizinho")[margem:-margem,
                                                       margem:-margem]
            K = np.clip(np.nan_to_num(K), 0.0, 1.0)
        else:
            K = float(fonte_k)
        resto = np.clip(K - ocupacao, 0.0, None)
        ny, nx = b - a, coluna1 - coluna0
        R[a:b, coluna0:coluna1] = resto.reshape(ny, fator, nx,
                                                fator).mean(axis=(1, 3))
    return R


def rasterizar_subbacias(caminho, camada, grade):
    """Índice da sub-bacia de cada pixel (centro), −1 fora de todas."""
    from osgeo import gdal, ogr, osr
    fonte = ogr.Open(caminho)
    if fonte is None:
        raise IOError("não abri as sub-bacias: %s" % caminho)
    origem = fonte.GetLayerByName(camada) if camada else fonte.GetLayer(0)
    destino = osr.SpatialReference()
    destino.ImportFromWkt(grade.wkt)
    destino.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    srs = origem.GetSpatialRef()
    transformar = None
    if srs is not None and not srs.IsSame(destino):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transformar = osr.CoordinateTransformation(srs, destino)
    memoria = ogr.GetDriverByName("Memory").CreateDataSource("zonas")
    zonas = memoria.CreateLayer("zonas", destino, ogr.wkbMultiPolygon)
    zonas.CreateField(ogr.FieldDefn("rid", ogr.OFTInteger))
    n = 0
    for feicao in origem:
        geom = feicao.GetGeometryRef()
        if geom is None:
            continue
        geom = geom.Clone()
        if transformar is not None:
            geom.Transform(transformar)
        nova = ogr.Feature(zonas.GetLayerDefn())
        nova.SetField("rid", n)
        nova.SetGeometry(geom)
        zonas.CreateFeature(nova)
        n += 1
    alvo = gdal.GetDriverByName("MEM").Create("", grade.nx, grade.ny, 1,
                                              gdal.GDT_Int32)
    alvo.SetGeoTransform(grade.gt)
    alvo.SetProjection(grade.wkt)
    banda = alvo.GetRasterBand(1)
    banda.Fill(-1)
    gdal.RasterizeLayer(alvo, [1], zonas, options=["ATTRIBUTE=rid"])
    indices = banda.ReadAsArray().astype(np.int64)
    return indices, n


def preencher_pelo_vizinho(indices, faltando):
    """Leva os pixels sem sub-bacia à sub-bacia mais próxima (SciPy)."""
    try:
        from scipy.ndimage import distance_transform_edt
    except ImportError:
        return indices, False
    _, (linhas, colunas) = distance_transform_edt(indices < 0,
                                                  return_indices=True)
    cheio = indices.copy()
    cheio[faltando] = indices[linhas[faltando], colunas[faltando]]
    return cheio, True


# =========================================================================
# um período
# =========================================================================
class Periodo(object):
    """Tudo de um período, restrito aos pixels do universo de avaliação."""


def preparar_periodo(p, periodo, grade, caminhos_k, verificacoes):
    raster = p["raster"]
    cfg = CONFIG
    pixel = grade.pixel_m
    area_ha = pixel ** 2 / 1e4
    mu0 = np.clip(np.nan_to_num(raster.ler_alinhado(
        periodo["MU_INICIAL"], grade, "media")), 0.0, 1.0)
    mu1 = np.clip(np.nan_to_num(raster.ler_alinhado(
        periodo["MU_FINAL"], grade, "media")), 0.0, 1.0)
    mascara = raster.ler_alinhado(cfg["MASCARA"], grade, "vizinho") > 0
    universo = mascara & (raster.ler_alinhado(cfg["MASCARA_AVALIACAO"], grade,
                                              "vizinho") > 0)
    if not universo.any():
        raise ValueError("o universo de avaliação está vazio na grade")
    obs = np.where(universo, np.clip(mu1 - mu0, 0.0, None), 0.0)

    v = verificacoes
    v["universo_km2"] = float(universo.sum() * pixel ** 2 / 1e6)
    v["garimpo_t0_ha"] = float(mu0[universo].sum() * area_ha)
    v["garimpo_novo_ha"] = float(obs.sum() * area_ha)
    v["universo_dentro_da_mascara"] = bool(np.all(mascara[universo]))
    for nome, arr in (("mapa inicial", mu0), ("mapa final", mu1)):
        if float(arr.max()) > 1.0001:
            raise ValueError("o %s tem valores acima de 1" % nome)

    # ---- P exato e a referência da validação (FFT, como na calibração)
    nucleo = p["kernels"].kernel_contagio(cfg["ESCALA_P_M"], pixel)
    P = proximidade_exata(mu0, nucleo)
    P_fft = p["numerico"].convolver(mu0, nucleo)
    # o núcleo é quadrado: alcance nos eixos e nas diagonais
    v["P_alcance_eixos_m"] = float((nucleo.shape[0] // 2) * pixel)
    v["P_alcance_diagonais_m"] = v["P_alcance_eixos_m"] * math.sqrt(2.0)
    v["P_fft_ruido_max"] = float(np.abs(P_fft[P == 0]).max()) \
        if (P == 0).any() else 0.0

    # ---- distância ao garimpo de t₀ e recortes
    distancia = p["numerico"].distancia_euclidiana(mu0 > 0, pixel)

    # ---- R de cada K, no suporte da calibração de K (D2)
    linhas = np.flatnonzero(universo.any(axis=1))
    colunas = np.flatnonzero(universo.any(axis=0))
    janela = (int(linhas[0]), int(linhas[-1]) + 1, int(colunas[0]),
              int(colunas[-1]) + 1)
    fino = p["raster"].grade_de_raster(caminhos_k["K_atual"])
    fator = int(round(grade.xres / fino.xres))
    if abs(grade.xres / fino.xres - fator) > 1e-6 or fator < 1:
        raise ValueError("a grade de %.0f m não é múltipla da de K (%.0f m)"
                         % (grade.xres, fino.xres))
    lado = p["estatico"].lado_da_janela(fino.xres, CONFIG_JANELA[0])
    R = {}
    for nome, fonte in (("K_atual", caminhos_k["K_atual"]),
                        ("K_sem_Est", caminhos_k["K_sem_Est"]),
                        ("K_1", 1.0)):
        inicio = time.time()
        R[nome] = remanescente_grosso(fonte, periodo["MU_INICIAL"], grade,
                                      fator, lado, janela)
        log("   remanescente %s: %.0f s" % (nome, time.time() - inicio))
    v["janela_ocupacao_px"] = int(lado)
    v["fator_fino"] = int(fator)

    # ---- tudo restrito ao universo
    u = np.flatnonzero(universo.ravel())
    per = Periodo()
    per.nome = periodo["nome"]
    per.grade = grade
    per.u = u
    per.area_ha = area_ha
    per.obs = obs.ravel()[u]
    per.positivo = per.obs > cfg["LIMIAR_NOVO"]
    per.distancia = distancia.ravel()[u]
    per.P = P.ravel()[u]
    per.S = {"P": per.P}
    per.chave = {"P": log_seguro(per.P)}
    for nome, arr in R.items():
        per.S[nome] = per.P * arr.ravel()[u]
        per.chave[nome] = per.chave["P"] + log_seguro(arr.ravel()[u])
    for nome in CANDIDATOS[1:]:
        r = R[nome].ravel()[u]
        v["R_zero_%s" % nome] = float((r <= 0).mean())
        s = per.S[nome]
        positivos = s[s > 0]
        v["S_min_positivo_%s" % nome] = (float(positivos.min())
                                         if positivos.size else None)
        v["S_subfluxo_%s" % nome] = int(np.sum((s == 0)
                                               & np.isfinite(per.chave[nome])))
    v["P_positivo_frac"] = float((per.P > 0).mean())

    # ---- unidades do bootstrap e blocos
    indices, n_feicoes = rasterizar_subbacias(cfg["SUBBACIAS"],
                                              cfg["SUBBACIAS_CAMADA"], grade)
    faltando = universo & (indices < 0)
    v["pixels_sem_subbacia"] = int(faltando.sum())
    if faltando.any():
        indices, preenchido = preencher_pelo_vizinho(indices, faltando)
        v["sem_subbacia_preenchidos_pelo_vizinho"] = preenchido
    bacia = indices.ravel()[u]
    if (bacia < 0).any():                    # sem SciPy: uma unidade a mais
        bacia = np.where(bacia < 0, n_feicoes, bacia)
    presentes, per.unidade = np.unique(bacia, return_inverse=True)
    per.n_unidades = int(presentes.size)
    contagem = np.bincount(per.unidade)
    v["subbacias_na_camada"] = int(n_feicoes)
    v["subbacias_no_universo"] = per.n_unidades
    v["subbacia_mediana_km2"] = float(np.median(contagem) * pixel ** 2 / 1e6)

    fator_bloco = max(int(round(cfg["BLOCO_M"] / pixel)), 1)
    per.fator_bloco = fator_bloco
    ny, nx = grade.shape
    nbx = -(-nx // fator_bloco)
    linha_px, coluna_px = np.divmod(u, nx)
    per.bloco = (linha_px // fator_bloco) * nbx + coluna_px // fator_bloco
    # unidade de cada bloco: a da sub-bacia no centroide do bloco; se o
    # centroide não for do universo, a mais frequente entre seus pixels
    blocos = np.unique(per.bloco)
    centro_l = (blocos // nbx) * fator_bloco + fator_bloco // 2
    centro_c = (blocos % nbx) * fator_bloco + fator_bloco // 2
    dentro = (centro_l < ny) & (centro_c < nx)
    pos = np.full(blocos.size, -1, dtype=np.int64)
    plano = np.full(ny * nx, -1, dtype=np.int64)
    plano[u] = per.unidade
    pos[dentro] = plano[centro_l[dentro] * nx + centro_c[dentro]]
    unidade_do_bloco = dict(zip(blocos.tolist(), pos.tolist()))
    for b in blocos[pos < 0].tolist():
        unidades = per.unidade[per.bloco == b]
        unidade_do_bloco[b] = int(np.bincount(unidades).argmax())
    per.unidade_do_bloco = unidade_do_bloco
    v["blocos_no_universo"] = int(blocos.size)
    v["blocos_pelo_centroide"] = int(np.sum(pos >= 0))

    per.extra = dict(mu0=mu0, universo=universo, P_fft=P_fft)
    return per


CONFIG_JANELA = [380.0]          # janela de ocupação do K (lida do registro)


def recortes(per):
    saida = []
    for nome, minimo, maximo in CONFIG["RECORTES"]:
        sel = np.ones(per.u.size, dtype=bool)
        if minimo is not None:
            sel &= per.distancia > minimo
        if maximo is not None:
            sel &= per.distancia <= maximo
        saida.append((nome, sel))
    return saida


def _blocos(per, sel):
    """Blocos do recorte: índice local de cada pixel e a unidade do bloco."""
    blocos, local = np.unique(per.bloco[sel], return_inverse=True)
    unidade = np.array([per.unidade_do_bloco[b] for b in blocos.tolist()],
                       dtype=np.int64)
    return local, unidade


class Recorte(object):
    """Um recorte pronto para o bootstrap: tudo o que não muda entre réplicas.

    A ordem de cada candidato, o garimpo novo, o alvo e a unidade de cada
    pixel já ficam na ordem da pontuação, e as somas por bloco já ficam
    feitas; cada réplica só troca os pesos (as multiplicidades).
    """

    def __init__(self, per, sel):
        self.obs = per.obs[sel]
        positivo = per.positivo[sel]
        unidade = per.unidade[sel]
        local, self.unidade_bloco = _blocos(per, sel)
        n_blocos = self.unidade_bloco.size
        self.obs_bloco = np.bincount(local, weights=self.obs,
                                     minlength=n_blocos)
        self.ordens, self.ordenado, self.s_bloco = {}, {}, {}
        for nome in CANDIDATOS:
            ordem = Ordem(per.chave[nome][sel])
            self.ordens[nome] = ordem
            self.ordenado[nome] = (unidade[ordem.idx], self.obs[ordem.idx],
                                   positivo[ordem.idx])
            self.s_bloco[nome] = np.bincount(local, weights=per.S[nome][sel],
                                             minlength=n_blocos)

    def avaliar(self, multiplicidade=None):
        """Métricas de cada candidato; ``multiplicidade`` por unidade."""
        peso_bloco = (None if multiplicidade is None
                      else multiplicidade[self.unidade_bloco])
        saida = {}
        for nome in CANDIDATOS:
            unidade, obs, positivo = self.ordenado[nome]
            inicios = self.ordens[nome].inicios
            w = (np.ones(obs.size) if multiplicidade is None
                 else multiplicidade[unidade])
            m = dict(zip(("captura_1", "captura_5", "captura_10"),
                         _capturas_ordenadas(w, obs * w, inicios,
                                             CONFIG["FRACOES"])))
            m["auc"] = _auc_ordenada(w, positivo, inicios)
            m["r_blocos"] = pearson(self.s_bloco[nome], self.obs_bloco,
                                    peso_bloco)
            saida[nome] = m
        return saida


def descrever_recorte(per, sel):
    obs = per.obs[sel]
    unidades = np.unique(per.unidade[sel][obs > 0])
    local, unidade_bloco = _blocos(per, sel)
    obs_bloco = np.bincount(local, weights=obs, minlength=unidade_bloco.size)
    return {"pixels": int(sel.sum()),
            "area_km2": float(sel.sum() * per.area_ha / 100.0),
            "convertida_ha": float(obs.sum() * per.area_ha),
            "pixels_com_evento": int(np.sum(per.positivo[sel])),
            "unidades": int(np.unique(per.unidade[sel]).size),
            "unidades_com_evento": int(unidades.size),
            "blocos": int(unidade_bloco.size),
            "blocos_com_evento": int(np.sum(obs_bloco > 0)),
            "P_positivo_frac": float(np.mean(per.P[sel] > 0))
            if sel.any() else 0.0}


def curva(ordem, obs, n=101):
    fracoes = np.linspace(0.0, 1.0, n)
    return fracoes, capturas(ordem, np.ones(obs.size), obs, fracoes)


def rodar_periodo(per, n_replicas, tabelas):
    """Métricas, bootstrap e diferenças de um período."""
    for nome_recorte, sel in recortes(per):
        descricao = descrever_recorte(per, sel)
        base = dict(periodo=per.nome, recorte=nome_recorte, **descricao)
        if not sel.any() or not np.any(per.P[sel] > 0):
            motivo = ("recorte vazio" if not sel.any() else
                      "P = 0 em todo o recorte: S = 0 para todos os "
                      "candidatos (alcance do núcleo de P)")
            log("   %-9s redundante: %s" % (nome_recorte, motivo))
            for nome in CANDIDATOS:
                tabelas["metricas"].append(dict(base, candidato=nome,
                                                situacao="redundante: "
                                                + motivo))
            continue
        if per.obs[sel].sum() <= 0:
            log("   %-9s sem garimpo novo: métricas indefinidas"
                % nome_recorte)
            for nome in CANDIDATOS:
                tabelas["metricas"].append(dict(
                    base, candidato=nome,
                    situacao="indefinido: sem garimpo novo"))
            continue

        recorte = Recorte(per, sel)
        ordens = recorte.ordens
        pontual = recorte.avaliar()
        # pares com a mesma ordenação têm as mesmas métricas de ordem por
        # construção: registra-se e dispensa-se o bootstrap delas
        equivalentes = {(a, b) for a, b in COMPARACOES
                        if mesma_ordenacao(per.S[a][sel], per.S[b][sel])}
        for nome in CANDIDATOS:
            linha = dict(base, candidato=nome, situacao="ok",
                         grupos_de_empate=ordens[nome].grupos)
            linha.update(pontual[nome])
            tabelas["metricas"].append(linha)
            fracoes, capt = curva(ordens[nome], per.obs[sel])
            for f, c in zip(fracoes, capt):
                tabelas["curvas"].append(dict(periodo=per.nome,
                                              recorte=nome_recorte,
                                              candidato=nome, fracao_area=f,
                                              captura=c))

        # bootstrap pareado: as mesmas réplicas para todos os candidatos
        inicio = time.time()
        replicas = {nome: {m: [] for m in METRICAS} for nome in CANDIDATOS}
        for multiplicidade in tabelas["sorteios"][per.nome]:
            r = recorte.avaliar(multiplicidade)
            for nome in CANDIDATOS:
                for m in METRICAS:
                    replicas[nome][m].append(r[nome][m])
        log("   %-9s %d pixels, %.0f ha novos, %d sub-bacias com evento; "
            "bootstrap %.0f s" % (nome_recorte, descricao["pixels"],
                                  descricao["convertida_ha"],
                                  descricao["unidades_com_evento"],
                                  time.time() - inicio))
        for a, b in COMPARACOES:
            for m in METRICAS:
                estimativa = pontual[a][m] - pontual[b][m]
                if (a, b) in equivalentes and m in METRICAS_ORDEM:
                    tabelas["diferencas"].append(dict(
                        periodo=per.nome, recorte=nome_recorte,
                        comparacao="%s - %s" % (a, b), metrica=m,
                        estimativa=0.0, ic_inf=0.0, ic_sup=0.0,
                        replicas_validas=0,
                        situacao="redundante: mesma ordenação"))
                    continue
                dif = (np.asarray(replicas[a][m])
                       - np.asarray(replicas[b][m]))
                inf, sup, validas = intervalo(dif)
                situacao = "ok"
                if validas < 0.95 * n_replicas:
                    situacao = ("só %d de %d réplicas estimaram a métrica"
                                % (validas, n_replicas))
                tabelas["diferencas"].append(dict(
                    periodo=per.nome, recorte=nome_recorte,
                    comparacao="%s - %s" % (a, b), metrica=m,
                    estimativa=estimativa, ic_inf=inf, ic_sup=sup,
                    replicas_validas=validas, situacao=situacao))


# =========================================================================
# reprodução da validação (linhas de base da calibração)
# =========================================================================
def reproduzir(p, per, caminho_k, caminho_csv, tabelas, verificacoes):
    """Recalcula as linhas de base como a calibração e compara com o CSV."""
    grade = per.grade
    cfg = CONFIG
    mu0 = per.extra["mu0"]
    universo = per.extra["universo"]
    K = np.nan_to_num(p["raster"].ler_alinhado(caminho_k, grade, "bilinear"))
    obs = np.zeros(grade.shape)
    obs.ravel()[per.u] = per.obs
    folga = np.clip(K - mu0, 0.0, None)
    linhas = {"base_proximidade_%dm" % cfg["ESCALA_P_M"]: per.extra["P_fft"],
              "base_proximidade_x_remanescente_%dm" % cfg["ESCALA_P_M"]:
                  per.extra["P_fft"] * folga}
    referencia = {}
    if caminho_csv and os.path.isfile(caminho_csv):
        with open(caminho_csv, encoding="utf-8") as arquivo:
            for linha in csv.DictReader(arquivo, delimiter=";"):
                referencia[linha["id"]] = linha
    for nome, pontuacao in linhas.items():
        m = p["validacao"].avaliar(pontuacao, obs, universo, grade.pixel_m,
                                   cfg["LIMIAR_NOVO"], cfg["FRACOES"],
                                   cfg["BLOCO_M"], magnitude=False)
        for metrica, chave in (("auc", "auc"), ("captura_1", "captura_1"),
                               ("captura_5", "captura_5"),
                               ("captura_10", "captura_10"),
                               ("r_blocos", "r_blocos")):
            ref = referencia.get(nome, {}).get(chave, "")
            valor = float(m[metrica])
            tabelas["reproducao"].append(dict(
                periodo=per.nome, linha=nome, metrica=metrica,
                reproduzido=valor,
                referencia=float(ref) if ref not in ("", None) else "",
                diferenca=(valor - float(ref)) if ref not in ("", None)
                else ""))
    # a definição antiga de r_blocos inclui os blocos fora do universo
    verificacoes["r_blocos_definicao"] = (
        "Pearson das somas em blocos de %d × %d pixels; na reprodução, "
        "como na calibração, sobre a grade inteira (blocos fora do universo "
        "entram como pares (0, 0)); no experimento, só os blocos com pixel "
        "do recorte" % (per.fator_bloco, per.fator_bloco))


# =========================================================================
# K sem Est (D1) e conferência do K atual
# =========================================================================
def gerar_k(p, registro_k, pasta, prefixo, sem_est):
    """Roda o modelo estático com a configuração do registro."""
    ee = p["execucao_estatica"]
    aceitos = set(inspect.signature(ee.ConfiguracaoEstatica).parameters)
    dados = {k: v for k, v in registro_k["configuracao"].items()
             if k in aceitos}
    dados.update(pasta_saida=pasta, prefixo=prefixo, camada_subbacias=None,
                 salvar_escore=False, salvar_remanescente=False,
                 carregar=False)
    if sem_est:
        dados["caminho_est"] = None
    config = ee.ConfiguracaoEstatica(**dados)
    os.makedirs(pasta, exist_ok=True)
    calculado = ee.executar(ee.preparar(config, registrar=log),
                            registrar=log)
    ee.escrever(calculado, registrar=log)
    return ee.caminho_de_saida(config, "K")


def _receita(registro_k):
    """Configuração do modelo estático, sem o que não muda o K."""
    fora = ("caminho_est", "pasta_saida", "prefixo", "salvar_escore",
            "salvar_remanescente", "salvar_tabela", "carregar",
            "camada_subbacias", "campo_id_subbacia", "caminho_mu_anterior")
    return {k: v for k, v in registro_k["configuracao"].items()
            if k not in fora}


def k_sem_est(p, registro_k, prefixo):
    """Gera o K sem Est (D1), ou reaproveita o da rodada anterior.

    Reaproveita só se o registro dele tiver a mesma receita do K atual
    (insumos e parâmetros), sem Est e com a mesma identidade dos insumos.
    """
    pasta = os.path.join(_pasta(), "k_sem_est")
    caminho_registro = os.path.join(pasta, prefixo + "_semEst_registro.json")
    if os.path.isfile(caminho_registro):
        anterior = p["registro"].ler(caminho_registro)
        fora = ("Est", "mu_anterior", "subbacias")
        mesmos_insumos = (
            {(i["papel"], i.get("sha256")) for i in anterior["insumos"]
             if i["papel"] not in fora}
            == {(i["papel"], i.get("sha256")) for i in registro_k["insumos"]
                if i["papel"] not in fora})
        if (_receita(anterior) == _receita(registro_k) and mesmos_insumos
                and os.path.isfile(anterior["saidas"]["K"])):
            log("K sem Est reaproveitado de %s" % caminho_registro)
            return anterior["saidas"]["K"]
    log("gerando o K sem Est (D1: recalibrado)")
    return gerar_k(p, registro_k, pasta, prefixo + "_semEst", sem_est=True)


def conferir_k_atual(p, registro_k, caminho_k, prefixo):
    """Regera o K atual e confere que reproduz o arquivo do registro.

    O resultado fica em k_atual_conferencia/conferencia.json, com o SHA-256
    do K conferido: a rodada seguinte não repete a conferência.
    """
    pasta = os.path.join(_pasta(), "k_atual_conferencia")
    marca = os.path.join(pasta, "conferencia.json")
    identidade = p["registro"].sha256(caminho_k)
    if os.path.isfile(marca):
        anterior = p["registro"].ler(marca)
        if anterior.get("sha256_K") == identidade:
            log("K atual já conferido (diferença %.3g)"
                % anterior["diferenca"])
            return anterior["diferenca"]
    log("conferindo o K atual: gerando de novo pelo mesmo procedimento")
    regerado = gerar_k(p, registro_k, pasta, prefixo + "_conferencia",
                       sem_est=False)
    diferenca = comparar_rasters(caminho_k, regerado)
    log("maior diferença entre o K atual e o regerado: %.3g" % diferenca)
    if diferenca > 1e-5:
        raise SystemExit("o K regerado não reproduz o K atual (diferença "
                         "%.3g): os insumos do registro mudaram? Pare e "
                         "confira antes do experimento." % diferenca)
    try:
        os.remove(regerado)                  # centenas de MB, já conferido
    except OSError:
        pass
    p["registro"].gravar(marca, {"sha256_K": identidade,
                                 "diferenca": diferenca, "K": caminho_k})
    return diferenca


def comparar_rasters(a, b, linhas=512):
    """Maior diferença absoluta entre dois rasters da mesma grade."""
    from osgeo import gdal
    da, db = gdal.Open(a), gdal.Open(b)
    if (da.RasterXSize, da.RasterYSize) != (db.RasterXSize, db.RasterYSize):
        return float("inf")
    ba, bb = da.GetRasterBand(1), db.GetRasterBand(1)
    maior = 0.0
    for y in range(0, da.RasterYSize, linhas):
        n = min(linhas, da.RasterYSize - y)
        x = ba.ReadAsArray(0, y, da.RasterXSize, n).astype(np.float64)
        z = bb.ReadAsArray(0, y, da.RasterXSize, n).astype(np.float64)
        maior = max(maior, float(np.nanmax(np.abs(x - z))))
    return maior


# =========================================================================
# saída
# =========================================================================
def _gravar_csv(nome, linhas):
    if not linhas:
        return None
    colunas = []
    for linha in linhas:
        for chave in linha:
            if chave not in colunas:
                colunas.append(chave)
    caminho = os.path.join(_pasta(), nome)
    with open(caminho, "w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, colunas, delimiter=";")
        escritor.writeheader()
        for linha in linhas:
            escritor.writerow({k: ("%.6g" % v if isinstance(v, float) else v)
                               for k, v in linha.items()})
    return caminho


def _gravar_figuras(curvas):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001 - a figura é acessória
        log("AVISO: matplotlib indisponível; as curvas estão no CSV")
        return []
    grupos = {}
    for linha in curvas:
        grupos.setdefault((linha["periodo"], linha["recorte"]), {}) \
            .setdefault(linha["candidato"], []).append(
                (linha["fracao_area"], linha["captura"]))
    gravados = []
    for (periodo, recorte), por_candidato in grupos.items():
        figura, eixo = plt.subplots(figsize=(5.5, 4.5))
        for nome, pontos in por_candidato.items():
            x, y = zip(*pontos)
            eixo.plot(x, y, label=nome)
        eixo.plot([0, 1], [0, 1], color="0.6", lw=0.8, ls="--",
                  label="acaso")
        eixo.set_xlabel("fração da área (maior risco primeiro)")
        eixo.set_ylabel("fração do garimpo novo capturada")
        eixo.set_title("%s — %s" % (periodo, recorte))
        eixo.legend(fontsize=8)
        caminho = os.path.join(_pasta(), "experimento_curvas_%s_%s.png"
                               % (periodo, recorte))
        figura.tight_layout()
        figura.savefig(caminho, dpi=150)
        plt.close(figura)
        gravados.append(caminho)
    return gravados


def _texto_verificacoes(verificacoes):
    linhas = []
    for periodo, itens in verificacoes.items():
        linhas.append("== %s" % periodo)
        for chave, valor in itens.items():
            if isinstance(valor, float):
                valor = "%.6g" % valor
            linhas.append("   %-36s %s" % (chave, valor))
    return "\n".join(linhas) + "\n"


# =========================================================================
# orquestração
# =========================================================================
def principal(modo="completo"):
    if modo not in ("piloto", "completo"):
        raise SystemExit("modo desconhecido: %r (use piloto ou completo)"
                         % modo)
    for pasta in reversed(_pastas_de_plugins()):
        if os.path.isdir(pasta) and pasta not in sys.path:
            sys.path.insert(0, pasta)
    p = _plugin()
    tracemalloc.start()
    inicio = time.time()
    os.makedirs(_pasta(), exist_ok=True)
    log("=== experimento K × K sem Est (%s) ===" % modo)
    log("plugin Risco Garimpo %s" % p["registro"].versao_plugin())

    registro_k = p["registro"].ler(CONFIG["REGISTRO_K"])
    caminho_k = registro_k["saidas"]["K"]
    CONFIG_JANELA[0] = float(registro_k["configuracao"].get("janela_m", 380.0))
    log("K atual: %s (plugin %s)" % (caminho_k,
                                     registro_k.get("versao_plugin")))
    prefixo = registro_k["configuracao"].get("prefixo", "K")
    conferencia = None
    if CONFIG["CONFERIR_K_ATUAL"]:
        conferencia = conferir_k_atual(p, registro_k, caminho_k, prefixo)
    caminho_sem_est = CONFIG["K_SEM_EST"] or k_sem_est(p, registro_k,
                                                        prefixo)
    caminhos_k = {"K_atual": caminho_k, "K_sem_Est": caminho_sem_est}

    grade = p["raster"].grade_reamostrada(
        p["raster"].grade_de_raster(caminho_k), CONFIG["RESOLUCAO_M"])
    log("grade: %s" % grade.descricao())
    n_replicas = (CONFIG["PILOTO_REPLICAS"] if modo == "piloto"
                  else CONFIG["N_REPLICAS"])
    periodos = CONFIG["PERIODOS"][:1] if modo == "piloto" \
        else CONFIG["PERIODOS"]
    rng = np.random.default_rng(CONFIG["SEMENTE"])
    tabelas = dict(metricas=[], diferencas=[], curvas=[], reproducao=[],
                   sorteios={})
    verificacoes = {}
    for periodo in periodos:
        log("--- período %s" % periodo["nome"])
        v = verificacoes.setdefault(periodo["nome"], {})
        t0 = time.time()
        per = preparar_periodo(p, periodo, grade, caminhos_k, v)
        log("   preparado em %.0f s; %d sub-bacias no universo (mediana "
            "%.0f km²)" % (time.time() - t0, per.n_unidades,
                           v["subbacia_mediana_km2"]))
        reproduzir(p, per, caminho_k, periodo.get("CALIBRACAO_CSV"),
                   tabelas, v)
        tabelas["sorteios"][per.nome] = [
            multiplicidades(rng, per.n_unidades) for _ in range(n_replicas)]
        rodar_periodo(per, n_replicas, tabelas)
        v["memoria_pico_python_gb"] = round(
            tracemalloc.get_traced_memory()[1] / 1e9, 2)
        v["segundos"] = round(time.time() - t0, 1)
        del per

    gravados = [c for c in (
        _gravar_csv("experimento_metricas.csv", tabelas["metricas"]),
        _gravar_csv("experimento_diferencas.csv", tabelas["diferencas"]),
        _gravar_csv("experimento_curvas.csv", tabelas["curvas"]),
        _gravar_csv("experimento_reproducao.csv", tabelas["reproducao"]))
        if c]
    gravados += _gravar_figuras(tabelas["curvas"])
    caminho = os.path.join(_pasta(), "experimento_verificacoes.txt")
    with open(caminho, "w", encoding="utf-8") as arquivo:
        arquivo.write(_texto_verificacoes(verificacoes))
    gravados.append(caminho)

    registro = p["registro"]
    insumos = [registro.descrever_arquivo(c, papel) for c, papel in (
        (CONFIG["REGISTRO_K"], "registro_K"), (caminho_k, "K_atual"),
        (caminho_sem_est, "K_sem_Est"), (CONFIG["MASCARA"], "mascara"),
        (CONFIG["MASCARA_AVALIACAO"], "mascara_avaliacao"),
        (CONFIG["SUBBACIAS"], "subbacias"))]
    for periodo in periodos:
        for chave in ("MU_INICIAL", "MU_FINAL"):
            insumos.append(registro.descrever_arquivo(
                periodo[chave], "%s %s" % (periodo["nome"], chave)))
    dados = {"ferramenta": "experimento_k", "modo": modo,
             "versao_plugin": registro.versao_plugin(),
             "ambiente": registro.ambiente(), "decisoes": DECISOES,
             "configuracao": {k: v for k, v in CONFIG.items()},
             "replicas": n_replicas, "conferencia_k_atual": conferencia,
             "verificacoes": verificacoes, "insumos": insumos,
             "saidas": gravados,
             "segundos": round(time.time() - inicio, 1)}
    caminho = registro.gravar(os.path.join(_pasta(),
                                           "experimento_registro.json"),
                              dados)
    log("gravado: %s" % caminho)
    log("=== fim em %.0f min; pico de memória (Python) %.2f GB ==="
        % ((time.time() - inicio) / 60.0,
           tracemalloc.get_traced_memory()[1] / 1e9))
    return dados


if __name__ == "__main__":
    principal(sys.argv[1] if len(sys.argv) > 1 else "completo")

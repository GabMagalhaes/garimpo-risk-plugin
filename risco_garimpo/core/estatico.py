# -*- coding: utf-8 -*-
"""Modelo estático: potencial aurífero físico, expresso como K.

O que este módulo entrega
-------------------------
Um raster **K(x) em [0, 1]**: a proporção da área do pixel que se espera
explorada **no máximo**, tudo mais constante. É a capacidade de suporte do
modelo dinâmico, e é também o resultado final do modelo estático — não um
índice adimensional de favorabilidade.

K tem duas propriedades independentes, e cada uma precisa do seu método:

**Ordenação** — quem é mais favorável que quem. Vem da combinação fuzzy
gamma ponderada das variáveis de terreno (IBx/PGgeom, Est, litologia). É o
que curvas de captura e AUC medem.

**Nível** — quanto, de fato, cabe no terreno mais favorável. Captura e AUC
são cegas a isso: multiplicar K inteiro por 0,5 não muda nenhuma delas. Aqui
o nível é **estimado**, por substituição espaço-tempo: para cada classe de
terreno, toma-se um quantil alto da ocupação local observada entre os pixels
de exposição mais longa ao processo. É a saturação que aquele terreno
demonstrou suportar.

Sobre a fronteira "K exclui μ"
------------------------------
Ela continua valendo onde importa: **μ não entra na ordenação**, nem como
variável nem pixel a pixel. O que μ faz aqui é calibrar uma curva global
unidimensional escore → nível, do mesmo tipo da transformação IBx → PGgeom.
A dependência é atenuada, não eliminada, e a validação honesta é calibrar
num recorte temporal e verificar no seguinte.

O quantil de ocupação observada é **limite inferior** do teto: nada garante
que algum lugar já tenha saturado. K estimado assim erra para baixo, o que é
preferível a um teto decretado.
"""

import numpy as np

from .numerico import convolver

PISO_PADRAO = 1e-3


# ===================================================================== ordem
def normalizar(arr, minimo=None, maximo=None, piso=0.0):
    """Leva a variável a [piso, 1] por reescala linear.

    `piso` existe por causa do veto: no produto ponderado, 0**w = 0 para
    qualquer peso, de modo que uma única variável zerada anula o pixel. Um
    piso pequeno preserva a ordem e evita que a variável mais fraca do
    modelo decida sozinha onde não pode haver garimpo.
    """
    arr = np.asarray(arr, dtype=np.float64)
    lo = float(np.nanmin(arr)) if minimo is None else float(minimo)
    hi = float(np.nanmax(arr)) if maximo is None else float(maximo)
    if not hi > lo:
        return np.full(arr.shape, max(piso, 0.0), dtype=np.float32)
    x = (arr - lo) / (hi - lo)
    x = np.clip(np.nan_to_num(x, nan=0.0), 0.0, 1.0)
    return (piso + (1.0 - piso) * x).astype(np.float32)


def fuzzy_gamma(camadas, pesos, gamma):
    """γ = S^gamma · P^(1−gamma), com AHP nos expoentes.

    P é o produto ponderado (conjuntivo, pessimista) e S a soma algébrica
    ponderada (disjuntiva, otimista). gamma baixo aproxima do produto.
    """
    camadas = [np.asarray(c, dtype=np.float64) for c in camadas]
    pesos = np.asarray(pesos, dtype=np.float64)
    if len(camadas) != pesos.size:
        raise ValueError("número de camadas e de pesos não confere")
    if not np.all(pesos >= 0) or pesos.sum() <= 0:
        raise ValueError("pesos precisam ser não negativos e somar mais que 0")
    pesos = pesos / pesos.sum()
    if not 0.0 <= gamma <= 1.0:
        raise ValueError("gamma precisa estar em [0, 1]")

    produto = np.ones_like(camadas[0])
    complementar = np.ones_like(camadas[0])
    for camada, peso in zip(camadas, pesos):
        c = np.clip(camada, 0.0, 1.0)
        produto *= np.power(c, peso)
        complementar *= np.power(1.0 - c, peso)
    soma = 1.0 - complementar
    escore = np.power(soma, gamma) * np.power(produto, 1.0 - gamma)
    return np.clip(escore, 0.0, 1.0).astype(np.float32)


def diagnosticar_veto(camadas, nomes):
    """Quanto da área cada variável zeraria sozinha no produto ponderado."""
    relatorio = []
    for camada, nome in zip(camadas, nomes):
        c = np.asarray(camada)
        zeros = np.count_nonzero(c <= 0.0)
        relatorio.append((nome, zeros, zeros / max(c.size, 1)))
    return relatorio


# ====================================================================== nível
def ocupacao_local(mu, pixel_m, janela_m=380.0):
    """Fração local ocupada, em janela da escala de contágio.

    O pixel isolado não mede saturação; a vizinhança mede. A janela padrão é
    a escala de contágio estimada para a província.
    """
    mu = np.asarray(mu, dtype=np.float32)
    raio = max(1, int(round(janela_m / (2.0 * float(pixel_m)))))
    lado = 2 * raio + 1
    nucleo = np.ones((lado, lado), dtype=np.float32)
    nucleo /= nucleo.sum()
    return np.clip(convolver(mu, nucleo), 0.0, 1.0).astype(np.float32)


def isotonica(y, peso=None):
    """Ajuste monótono não decrescente por pool-adjacent-violators."""
    y = np.asarray(y, dtype=np.float64)
    w = np.ones_like(y) if peso is None else np.asarray(peso, dtype=np.float64)
    valores, pesos, tamanhos = [], [], []
    for valor, p in zip(y, w):
        valores.append(float(valor))
        pesos.append(float(p))
        tamanhos.append(1)
        while len(valores) > 1 and valores[-2] > valores[-1]:
            v2, p2, t2 = valores.pop(), pesos.pop(), tamanhos.pop()
            v1, p1, t1 = valores.pop(), pesos.pop(), tamanhos.pop()
            p_total = p1 + p2
            media = ((v1 * p1 + v2 * p2) / p_total) if p_total > 0 \
                else 0.5 * (v1 + v2)
            valores.append(media)
            pesos.append(p_total)
            tamanhos.append(t1 + t2)
    saida = np.empty_like(y)
    i = 0
    for valor, tamanho in zip(valores, tamanhos):
        saida[i:i + tamanho] = valor
        i += tamanho
    return saida


def saturacao_por_faixa(escore, ocupacao, maduro, n_faixas=51,
                        quantil=0.99, minimo_por_faixa=200):
    """Quantil alto da ocupação local, por faixa de escore, entre maduros.

    Devolve (centros, saturação, contagem). Faixas com amostra insuficiente
    saem como NaN — o ajuste monótono depois as ignora.
    """
    escore = np.asarray(escore, dtype=np.float64).ravel()
    ocupacao = np.asarray(ocupacao, dtype=np.float64).ravel()
    maduro = np.asarray(maduro, dtype=bool).ravel()
    if escore.size != ocupacao.size or escore.size != maduro.size:
        raise ValueError("escore, ocupação e maturidade com tamanhos diferentes")
    if not maduro.any():
        raise ValueError("nenhum pixel maduro — sem exposição não há saturação "
                         "observável")
    if not 0.0 < quantil <= 1.0:
        raise ValueError("quantil precisa estar em (0, 1]")

    e = escore[maduro]
    o = ocupacao[maduro]
    bordas = np.linspace(0.0, 1.0, n_faixas + 1)
    centros = 0.5 * (bordas[:-1] + bordas[1:])
    indice = np.clip(np.digitize(e, bordas) - 1, 0, n_faixas - 1)

    saturacao = np.full(n_faixas, np.nan)
    contagem = np.bincount(indice, minlength=n_faixas)
    ordem = np.argsort(indice, kind="stable")
    indice_ord = indice[ordem]
    o_ord = o[ordem]
    inicios = np.searchsorted(indice_ord, np.arange(n_faixas), side="left")
    fins = np.searchsorted(indice_ord, np.arange(n_faixas), side="right")
    for i in range(n_faixas):
        if contagem[i] < minimo_por_faixa:
            continue
        saturacao[i] = float(np.quantile(o_ord[inicios[i]:fins[i]], quantil))
    return centros, saturacao, contagem


def curva_de_nivel(centros, saturacao, contagem):
    """Curva monótona escore → nível de saturação, a partir das faixas."""
    ok = np.isfinite(saturacao)
    if ok.sum() < 2:
        raise ValueError("faixas de escore insuficientes para estimar o nível "
                         "— reduza n_faixas ou minimo_por_faixa")
    x = np.asarray(centros, dtype=np.float64)[ok]
    y = isotonica(np.asarray(saturacao, dtype=np.float64)[ok],
                  np.asarray(contagem, dtype=np.float64)[ok])
    return x, np.clip(y, 0.0, 1.0)


def aplicar_nivel(escore, x, y):
    """K = curva(escore), com extrapolação constante nas pontas."""
    return np.clip(np.interp(np.asarray(escore, dtype=np.float64), x, y,
                             left=y[0], right=y[-1]),
                   0.0, 1.0).astype(np.float32)


# ================================================================ orquestração
class ResultadoEstatico(object):
    """K e o rastro de como ele foi produzido."""

    def __init__(self, K, escore, centros, saturacao, contagem, curva,
                 veto, teto_estimado, por_estrato=None):
        self.K = K
        self.escore = escore
        self.centros = centros
        self.saturacao = saturacao
        self.contagem = contagem
        self.curva = curva
        self.veto = veto
        self.teto_estimado = teto_estimado
        self.por_estrato = por_estrato or {}


def gerar_k(camadas, nomes, pesos, gamma, mu, pixel_m, maduro,
            pisos=None, janela_m=380.0, n_faixas=51, quantil=0.99,
            minimo_por_faixa=200, teto=None, estratos=None,
            normalizar_entradas=True):
    """Produz K em [0, 1] a partir das variáveis de terreno.

    camadas   sequência de arrays de terreno, na mesma grade
    nomes     rótulos, para o diagnóstico de veto
    pesos     pesos AHP, na ordem das camadas
    gamma     parâmetro do fuzzy gamma
    mu        ocupação observada (0..1) na data de referência
    maduro    booleano: pixels com exposição longa ao processo
    teto      None estima o nível pelos dados; um número fixa-o por decreto
    estratos  dict rótulo -> máscara booleana, para estimar o nível separado
              (ex.: terra e leito de rio, cujas mecânicas não são a mesma)
    """
    if pisos is None:
        pisos = [PISO_PADRAO] * len(camadas)
    if normalizar_entradas:
        prontas = [normalizar(c, piso=p) for c, p in zip(camadas, pisos)]
    else:
        prontas = [np.clip(np.asarray(c, dtype=np.float32), p, 1.0)
                   for c, p in zip(camadas, pisos)]

    veto = diagnosticar_veto(camadas, nomes)
    escore = fuzzy_gamma(prontas, pesos, gamma)
    ocupacao = ocupacao_local(mu, pixel_m, janela_m)

    if teto is not None:
        K = (np.clip(escore, 0.0, 1.0) * float(teto)).astype(np.float32)
        return ResultadoEstatico(K, escore, None, None, None, None, veto,
                                 float(teto))

    centros, saturacao, contagem = saturacao_por_faixa(
        escore, ocupacao, maduro, n_faixas, quantil, minimo_por_faixa)
    x, y = curva_de_nivel(centros, saturacao, contagem)
    K = aplicar_nivel(escore, x, y)

    por_estrato = {}
    if estratos:
        for rotulo, mascara in estratos.items():
            m = np.asarray(mascara, dtype=bool)
            if not (m & maduro).any():
                continue
            try:
                c_e, s_e, n_e = saturacao_por_faixa(
                    escore[m], ocupacao[m], maduro[m], n_faixas, quantil,
                    minimo_por_faixa)
                x_e, y_e = curva_de_nivel(c_e, s_e, n_e)
            except ValueError:
                continue
            por_estrato[rotulo] = (x_e, y_e)
            K[m] = aplicar_nivel(escore[m], x_e, y_e)

    return ResultadoEstatico(K, escore, centros, saturacao, contagem, (x, y),
                             veto, float(y[-1]), por_estrato)

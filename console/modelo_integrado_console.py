# -*- coding: utf-8 -*-
"""MODELO INTEGRADO — áreas críticas de garimpo, console Python do QGIS 4.2.

Abra este arquivo no EDITOR do console Python e clique em Executar.
As configurações ficam abaixo. Os inputs não são editados.

Junta os quatro blocos das figuras:
  * K estático: favorabilidade de garimpo ilegal (potencial remanescente com
    máscara de mineração legal), já calculada; entra como covariável.
  * Pressão antrópica recente: alertas de garimpo (G, também nas vizinhas),
    Acesso (A), outros desmatamentos (D) e eventos repressivos (REPR).
  * Transformação territorial recente: estradas (R) e construções (B) com
    passa-baixa gaussiano; CAR/SIGEF/ANM só com eventos históricos configurados.
  * Prioridade socioambiental preventiva (socio_pre), gerada pelo
    risco_socioambiental_console_v2.py.

Previsão (sub-bacia × origem mensal): hectares de alertas de garimpo nos 6 meses
seguintes, em duas partes: presença (logística) × área condicional. Os sinais
(−) e (x) das figuras viram coeficientes estimados, não operações fixas.
Decisão: crit = hectares previstos × socio_pre/5, convenção normativa explícita,
com cenários de sensibilidade. socio_pre não entra no ajuste nem na avaliação.

Transformação de K, família da área, σ do passa-baixa e especificação são
escolhidos só com ajuste/validação internos. O teste histórico já examinado é
reportado como exploratório. Gera GeoPackage, CSV, JSON, TXT e estilo QML.
"""
import csv
import datetime as dt
import json
import math
import re
import time
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np


# CONFIGURAÇÕES — ALTERE SOMENTE ESTE BLOCO, SE NECESSÁRIO.
CAMADA_SUBBACIAS = 'PMT_v2_subbacias'
ID_CAMADA_SUBBACIAS = None        # alternativa: ID da camada, se o nome se repete
CAMPO_ID_SUBBACIAS = 'HYBAS_ID'

FONTE_K = 'campo'                 # 'campo' nas sub-bacias, 'raster' ou 'vetor' (grade com campo)
CAMPO_K = None                    # obrigatório para 'campo' e 'vetor'
CAMADA_K = 'PMT_v2_K'             # usado com 'raster' ou 'vetor'
ID_CAMADA_K = None
TRANSFORMACOES_K = ('nenhuma', 'logit')   # candidatas; escolha na validação interna

CAMADA_SOCIO = None               # None: camada única carregada com HYBAS_ID e socio_pre
ID_CAMADA_SOCIO = None

CAMADA_ALERTAS = 'alertas'        # precisa conter garimpo, Acesso, estradas e construções
ID_CAMADA_ALERTAS = None
CAMPO_CLASSE, CAMPO_DETECCAO, CAMPO_PUBLICACAO = 'tipo', 'dat_depois', 'dat_public'
DISPONIBILIDADE = 'publicacao'    # 'publicacao': max(detecção, publicação) < origem
                                  # 'deteccao': retrospectivo, detecção < origem
CLASSES = {
    'garimpo': ('Desmatamento - Extração Mineral/Garimpo',),
    'acesso': ('Desmatamento - Acesso',),
    'estradas': ('Detecção Mensal de Estradas - Experimental',),
    'construcoes': ('Detecção Mensal de Construções - Experimental',),
    'outros': None,               # None: demais classes iniciadas por Desmatamento/Degradação
}

# Eventos pontuais/poligonais com data; só entram se configurados explicitamente.
# sigla: 'REPR' (IBAMA/ICMBio), 'CAR', 'SIGEF' ou 'ANM'. Exemplo:
# dict(sigla='REPR', camada='autos_ibama', id=None, campo_data='dat_auto',
#      campo_disponibilidade=None, expressao=None),
EVENTOS = ()

DATA_INICIO_COBERTURA = '2020-09' # primeiro mês com cobertura dos alertas
DATA_FIM_COBERTURA = '2025-11'    # último mês completo observado
MESES_HISTORICO = 6               # janela L dos preditores dinâmicos
HORIZONTE_MESES = 6               # janela H do desfecho
PERIODOS = {                      # meses de ORIGEM
    'ajuste_interno': ('2021-01', '2022-06'),
    'validacao_interna': ('2023-01', '2023-06'),
    'ajuste_final': ('2021-01', '2023-06'),
    'teste': ('2024-01', '2025-06'),
}
ORIGEM_OPERACIONAL = '2025-12'    # previsão para dez/2025–mai/2026
REAJUSTE_OPERACIONAL = 'todas'    # 'todas' as origens com desfecho completo ou 'ajuste_final'

CRS_METRICO = 'EPSG:31981'        # passa-baixa, vizinhança e blocos do bootstrap
SIGMAS_M = (125, 250, 500)        # σ por eixo, em metros; σ = 0 só como diagnóstico
TRUNCAMENTO_SIGMAS = 4
PONTOS_POR_DETECCAO = 4

RIDGE = 1.0                       # penalização leve nas covariáveis padronizadas
FAMILIAS_AREA = ('lognormal', 'gama')
TOLERANCIA_SELECAO = 0.005        # prefere a especificação mais simples dentro de 0,5 p.p.
TOPO_CRITICO = 0.10
FRACOES_TOPO = (.01, .05, .10)
BLOCO_BOOTSTRAP_KM = 100
N_BOOTSTRAP = 500
SEMENTE = 20261006
RESPEITAR_SELECAO = True          # True: só feições selecionadas, se houver seleção
PASTA_SAIDA = Path.home() / 'Etapa1_modelo_integrado'
EXECUTAR_AO_ABRIR = True
# FIM DAS CONFIGURAÇÕES.

VERSAO = 'integrado_console_v1_20261006'
GRUPOS_SUAVIZADOS = ('estradas', 'construcoes')
SIGLAS_EVENTOS = ('REPR', 'CAR', 'SIGEF', 'ANM')
TERMOS = {
    'log_area': 'log da área da sub-bacia (km²)',
    'K': 'favorabilidade estática de garimpo ilegal (K)',
    'G': 'garimpo detectado na sub-bacia nos últimos L meses (log1p ha)',
    'Gviz': 'garimpo nas sub-bacias vizinhas nos últimos L meses (log1p ha)',
    'A': 'Desmatamento - Acesso nos últimos L meses (log1p ha)',
    'D': 'outros desmatamentos/degradação nos últimos L meses (log1p ha)',
    'REPR': 'eventos repressivos IBAMA/ICMBio nos últimos L meses (log1p n)',
    'R': 'estradas detectadas, passa-baixa σ (log1p ha)',
    'B': 'construções detectadas, passa-baixa σ (log1p ha)',
    'CAR': 'eventos CAR nos últimos L meses (log1p n)',
    'SIGEF': 'eventos SIGEF nos últimos L meses (log1p n)',
    'ANM': 'eventos ANM nos últimos L meses (log1p n)',
}
GRUPO_DO_TERMO = {'G': 'garimpo', 'A': 'acesso', 'D': 'outros', 'R': 'estradas',
                  'B': 'construcoes', 'REPR': 'REPR', 'CAR': 'CAR', 'SIGEF': 'SIGEF',
                  'ANM': 'ANM'}
ESPECIFICACOES = (                # ordem de complexidade; nomes fixados antes dos dados
    ('K', ('log_area', 'K')),
    ('KG', ('log_area', 'K', 'G', 'Gviz')),
    ('KG+P', ('log_area', 'K', 'G', 'Gviz', 'A', 'D', 'REPR')),
    ('KG+RB', ('log_area', 'K', 'G', 'Gviz', 'R', 'B')),
    ('integrado', ('log_area', 'K', 'G', 'Gviz', 'A', 'D', 'REPR', 'R', 'B',
                   'CAR', 'SIGEF', 'ANM')),
)
# Gauss–Hermite de 5 nós por eixo: deslocamento gaussiano isotrópico de desvio σ.
GH_X = (-2.0201828704560856, -0.9585724646138185, 0.0,
        0.9585724646138185, 2.0201828704560856)
GH_W = (0.019953242059045913, 0.39361932315224116, 0.9453087204829419,
        0.39361932315224116, 0.019953242059045913)
CLASSES_CRITICAS = ('Crítica', 'Crítica sob sensibilidade', 'Não crítica', 'Sem previsão')


# ---------------------------------------------------------------------------
# Núcleo independente do QGIS (testável com dados sintéticos).
# ---------------------------------------------------------------------------
def log(mensagem):
    print(time.strftime('%H:%M:%S'), mensagem, flush=True)


def ausente(v):
    if v is None or (hasattr(v, 'isNull') and v.isNull()):
        return True
    if isinstance(v, str):
        return v.strip().lower() in ('', 'null', 'none', 'nan')
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def valor_finito(v, nome):
    """NULL/NaN são ausentes; texto não numérico ou infinito é erro."""
    if ausente(v):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError('%s não é numérico: %r.' % (nome, v)) from None
    if not math.isfinite(x):
        raise ValueError('%s não é finito: %r.' % (nome, v))
    return x


def chave(v):
    """Aceita HYBAS_ID textual/inteiro/decimal integral, sem perder precisão."""
    if ausente(v):
        raise ValueError('HYBAS_ID ausente.')
    if isinstance(v, float) and (not math.isfinite(v) or abs(v) >= 2**53):
        raise ValueError('Identificador em ponto flutuante sem precisão segura.')
    s = str(v).strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        return s
    if not d.is_finite() or d < 0 or d != d.to_integral_value():
        raise ValueError('HYBAS_ID numérico deve ser inteiro não negativo: %r.' % v)
    return str(int(d))


def normalizar(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', s.lower())


def ler_data(v):
    if ausente(v) or (hasattr(v, 'isValid') and not v.isValid()):
        return None
    try:
        if hasattr(v, 'toPyDateTime'):
            v = v.toPyDateTime()
        elif hasattr(v, 'toPyDate'):
            v = v.toPyDate()
        if isinstance(v, (dt.date, dt.datetime)):
            return dt.date(v.year, v.month, v.day)
        s = str(v).strip()
        m = re.match(r'^(\d{4})[-/.](\d{2})[-/.](\d{2})(?:$|[ T])', s)
        if m:
            return dt.date(*map(int, m.groups()))
        m = re.match(r'^(\d{2})[-/.](\d{2})[-/.](\d{4})(?:$|[ T])', s)
        if m:
            dia, mes, ano = map(int, m.groups())
            return dt.date(ano, mes, dia)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def mes_tupla(texto):
    m = re.match(r'^(\d{4})-(\d{2})', str(texto).strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError('Mês inválido: %r. Use AAAA-MM.' % texto)
    return int(m.group(1)), int(m.group(2))


def indice_mes(valor, base):
    ano, mes = valor if isinstance(valor, tuple) else (valor.year, valor.month)
    return (ano - base[0]) * 12 + mes - base[1]


def rotulo_mes(i, base):
    a, m = divmod(base[1] - 1 + int(i), 12)
    return '%04d-%02d' % (base[0] + a, m + 1)


def mapa_classes(valores, config):
    """Classe normalizada → grupo. 'outros' = None reúne Desmatamento/Degradação restantes."""
    mapa = {}
    for grupo, nomes in config.items():
        for nome in nomes or ():
            mapa[normalizar(nome)] = grupo
    if 'outros' in config and config['outros'] is None:
        for v in valores:
            nv = normalizar(v)
            if nv not in mapa and nv.startswith(('desmatamento', 'degradacao')):
                mapa[nv] = 'outros'
    return mapa


def limpar(a):
    a[np.abs(a) < 1e-9] = 0.0
    return a


def acumular(contribuicoes, n_b, n_m, L, H, desfechos=('garimpo',)):
    """Painéis sub-bacia × origem a partir de (grupo, bacia, mês_det, mês_disp, valor).

    Preditor em t: detecções com mês em [t−L, t−1] e disponíveis antes de t
    (mês_disp ≤ t−1); mês_disp None nunca entra como preditor.
    Desfecho em t: detecções com mês em [t, t+H−1], pela data do evento.
    """
    por_grupo = defaultdict(list)
    for g, b, det, disp, v in contribuicoes:
        por_grupo[g].append((b, det, -1 if disp is None else disp, v))
    pred, desf = {}, {}
    for g, regs in por_grupo.items():
        a = np.array(regs, dtype=float)
        b, det, disp, v = a[:, 0].astype(int), a[:, 1].astype(int), a[:, 2].astype(int), a[:, 3]
        lo = np.maximum(det, disp) + 1
        hi = np.minimum(det + L, n_m - 1)
        ok = (disp >= 0) & (lo <= hi)
        acc = np.zeros((n_b, n_m + 1))
        np.add.at(acc, (b[ok], lo[ok]), v[ok])
        np.add.at(acc, (b[ok], hi[ok] + 1), -v[ok])
        pred[g] = limpar(np.cumsum(acc, axis=1)[:, :n_m])
        if g in desfechos:
            acc = np.zeros((n_b, n_m + 1))
            np.add.at(acc, (b, np.maximum(det - H + 1, 0)), v)
            np.add.at(acc, (b, det + 1), -v)
            desf[g] = limpar(np.cumsum(acc, axis=1)[:, :n_m])
    return pred, desf


def soma_vizinhos(a, viz):
    src, dst = viz
    out = np.zeros_like(a)
    if len(src):
        np.add.at(out, src, a[dst])
    return out


def transformar_k(k, modo):
    if modo == 'nenhuma':
        return k
    if modo == 'logit':
        kc = np.clip(k, 1e-4, 1 - 1e-4)
        return np.log(kc / (1 - kc))
    raise ValueError('Transformação de K desconhecida: %s.' % modo)


def valores_termo(p, nome, ktrans='nenhuma', sigma=None):
    if nome == 'log_area':
        return np.log(p['area_km2'])
    if nome == 'K':
        return transformar_k(p['K'], ktrans)
    if nome == 'Gviz':
        return np.log1p(p['viz_garimpo'])
    g = GRUPO_DO_TERMO[nome]
    if g in GRUPOS_SUAVIZADOS:
        g = '%s@%d' % (g, sigma)
    return np.log1p(p['pred'][g])


def termos_disponiveis(p):
    disp = {'log_area', 'K'}
    pred = p['pred']
    if 'garimpo' in pred:
        disp.add('G')
        if len(p['viz'][0]):
            disp.add('Gviz')
    for termo, g in GRUPO_DO_TERMO.items():
        if termo == 'G':
            continue
        nome = '%s@0' % g if g in GRUPOS_SUAVIZADOS else g
        if nome in pred and np.any(pred[nome] > 0):
            disp.add(termo)
    return disp


def especificacoes(disp):
    out, vistas = {}, set()
    for nome, termos in ESPECIFICACOES:
        t = tuple(x for x in termos if x in disp)
        if t not in vistas:
            vistas.add(t)
            out[nome] = t
    return out


def linhas(p, origens):
    """Linhas (sub-bacia, origem) com K válido, ordenadas por origem."""
    b_ok = np.flatnonzero(np.isfinite(p['K']))
    origens = np.asarray(origens, int)
    return np.tile(b_ok, len(origens)), np.repeat(origens, len(b_ok))


def desenho(p, termos, b, t, ktrans, sigma):
    cols = []
    for nome in termos:
        v = valores_termo(p, nome, ktrans, sigma)
        cols.append(v[b] if v.ndim == 1 else v[b, t])
    return np.column_stack(cols)


def expit(x):
    return 0.5 * (1.0 + np.tanh(0.5 * x))


def ep_cluster(X, r, H, grupos):
    """Erro-padrão robusto por agrupamento espacial (blocos): sanduíche."""
    if grupos is None:
        return None
    _, gi = np.unique(grupos, return_inverse=True)
    gi = gi.ravel()
    S = np.zeros((int(gi.max()) + 1, X.shape[1]))
    np.add.at(S, gi, X * r[:, None])
    A = np.linalg.inv(H)
    V = A @ (S.T @ S) @ A
    ng = S.shape[0]
    if ng > 1:
        V *= ng / (ng - 1)
    return np.sqrt(np.clip(np.diag(V), 0, None))


def _penal(k, ridge):
    pen = np.full(k + 1, float(ridge))
    pen[0] = 0.0
    return pen


def ajustar_logit(Z, y, ridge, grupos=None, max_iter=100):
    n, k = Z.shape
    X = np.column_stack([np.ones(n), Z])
    pen = _penal(k, ridge)
    beta = np.zeros(k + 1)
    m = min(max(float(y.mean()), 1e-6), 1 - 1e-6)
    beta[0] = math.log(m / (1 - m))

    def objetivo(b):
        eta = X @ b
        return float(np.sum(np.logaddexp(0.0, eta) - y * eta) + 0.5 * np.sum(pen * b * b))
    f, conv = objetivo(beta), False
    for _ in range(max_iter):
        p = expit(X @ beta)
        g = X.T @ (p - y) + pen * beta
        H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(pen) + 1e-10 * np.eye(k + 1)
        passo, s = np.linalg.solve(H, g), 1.0
        while True:
            nb = beta - s * passo
            nf = objetivo(nb)
            if nf <= f + 1e-10 * (1 + abs(f)) or s < 1e-8:
                break
            s *= 0.5
        delta, beta, f = f - nf, nb, nf
        if abs(delta) <= 1e-9 * (1 + abs(f)):
            conv = True
            break
    p = expit(X @ beta)
    H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(pen)
    return beta, conv, ep_cluster(X, y - p, H, grupos)


def ajustar_lognormal(Z, y, ridge, grupos=None):
    X = np.column_stack([np.ones(len(Z)), Z])
    H = X.T @ X + np.diag(_penal(Z.shape[1], ridge))
    z = np.log(y)
    beta = np.linalg.solve(H, X.T @ z)
    r = z - X @ beta
    return beta, float(np.mean(np.exp(r))), True, ep_cluster(X, r, H, grupos)


def ajustar_gama(Z, y, ridge, grupos=None, max_iter=100):
    """GLM gama com ligação log (escore de Fisher, W = 1)."""
    X = np.column_stack([np.ones(len(Z)), Z])
    pen = _penal(Z.shape[1], ridge)
    H = X.T @ X + np.diag(pen)
    beta = np.zeros(X.shape[1])
    beta[0] = math.log(float(y.mean()))

    def objetivo(b):
        mu = np.exp(np.clip(X @ b, -50, 50))
        return float(np.sum(-np.log(y / mu) + (y - mu) / mu) + 0.5 * np.sum(pen * b * b))
    f, conv = objetivo(beta), False
    for _ in range(max_iter):
        mu = np.exp(np.clip(X @ beta, -50, 50))
        g = -X.T @ ((y - mu) / mu) + pen * beta
        passo, s = np.linalg.solve(H, g), 1.0
        while True:
            nb = beta - s * passo
            nf = objetivo(nb)
            if nf <= f + 1e-10 * (1 + abs(f)) or s < 1e-8:
                break
            s *= 0.5
        delta, beta, f = f - nf, nb, nf
        if abs(delta) <= 1e-9 * (1 + abs(f)):
            conv = True
            break
    mu = np.exp(np.clip(X @ beta, -50, 50))
    return beta, 1.0, conv, ep_cluster(X, (y - mu) / mu, H, grupos)


class DuasPartes:
    """E[ha] = P(ha > 0) × E[ha | ha > 0], com covariáveis padronizadas no ajuste."""

    def __init__(self, termos, familia, ridge):
        self.termos, self.familia, self.ridge = tuple(termos), familia, ridge

    def ajustar(self, X, y, grupos=None):
        dp = X.std(axis=0)
        self.ativos = dp > 1e-12
        self.descartados = [t for t, a in zip(self.termos, self.ativos) if not a]
        Xa = X[:, self.ativos]
        self.media, self.dp = Xa.mean(axis=0), Xa.std(axis=0)
        Z = (Xa - self.media) / self.dp
        pos = y > 0
        self.n, self.n_pos = len(y), int(pos.sum())
        if self.n_pos < 10 or self.n - self.n_pos < 10:
            raise ValueError('Ajuste com menos de 10 presenças ou ausências (%d de %d).'
                             % (self.n_pos, self.n))
        self.b_p, self.conv_p, self.ep_p = ajustar_logit(Z, pos.astype(float), self.ridge, grupos)
        g2 = grupos[pos] if grupos is not None else None
        if self.familia == 'lognormal':
            ajuste = ajustar_lognormal
        elif self.familia == 'gama':
            ajuste = ajustar_gama
        else:
            raise ValueError('Família de área desconhecida: %s.' % self.familia)
        self.b_a, self.smear, self.conv_a, self.ep_a = ajuste(Z[pos], y[pos], self.ridge, g2)
        # Salvaguarda contra extrapolação: média condicional limitada a 10× o maior valor visto.
        self.eta_max = math.log(float(y[pos].max()) * 10)
        return self

    def prever(self, X):
        Z = (X[:, self.ativos] - self.media) / self.dp
        Xc = np.column_stack([np.ones(len(Z)), Z])
        p = expit(Xc @ self.b_p)
        mu = np.exp(np.minimum(Xc @ self.b_a, self.eta_max)) * self.smear
        return p, mu, p * mu

    def coeficientes(self):
        nomes = ['intercepto'] + [t for t, a in zip(self.termos, self.ativos) if a]
        medias = [None] + list(self.media)
        dps = [None] + list(self.dp)
        out = []
        for i, nome in enumerate(nomes):
            out.append(dict(
                termo=nome, media=medias[i], dp=dps[i],
                presenca_beta=self.b_p[i],
                presenca_ep=None if self.ep_p is None else self.ep_p[i],
                area_beta=self.b_a[i],
                area_ep=None if self.ep_a is None else self.ep_a[i]))
        return dict(familia=self.familia, n=self.n, n_positivos=self.n_pos,
                    convergiu=bool(self.conv_p and self.conv_a), smearing=self.smear,
                    descartados_constantes=self.descartados, coeficientes=out,
                    escala='β por desvio-padrão do termo no ajuste; EP robusto por bloco espacial')


def ordenar(chaves):
    """Ordem decrescente lexicográfica: primeira chave principal, demais desempatam."""
    return np.lexsort(tuple(-np.asarray(c, dtype=float) for c in reversed(chaves)))


def captura(y, t, chaves, frac, area=None):
    """Hectares nas top-frac sub-bacias de cada origem; agregado = Σ capturado / Σ total."""
    cap = tot = 0.0
    por, fa, lista = [], [], []
    for o in np.unique(t):
        idx = np.flatnonzero(t == o)
        ordem = idx[ordenar([np.asarray(c)[idx] for c in chaves])]
        sel = ordem[:math.ceil(frac * len(idx))]
        c, s = float(y[sel].sum()), float(y[idx].sum())
        cap += c
        tot += s
        if s > 0:
            por.append(c / s)
        if area is not None:
            fa.append(float(area[sel].sum() / area[idx].sum()))
        lista.append(dict(origem=int(o), ha=s, ha_capturados=c))
    return dict(agregada=cap / tot if tot > 0 else None,
                media_origens=float(np.mean(por)) if por else None,
                fracao_area=float(np.mean(fa)) if fa else None, por_origem=lista)


def postos_medios(x):
    ordem = np.argsort(x, kind='mergesort')
    xs = x[ordem]
    inicio = np.r_[True, xs[1:] != xs[:-1]]
    grupo = np.cumsum(inicio) - 1
    starts = np.flatnonzero(inicio)
    cont = np.diff(np.r_[starts, len(x)])
    r = np.empty(len(x))
    r[ordem] = (starts + (cont + 1) / 2.0)[grupo]
    return r


def auc(score, presenca):
    presenca = np.asarray(presenca, bool)
    n1 = int(presenca.sum())
    n0 = len(presenca) - n1
    if n1 == 0 or n0 == 0:
        return None
    r = postos_medios(np.asarray(score, float))
    return float((r[presenca].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def calibracao_decis(previsto, observado, n=10):
    ordem = np.argsort(previsto, kind='mergesort')
    out = []
    for i, parte in enumerate(np.array_split(ordem, n), 1):
        if len(parte):
            out.append(dict(decil=i, n=int(len(parte)),
                            ha_previsto_medio=float(previsto[parte].mean()),
                            ha_observado_medio=float(observado[parte].mean())))
    return out


def ordens_origem(t, chaves):
    out = []
    for o in np.unique(t):
        idx = np.flatnonzero(t == o)
        out.append(ordenar([np.asarray(c)[idx] for c in chaves]))
    return out


def bootstrap_pareado(y, t, blocos_linha, ordens, frac, n_boot, semente, pares):
    """Reamostra blocos espaciais; as mesmas réplicas servem a todos os modelos."""
    if n_boot <= 0:
        return {}
    origens = np.unique(t)
    idx_o = [np.flatnonzero(t == o) for o in origens]
    _, bl = np.unique(blocos_linha, return_inverse=True)
    bl = bl.ravel()
    nbk = int(bl.max()) + 1
    rng = np.random.default_rng(semente)
    cap = {m: np.zeros(n_boot) for m in ordens}
    tot = np.zeros(n_boot)
    for ini in range(0, n_boot, 100):
        R = min(100, n_boot - ini)
        sl = slice(ini, ini + R)
        C = np.stack([np.bincount(rng.integers(0, nbk, nbk), minlength=nbk)
                      for _ in range(R)]).astype(float)
        for k, idx in enumerate(idx_o):
            W = C[:, bl[idx]]
            yo = y[idx]
            tot[sl] += W @ yo
            for m, ords in ordens.items():
                o = ords[k]
                Wo = W[:, o]
                antes = np.cumsum(Wo, axis=1) - Wo
                alvo = np.ceil(frac * Wo.sum(axis=1))
                cap[m][sl] += (Wo * (antes < alvo[:, None])) @ yo[o]
    ok = tot > 0
    res = dict(n_replicas=n_boot, n_replicas_validas=int(ok.sum()), n_blocos=nbk,
               modelos={}, diferencas={})
    if not ok.any():
        return res
    taxa = {m: v[ok] / tot[ok] for m, v in cap.items()}
    for m, v in taxa.items():
        res['modelos'][m] = dict(ic95=[float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))])
    for a, b in pares:
        if a in taxa and b in taxa:
            d = taxa[a] - taxa[b]
            res['diferencas']['%s − %s' % (a, b)] = dict(
                media=float(d.mean()), ic95=[float(np.percentile(d, 2.5)),
                                             float(np.percentile(d, 97.5))],
                prop_positiva=float((d > 0).mean()))
    return res


def membros_topo(score, n):
    ok = np.isfinite(score)
    if n <= 0 or not ok.any():
        return np.zeros(len(score), bool)
    vals = np.sort(score[ok])[::-1]
    return ok & (score >= vals[min(n, len(vals)) - 1])


def areas_criticas(E, s_est, s_min, s_max, fracoes, frac_op):
    """crit = E[ha] × S/5 em três cenários; ausência de S vira faixa 1–5.

    topo_crit: menor topo (%) com inclusão estável nos três cenários.
    topo_sens: menor topo (%) em que a inclusão muda entre cenários.
    topo_prev: menor topo (%) pela previsão sem peso socioambiental.
    Empates no corte entram; n alvo = ceil(fração × sub-bacias com previsão).
    """
    E = np.asarray(E, float)
    s_min = np.where(np.isfinite(s_min), s_min, np.where(np.isfinite(s_est), s_est, 1.0))
    s_max = np.where(np.isfinite(s_max), s_max, np.where(np.isfinite(s_est), s_est, 5.0))
    cen = dict(crit_min=E * s_min / 5, crit_est=E * s_est / 5, crit_max=E * s_max / 5)
    n_base = int(np.isfinite(E).sum())
    topo = {k: np.full(len(E), np.nan) for k in ('topo_prev', 'topo_crit', 'topo_sens')}
    resumo = {}
    for f in sorted(fracoes):
        n = math.ceil(f * n_base)
        pct = int(round(f * 100))
        mp = membros_topo(E, n)
        ms = [membros_topo(c, n) for c in cen.values()]
        todos, algum = np.logical_and.reduce(ms), np.logical_or.reduce(ms)
        inst = algum & ~todos
        for nome, m in (('topo_prev', mp), ('topo_crit', todos), ('topo_sens', inst)):
            topo[nome][m & np.isnan(topo[nome])] = pct
        resumo[str(f)] = dict(n_base=n_base, n_alvo=n, n_topo_previsao=int(mp.sum()),
                              n_estaveis=int(todos.sum()), n_instaveis=int(inst.sum()),
                              n_previsao_e_criticas=int((mp & todos).sum()),
                              n_previsao_fora_das_criticas=int((mp & ~algum).sum()))
    lim = int(round(frac_op * 100))
    classe = np.array(['Não crítica'] * len(E), dtype=object)
    classe[~np.isfinite(E)] = 'Sem previsão'
    classe[np.nan_to_num(topo['topo_sens'], nan=999) <= lim] = 'Crítica sob sensibilidade'
    classe[np.nan_to_num(topo['topo_crit'], nan=999) <= lim] = 'Crítica'
    return dict(cen, classe_crit=classe, resumo=resumo, **topo)


def melhor(tab, chave_valor='captura'):
    escolha = None
    for r in tab:
        v = r[chave_valor]
        if v is not None and (escolha is None or v > escolha[chave_valor] + 1e-12):
            escolha = r
    if escolha is None:
        raise ValueError('Nenhum candidato com hectares de garimpo na validação interna.')
    return escolha


def executar_modelagem(p, cfg, progresso=log):
    """Seleção interna, ajuste final, teste exploratório, previsão operacional e decisão."""
    rel = dict(avisos=[])
    L, H, ult, base = cfg['L'], cfg['H'], cfg['ultimo_mes'], p['base']
    origens = {}
    for nome, (a, z) in cfg['periodos'].items():
        validas = [t for t in range(a, z + 1) if t - L >= 0 and t + H - 1 <= ult]
        excl = [rotulo_mes(t, base) for t in range(a, z + 1) if t not in validas]
        if excl:
            rel['avisos'].append('%s: origens sem histórico de %d meses ou desfecho completo '
                                 'excluídas de todos os modelos: %s.' % (nome, L, ', '.join(excl)))
        if not validas:
            raise ValueError('Período %s sem origens válidas na cobertura configurada.' % nome)
        origens[nome] = np.array(validas, int)
    rel['origens'] = {n: [rotulo_mes(t, base) for t in o] for n, o in origens.items()}
    disp = termos_disponiveis(p)
    especs = especificacoes(disp)
    if 'KG' not in especs:
        raise ValueError('Sem garimpo disponível como preditor: K + G não pode ser ajustado.')
    rel['termos_disponiveis'] = {t: TERMOS[t] for t in TERMOS if t in disp}
    rel['termos_indisponiveis'] = sorted(set(TERMOS) - disp)
    rel['especificacoes'] = {n: list(t) for n, t in especs.items()}
    rk = np.random.default_rng(cfg['semente']).random(p['n_b'])
    kv = p['K'][np.isfinite(p['K'])]
    ktrans = [k for k in cfg['ktrans'] if k != 'logit' or (kv.min() >= 0 and kv.max() <= 1)]
    if len(ktrans) < len(cfg['ktrans']):
        rel['avisos'].append('K fora de [0,1]: transformação logit descartada.')
    frac = cfg['topo']

    def ajustar(termos, fam, kt, sig, origs, agrupar=False):
        b, t = linhas(p, origs)
        X = desenho(p, termos, b, t, kt, sig)
        return DuasPartes(termos, fam, cfg['ridge']).ajustar(
            X, p['y'][b, t], p['bloco'][b] if agrupar else None)

    def prever(m, kt, sig, origs):
        b, t = linhas(p, origs)
        pr, mu, E = m.prever(desenho(p, m.termos, b, t, kt, sig))
        return dict(b=b, t=t, y=p['y'][b, t], p=pr, mu=mu, E=E)

    def criterio(pv):
        return captura(pv['y'], pv['t'], [pv['E'], rk[pv['b']]], frac)['agregada']

    aj, va = origens['ajuste_interno'], origens['validacao_interna']
    progresso('Seleção interna 1/3: transformação de K e família da área (K + G).')
    tab1 = []
    for kt in ktrans:
        for fam in cfg['familias']:
            m = ajustar(especs['KG'], fam, kt, None, aj)
            tab1.append(dict(transformacao_k=kt, familia=fam,
                             captura=criterio(prever(m, kt, None, va))))
    esc = melhor(tab1)
    kt, fam = esc['transformacao_k'], esc['familia']
    sigma, tab2 = None, []
    if 'KG+RB' in especs:
        progresso('Seleção interna 2/3: σ do passa-baixa (K + G + R + B).')
        for s in (0,) + tuple(cfg['sigmas']):
            m = ajustar(especs['KG+RB'], fam, kt, s, aj)
            tab2.append(dict(sigma_m=s, selecionavel=s > 0,
                             captura=criterio(prever(m, kt, s, va))))
        sigma = melhor([r for r in tab2 if r['selecionavel']])['sigma_m']
    progresso('Seleção interna 3/3: especificação.')
    b_va, t_va = linhas(p, va)
    tab3 = [dict(especificacao='regua', termos=['G bruto; desempate por K'], selecionavel=False,
                 captura=captura(p['y'][b_va, t_va], t_va,
                                 [p['pred']['garimpo'][b_va, t_va], p['K'][b_va], rk[b_va]],
                                 frac)['agregada'])]
    for nome, termos in especs.items():
        m = ajustar(termos, fam, kt, sigma, aj)
        tab3.append(dict(especificacao=nome, termos=list(termos), selecionavel=True,
                         captura=criterio(prever(m, kt, sigma, va))))
    candidatos = [r for r in tab3 if r['selecionavel'] and r['captura'] is not None]
    if not candidatos:
        raise ValueError('Validação interna sem hectares de garimpo.')
    teto = max(r['captura'] for r in candidatos)
    escolhida = next(r['especificacao'] for r in candidatos
                     if r['captura'] >= teto - cfg['tolerancia'])
    rel['selecao_interna'] = dict(
        criterio='captura agregada de hectares nas %d%% sub-bacias priorizadas' % round(frac * 100),
        transformacao_k_familia=tab1, sigma=tab2, especificacoes=tab3,
        escolhas=dict(transformacao_k=kt, familia=fam, sigma_m=sigma, especificacao=escolhida,
                      tolerancia=cfg['tolerancia']))

    progresso('Ajuste final e teste histórico (exploratório).')
    af, te = origens['ajuste_final'], origens['teste']
    bt, tt = linhas(p, te)
    yt = p['y'][bt, tt]
    area_t = p['area_km2'][bt]
    chaves = {'regua': [p['pred']['garimpo'][bt, tt], p['K'][bt], rk[bt]]}
    modelos, prev_teste = {}, {}
    for nome, termos in especs.items():
        modelos[nome] = ajustar(termos, fam, kt, sigma, af, agrupar=True)
        prev_teste[nome] = prever(modelos[nome], kt, sigma, te)
        chaves[nome] = [prev_teste[nome]['E'], rk[bt]]
    if sigma is not None:
        m0 = ajustar(especs['KG+RB'], fam, kt, 0, af)
        chaves['KG+RB σ=0 (diagnóstico)'] = [prever(m0, kt, 0, te)['E'], rk[bt]]
    metr = {}
    for nome, ch in chaves.items():
        d = {}
        for f in cfg['fracoes']:
            c = captura(yt, tt, ch, f, area_t)
            d['captura_%d' % round(f * 100)] = c['agregada']
            d['captura_media_origens_%d' % round(f * 100)] = c['media_origens']
            d['fracao_area_%d' % round(f * 100)] = c['fracao_area']
            if abs(f - frac) < 1e-12:
                d['por_origem'] = [dict(origem=rotulo_mes(r['origem'], base), ha=r['ha'],
                                        ha_capturados=r['ha_capturados']) for r in c['por_origem']]
        if nome in prev_teste:
            pv = prev_teste[nome]
            d.update(auc_presenca=auc(pv['p'], yt > 0), brier=float(np.mean((pv['p'] - (yt > 0)) ** 2)),
                     presenca_prevista=float(pv['p'].mean()), presenca_observada=float((yt > 0).mean()),
                     ha_previstos=float(pv['E'].sum()), ha_observados=float(yt.sum()))
        metr[nome] = d
    pares = [(n, 'KG') for n in especs if n != 'KG'] + [(n, 'regua') for n in especs]
    progresso('Bootstrap espacial pareado (%d réplicas).' % cfg['n_boot'])
    ords = {n: ordens_origem(tt, ch) for n, ch in chaves.items()}
    boot = bootstrap_pareado(yt, tt, p['bloco'][bt], ords, frac, cfg['n_boot'],
                             cfg['semente'], pares)
    rel['teste'] = dict(
        natureza='exploratório: período já examinado em rodadas anteriores',
        metricas=metr, bootstrap=boot,
        calibracao_decis=calibracao_decis(prev_teste[escolhida]['E'], yt),
        coeficientes_ajuste_final=modelos[escolhida].coeficientes())

    if cfg['reajuste'] == 'ajuste_final':
        oo = af
    else:
        ini = min(int(o.min()) for o in origens.values())
        oo = np.array([t for t in range(ini, ult - H + 2) if t - L >= 0], int)
    t_op = cfg['t_op']
    if t_op - L < 0 or t_op > p['n_m'] - 1:
        raise ValueError('Origem operacional fora da cobertura dos preditores.')
    progresso('Ajuste operacional (%s a %s) e previsão em %s.'
              % (rotulo_mes(oo[0], base), rotulo_mes(oo[-1], base), rotulo_mes(t_op, base)))
    m_op = ajustar(especs[escolhida], fam, kt, sigma, oo, agrupar=True)
    b_ok = np.flatnonzero(np.isfinite(p['K']))
    pr, mu, E = m_op.prever(desenho(p, m_op.termos, b_ok, np.full(len(b_ok), t_op), kt, sigma))
    n_b = p['n_b']
    P, MU, EE = (np.full(n_b, np.nan) for _ in range(3))
    P[b_ok], MU[b_ok], EE[b_ok] = pr, mu, E
    s = p['socio']
    crit = areas_criticas(EE, s['est'], s['min'], s['max'], cfg['fracoes'], frac)
    rank = np.full(n_b, np.nan)
    rank[b_ok[ordenar([E, rk[b_ok]])]] = np.arange(1, len(b_ok) + 1)
    rel['operacional'] = dict(
        origem=rotulo_mes(t_op, base),
        horizonte='%s a %s' % (rotulo_mes(t_op, base), rotulo_mes(t_op + H - 1, base)),
        origens_ajuste=[rotulo_mes(oo[0], base), rotulo_mes(oo[-1], base)],
        especificacao=escolhida, coeficientes=m_op.coeficientes(),
        ha_previstos_total=float(np.nansum(EE)),
        areas_criticas=crit['resumo'],
        contagem_classes={c: int((crit['classe_crit'] == c).sum()) for c in CLASSES_CRITICAS},
        contagem_status_socio=dict(Counter(s['status'])))
    saida = dict(HYBAS_ID=list(p['ids']), area_km2=p['area_km2'], K=p['K'])
    if p.get('k_cob') is not None:
        saida['k_cob'] = p['k_cob']
    saida['G_ha'] = p['pred']['garimpo'][:, t_op]
    saida['Gviz_ha'] = p['viz_garimpo'][:, t_op]
    for termo, nome in (('A', 'A_ha'), ('D', 'D_ha'), ('REPR', 'REPR_n'), ('CAR', 'CAR_n'),
                        ('SIGEF', 'SIGEF_n'), ('ANM', 'ANM_n')):
        if termo in disp:
            saida[nome] = p['pred'][GRUPO_DO_TERMO[termo]][:, t_op]
    for termo, nome in (('R', 'R_ha'), ('B', 'B_ha')):
        if termo in disp and sigma is not None:
            saida[nome] = p['pred']['%s@%d' % (GRUPO_DO_TERMO[termo], sigma)][:, t_op]
    saida.update(p_prev=P, mu_prev=MU, ha_prev=EE, dens_prev=EE / p['area_km2'],
                 rank_prev=rank, topo_prev=crit['topo_prev'],
                 socio_pre=s['est'], socio_min=s['min'], socio_max=s['max'],
                 hg_imput=s['hg'], s_status=list(s['status']),
                 crit_est=crit['crit_est'], crit_min=crit['crit_min'], crit_max=crit['crit_max'],
                 topo_crit=crit['topo_crit'], topo_sens=crit['topo_sens'],
                 classe_crit=list(crit['classe_crit']),
                 modelo=[escolhida] * n_b, origem=[rotulo_mes(t_op, base)] * n_b)
    return dict(relatorio=rel, saida=saida)


def json_limpo(x):
    if isinstance(x, dict):
        return {str(k): json_limpo(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [json_limpo(v) for v in x]
    if isinstance(x, np.ndarray):
        return json_limpo(x.tolist())
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    return x


def pct(v, casas=1):
    return '—' if v is None else ('%.' + str(casas) + 'f%%') % (100 * v)


def texto_resumo(rel):
    sel = rel['modelagem']['selecao_interna']
    te = rel['modelagem']['teste']
    op = rel['modelagem']['operacional']
    orig = rel['modelagem']['origens']

    def faixa(nome):
        return '%s a %s' % (orig[nome][0], orig[nome][-1])
    lin = ['MODELO INTEGRADO — ÁREAS CRÍTICAS DE GARIMPO',
           'Versão %s | executado em %s' % (rel['versao'], rel['data_execucao']), '',
           'ENTRADAS',
           '  Sub-bacias: %d (%d com K) | socio_pre ligado: %d' % (
               rel['entradas']['n_subbacias'], rel['entradas']['n_com_k'],
               rel['entradas']['n_com_socio']),
           '  Disponibilidade dos alertas: %s' % rel['entradas']['disponibilidade'],
           '  Termos disponíveis: %s' % ', '.join(rel['modelagem']['termos_disponiveis']),
           '  Termos indisponíveis: %s' % (', '.join(rel['modelagem']['termos_indisponiveis']) or '—'),
           '', 'SELEÇÃO INTERNA (origens de ajuste %s; validação %s)' % (
               faixa('ajuste_interno'), faixa('validacao_interna')),
           '  Critério: ' + sel['criterio']]
    for r in sel['transformacao_k_familia']:
        lin.append('  K %-8s | área %-9s | %s' % (r['transformacao_k'], r['familia'], pct(r['captura'])))
    for r in sel['sigma']:
        lin.append('  σ = %4d m%s | %s' % (r['sigma_m'], '' if r['selecionavel'] else ' (diagnóstico)',
                                          pct(r['captura'])))
    for r in sel['especificacoes']:
        lin.append('  %-10s | %s' % (r['especificacao'], pct(r['captura'])))
    e = sel['escolhas']
    lin += ['  ESCOLHAS: K %s, área %s, σ %s m, especificação %s' % (
        e['transformacao_k'], e['familia'], e['sigma_m'], e['especificacao']), '',
        'TESTE HISTÓRICO — EXPLORATÓRIO (origens %s; ajuste final %s)' % (
            faixa('teste'), faixa('ajuste_final')),
        '  %-26s %8s %8s %8s %8s %8s' % ('modelo', 'cap 10%', 'cap 5%', 'cap 1%', 'AUC', 'Σprev/obs')]
    for nome, d in te['metricas'].items():
        razao = (d['ha_previstos'] / d['ha_observados']) if d.get('ha_observados') else None
        lin.append('  %-26s %8s %8s %8s %8s %8s' % (
            nome, pct(d.get('captura_10')), pct(d.get('captura_5')), pct(d.get('captura_1')),
            '—' if d.get('auc_presenca') is None else '%.3f' % d['auc_presenca'],
            '—' if razao is None else '%.2f' % razao))
    for par, d in te['bootstrap'].get('diferencas', {}).items():
        lin.append('  Δ captura 10%% %-24s %+.1f p.p. [IC95 %+.1f; %+.1f]' % (
            par, 100 * d['media'], 100 * d['ic95'][0], 100 * d['ic95'][1]))
    lin += ['', 'PREVISÃO OPERACIONAL — origem %s, horizonte %s' % (op['origem'], op['horizonte']),
            '  Especificação: %s | hectares previstos no domínio: %.1f' % (
                op['especificacao'], op['ha_previstos_total']),
            '  Coeficientes (β por DP; EP robusto por bloco):']
    for c in op['coeficientes']['coeficientes']:
        lin.append('    %-10s presença %+.3f (%s) | área %+.3f (%s)' % (
            c['termo'], c['presenca_beta'],
            '—' if c['presenca_ep'] is None else '%.3f' % c['presenca_ep'],
            c['area_beta'], '—' if c['area_ep'] is None else '%.3f' % c['area_ep']))
    lin += ['', 'ÁREAS CRÍTICAS (crit = hectares previstos × socio_pre/5)']
    for c, n in op['contagem_classes'].items():
        lin.append('  %-26s %d' % (c, n))
    for f, d in op['areas_criticas'].items():
        lin.append('  topo %s: alvo %d | estáveis %d | instáveis %d | topo da previsão fora '
                   'das críticas %d' % (pct(float(f), 0), d['n_alvo'], d['n_estaveis'],
                                        d['n_instaveis'], d['n_previsao_fora_das_criticas']))
    lin += ['', 'AVISOS'] + ['  * ' + a for a in rel['avisos']]
    return '\n'.join(lin)


# ---------------------------------------------------------------------------
# Leitura das camadas no QGIS.
# ---------------------------------------------------------------------------
try:
    from qgis.PyQt.QtCore import QMetaType
    from qgis.core import (
        Qgis, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
        QgsCoordinateTransform, QgsDistanceArea, QgsFeature, QgsFeatureRequest, QgsField,
        QgsFillSymbol, QgsGeometry, QgsPoint, QgsProject, QgsRectangle, QgsRendererCategory,
        QgsSimpleFillSymbolLayer, QgsSpatialIndex, QgsVectorFileWriter, QgsVectorLayer,
    )
    TEM_QGIS = True
except ModuleNotFoundError as e:
    if not (e.name or '').startswith('qgis'):
        raise
    TEM_QGIS = False


def feicoes(lyr, pedido=None):
    pedido = pedido if pedido is not None else QgsFeatureRequest()
    if RESPEITAR_SELECAO and lyr.selectedFeatureCount():
        return lyr.getSelectedFeatures(pedido)
    return lyr.getFeatures(pedido)


def resolver_camada(projeto, nome, ident, papel, problemas, raster=False):
    if ident:
        c = projeto.mapLayer(ident)
        enc = [c] if c else []
    else:
        enc = projeto.mapLayersByName(nome) if nome else []
    if len(enc) != 1:
        problemas.append('%s: %d camadas correspondem a %r. Configure o nome ou o ID.'
                         % (papel, len(enc), ident or nome))
        return None
    c = enc[0]
    eh_raster = hasattr(c, 'bandCount')
    eh_vetor = hasattr(c, 'fields') and hasattr(c, 'getFeatures')
    if not c.isValid() or (raster and not eh_raster) or (not raster and not eh_vetor):
        problemas.append('%s: camada inválida ou de tipo incompatível (%s).' % (papel, c.name()))
        return None
    return c


def campo_existente(lyr, nome, papel, problemas):
    if lyr is None:
        return None
    nomes = lyr.fields().names()
    if nome in nomes:
        return nome
    enc = [n for n in nomes if nome and n.casefold() == nome.casefold()]
    if len(enc) == 1:
        return enc[0]
    problemas.append('%s: campo %r não encontrado de modo inequívoco em %s. Campos: %s.'
                     % (papel, nome, lyr.name(), ', '.join(nomes)))
    return None


def eh_poligono(lyr):
    g = lyr.geometryType()
    return getattr(g, 'value', g) == 2


def achar_socio(projeto, problemas):
    if CAMADA_SOCIO or ID_CAMADA_SOCIO:
        return resolver_camada(projeto, CAMADA_SOCIO, ID_CAMADA_SOCIO, 'Socioambiental', problemas)
    cands = [c for c in projeto.mapLayers().values()
             if c.isValid() and hasattr(c, 'fields')
             and {'socio_pre', CAMPO_ID_SUBBACIAS} <= set(c.fields().names())
             and 'classe_crit' not in c.fields().names()]   # exclui saídas deste script
    if len(cands) != 1:
        problemas.append('Socioambiental: %d camadas carregadas têm %s e socio_pre%s. Execute '
                         'risco_socioambiental_console_v2.py ou configure CAMADA_SOCIO/ID.'
                         % (len(cands), CAMPO_ID_SUBBACIAS, ': ' + ', '.join(
                             '%s [ID=%s]' % (c.name(), c.id()) for c in cands) if cands else ''))
        return None
    return cands[0]


def ler_subbacias(lyr, campo_id, campo_k, crs_m, ctx):
    da = QgsDistanceArea()
    da.setSourceCrs(lyr.crs(), ctx)
    da.setEllipsoid('WGS84')
    tr = QgsCoordinateTransform(lyr.crs(), crs_m, ctx)
    B = dict(ids=[], area=[], geom=[], geom_m=[], k=[], crs=lyr.crs())
    vistos = set()
    for ft in feicoes(lyr):
        k = chave(ft[campo_id])
        if k in vistos:
            raise ValueError('HYBAS_ID repetido nas sub-bacias: %s. Dissolva as geometrias.' % k)
        vistos.add(k)
        g = ft.geometry()
        if g.isNull() or g.isEmpty() or not g.isGeosValid():
            raise ValueError('Geometria vazia/inválida na sub-bacia %s. Corrija a fonte.' % k)
        gm = QgsGeometry(g)
        gm.transform(tr)
        B['ids'].append(k)
        B['area'].append(abs(da.measureArea(g)) / 1e6)
        B['geom'].append(QgsGeometry(g))
        B['geom_m'].append(gm)
        B['k'].append(valor_finito(ft[campo_k], 'K de %s' % k) if campo_k else None)
    if not B['ids']:
        raise ValueError('Nenhuma sub-bacia lida; verifique filtro e seleção.')
    B['area_km2'] = np.array(B['area'])
    B['index'], B['eng'] = QgsSpatialIndex(), []
    for i, gm in enumerate(B['geom_m']):
        B['index'].addFeature(i, gm.boundingBox())
        e = QgsGeometry.createGeometryEngine(gm.constGet())
        e.prepareGeometry()
        B['eng'].append(e)
    B['pos'] = {k: i for i, k in enumerate(B['ids'])}
    return B


def bacia_do_ponto(B, x, y, cands=None):
    pt = QgsPoint(x, y)
    if cands is None:
        cands = B['index'].intersects(QgsRectangle(x, y, x, y))
    for c in cands:
        if B['eng'][c].intersects(pt):
            return c
    return None


def partes_bacias(B, gm):
    """[(bacia, fração da área)] por interseção exata no CRS métrico."""
    cands = B['index'].intersects(gm.boundingBox())
    if not cands:
        return []
    geo = gm.constGet()
    for c in cands:
        if B['eng'][c].contains(geo):
            return [(c, 1.0)]
    a = gm.area()
    if a <= 0:
        return []
    out = []
    for c in cands:
        if B['eng'][c].intersects(geo):
            fr = gm.intersection(B['geom_m'][c]).area() / a
            if fr > 0:
                out.append((c, min(fr, 1.0)))
    return out


def vizinhanca(B):
    src, dst = [], []
    for i, g in enumerate(B['geom_m']):
        r = g.boundingBox()
        r.grow(1.0)
        for j in B['index'].intersects(r):
            if j <= i:
                continue
            gj = B['geom_m'][j]
            if B['eng'][i].intersects(gj.constGet()) or g.distance(gj) <= 1.0:
                src += [i, j]
                dst += [j, i]
    return np.array(src, int), np.array(dst, int)


def blocos(B, km):
    lado = km * 1000.0
    chaves = []
    for gm in B['geom_m']:
        c = gm.centroid().asPoint()
        chaves.append((math.floor(c.x() / lado), math.floor(c.y() / lado)))
    _, inv = np.unique(np.array(chaves), axis=0, return_inverse=True)
    return inv.ravel().astype(int)


class MediaRaster:
    """Média de K nos centros de pixels válidos da grade original, banda 1."""

    def __init__(self, raster, crs_bacias, ctx):
        from osgeo import gdal, ogr, osr
        self.gdal, self.ogr = gdal, ogr
        self.ds = gdal.Open(raster.source(), gdal.GA_ReadOnly)
        if self.ds is None:
            raise ValueError('O GDAL não abriu o raster de K: %s.' % raster.source())
        gt = self.gt = self.ds.GetGeoTransform()
        if gt[2] != 0 or gt[4] != 0 or gt[1] <= 0 or gt[5] >= 0:
            raise ValueError('Raster de K com rotação ou orientação não suportada.')
        self.band = self.ds.GetRasterBand(1)
        self.nodata = self.band.GetNoDataValue()
        self.scale, self.offset = self.band.GetScale(), self.band.GetOffset()
        self.tr = QgsCoordinateTransform(crs_bacias, raster.crs(), ctx)
        self.wkt = self.ds.GetProjection() or raster.crs().toWkt()
        self.srs = osr.SpatialReference()
        self.srs.ImportFromWkt(self.wkt)
        self.drv = ogr.GetDriverByName('MEM') or ogr.GetDriverByName('Memory')

    def media(self, geometria):
        gdal, ogr = self.gdal, self.ogr
        geom = QgsGeometry(geometria)
        geom.transform(self.tr)
        bb, gt = geom.boundingBox(), self.gt
        nx, ny = self.ds.RasterXSize, self.ds.RasterYSize
        x0 = max(0, math.floor((bb.xMinimum() - gt[0]) / gt[1]))
        x1 = min(nx, math.ceil((bb.xMaximum() - gt[0]) / gt[1]))
        y0 = max(0, math.floor((bb.yMaximum() - gt[3]) / gt[5]))
        y1 = min(ny, math.ceil((bb.yMinimum() - gt[3]) / gt[5]))
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return None, 0.0
        if w * h > 25_000_000:
            raise ValueError('Janela de K maior que 25 milhões de pixels; use um campo zonal.')
        mascara = gdal.GetDriverByName('MEM').Create('', w, h, 1, gdal.GDT_Byte)
        mascara.SetGeoTransform((gt[0] + x0 * gt[1], gt[1], 0, gt[3] + y0 * gt[5], 0, gt[5]))
        mascara.SetProjection(self.wkt)
        mascara.GetRasterBand(1).Fill(0)
        vetor = self.drv.CreateDataSource('')
        camada = vetor.CreateLayer('bacia', self.srs, ogr.wkbUnknown)
        feat = ogr.Feature(camada.GetLayerDefn())
        feat.SetGeometry(ogr.CreateGeometryFromWkt(geom.asWkt()))
        camada.CreateFeature(feat)
        gdal.RasterizeLayer(mascara, [1], camada, burn_values=[1])
        dentro = mascara.GetRasterBand(1).ReadAsArray().astype(bool)
        brutos = self.band.ReadAsArray(x0, y0, w, h)
        validos = self.band.GetMaskBand().ReadAsArray(x0, y0, w, h) != 0
        if self.nodata is not None and math.isfinite(self.nodata):
            validos &= brutos != self.nodata
        dados = brutos.astype(np.float64)
        if self.scale is not None:
            dados *= self.scale
        if self.offset is not None:
            dados += self.offset
        validos &= np.isfinite(dados)
        n = int(dentro.sum())
        ok = dentro & validos
        if n == 0 or not ok.any():
            return None, 0.0
        return float(dados[ok].mean()), float(ok.sum()) / n


def k_por_raster(lyr, B, ctx):
    mr = MediaRaster(lyr, B['crs'], ctx)
    K, cob = np.full(len(B['ids']), np.nan), np.zeros(len(B['ids']))
    for i, g in enumerate(B['geom']):
        v, c = mr.media(g)
        cob[i] = c
        if v is not None:
            K[i] = v
    return K, cob


def k_por_vetor(lyr, campo, B, crs_m, ctx):
    """Média de K ponderada pela área das células cujo centróide cai na sub-bacia."""
    tr = QgsCoordinateTransform(lyr.crs(), crs_m, ctx)
    soma, peso = np.zeros(len(B['ids'])), np.zeros(len(B['ids']))
    pedido = QgsFeatureRequest().setSubsetOfAttributes([campo], lyr.fields())
    for n, ft in enumerate(feicoes(lyr, pedido), 1):
        v = valor_finito(ft[campo], 'K na grade')
        if v is None:
            continue
        g = QgsGeometry(ft.geometry())
        if g.isNull() or g.isEmpty():
            continue
        g.transform(tr)
        c = g.centroid().asPoint()
        b = bacia_do_ponto(B, c.x(), c.y())
        if b is not None:
            a = g.area()
            soma[b] += a * v
            peso[b] += a
        if n % 200000 == 0:
            log('  K: %d células lidas' % n)
    area_m = np.array([g.area() for g in B['geom_m']])
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(peso > 0, soma / peso, np.nan), peso / area_m


def ler_socio(lyr, campo_id, B):
    n = len(B['ids'])
    s = {k: np.full(n, np.nan) for k in ('est', 'min', 'max', 'hg')}
    nomes = lyr.fields().names()
    ligada = np.zeros(n, bool)
    vistos = set()
    for ft in feicoes(lyr):
        k = chave(ft[campo_id])
        if k in vistos:
            raise ValueError('HYBAS_ID repetido na camada socioambiental: %s.' % k)
        vistos.add(k)
        i = B['pos'].get(k)
        if i is None:
            continue
        ligada[i] = True
        for campo, alvo in (('socio_pre', 'est'), ('socio_min', 'min'),
                            ('socio_max', 'max'), ('hg_imput', 'hg')):
            if campo in nomes:
                v = valor_finito(ft[campo], '%s de %s' % (campo, k))
                if v is not None:
                    if alvo != 'hg' and not 1 <= v <= 5:
                        raise ValueError('%s de %s fora de 1–5: %r.' % (campo, k, v))
                    s[alvo][i] = v
    if not ligada.any():
        raise ValueError('Nenhum HYBAS_ID da camada socioambiental corresponde às sub-bacias.')
    s['min'] = np.where(np.isfinite(s['min']), s['min'], s['est'])
    s['max'] = np.where(np.isfinite(s['max']), s['max'], s['est'])
    s['status'] = ['sem_ligacao' if not ligada[i] else 'socio_ausente' if np.isnan(s['est'][i])
                   else 'hg_imputado' if s['hg'][i] == 1 else 'observado' for i in range(n)]
    return s


def ler_alertas(lyr, B, base, ultimo, crs_m, ctx):
    campos = lyr.fields()
    i_cls, i_det = campos.indexFromName(CAMPO_CLASSE), campos.indexFromName(CAMPO_DETECCAO)
    i_pub = campos.indexFromName(CAMPO_PUBLICACAO)
    valores = [v for v in lyr.uniqueValues(i_cls) if not ausente(v)]
    mapa = mapa_classes(valores, CLASSES)
    da = QgsDistanceArea()
    da.setSourceCrs(lyr.crs(), ctx)
    da.setEllipsoid('WGS84')
    tr = QgsCoordinateTransform(lyr.crs(), crs_m, ctx)
    pedido = QgsFeatureRequest().setSubsetOfAttributes([i for i in (i_cls, i_det, i_pub) if i >= 0])
    lim = TRUNCAMENTO_SIGMAS * max(SIGMAS_M) if SIGMAS_M else 0
    contribs, rb, bordas = [], [], {}
    st, classes, atrasos = Counter(), Counter(), Counter()
    for n, ft in enumerate(feicoes(lyr, pedido), 1):
        if n % 50000 == 0:
            log('  alertas: %d feições lidas' % n)
        attrs = ft.attributes()
        cls = attrs[i_cls]
        g = None if ausente(cls) else mapa.get(normalizar(cls))
        if g is None:
            st['classe_fora_dos_grupos'] += 1
            continue
        d = ler_data(attrs[i_det])
        if d is None:
            st['deteccao_invalida'] += 1
            continue
        det = indice_mes(d, base)
        if det < 0 or det > ultimo:
            st['fora_da_cobertura_temporal'] += 1
            continue
        if DISPONIBILIDADE == 'publicacao':
            pb = ler_data(attrs[i_pub])
            if pb is None:
                st['sem_publicacao_valida_so_desfecho'] += 1
                disp = None
            else:
                if pb < d:
                    st['publicacao_anterior_a_deteccao'] += 1
                disp = indice_mes(max(pb, d), base)
                atrasos[disp - det] += 1
        else:
            disp = det
        geom = ft.geometry()
        if geom.isNull() or geom.isEmpty():
            st['geometria_vazia'] += 1
            continue
        if not geom.isGeosValid():
            geom = geom.makeValid()
            st['geometria_corrigida'] += 1
        ha = abs(da.measureArea(geom)) / 1e4
        gm = QgsGeometry(geom)
        gm.transform(tr)
        partes = partes_bacias(B, gm)
        if not partes:
            st['fora_do_dominio'] += 1
            continue
        classes['%s → %s' % (cls, g)] += 1
        if g in GRUPOS_SUAVIZADOS:
            dist = None
            if len(partes) == 1 and lim > 0:
                b = partes[0][0]
                if b not in bordas:
                    bordas[b] = QgsGeometry(B['geom_m'][b].constGet().boundary())
                dist = gm.distance(bordas[b])
            perto = lim > 0 and (dist is None or dist <= lim)
            rb.append((g, det, disp, [(b, ha * fr) for b, fr in partes], dist,
                       gm if perto else None))
        else:
            contribs.extend((g, b, det, disp, ha * fr) for b, fr in partes)
    info = dict(contagens=dict(st), classes_lidas=dict(classes),
                classes_sem_grupo=sorted(str(v) for v in valores if normalizar(v) not in mapa),
                atraso_publicacao_meses=dict(sorted(atrasos.items())))
    return contribs, rb, info


def suavizar(B, rb, sigma, semente):
    """Passa-baixa gaussiano na unidade sub-bacia.

    Soma na sub-bacia b do campo suavizado = Σ_D massa(D) · P(D + ε ∈ b), ε ~ N(0, σ²I).
    Detecções a mais de 4σ da divisa ficam inteiras; as demais são distribuídas por
    amostras na detecção e quadratura de Gauss–Hermite. Massa deslocada para fora do
    domínio é renormalizada (análogo a Gσ(v·z)/Gσ(v)).
    """
    nos = [(math.sqrt(2) * sigma * xi, math.sqrt(2) * sigma * yj, wi * wj / math.pi)
           for xi, wi in zip(GH_X, GH_W) for yj, wj in zip(GH_X, GH_W)]
    lim = TRUNCAMENTO_SIGMAS * sigma
    out, info = [], Counter()
    for i, (g, det, disp, pha, dist, gm) in enumerate(rb):
        nome = '%s@%d' % (g, sigma)
        total = sum(v for _, v in pha)
        if sigma == 0 or gm is None or (dist is not None and dist > lim):
            out.extend((nome, b, det, disp, v) for b, v in pha)
            info['inteiras'] += 1
            continue
        n = min(PONTOS_POR_DETECCAO, max(1, math.ceil(gm.area() / 62500.0)))
        try:
            pts = gm.randomPointsInPolygon(n, semente + i)
        except Exception:
            pts = []
        if not pts:
            pts = [gm.pointOnSurface().asPoint()]
        r = gm.boundingBox()
        r.grow(lim)
        cands = B['index'].intersects(r)
        acc, soma = defaultdict(float), 0.0
        for pt in pts:
            for dx, dy, w in nos:
                q = QgsPoint(pt.x() + dx, pt.y() + dy)
                soma += w
                for c in cands:
                    if B['eng'][c].intersects(q):
                        acc[c] += w
                        break
        dentro = sum(acc.values())
        if dentro <= 0:
            out.extend((nome, b, det, disp, v) for b, v in pha)
            info['sem_suporte_mantidas'] += 1
            continue
        info['distribuidas'] += 1
        info['massa_ha'] += total
        info['massa_renormalizada_ha'] += total * (1 - dentro / soma)
        out.extend((nome, c, det, disp, total * w / dentro) for c, w in acc.items())
        if (i + 1) % 20000 == 0:
            log('  passa-baixa σ=%d m: %d de %d detecções' % (sigma, i + 1, len(rb)))
    return out, dict(info)


def preparar_eventos(projeto, problemas):
    out = []
    for ev in EVENTOS:
        sigla = ev.get('sigla')
        if sigla not in SIGLAS_EVENTOS:
            problemas.append('EVENTOS: sigla %r inválida; use %s.' % (sigla, ', '.join(SIGLAS_EVENTOS)))
            continue
        lyr = resolver_camada(projeto, ev.get('camada'), ev.get('id'), 'Eventos %s' % sigla, problemas)
        cd = campo_existente(lyr, ev.get('campo_data'), 'Data de %s' % sigla, problemas)
        cdisp = (campo_existente(lyr, ev['campo_disponibilidade'], 'Disponibilidade de %s' % sigla,
                                 problemas) if ev.get('campo_disponibilidade') else None)
        if lyr is not None and cd:
            out.append(dict(ev, lyr=lyr, cd=cd, cdisp=cdisp))
    return out


def ler_eventos(ev, B, base, ultimo, crs_m, ctx):
    lyr, sigla = ev['lyr'], ev['sigla']
    tr = QgsCoordinateTransform(lyr.crs(), crs_m, ctx)
    pedido = QgsFeatureRequest()
    if ev.get('expressao'):
        pedido.setFilterExpression(ev['expressao'])
    contribs, st = [], Counter()
    for ft in feicoes(lyr, pedido):
        d = ler_data(ft[ev['cd']])
        if d is None:
            st['data_invalida'] += 1
            continue
        det = indice_mes(d, base)
        if det < 0 or det > ultimo:
            st['fora_da_cobertura_temporal'] += 1
            continue
        if ev['cdisp']:
            dd = ler_data(ft[ev['cdisp']])
            disp = None if dd is None else indice_mes(max(d, dd), base)
            st['sem_disponibilidade_so_desfecho' if dd is None else 'com_disponibilidade'] += 1
        else:
            disp = det
        g = ft.geometry()
        if g.isNull() or g.isEmpty():
            st['geometria_vazia'] += 1
            continue
        pt = g.pointOnSurface()
        pt.transform(tr)
        xy = pt.asPoint()
        b = bacia_do_ponto(B, xy.x(), xy.y())
        if b is None:
            st['fora_do_dominio'] += 1
            continue
        st['lidos'] += 1
        contribs.append((sigla, b, det, disp, 1.0))
    st['disponibilidade'] = ('max(evento, %s)' % ev['cdisp'] if ev['cdisp']
                             else 'data do evento (retrospectivo)')
    return contribs, dict(st)


def tipo_qgis(valores, nome):
    textos = {'HYBAS_ID', 's_status', 'classe_crit', 'modelo', 'origem'}
    inteiros = {'rank_prev', 'topo_prev', 'topo_crit', 'topo_sens', 'hg_imput',
                'socio_pre', 'socio_min', 'socio_max'}
    if nome in textos:
        return QMetaType.Type.QString
    if nome in inteiros:
        return QMetaType.Type.Int
    return QMetaType.Type.Double


def valor_saida(v, inteiro=False):
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        if not math.isfinite(float(v)):
            return None
        return int(round(float(v))) if inteiro else float(v)
    if isinstance(v, np.integer):
        return int(v)
    return v


def gravar_camada(B, saida, caminho, nome_camada, ctx):
    mem = QgsVectorLayer('MultiPolygon', nome_camada, 'memory')
    mem.setCrs(B['crs'])
    prov = mem.dataProvider()
    tipos = {n: tipo_qgis(v, n) for n, v in saida.items()}
    prov.addAttributes([QgsField(n, t) for n, t in tipos.items()])
    mem.updateFields()
    feats = []
    for i, g in enumerate(B['geom']):
        f = QgsFeature(mem.fields())
        gg = QgsGeometry(g)
        gg.convertToMultiType()
        f.setGeometry(gg)
        f.setAttributes([valor_saida(saida[n][i], tipos[n] == QMetaType.Type.Int) for n in saida])
        feats.append(f)
    prov.addFeatures(feats)
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = 'GPKG'
    opts.layerName = nome_camada
    res = QgsVectorFileWriter.writeAsVectorFormatV3(mem, str(caminho), ctx, opts)
    if res[0] != QgsVectorFileWriter.WriterError.NoError:
        raise RuntimeError('Falha ao gravar o GeoPackage: %s' % res[1])


def estilizar(camada, pasta):
    cores = {'Crítica': '#bd0026', 'Crítica sob sensibilidade': '#fd8d3c',
             'Não crítica': '#f7f7f7', 'Sem previsão': '#bdbdbd'}
    cats = []
    for classe in CLASSES_CRITICAS:
        s = QgsFillSymbol.createSimple({'color': cores[classe], 'outline_color': '#666666',
                                        'outline_width': '0.15'})
        if classe == 'Crítica sob sensibilidade':
            s.appendSymbolLayer(QgsSimpleFillSymbolLayer.create({
                'color': '#7f2704', 'style': 'b_diagonal', 'outline_style': 'no'}))
        cats.append(QgsRendererCategory(classe, s, classe))
    camada.setRenderer(QgsCategorizedSymbolRenderer('classe_crit', cats))
    camada.triggerRepaint()
    msg, ok = camada.saveNamedStyle(str(pasta / 'areas_criticas.qml'))
    if not ok:
        log('Estilo não salvo: %s' % msg)


def gravar_csv(caminho, linhas_csv):
    if not linhas_csv:
        return
    campos = list(dict.fromkeys(k for r in linhas_csv for k in r))
    with open(caminho, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=campos, delimiter=';', restval='')
        w.writeheader()
        w.writerows(linhas_csv)


def inventario(projeto, pasta, problemas, avisos):
    camadas = []
    for c in projeto.mapLayers().values():
        vetor = hasattr(c, 'fields') and hasattr(c, 'getFeatures')
        r = dict(nome=c.name(), id=c.id(), valida=c.isValid(), crs=c.crs().authid(),
                 tipo='vetor' if vetor else 'raster' if hasattr(c, 'bandCount') else 'outro')
        if vetor:
            r.update(feicoes_visiveis=c.featureCount(), filtro_ativo=bool(c.subsetString()),
                     selecionadas=c.selectedFeatureCount(), campos=c.fields().names())
        camadas.append(r)
    (pasta / 'diagnostico_inputs.json').write_text(json.dumps(
        dict(versao=VERSAO, pendencias=problemas, avisos=avisos, camadas=camadas),
        ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    if not TEM_QGIS:
        raise RuntimeError('Execute este arquivo no editor do console Python do QGIS 4.2.')
    t0 = time.time()
    projeto = QgsProject.instance()
    ctx = projeto.transformContext()
    pasta = Path(PASTA_SAIDA) / dt.datetime.now().strftime('integrado_%Y%m%d_%H%M%S_%f')
    pasta.mkdir(parents=True, exist_ok=False)
    log('Versão %s. Saídas em %s.' % (VERSAO, pasta))
    problemas, avisos = [], []

    bac = resolver_camada(projeto, CAMADA_SUBBACIAS, ID_CAMADA_SUBBACIAS, 'Sub-bacias', problemas)
    if bac is not None and not eh_poligono(bac):
        problemas.append('Sub-bacias: a camada precisa ser poligonal.')
    cid = campo_existente(bac, CAMPO_ID_SUBBACIAS, 'Chave das sub-bacias', problemas)
    ck = lk = None
    if FONTE_K not in ('campo', 'raster', 'vetor'):
        problemas.append("FONTE_K precisa ser 'campo', 'raster' ou 'vetor'.")
    elif FONTE_K in ('campo', 'vetor') and not CAMPO_K:
        problemas.append('Configure CAMPO_K: favorabilidade estática de garimpo ilegal, sem '
                         'população ou mercúrio. Campos das sub-bacias: %s.'
                         % (', '.join(bac.fields().names()) if bac else '—'))
    elif FONTE_K == 'campo':
        ck = campo_existente(bac, CAMPO_K, 'K', problemas)
    else:
        lk = resolver_camada(projeto, CAMADA_K, ID_CAMADA_K, 'K', problemas,
                             raster=FONTE_K == 'raster')
        if FONTE_K == 'vetor':
            ck = campo_existente(lk, CAMPO_K, 'K na grade', problemas)
    soc = achar_socio(projeto, problemas)
    sid = campo_existente(soc, CAMPO_ID_SUBBACIAS, 'Chave socioambiental', problemas)
    al = resolver_camada(projeto, CAMADA_ALERTAS, ID_CAMADA_ALERTAS, 'Alertas', problemas)
    if al is not None and not eh_poligono(al):
        problemas.append('Alertas: a camada precisa ser poligonal para medir hectares.')
    for campo, papel in ((CAMPO_CLASSE, 'Classe dos alertas'), (CAMPO_DETECCAO, 'Detecção')):
        campo_existente(al, campo, papel, problemas)
    if DISPONIBILIDADE == 'publicacao':
        campo_existente(al, CAMPO_PUBLICACAO, 'Publicação', problemas)
    elif DISPONIBILIDADE != 'deteccao':
        problemas.append("DISPONIBILIDADE precisa ser 'publicacao' ou 'deteccao'.")
    evs = preparar_eventos(projeto, problemas)
    try:
        base = mes_tupla(DATA_INICIO_COBERTURA)
        ultimo = indice_mes(mes_tupla(DATA_FIM_COBERTURA), base)
        t_op = indice_mes(mes_tupla(ORIGEM_OPERACIONAL), base)
        periodos = {n: (indice_mes(mes_tupla(a), base), indice_mes(mes_tupla(z), base))
                    for n, (a, z) in PERIODOS.items()}
        faltam = {'ajuste_interno', 'validacao_interna', 'ajuste_final', 'teste'} - set(periodos)
        if faltam:
            problemas.append('PERIODOS sem: %s.' % ', '.join(sorted(faltam)))
        if t_op > ultimo + 1:
            problemas.append('ORIGEM_OPERACIONAL depois do mês seguinte ao fim da cobertura.')
    except ValueError as e:
        problemas.append(str(e))
    crs_m = QgsCoordinateReferenceSystem(CRS_METRICO)
    if not crs_m.isValid() or crs_m.isGeographic():
        problemas.append('CRS_METRICO precisa ser projetado em metros: %s.' % CRS_METRICO)
    for lyr in [c for c in (bac, soc, al, lk) if c is not None] + [e['lyr'] for e in evs]:
        if hasattr(lyr, 'selectedFeatureCount') and lyr.selectedFeatureCount():
            avisos.append('%s: %d feições selecionadas; %s (RESPEITAR_SELECAO = %s).' % (
                lyr.name(), lyr.selectedFeatureCount(),
                'SÓ elas serão lidas' if RESPEITAR_SELECAO else 'seleção IGNORADA',
                RESPEITAR_SELECAO))
    inventario(projeto, pasta, problemas, avisos)
    for a in avisos:
        log('ATENÇÃO: ' + a)
    if problemas:
        for p_ in problemas:
            log('CONFIGURAR: ' + p_)
        log('CÁLCULO NÃO EXECUTADO. Ajuste o bloco de configurações. Diagnóstico: %s'
            % (pasta / 'diagnostico_inputs.json'))
        return {'status': 'configurar', 'pasta': str(pasta)}

    log('Lendo sub-bacias.')
    B = ler_subbacias(bac, cid, ck if FONTE_K == 'campo' else None, crs_m, ctx)
    n_b = len(B['ids'])
    k_cob = None
    if FONTE_K == 'campo':
        K = np.array([np.nan if v is None else v for v in B['k']])
    elif FONTE_K == 'raster':
        log('Média de K por sub-bacia (raster).')
        K, k_cob = k_por_raster(lk, B, ctx)
    else:
        log('Média de K por sub-bacia (grade vetorial).')
        K, k_cob = k_por_vetor(lk, ck, B, crs_m, ctx)
    if not np.isfinite(K).any():
        raise RuntimeError('Nenhuma sub-bacia com K válido.')
    log('Lendo prioridade socioambiental.')
    socio = ler_socio(soc, sid, B)
    log('Vizinhança e blocos espaciais.')
    viz = vizinhanca(B)
    bloco = blocos(B, BLOCO_BOOTSTRAP_KM)
    log('Lendo alertas (pode demorar).')
    contribs, rb, info_al = ler_alertas(al, B, base, ultimo, crs_m, ctx)
    info_ev = {}
    for ev in evs:
        log('Lendo eventos %s.' % ev['sigla'])
        c, info_ev[ev['sigla']] = ler_eventos(ev, B, base, ultimo, crs_m, ctx)
        contribs.extend(c)
    info_pb = {}
    if rb:
        for s in (0,) + tuple(SIGMAS_M):
            log('Passa-baixa de estradas/construções, σ = %d m.' % s)
            c, info_pb[str(s)] = suavizar(B, rb, s, SEMENTE)
            contribs.extend(c)
    del rb
    log('Montando painel sub-bacia × origem.')
    n_m = ultimo + 2
    pred, desf = acumular(contribs, n_b, n_m, MESES_HISTORICO, HORIZONTE_MESES)
    del contribs
    if 'garimpo' not in desf:
        raise RuntimeError('Nenhum alerta de garimpo lido no domínio e na cobertura temporal.')
    painel = dict(base=base, n_b=n_b, n_m=n_m, ids=B['ids'], area_km2=B['area_km2'], K=K,
                  k_cob=k_cob, pred=pred, y=desf['garimpo'], viz=viz,
                  viz_garimpo=soma_vizinhos(pred.get('garimpo', np.zeros((n_b, n_m))), viz),
                  bloco=bloco, socio=socio)
    cfg = dict(L=MESES_HISTORICO, H=HORIZONTE_MESES, ultimo_mes=ultimo, periodos=periodos,
               t_op=t_op, reajuste=REAJUSTE_OPERACIONAL, ktrans=TRANSFORMACOES_K,
               familias=FAMILIAS_AREA, sigmas=SIGMAS_M, tolerancia=TOLERANCIA_SELECAO,
               ridge=RIDGE, topo=TOPO_CRITICO, fracoes=FRACOES_TOPO, n_boot=N_BOOTSTRAP,
               semente=SEMENTE)
    res = executar_modelagem(painel, cfg)

    rel = dict(
        versao=VERSAO, data_execucao=dt.datetime.now().astimezone().isoformat(),
        qgis=Qgis.QGIS_VERSION, finalidade='definição de áreas críticas: previsão calibrada '
        'de hectares de garimpo × prioridade socioambiental preventiva',
        configuracao=dict(
            subbacias=bac.name(), campo_id=cid, fonte_k=FONTE_K, campo_k=ck,
            camada_k=lk.name() if lk else None, socio=soc.name(), alertas=al.name(),
            disponibilidade=DISPONIBILIDADE, classes=CLASSES,
            eventos=[{k: v for k, v in e.items() if k not in ('lyr',)} for e in evs],
            cobertura=[DATA_INICIO_COBERTURA, DATA_FIM_COBERTURA], L=MESES_HISTORICO,
            H=HORIZONTE_MESES, periodos=PERIODOS, origem_operacional=ORIGEM_OPERACIONAL,
            reajuste_operacional=REAJUSTE_OPERACIONAL, crs_metrico=CRS_METRICO,
            sigmas_m=SIGMAS_M, truncamento_sigmas=TRUNCAMENTO_SIGMAS,
            pontos_por_deteccao=PONTOS_POR_DETECCAO, ridge=RIDGE, familias=FAMILIAS_AREA,
            tolerancia=TOLERANCIA_SELECAO, topo_critico=TOPO_CRITICO, fracoes=FRACOES_TOPO,
            bloco_km=BLOCO_BOOTSTRAP_KM, n_bootstrap=N_BOOTSTRAP, semente=SEMENTE,
            respeitar_selecao=RESPEITAR_SELECAO),
        entradas=dict(n_subbacias=n_b, n_com_k=int(np.isfinite(K).sum()),
                      n_com_socio=int(np.isfinite(socio['est']).sum()),
                      n_pares_vizinhos=int(len(viz[0]) // 2), n_blocos=int(bloco.max() + 1),
                      disponibilidade=('max(detecção, publicação) antes da origem'
                                       if DISPONIBILIDADE == 'publicacao' else
                                       'detecção antes da origem — retrospectivo'),
                      alertas=info_al, eventos=info_ev, passa_baixa=info_pb,
                      k_cobertura_media=None if k_cob is None else float(np.nanmean(k_cob))),
        modelagem=res['relatorio'],
        avisos=avisos + res['relatorio']['avisos'] + [
            'Sinais (−) e (x) das figuras foram substituídos por coeficientes estimados; '
            'crit = hectares previstos × socio_pre/5 é convenção normativa, não estimativa.',
            'socio_pre (WorldPop 2025, Rsc_RbM) não entra no ajuste nem na avaliação histórica.',
            'O teste 2024–2025 já foi examinado: os resultados são exploratórios.',
            'G e vizinhança são versões por sub-bacia; não reproduzem a convolução de grade de G.',
            'K é tratado como estático; suas fontes devem anteceder o período de teste.',
            'CAR/SIGEF/ANM só entram com eventos históricos configurados; a camada atual do '
            'SIGMINE não reconstrói estados passados.',
            'Zeros de alertas pressupõem cobertura do produto em todo o domínio e período.',
            'Coeficientes são associações preditivas, não efeitos causais.',
        ])
    if info_al['contagens'].get('sem_publicacao_valida_so_desfecho'):
        rel['avisos'].append('%d alertas sem publicação válida entraram só no desfecho.'
                             % info_al['contagens']['sem_publicacao_valida_so_desfecho'])
    if info_al['classes_sem_grupo']:
        rel['avisos'].append('Classes sem grupo (ignoradas): %s.'
                             % '; '.join(info_al['classes_sem_grupo']))

    log('Gravando saídas.')
    saida = res['saida']
    gpkg = pasta / 'modelo_integrado.gpkg'
    gravar_camada(B, saida, gpkg, 'areas_criticas', ctx)
    n_s = len(saida['HYBAS_ID'])
    gravar_csv(pasta / 'areas_criticas.csv',
               [{k: valor_saida(v[i]) for k, v in saida.items()} for i in range(n_s)])
    sel = res['relatorio']['selecao_interna']
    gravar_csv(pasta / 'selecao_interna.csv',
               [dict(etapa='K/área', **r) for r in sel['transformacao_k_familia']]
               + [dict(etapa='sigma', **r) for r in sel['sigma']]
               + [dict(etapa='especificacao', especificacao=r['especificacao'],
                       termos=' + '.join(r['termos']), captura=r['captura'])
                  for r in sel['especificacoes']])
    gravar_csv(pasta / 'teste_por_origem.csv',
               [dict(modelo=m, **o) for m, d in res['relatorio']['teste']['metricas'].items()
                for o in d.get('por_origem', [])])
    (pasta / 'relatorio_modelo_integrado.json').write_text(
        json.dumps(json_limpo(rel), ensure_ascii=False, indent=2, allow_nan=False),
        encoding='utf-8')
    resumo = texto_resumo(json_limpo(rel))
    (pasta / 'relatorio_modelo_integrado.txt').write_text(resumo, encoding='utf-8')
    camada = QgsVectorLayer('%s|layername=areas_criticas' % gpkg,
                            'Modelo integrado — áreas críticas', 'ogr')
    if camada.isValid():
        estilizar(camada, pasta)
        projeto.addMapLayer(camada)
    else:
        log('GeoPackage gravado, mas a camada não carregou: %s' % gpkg)
    print(resumo)
    log('CONCLUÍDO em %.1f min. Saídas: %s' % ((time.time() - t0) / 60, pasta))
    return {'status': 'ok', 'pasta': str(pasta)}


if TEM_QGIS and EXECUTAR_AO_ABRIR:
    main()

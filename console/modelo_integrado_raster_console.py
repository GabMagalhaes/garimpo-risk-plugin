# -*- coding: utf-8 -*-
"""MODELO INTEGRADO EM RASTER (100 m) — console Python do QGIS 4.2.

Abra este arquivo no EDITOR do console Python e clique em Executar.

Versão em pixel do modelo integrado. Usa as MESMAS entradas, regras temporais,
modelo em duas partes, etapas de seleção e camada de decisão de
modelo_integrado_console.py, carregado como núcleo (sem executar o modelo por
sub-bacia). Camadas, classes, períodos, disponibilidade, eventos, σ, famílias,
ridge e topo vêm do bloco de configurações do núcleo; aqui ficam só as
configurações próprias do raster.

O que muda com a unidade:
  * K: raster de potencial remanescente, reamostrado por média para a grade.
  * G, A, D, R, B e eventos: soma numa janela quadrada de meia-largura r em
    torno do pixel, normalizada pelo suporte do domínio (análogo da
    sub-bacia). Gviz: anel de r a 3r (análogo das vizinhas). r é escolhido na
    validação interna, como σ.
  * R e B: passa-baixa gaussiano normalizado Gσ(v·z)/Gσ(v) na grade, antes da
    janela, como no protocolo de transformação territorial.
  * Ajuste com todas as presenças e uma amostra de ausências por origem,
    ponderada por 1/q; a calibração da probabilidade é preservada.
  * Critério de seleção: captura de hectares nos 10% de pixels priorizados.
    A captura nas 10% sub-bacias, somando a previsão dos pixels, é reportada
    para comparar com o modelo por sub-bacia.
  * socio_pre da sub-bacia vale para todos os seus pixels.

Saídas: GeoTIFFs da previsão, da prioridade e das classes críticas, CSV por
sub-bacia, relatório JSON/TXT e estilo QML. Os inputs não são editados.
"""
import datetime as dt
import importlib.util
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import numpy as np


# CONFIGURAÇÕES DO RASTER — as demais ficam em modelo_integrado_console.py.
ARQUIVO_NUCLEO = (Path.home() / 'OneDrive - Polícia Federal' / 'Mestrado' / 'Produto'
                  / 'garimpo_risk' / 'console' / 'modelo_integrado_console.py')
RESOLUCAO_M = 100
CAMADA_K_RASTER = 'PMT_v2_remanescente'   # proporção remanescente máx(K−μ,0) por pixel
ID_CAMADA_K_RASTER = None
ESCALAS_LOCAIS_M = (1000, 2500, 5000)     # meia-largura r da janela; escolha interna
FATOR_REGIONAL = 3                        # Gviz: anel de r a 3r
SUBPIXEIS = 5                             # 5 × 5 subpixels para a fração de área do alerta
NEGATIVOS_POR_ORIGEM = 30000              # ausências amostradas por origem no ajuste
NEGATIVOS_AUC = 50000                     # ausências por origem para a AUC do teste
BLOCO_PIXELS = 2_000_000                  # pixels por bloco de cálculo (memória)
SALVAR_PREDITORES = False                 # GeoTIFF com os preditores da origem operacional
PASTA_SAIDA = Path.home() / 'Etapa1_modelo_integrado_raster'
EXECUTAR_AO_ABRIR = True
# FIM DAS CONFIGURAÇÕES.

VERSAO = 'integrado_raster_console_v1_20261009'
CODIGOS_CLASSE = {1: 'Crítica', 2: 'Crítica sob sensibilidade', 3: 'Não crítica'}
GRUPOS_JANELA = (('garimpo', 'G'), ('acesso', 'A'), ('outros', 'D'))
GRUPOS_SUAVIZADOS = (('estradas', 'R'), ('construcoes', 'B'))
mi = None                                 # núcleo: modelo_integrado_console.py


def log(mensagem):
    print(time.strftime('%H:%M:%S'), mensagem, flush=True)


def usar_nucleo(modulo):
    global mi
    mi = modulo
    return modulo


def carregar_nucleo(caminho):
    caminho = Path(caminho)
    if not caminho.is_file():
        raise RuntimeError('Núcleo não encontrado: %s. Ajuste ARQUIVO_NUCLEO para o caminho de '
                           'modelo_integrado_console.py.' % caminho)
    os.environ['MODELO_INTEGRADO_SO_NUCLEO'] = '1'
    try:
        spec = importlib.util.spec_from_file_location('modelo_integrado_nucleo', str(caminho))
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
    finally:
        os.environ.pop('MODELO_INTEGRADO_SO_NUCLEO', None)
    return usar_nucleo(modulo)


# ---------------------------------------------------------------------------
# Núcleo numérico da grade (testável sem QGIS).
# ---------------------------------------------------------------------------
class Integral:
    """Imagem integral: soma em janela quadrada (2h+1)² em O(1) por pixel."""

    def __init__(self, a2d):
        ny, nx = a2d.shape
        S = np.zeros((ny + 1, nx + 1))
        S[1:, 1:] = a2d
        np.cumsum(S, axis=0, out=S)
        np.cumsum(S, axis=1, out=S)
        self.S, self.ny, self.nx = S, ny, nx

    def janela(self, iy, ix, h):
        S, ny, nx = self.S, self.ny, self.nx
        out = np.empty(len(iy))
        for a in range(0, len(iy), BLOCO_PIXELS):
            y, x = iy[a:a + BLOCO_PIXELS], ix[a:a + BLOCO_PIXELS]
            y0, y1 = np.clip(y - h, 0, ny), np.clip(y + h + 1, 0, ny)
            x0, x1 = np.clip(x - h, 0, nx), np.clip(x + h + 1, 0, nx)
            out[a:a + len(y)] = S[y1, x1] - S[y0, x1] - S[y1, x0] + S[y0, x0]
        return out


def pesos_gauss(sigma_px, trunc):
    h = max(1, int(math.ceil(trunc * sigma_px)))
    d = np.arange(-h, h + 1)
    w = np.exp(-0.5 * (d / sigma_px) ** 2)
    return d, w / w.sum()


def gauss_denso(a2d, sigma_px, trunc):
    """Convolução gaussiana separável, zeros fora da grade."""
    d, w = pesos_gauss(sigma_px, trunc)
    out = a2d.astype(np.float64)
    for eixo in (1, 0):
        tmp = np.zeros_like(out)
        n = out.shape[eixo]
        for k, wk in zip(d, w):
            if abs(k) >= n:
                continue
            if eixo == 1:
                if k >= 0:
                    tmp[:, k:] += wk * out[:, :n - k]
                else:
                    tmp[:, :n + k] += wk * out[:, -k:]
            elif k >= 0:
                tmp[k:, :] += wk * out[:n - k, :]
            else:
                tmp[:n + k, :] += wk * out[-k:, :]
        out = tmp
    return out


def gauss_esparso(z, ny, nx, sigma_px, trunc):
    """Mesma convolução de gauss_denso, espalhando só os pixels não nulos."""
    out = np.zeros(ny * nx)
    nz = np.flatnonzero(z)
    if not len(nz):
        return out
    d, w = pesos_gauss(sigma_px, trunc)
    dy, dx = (m.ravel() for m in np.meshgrid(d, d, indexing='ij'))
    W = np.outer(w, w).ravel()
    iy, ix = np.divmod(nz, nx)
    val = z[nz]
    passo = max(1, 4_000_000 // len(W))
    for a in range(0, len(nz), passo):
        ty = iy[a:a + passo, None] + dy
        tx = ix[a:a + passo, None] + dx
        ok = (ty >= 0) & (ty < ny) & (tx >= 0) & (tx < nx)
        out += np.bincount((ty * nx + tx)[ok], weights=(val[a:a + passo, None] * W)[ok],
                           minlength=ny * nx)
    return out


def chave(nome, h, sigma=None):
    return '%s|%d|%d' % (nome, h, sigma) if nome in ('R', 'B') else '%s|%d' % (nome, h)


class Contexto:
    def __init__(self, d, cfg):
        g = d['grade']
        self.ny, self.nx, self.res = int(g['ny']), int(g['nx']), float(g['res'])
        self.N = self.ny * self.nx
        self.zona = np.asarray(d['zona'], dtype=np.int32)
        self.v = (self.zona > 0).astype(np.float64)
        self.K = np.asarray(d['K'], dtype=np.float64)
        self.validos = np.flatnonzero((self.zona > 0) & np.isfinite(self.K))
        self.pos_validos = np.full(self.N, -1, dtype=np.int64)
        self.pos_validos[self.validos] = np.arange(len(self.validos))
        self.Sv = Integral(self.v.reshape(self.ny, self.nx))
        self.eventos = d['eventos']
        self.L, self.H, self.F = cfg['L'], cfg['H'], cfg['fator_regional']
        self.trunc = cfg['truncamento']
        self.teto = self.res * self.res / 1e4
        self.hs = {r: max(1, int(round(r / self.res))) for r in cfg['escalas']}
        self.suporte = {}                 # σ → Gσ(v), calculado na primeira vez que for usado

    def gsuporte(self, sigma):
        if sigma not in self.suporte:
            self.suporte[sigma] = gauss_denso(self.v.reshape(self.ny, self.nx), sigma / self.res,
                                              self.trunc).ravel()
        return self.suporte[sigma]


def soma_temporal(c, grupo, t):
    """Detecções em [t−L, t−1] disponíveis antes de t, somadas por pixel."""
    ev = c.eventos[grupo]
    sel = ((ev['disp'] >= 0) & (ev['det'] >= t - c.L) & (ev['det'] <= t - 1)
           & (ev['disp'] <= t - 1))
    return np.bincount(ev['pix'][sel], weights=ev['val'][sel], minlength=c.N)


def desfecho(c, t):
    """Hectares de garimpo detectados em [t, t+H−1], limitados à área do pixel."""
    ev = c.eventos['garimpo']
    sel = (ev['det'] >= t) & (ev['det'] <= t + c.H - 1)
    return np.minimum(np.bincount(ev['pix'][sel], weights=ev['val'][sel], minlength=c.N), c.teto)


def suavizado(c, z, sigma):
    if sigma == 0:
        return z
    num = gauss_esparso(z, c.ny, c.nx, sigma / c.res, c.trunc)
    den = c.gsuporte(sigma)
    return np.where(den > 1e-12, num / np.maximum(den, 1e-12), 0.0) * c.v


def termos_origem(c, t, pts, hs, sigmas, disp):
    """Somas em janela (normalizadas pelo suporte) para os pixels pts na origem t."""
    iy, ix = np.divmod(pts, c.nx)
    den = {h: c.Sv.janela(iy, ix, h) for h in hs}
    den3 = {h: c.Sv.janela(iy, ix, c.F * h) for h in hs} if 'Gviz' in disp else {}
    out = {}

    def janelas(z, nome, sigma=None, anel=False):
        S = Integral(z.reshape(c.ny, c.nx))
        for h in hs:
            n = (2 * h + 1) ** 2
            num = S.janela(iy, ix, h)
            out[chave(nome, h, sigma)] = (num / np.maximum(den[h], 1.0) * n).astype(np.float32)
            if anel:
                n3 = (2 * c.F * h + 1) ** 2
                num3 = S.janela(iy, ix, c.F * h)
                dd = den3[h] - den[h]
                out[chave('Gviz', h)] = np.where(
                    dd > 0.5, (num3 - num) / np.maximum(dd, 1.0) * (n3 - n), 0.0).astype(np.float32)

    for grupo, termo in GRUPOS_JANELA + tuple((s, s) for s in mi.SIGLAS_EVENTOS):
        if termo in disp:
            janelas(soma_temporal(c, grupo, t), termo, anel=termo == 'G' and 'Gviz' in disp)
    for grupo, termo in GRUPOS_SUAVIZADOS:
        if termo in disp:
            z = soma_temporal(c, grupo, t)
            for s in sigmas:
                janelas(suavizado(c, z, s), termo, s)
    return out


def termos_disponiveis_raster(eventos):
    disp = {'K'}
    for grupo, termo in GRUPOS_JANELA + GRUPOS_SUAVIZADOS + tuple((s, s) for s in mi.SIGLAS_EVENTOS):
        ev = eventos.get(grupo)
        if ev is not None and len(ev['disp']) and np.any(ev['disp'] >= 0):
            disp.add(termo)
    if 'G' in disp:
        disp.add('Gviz')
    return disp


def especificacoes_raster(disp):
    out, vistas = {}, set()
    for nome, termos in mi.ESPECIFICACOES:
        t = tuple(x for x in termos if x in disp and x != 'log_area')
        if t not in vistas:
            vistas.add(t)
            out[nome] = t
    return out


def chaves_termos(termos, h, sigma):
    return [chave(n, h, sigma) for n in termos if n != 'K']


def colunas(linhas_amostra, origens, chaves):
    sel = [r for r in linhas_amostra if r['t'] in origens]
    return {k: np.concatenate([r[k] for r in sel]) for k in chaves}


def desenho_linhas(D, termos, kt, h, sigma):
    cols = []
    for nome in termos:
        if nome == 'K':
            cols.append(mi.transformar_k(D['K'].astype(np.float64), kt))
        else:
            cols.append(np.log1p(D[chave(nome, h, sigma)].astype(np.float64)))
    return np.column_stack(cols)


def ajustar_raster(linhas_amostra, origens, termos, fam, kt, h, sigma, cfg, bloco_bacia=None):
    D = colunas(linhas_amostra, set(int(o) for o in origens),
                ['K', 'y', 'w', 'zona'] + chaves_termos(termos, h, sigma))
    X = desenho_linhas(D, termos, kt, h, sigma)
    grupos = bloco_bacia[D['zona']] if bloco_bacia is not None else None
    return mi.DuasPartes(termos, fam, cfg['ridge']).ajustar(
        X, D['y'].astype(np.float64), grupos, pesos=D['w'].astype(np.float64), teto=cfg['teto'])


def captura_amostra(y, t, chaves_ordem, w, frac):
    """Captura nos top-frac da área (Σ pesos) em cada origem, com amostra ponderada."""
    cap = tot = 0.0
    for o in np.unique(t):
        idx = np.flatnonzero(t == o)
        ordem = idx[mi.ordenar([np.asarray(k)[idx] for k in chaves_ordem])]
        wo = w[ordem]
        antes = np.cumsum(wo) - wo
        sel = ordem[antes < frac * wo.sum()]
        cap += float(y[sel].sum())
        tot += float(y[idx].sum())
    return cap / tot if tot > 0 else None


def criterio_linhas(m, linhas_amostra, origens, kt, h, sigma, frac, rk):
    D = colunas(linhas_amostra, origens, ['K', 'y', 'w', 'tt', 'pix'] + chaves_termos(m.termos, h, sigma))
    _, _, E = m.prever(desenho_linhas(D, m.termos, kt, h, sigma))
    return captura_amostra(D['y'], D['tt'], [E, rk[D['pix']]], D['w'], frac)


def prever_pixels(m, T, Kv, kt, h, sigma):
    n = len(Kv)
    p, E = np.empty(n, np.float32), np.empty(n, np.float32)
    ch = chaves_termos(m.termos, h, sigma)
    for a in range(0, n, BLOCO_PIXELS):
        D = {'K': Kv[a:a + BLOCO_PIXELS]}
        for k in ch:
            D[k] = T[k][a:a + BLOCO_PIXELS]
        pr, _, e = m.prever(desenho_linhas(D, m.termos, kt, h, sigma))
        p[a:a + len(pr)], E[a:a + len(e)] = pr, e
    return p, E


def topo(score, n):
    n = max(1, min(int(n), len(score)))
    return np.argpartition(-score, n - 1)[:n]


def membros(score, n):
    ok = np.isfinite(score)
    m = np.zeros(len(score), bool)
    k = int(ok.sum())
    if n <= 0 or k == 0:
        return m
    n = min(int(n), k)
    corte = np.partition(score[ok], k - n)[k - n]
    return ok & (score >= corte)


def classes_criticas(E, s_est, s_min, s_max, fracoes, frac_op, km2_pixel):
    """Mesma regra de areas_criticas do núcleo, com topo em fração da área (pixels)."""
    s_min = np.where(np.isfinite(s_min), s_min, np.where(np.isfinite(s_est), s_est, 1.0))
    s_max = np.where(np.isfinite(s_max), s_max, np.where(np.isfinite(s_est), s_est, 5.0))
    cen = dict(crit_min=E * s_min / 5, crit_est=E * s_est / 5, crit_max=E * s_max / 5)
    n_base = len(E)
    tops = {k: np.zeros(n_base, np.uint8) for k in ('topo_prev', 'topo_crit', 'topo_sens')}
    resumo = {}
    for f in sorted(fracoes):
        n = math.ceil(f * n_base)
        pct = int(round(f * 100))
        mp = membros(E, n)
        ms = [membros(v, n) for v in cen.values()]
        todos, algum = np.logical_and.reduce(ms), np.logical_or.reduce(ms)
        inst = algum & ~todos
        for nome, m in (('topo_prev', mp), ('topo_crit', todos), ('topo_sens', inst)):
            tops[nome][m & (tops[nome] == 0)] = pct
        resumo[str(f)] = dict(n_base=n_base, n_alvo=n, area_alvo_km2=n * km2_pixel,
                              n_topo_previsao=int(mp.sum()), n_estaveis=int(todos.sum()),
                              n_instaveis=int(inst.sum()),
                              n_previsao_e_criticas=int((mp & todos).sum()),
                              n_previsao_fora_das_criticas=int((mp & ~algum).sum()))
    lim = int(round(frac_op * 100))
    classe = np.full(n_base, 3, np.uint8)
    classe[(tops['topo_sens'] > 0) & (tops['topo_sens'] <= lim)] = 2
    classe[(tops['topo_crit'] > 0) & (tops['topo_crit'] <= lim)] = 1
    return dict(cen, classe=classe, resumo=resumo, **tops)


def selecionar_raster(linhas_amostra, especs, disp, origens, ktrans, cfg, rk, c, progresso):
    aj = set(int(o) for o in origens['ajuste_interno'])
    va = set(int(o) for o in origens['validacao_interna'])
    frac = cfg['topo']
    progresso('Seleção interna 1/3: escala r, transformação de K e família da área (K + G).')
    tab1 = []
    for r_m, h in sorted(c.hs.items()):
        for kt in ktrans:
            for fam in cfg['familias']:
                m = ajustar_raster(linhas_amostra, aj, especs['KG'], fam, kt, h, None, cfg)
                tab1.append(dict(escala_m=r_m, h_px=h, transformacao_k=kt, familia=fam,
                                 captura=criterio_linhas(m, linhas_amostra, va, kt, h, None, frac, rk)))
    e1 = mi.melhor(tab1)
    r_m, h, kt, fam = e1['escala_m'], e1['h_px'], e1['transformacao_k'], e1['familia']
    sigma, tab2 = None, []
    if 'KG+RB' in especs:
        progresso('Seleção interna 2/3: σ do passa-baixa (K + G + R + B).')
        for s in (0,) + tuple(cfg['sigmas']):
            m = ajustar_raster(linhas_amostra, aj, especs['KG+RB'], fam, kt, h, s, cfg)
            tab2.append(dict(sigma_m=s, selecionavel=s > 0,
                             captura=criterio_linhas(m, linhas_amostra, va, kt, h, s, frac, rk)))
        sigma = mi.melhor([r for r in tab2 if r['selecionavel']])['sigma_m']
    progresso('Seleção interna 3/3: especificação.')
    D = colunas(linhas_amostra, va, ['K', 'y', 'w', 'tt', 'pix', chave('G', h)])
    tab3 = [dict(especificacao='regua', termos=['G bruto na janela; desempate por K'],
                 selecionavel=False,
                 captura=captura_amostra(D['y'], D['tt'], [D[chave('G', h)], D['K'], rk[D['pix']]],
                                         D['w'], frac))]
    for nome, termos in especs.items():
        m = ajustar_raster(linhas_amostra, aj, termos, fam, kt, h, sigma, cfg)
        tab3.append(dict(especificacao=nome, termos=list(termos), selecionavel=True,
                         captura=criterio_linhas(m, linhas_amostra, va, kt, h, sigma, frac, rk)))
    cands = [r for r in tab3 if r['selecionavel'] and r['captura'] is not None]
    teto = max(r['captura'] for r in cands)
    escolhida = next(r['especificacao'] for r in cands if r['captura'] >= teto - cfg['tolerancia'])
    escolha = dict(escala_m=r_m, h=h, transformacao_k=kt, familia=fam, sigma=sigma,
                   especificacao=escolhida)
    rel = dict(criterio='captura de hectares nos %d%% de pixels priorizados (área), com '
               'ausências amostradas ponderadas' % round(frac * 100),
               escala_transformacao_k_familia=tab1, sigma=tab2, especificacoes=tab3,
               escolhas=dict(escala_m=r_m, transformacao_k=kt, familia=fam, sigma_m=sigma,
                             especificacao=escolhida, tolerancia=cfg['tolerancia']))
    return escolha, rel


def executar_raster(d, cfg, progresso=log):
    """Passagem cronológica pelas origens: amostra, seleção, teste em grade cheia, operação."""
    rel = dict(avisos=[])
    c = Contexto(d, cfg)
    base, L, H, ult = d['base'], cfg['L'], cfg['H'], cfg['ultimo_mes']
    origens = {}
    for nome, (a, z) in cfg['periodos'].items():
        validas = [t for t in range(a, z + 1) if t - L >= 0 and t + H - 1 <= ult]
        excl = [mi.rotulo_mes(t, base) for t in range(a, z + 1) if t not in validas]
        if excl:
            rel['avisos'].append('%s: origens sem histórico ou desfecho completos excluídas: %s.'
                                 % (nome, ', '.join(excl)))
        if not validas:
            raise ValueError('Período %s sem origens válidas.' % nome)
        origens[nome] = np.array(validas, int)
    t_sel = int(max(origens['ajuste_final'].max(), origens['validacao_interna'].max()))
    if origens['teste'].min() <= t_sel or origens['ajuste_interno'].max() >= origens['validacao_interna'].min():
        raise ValueError('Períodos sobrepostos: ajuste interno < validação ≤ ajuste final < teste.')
    rel['origens'] = {n: [mi.rotulo_mes(t, base) for t in o] for n, o in origens.items()}
    disp = termos_disponiveis_raster(c.eventos)
    especs = especificacoes_raster(disp)
    if 'KG' not in especs:
        raise ValueError('Sem garimpo disponível como preditor: K + G não pode ser ajustado.')
    rel['termos_disponiveis'] = sorted(disp)
    rel['especificacoes'] = {n: list(t) for n, t in especs.items()}
    nv = len(c.validos)
    if nv == 0:
        raise ValueError('Nenhum pixel com K válido dentro das sub-bacias.')
    Kv = c.K[c.validos]
    ktrans = [k for k in cfg['ktrans'] if mi.transformacao_valida(k, Kv)]
    if len(ktrans) < len(cfg['ktrans']):
        rel['avisos'].append('K em [%.4g, %.4g]: transformações incompatíveis descartadas: %s.'
                             % (Kv.min(), Kv.max(), ', '.join(k for k in cfg['ktrans'] if k not in ktrans)))
    rng = np.random.default_rng(cfg['semente'])
    rk = rng.random(c.N).astype(np.float32)
    hs_todos = sorted(set(c.hs.values()))
    sig_todos = ((0,) + tuple(cfg['sigmas'])) if ('R' in disp or 'B' in disp) else ()
    t_fim = ult - H + 1
    t_ini = int(min(o.min() for o in origens.values()))
    teste = set(int(o) for o in origens['teste'])
    zona_v = c.zona[c.validos] - 1
    n_b = d['n_b']
    n_px_b = np.bincount(zona_v, minlength=n_b)
    K_b = np.where(n_px_b > 0, np.bincount(zona_v, Kv, minlength=n_b) / np.maximum(n_px_b, 1), 0.0)
    dominio = np.flatnonzero(c.zona > 0)
    amostras, escolha, finais = [], None, None
    acc = dict(cap={}, Eb={}, yb=[], p_auc={}, y_auc=[], soma_E={}, soma_y=0.0, por_origem=[])

    for t in range(t_ini, t_fim + 1):
        t0 = time.time()
        y_full = desfecho(c, t)
        yv = y_full[c.validos]
        pos = c.validos[yv > 0]
        pool = c.validos[yv == 0]
        n_neg = min(cfg['negativos'], len(pool))
        neg = rng.choice(pool, n_neg, replace=False) if n_neg else pool[:0]
        q = n_neg / max(len(pool), 1)
        pts = np.concatenate([pos, neg])
        w = np.concatenate([np.ones(len(pos)), np.full(n_neg, 1.0 / q if q > 0 else 0.0)])
        if escolha is None:
            hs, sigs = hs_todos, sig_todos
        else:
            hs = [escolha['h']]
            sigs = [escolha['sigma']] if escolha['sigma'] is not None else []
        if t in teste:
            Tf = termos_origem(c, t, c.validos, hs, sigs, disp)
            T = {k: v[c.pos_validos[pts]] for k, v in Tf.items()}
        else:
            Tf, T = None, termos_origem(c, t, pts, hs, sigs, disp)
        amostras.append(dict(t=t, tt=np.full(len(pts), t, np.int32), pix=pts,
                             zona=(c.zona[pts] - 1).astype(np.int32), w=w.astype(np.float32),
                             y=y_full[pts].astype(np.float32), K=c.K[pts].astype(np.float32), **T))

        if t == t_sel:
            escolha, rel['selecao_interna'] = selecionar_raster(
                amostras, especs, disp, origens, ktrans, cfg, rk, c, progresso)
            progresso('Escolhas: r = %d m, K %s, área %s, σ %s m, especificação %s.' % (
                escolha['escala_m'], escolha['transformacao_k'], escolha['familia'],
                escolha['sigma'], escolha['especificacao']))
            h, s, kt, fam = escolha['h'], escolha['sigma'], escolha['transformacao_k'], escolha['familia']
            finais = {nome: ajustar_raster(amostras, origens['ajuste_final'], termos, fam, kt, h, s,
                                           cfg, d['bloco']) for nome, termos in especs.items()}
            manter = {'t', 'tt', 'pix', 'zona', 'w', 'y', 'K'} | set(
                chaves_termos([n for n in disp if n != 'K'], h, s))
            for r in amostras:
                for k in [k for k in r if k not in manter]:
                    del r[k]

        if t in teste:
            h, s, kt = escolha['h'], escolha['sigma'], escolha['transformacao_k']
            soma = float(yv.sum())
            acc['soma_y'] += soma
            acc['yb'].append(np.bincount(zona_v, yv, minlength=n_b))
            pos_v = np.flatnonzero(yv > 0)
            neg_v = rng.choice(np.flatnonzero(yv == 0), min(cfg['negativos_auc'], int((yv == 0).sum())),
                               replace=False)
            idx_auc = np.concatenate([pos_v, neg_v])
            acc['y_auc'].append(yv[idx_auc] > 0)
            linha = dict(origem=mi.rotulo_mes(t, base), ha=soma)
            ordem_regua = mi.ordenar([Tf[chave('G', h)], Kv, rk[c.validos]])
            z_gar = soma_temporal(c, 'garimpo', t)
            for nome in list(finais) + ['regua']:
                if nome == 'regua':
                    ordem = ordem_regua
                    E_b = np.bincount(c.zona[dominio] - 1, z_gar[dominio], minlength=n_b)
                    chaves_b = [E_b, K_b]
                else:
                    pr, E = prever_pixels(finais[nome], Tf, Kv, kt, h, s)
                    acc['soma_E'][nome] = acc['soma_E'].get(nome, 0.0) + float(E.sum())
                    acc['p_auc'].setdefault(nome, []).append(pr[idx_auc])
                    E_b = np.bincount(zona_v, E, minlength=n_b)
                    chaves_b = [E_b]
                for f in cfg['fracoes']:
                    n = math.ceil(f * nv)
                    sel = ordem[:n] if nome == 'regua' else topo(E, n)
                    cap = float(yv[sel].sum())
                    acc['cap'].setdefault(nome, {}).setdefault(f, [0.0, 0.0])
                    acc['cap'][nome][f][0] += cap
                    acc['cap'][nome][f][1] += soma
                    if abs(f - cfg['topo']) < 1e-12:
                        linha[nome] = cap / soma if soma > 0 else None
                acc['Eb'].setdefault(nome, []).append(chaves_b)
            acc['por_origem'].append(linha)
            del Tf
        progresso('Origem %s: %d presenças, %d ausências amostradas%s (%.0f s).' % (
            mi.rotulo_mes(t, base), len(pos), n_neg, ' + teste em grade cheia' if t in teste else '',
            time.time() - t0))

    progresso('Métricas do teste e bootstrap por sub-bacia.')
    n_o = len(acc['yb'])
    yb = np.concatenate(acc['yb'])
    tb = np.repeat(np.arange(n_o), n_b)
    bb = np.tile(np.arange(n_b), n_o)
    rk_b = np.random.default_rng(cfg['semente'] + 1).random(n_b)
    metr, ordens = {}, {}
    y_auc = np.concatenate(acc['y_auc'])
    for nome in list(finais) + ['regua']:
        dm = {}
        for f, (cap, tot) in acc['cap'][nome].items():
            dm['captura_pixel_%d' % round(f * 100)] = cap / tot if tot > 0 else None
        chaves_b = [np.concatenate([e[i] for e in acc['Eb'][nome]]) for i in range(len(acc['Eb'][nome][0]))]
        chaves_b.append(rk_b[bb])
        for f in cfg['fracoes']:
            cb = mi.captura(yb, tb, chaves_b, f, d['area_km2'][bb])
            dm['captura_subbacia_%d' % round(f * 100)] = cb['agregada']
            dm['fracao_area_subbacia_%d' % round(f * 100)] = cb['fracao_area']
        ordens[nome] = mi.ordens_origem(tb, chaves_b)
        if nome in finais:
            p_auc = np.concatenate(acc['p_auc'][nome])
            dm.update(auc_presenca=mi.auc(p_auc, y_auc), ha_previstos=acc['soma_E'][nome],
                      ha_observados=acc['soma_y'])
        metr[nome] = dm
    pares = [(n, 'KG') for n in finais if n != 'KG'] + [(n, 'regua') for n in finais]
    boot = mi.bootstrap_pareado(yb, tb, d['bloco'][bb], ordens, cfg['topo'], cfg['n_boot'],
                                cfg['semente'], pares)
    escolhida = escolha['especificacao']
    rel['teste'] = dict(natureza='exploratório: período já examinado em rodadas anteriores',
                        metricas=metr, bootstrap_subbacias=boot, por_origem=acc['por_origem'],
                        coeficientes_ajuste_final=finais[escolhida].coeficientes())

    h, s, kt, fam = escolha['h'], escolha['sigma'], escolha['transformacao_k'], escolha['familia']
    oo = origens['ajuste_final'] if cfg['reajuste'] == 'ajuste_final' else np.arange(t_ini, t_fim + 1)
    t_op = cfg['t_op']
    if t_op - L < 0 or t_op > ult + 1:
        raise ValueError('Origem operacional fora da cobertura dos preditores.')
    progresso('Ajuste operacional (%s a %s) e previsão em grade cheia na origem %s.' % (
        mi.rotulo_mes(oo[0], base), mi.rotulo_mes(oo[-1], base), mi.rotulo_mes(t_op, base)))
    m_op = ajustar_raster(amostras, oo, especs[escolhida], fam, kt, h, s, cfg, d['bloco'])
    del amostras
    sigs = [s] if s is not None else []
    T = termos_origem(c, t_op, c.validos, [h], sigs, disp)
    p, E = prever_pixels(m_op, T, Kv, kt, h, s)
    so = d['socio']
    km2_px = c.teto / 100.0
    cls = classes_criticas(E.astype(np.float64), so['est'][zona_v], so['min'][zona_v],
                           so['max'][zona_v], cfg['fracoes'], cfg['topo'], km2_px)

    def cheio(v, tipo=np.float32, vazio=np.nan):
        a = np.full(c.N, vazio, dtype=tipo)
        a[c.validos] = v
        return a
    rasters = dict(ha_prev=cheio(E), p_prev=cheio(p), crit_est=cheio(cls['crit_est']),
                   crit_min=cheio(cls['crit_min']), crit_max=cheio(cls['crit_max']))
    codigos = dict(classe=cheio(cls['classe'], np.uint8, 0), topo_prev=cheio(cls['topo_prev'], np.uint8, 0),
                   topo_crit=cheio(cls['topo_crit'], np.uint8, 0),
                   topo_sens=cheio(cls['topo_sens'], np.uint8, 0))
    preditores = {'K': cheio(Kv)}
    preditores.update({k: cheio(v) for k, v in T.items()})
    with np.errstate(invalid='ignore', divide='ignore'):
        por_bacia = dict(
            HYBAS_ID=list(d['ids']), n_pixels=n_px_b, ha_prev=np.bincount(zona_v, E, minlength=n_b),
            crit_est=np.bincount(zona_v, np.nan_to_num(cls['crit_est']), minlength=n_b),
            pct_critica=np.bincount(zona_v, cls['classe'] == 1, minlength=n_b) / n_px_b,
            pct_sensivel=np.bincount(zona_v, cls['classe'] == 2, minlength=n_b) / n_px_b,
            socio_pre=so['est'], s_status=list(so['status']))
    rel['operacional'] = dict(
        origem=mi.rotulo_mes(t_op, base),
        horizonte='%s a %s' % (mi.rotulo_mes(t_op, base), mi.rotulo_mes(t_op + H - 1, base)),
        especificacao=escolhida, coeficientes=m_op.coeficientes(),
        ha_previstos_total=float(E.sum()), n_pixels_validos=nv,
        areas_criticas=cls['resumo'],
        contagem_classes={CODIGOS_CLASSE[k]: int((cls['classe'] == k).sum()) for k in CODIGOS_CLASSE})
    return dict(relatorio=rel, rasters=rasters, codigos=codigos, preditores=preditores,
                por_bacia=por_bacia, escolha=escolha)


def texto_resumo_raster(rel):
    pct = mi.pct
    md = rel['modelagem']
    sel, te, op = md['selecao_interna'], md['teste'], md['operacional']
    g = rel['grade']
    lin = ['MODELO INTEGRADO EM RASTER — ÁREAS CRÍTICAS DE GARIMPO',
           'Versão %s | núcleo %s | %s' % (rel['versao'], rel['versao_nucleo'], rel['data_execucao']),
           '', 'GRADE: %d m | %d × %d | %d pixels válidos (%.0f km²) | %s' % (
               g['res'], g['nx'], g['ny'], g['n_validos'], g['area_validos_km2'], g['crs']),
           '  Termos disponíveis: %s' % ', '.join(md['termos_disponiveis']),
           '', 'SELEÇÃO INTERNA — ' + sel['criterio']]
    for r in sel['escala_transformacao_k_familia']:
        lin.append('  r %5d m | K %-8s | área %-9s | %s' % (
            r['escala_m'], r['transformacao_k'], r['familia'], pct(r['captura'])))
    for r in sel['sigma']:
        lin.append('  σ = %4d m%s | %s' % (r['sigma_m'], '' if r['selecionavel'] else ' (diagnóstico)',
                                          pct(r['captura'])))
    for r in sel['especificacoes']:
        lin.append('  %-10s | %s' % (r['especificacao'], pct(r['captura'])))
    e = sel['escolhas']
    lin += ['  ESCOLHAS: r %s m, K %s, área %s, σ %s m, especificação %s' % (
        e['escala_m'], e['transformacao_k'], e['familia'], e['sigma_m'], e['especificacao']), '',
        'TESTE HISTÓRICO — EXPLORATÓRIO (%s a %s)' % (md['origens']['teste'][0], md['origens']['teste'][-1]),
        '  %-10s %9s %9s %9s %9s %9s %7s' % ('modelo', 'pix 10%', 'pix 5%', 'pix 1%', 'sub 10%',
                                            'sub 5%', 'AUC')]
    for nome, d in te['metricas'].items():
        lin.append('  %-10s %9s %9s %9s %9s %9s %7s' % (
            nome, pct(d.get('captura_pixel_10')), pct(d.get('captura_pixel_5')),
            pct(d.get('captura_pixel_1')), pct(d.get('captura_subbacia_10')),
            pct(d.get('captura_subbacia_5')),
            '—' if d.get('auc_presenca') is None else '%.3f' % d['auc_presenca']))
    lin.append('  "pix": hectares nos x% de pixels; "sub": hectares nas x% sub-bacias, somando os '
               'pixels — comparável ao modelo por sub-bacia.')
    for par, d in te['bootstrap_subbacias'].get('diferencas', {}).items():
        lin.append('  Δ captura sub-bacias 10%% %-22s %+.1f p.p. [IC95 %+.1f; %+.1f]' % (
            par, 100 * d['media'], 100 * d['ic95'][0], 100 * d['ic95'][1]))
    lin += ['', 'PREVISÃO OPERACIONAL — origem %s, horizonte %s' % (op['origem'], op['horizonte']),
            '  Especificação %s | hectares previstos: %.1f' % (op['especificacao'], op['ha_previstos_total']),
            '  Coeficientes (β por DP; EP robusto por bloco):']
    for c in op['coeficientes']['coeficientes']:
        lin.append('    %-10s presença %+.3f (%s) | área %+.3f (%s)' % (
            c['termo'], c['presenca_beta'], '—' if c['presenca_ep'] is None else '%.3f' % c['presenca_ep'],
            c['area_beta'], '—' if c['area_ep'] is None else '%.3f' % c['area_ep']))
    lin += ['', 'ÁREAS CRÍTICAS (crit = hectares previstos × socio_pre/5; topo em fração da área)']
    for k, n in op['contagem_classes'].items():
        lin.append('  %-26s %d pixels' % (k, n))
    for f, d in op['areas_criticas'].items():
        lin.append('  topo %s (%.0f km²): estáveis %d | instáveis %d | topo da previsão fora das '
                   'críticas %d' % (pct(float(f), 0), d['area_alvo_km2'], d['n_estaveis'],
                                    d['n_instaveis'], d['n_previsao_fora_das_criticas']))
    lin += ['', 'AVISOS'] + ['  * ' + a for a in rel['avisos']]
    return '\n'.join(lin)


# ---------------------------------------------------------------------------
# Leitura e gravação no QGIS.
# ---------------------------------------------------------------------------
try:
    from qgis.PyQt.QtGui import QColor
    from qgis.core import (
        Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeatureRequest,
        QgsGeometry, QgsPalettedRasterRenderer, QgsProject, QgsRasterLayer,
    )
    TEM_QGIS = True
except ModuleNotFoundError as e:
    if not (e.name or '').startswith('qgis'):
        raise
    TEM_QGIS = False


def srs_grade(grade):
    from osgeo import osr
    srs = osr.SpatialReference()
    srs.SetFromUserInput(grade['srs'])
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def transformada(grade):
    return (grade['x0'], grade['res'], 0.0, grade['ytop'], 0.0, -grade['res'])


def geom_ogr(g):
    from osgeo import ogr
    wkb = g.asWkb()
    wkb = wkb.data() if hasattr(wkb, 'data') else bytes(wkb)
    o = ogr.CreateGeometryFromWkb(wkb)
    return o if o is not None else ogr.CreateGeometryFromWkt(g.asWkt())


def montar_grade(B, res, crs_m):
    caixas = [g.boundingBox() for g in B['geom_m']]
    xmin, xmax = min(b.xMinimum() for b in caixas), max(b.xMaximum() for b in caixas)
    ymin, ymax = min(b.yMinimum() for b in caixas), max(b.yMaximum() for b in caixas)
    x0, ytop = math.floor(xmin / res) * res, math.ceil(ymax / res) * res
    return dict(x0=float(x0), ytop=float(ytop), res=float(res),
                nx=int(math.ceil((xmax - x0) / res)), ny=int(math.ceil((ytop - ymin) / res)),
                srs=crs_m.authid() or crs_m.toWkt(), crs=crs_m.authid())


def rasterizar_zonas(B, grade):
    from osgeo import gdal, ogr
    srs = srs_grade(grade)
    drv = ogr.GetDriverByName('MEM') or ogr.GetDriverByName('Memory')
    ds = drv.CreateDataSource('')
    lyr = ds.CreateLayer('z', srs, ogr.wkbUnknown)
    lyr.CreateField(ogr.FieldDefn('z', ogr.OFTInteger))
    for i, gm in enumerate(B['geom_m']):
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField('z', i + 1)
        f.SetGeometry(geom_ogr(gm))
        lyr.CreateFeature(f)
    r = gdal.GetDriverByName('MEM').Create('', grade['nx'], grade['ny'], 1, gdal.GDT_Int32)
    r.SetGeoTransform(transformada(grade))
    r.SetProjection(srs.ExportToWkt())
    gdal.RasterizeLayer(r, [1], lyr, options=['ATTRIBUTE=z'])
    return r.GetRasterBand(1).ReadAsArray().ravel().astype(np.int32)


def k_na_grade(lyr, grade):
    """K reamostrado por média da área para a grade (GDAL Warp 'average')."""
    from osgeo import gdal
    xmax = grade['x0'] + grade['nx'] * grade['res']
    ymin = grade['ytop'] - grade['ny'] * grade['res']
    opcoes = dict(format='MEM', dstSRS=srs_grade(grade).ExportToWkt(),
                  outputBounds=(grade['x0'], ymin, xmax, grade['ytop']),
                  xRes=grade['res'], yRes=grade['res'], resampleAlg='average',
                  outputType=gdal.GDT_Float32, dstNodata=-9999.0, multithread=True)
    if lyr.crs().isValid():
        opcoes['srcSRS'] = lyr.crs().toWkt()
    ds = gdal.Warp('', lyr.source(), **opcoes)
    if ds is None:
        raise RuntimeError('O GDAL não reamostrou o raster de K: %s.' % lyr.source())
    a = ds.GetRasterBand(1).ReadAsArray().astype(np.float64).ravel()
    a[(a == -9999.0) | ~np.isfinite(a)] = np.nan
    return a


class Rasterizador:
    """Fração da área de cada pixel coberta pelo polígono, com subpixels."""

    def __init__(self, grade, sub):
        from osgeo import gdal, ogr
        self.gdal, self.g, self.sub = gdal, grade, sub
        self.mem = gdal.GetDriverByName('MEM')
        drv = ogr.GetDriverByName('MEM') or ogr.GetDriverByName('Memory')
        self.ds = drv.CreateDataSource('')
        self.srs = srs_grade(grade)
        self.wkt = self.srs.ExportToWkt()
        self.lyr = self.ds.CreateLayer('a', self.srs, ogr.wkbUnknown)
        self.ogr = ogr

    def fracoes(self, geom):
        g, R = self.g, self.g['res']
        bb = geom.boundingBox()
        x0 = max(0, math.floor((bb.xMinimum() - g['x0']) / R))
        x1 = min(g['nx'], math.ceil((bb.xMaximum() - g['x0']) / R))
        y0 = max(0, math.floor((g['ytop'] - bb.yMaximum()) / R))
        y1 = min(g['ny'], math.ceil((g['ytop'] - bb.yMinimum()) / R))
        vazio = (np.zeros(0, np.int64), np.zeros(0))
        if x1 <= x0 or y1 <= y0:
            return vazio
        w, h = x1 - x0, y1 - y0
        s = self.sub if w * h * self.sub ** 2 <= 25_000_000 else 1
        r = self.mem.Create('', w * s, h * s, 1, self.gdal.GDT_Byte)
        r.SetGeoTransform((g['x0'] + x0 * R, R / s, 0, g['ytop'] - y0 * R, 0, -R / s))
        r.SetProjection(self.wkt)
        f = self.ogr.Feature(self.lyr.GetLayerDefn())
        f.SetGeometry(geom_ogr(geom))
        self.lyr.CreateFeature(f)
        self.gdal.RasterizeLayer(r, [1], self.lyr, burn_values=[1])
        self.lyr.DeleteFeature(f.GetFID())
        a = r.GetRasterBand(1).ReadAsArray().reshape(h, s, w, s).sum(axis=(1, 3)).astype(np.float64)
        area_px = geom.area() / (R * R)
        iy, ix = np.nonzero(a)
        if not len(iy):
            pt = geom.pointOnSurface().asPoint()
            cx, cy = int((pt.x() - g['x0']) // R), int((g['ytop'] - pt.y()) // R)
            if 0 <= cx < g['nx'] and 0 <= cy < g['ny']:
                return np.array([cy * g['nx'] + cx], np.int64), np.array([area_px])
            return vazio
        fr = a[iy, ix]
        return (iy + y0).astype(np.int64) * g['nx'] + (ix + x0), fr * (area_px / fr.sum())


def juntar_eventos(partes):
    out = {}
    for grupo, lst in partes.items():
        if lst['pix']:
            out[grupo] = dict(pix=np.concatenate(lst['pix']), det=np.concatenate(lst['det']).astype(np.int32),
                              disp=np.concatenate(lst['disp']).astype(np.int32),
                              val=np.concatenate(lst['val']))
    return out


def ler_alertas_raster(lyr, grade, zona, base, ultimo, crs_m, ctx):
    campos = lyr.fields()
    i_cls, i_det = campos.indexFromName(mi.CAMPO_CLASSE), campos.indexFromName(mi.CAMPO_DETECCAO)
    i_pub = campos.indexFromName(mi.CAMPO_PUBLICACAO)
    valores = [v for v in lyr.uniqueValues(i_cls) if not mi.ausente(v)]
    mapa = mi.mapa_classes(valores, mi.CLASSES)
    tr = QgsCoordinateTransform(lyr.crs(), crs_m, ctx)
    ras = Rasterizador(grade, SUBPIXEIS)
    ha_px = grade['res'] ** 2 / 1e4
    partes = {g: dict(pix=[], det=[], disp=[], val=[]) for g in set(mapa.values())}
    st, classes = Counter(), Counter()
    pedido = QgsFeatureRequest().setSubsetOfAttributes([i for i in (i_cls, i_det, i_pub) if i >= 0])
    for n, ft in enumerate(mi.feicoes(lyr, pedido), 1):
        if n % 20000 == 0:
            log('  alertas: %d feições rasterizadas' % n)
        attrs = ft.attributes()
        cls = attrs[i_cls]
        g = None if mi.ausente(cls) else mapa.get(mi.normalizar(cls))
        if g is None:
            st['classe_fora_dos_grupos'] += 1
            continue
        d = mi.ler_data(attrs[i_det])
        if d is None:
            st['deteccao_invalida'] += 1
            continue
        det = mi.indice_mes(d, base)
        if det < 0 or det > ultimo:
            st['fora_da_cobertura_temporal'] += 1
            continue
        if mi.DISPONIBILIDADE == 'publicacao':
            pb = mi.ler_data(attrs[i_pub])
            disp = -1 if pb is None else mi.indice_mes(max(pb, d), base)
            if pb is None:
                st['sem_publicacao_valida_so_desfecho'] += 1
        else:
            disp = det
        geom = ft.geometry()
        if geom.isNull() or geom.isEmpty():
            st['geometria_vazia'] += 1
            continue
        if not geom.isGeosValid():
            geom = geom.makeValid()
            st['geometria_corrigida'] += 1
        gm = QgsGeometry(geom)
        gm.transform(tr)
        pix, fr = ras.fracoes(gm)
        dentro = zona[pix] > 0 if len(pix) else np.zeros(0, bool)
        if not dentro.any():
            st['fora_do_dominio'] += 1
            continue
        pix, fr = pix[dentro], fr[dentro]
        p = partes[g]
        p['pix'].append(pix)
        p['det'].append(np.full(len(pix), det, np.int32))
        p['disp'].append(np.full(len(pix), disp, np.int32))
        p['val'].append(fr * ha_px)
        classes['%s → %s' % (cls, g)] += 1
    info = dict(contagens=dict(st), classes_lidas=dict(classes),
                classes_sem_grupo=sorted(str(v) for v in valores if mi.normalizar(v) not in mapa))
    return juntar_eventos(partes), info


def ler_eventos_raster(ev, grade, zona, base, ultimo, crs_m, ctx):
    lyr, sigla = ev['lyr'], ev['sigla']
    tr = QgsCoordinateTransform(lyr.crs(), crs_m, ctx)
    pedido = QgsFeatureRequest()
    if ev.get('expressao'):
        pedido.setFilterExpression(ev['expressao'])
    partes = {sigla: dict(pix=[], det=[], disp=[], val=[])}
    st = Counter()
    for ft in mi.feicoes(lyr, pedido):
        d = mi.ler_data(ft[ev['cd']])
        if d is None:
            st['data_invalida'] += 1
            continue
        det = mi.indice_mes(d, base)
        if det < 0 or det > ultimo:
            st['fora_da_cobertura_temporal'] += 1
            continue
        if ev['cdisp']:
            dd = mi.ler_data(ft[ev['cdisp']])
            disp = -1 if dd is None else mi.indice_mes(max(d, dd), base)
        else:
            disp = det
        g = ft.geometry()
        if g.isNull() or g.isEmpty():
            st['geometria_vazia'] += 1
            continue
        pt = g.pointOnSurface()
        pt.transform(tr)
        xy = pt.asPoint()
        cx = int((xy.x() - grade['x0']) // grade['res'])
        cy = int((grade['ytop'] - xy.y()) // grade['res'])
        if not (0 <= cx < grade['nx'] and 0 <= cy < grade['ny']) or zona[cy * grade['nx'] + cx] <= 0:
            st['fora_do_dominio'] += 1
            continue
        st['lidos'] += 1
        p = partes[sigla]
        p['pix'].append(np.array([cy * grade['nx'] + cx], np.int64))
        p['det'].append(np.array([det], np.int32))
        p['disp'].append(np.array([disp], np.int32))
        p['val'].append(np.array([1.0]))
    return juntar_eventos(partes).get(sigla), dict(st)


def gravar_tif(caminho, grade, bandas, inteiro=False):
    from osgeo import gdal
    tipo = gdal.GDT_Byte if inteiro else gdal.GDT_Float32
    ds = gdal.GetDriverByName('GTiff').Create(
        str(caminho), grade['nx'], grade['ny'], len(bandas), tipo,
        options=['COMPRESS=DEFLATE', 'TILED=YES', 'BIGTIFF=IF_SAFER',
                 'PREDICTOR=%d' % (2 if inteiro else 3)])
    ds.SetGeoTransform(transformada(grade))
    ds.SetProjection(srs_grade(grade).ExportToWkt())
    for i, (nome, arr) in enumerate(bandas, 1):
        b = ds.GetRasterBand(i)
        b.SetDescription(nome)
        if inteiro:
            b.SetNoDataValue(0)
            b.WriteArray(arr.astype(np.uint8).reshape(grade['ny'], grade['nx']))
        else:
            b.SetNoDataValue(-9999.0)
            b.WriteArray(np.where(np.isfinite(arr), arr, -9999.0).astype(np.float32)
                         .reshape(grade['ny'], grade['nx']))
    ds.FlushCache()
    ds = None


def estilizar_classes(camada, caminho_qml):
    cores = {1: '#bd0026', 2: '#fd8d3c', 3: '#f7f7f7'}
    classes = [QgsPalettedRasterRenderer.Class(v, QColor(cores[v]), CODIGOS_CLASSE[v])
               for v in CODIGOS_CLASSE]
    camada.setRenderer(QgsPalettedRasterRenderer(camada.dataProvider(), 1, classes))
    camada.triggerRepaint()
    camada.saveNamedStyle(str(caminho_qml))


def main():
    if not TEM_QGIS:
        raise RuntimeError('Execute este arquivo no editor do console Python do QGIS 4.2.')
    t0 = time.time()
    carregar_nucleo(ARQUIVO_NUCLEO)
    projeto = QgsProject.instance()
    ctx = projeto.transformContext()
    pasta = Path(PASTA_SAIDA) / dt.datetime.now().strftime('raster_%Y%m%d_%H%M%S_%f')
    pasta.mkdir(parents=True, exist_ok=False)
    log('Versão %s com núcleo %s. Saídas em %s.' % (VERSAO, mi.VERSAO, pasta))
    problemas, avisos = [], []
    bac = mi.resolver_camada(projeto, mi.CAMADA_SUBBACIAS, mi.ID_CAMADA_SUBBACIAS, 'Sub-bacias', problemas)
    cid = mi.campo_existente(bac, mi.CAMPO_ID_SUBBACIAS, 'Chave das sub-bacias', problemas)
    lk = mi.resolver_camada(projeto, CAMADA_K_RASTER, ID_CAMADA_K_RASTER, 'K (raster)', problemas,
                            raster=True)
    soc = mi.achar_socio(projeto, problemas, avisos)
    sid = mi.campo_existente(soc, mi.CAMPO_ID_SUBBACIAS, 'Chave socioambiental', problemas)
    al = mi.resolver_camada(projeto, mi.CAMADA_ALERTAS, mi.ID_CAMADA_ALERTAS, 'Alertas', problemas)
    if al is not None and not mi.eh_poligono(al):
        problemas.append('Alertas: a camada precisa ser poligonal.')
    for campo, papel in ((mi.CAMPO_CLASSE, 'Classe dos alertas'), (mi.CAMPO_DETECCAO, 'Detecção')):
        mi.campo_existente(al, campo, papel, problemas)
    if mi.DISPONIBILIDADE == 'publicacao':
        mi.campo_existente(al, mi.CAMPO_PUBLICACAO, 'Publicação', problemas)
    evs = mi.preparar_eventos(projeto, problemas)
    try:
        base = mi.mes_tupla(mi.DATA_INICIO_COBERTURA)
        ultimo = mi.indice_mes(mi.mes_tupla(mi.DATA_FIM_COBERTURA), base)
        t_op = mi.indice_mes(mi.mes_tupla(mi.ORIGEM_OPERACIONAL), base)
        periodos = {n: (mi.indice_mes(mi.mes_tupla(a), base), mi.indice_mes(mi.mes_tupla(z), base))
                    for n, (a, z) in mi.PERIODOS.items()}
    except ValueError as e:
        problemas.append(str(e))
    crs_m = QgsCoordinateReferenceSystem(mi.CRS_METRICO)
    if not crs_m.isValid() or crs_m.isGeographic():
        problemas.append('CRS_METRICO precisa ser projetado em metros: %s.' % mi.CRS_METRICO)
    for lyr in [c for c in (bac, soc, al) if c is not None] + [e['lyr'] for e in evs]:
        if lyr.selectedFeatureCount():
            avisos.append('%s: %d feições selecionadas; %s.' % (
                lyr.name(), lyr.selectedFeatureCount(),
                'SÓ elas serão lidas' if mi.RESPEITAR_SELECAO else 'seleção IGNORADA'))
    mi.inventario(projeto, pasta, problemas, avisos)
    for a in avisos:
        log('ATENÇÃO: ' + a)
    if problemas:
        for p_ in problemas:
            log('CONFIGURAR: ' + p_)
        log('CÁLCULO NÃO EXECUTADO. Diagnóstico: %s' % (pasta / 'diagnostico_inputs.json'))
        return {'status': 'configurar', 'pasta': str(pasta)}

    log('Lendo sub-bacias e montando a grade de %d m.' % RESOLUCAO_M)
    B = mi.ler_subbacias(bac, cid, None, crs_m, ctx)
    grade = montar_grade(B, RESOLUCAO_M, crs_m)
    if grade['nx'] * grade['ny'] > 80_000_000:
        raise RuntimeError('Grade de %d × %d pixels é grande demais; aumente RESOLUCAO_M.'
                           % (grade['nx'], grade['ny']))
    log('  grade %d × %d (%d pixels).' % (grade['nx'], grade['ny'], grade['nx'] * grade['ny']))
    zona = rasterizar_zonas(B, grade)
    log('Reamostrando K (%s) por média.' % lk.name())
    K = k_na_grade(lk, grade)
    K[zona == 0] = np.nan
    socio = mi.ler_socio(soc, sid, B)
    bloco = mi.blocos(B, mi.BLOCO_BOOTSTRAP_KM)
    log('Rasterizando alertas com %d × %d subpixels (pode demorar).' % (SUBPIXEIS, SUBPIXEIS))
    eventos, info_al = ler_alertas_raster(al, grade, zona, base, ultimo, crs_m, ctx)
    info_ev = {}
    for ev in evs:
        log('Lendo eventos %s.' % ev['sigla'])
        e_, info_ev[ev['sigla']] = ler_eventos_raster(ev, grade, zona, base, ultimo, crs_m, ctx)
        if e_ is not None:
            eventos[ev['sigla']] = e_
    if 'garimpo' not in eventos:
        raise RuntimeError('Nenhum alerta de garimpo rasterizado no domínio.')
    d = dict(base=base, grade=grade, zona=zona, K=K, eventos=eventos, n_b=len(B['ids']),
             ids=B['ids'], area_km2=B['area_km2'], bloco=bloco, socio=socio)
    cfg = dict(L=mi.MESES_HISTORICO, H=mi.HORIZONTE_MESES, ultimo_mes=ultimo, periodos=periodos,
               t_op=t_op, reajuste=mi.REAJUSTE_OPERACIONAL, ktrans=mi.TRANSFORMACOES_K,
               familias=mi.FAMILIAS_AREA, sigmas=mi.SIGMAS_M, escalas=ESCALAS_LOCAIS_M,
               fator_regional=FATOR_REGIONAL, truncamento=mi.TRUNCAMENTO_SIGMAS, ridge=mi.RIDGE,
               tolerancia=mi.TOLERANCIA_SELECAO, topo=mi.TOPO_CRITICO, fracoes=mi.FRACOES_TOPO,
               n_boot=mi.N_BOOTSTRAP, semente=mi.SEMENTE, negativos=NEGATIVOS_POR_ORIGEM,
               negativos_auc=NEGATIVOS_AUC, teto=grade['res'] ** 2 / 1e4)
    res = executar_raster(d, cfg)

    n_val = int(np.isfinite(K).sum())
    rel = dict(
        versao=VERSAO, versao_nucleo=mi.VERSAO,
        data_execucao=dt.datetime.now().astimezone().isoformat(), qgis=Qgis.QGIS_VERSION,
        grade=dict(res=grade['res'], nx=grade['nx'], ny=grade['ny'], crs=grade['crs'],
                   n_validos=n_val, area_validos_km2=n_val * grade['res'] ** 2 / 1e6),
        configuracao=dict(nucleo=str(ARQUIVO_NUCLEO), k_raster=lk.name(), escalas_m=ESCALAS_LOCAIS_M,
                          fator_regional=FATOR_REGIONAL, subpixeis=SUBPIXEIS,
                          negativos_por_origem=NEGATIVOS_POR_ORIGEM, negativos_auc=NEGATIVOS_AUC,
                          compartilhada=dict(subbacias=bac.name(), socio=soc.name(), alertas=al.name(),
                                             disponibilidade=mi.DISPONIBILIDADE, classes=mi.CLASSES,
                                             periodos=mi.PERIODOS, sigmas_m=mi.SIGMAS_M,
                                             familias=mi.FAMILIAS_AREA, topo=mi.TOPO_CRITICO)),
        entradas=dict(alertas=info_al, eventos=info_ev, n_subbacias=len(B['ids'])),
        modelagem=res['relatorio'],
        avisos=avisos + res['relatorio']['avisos'] + [
            'Versão em pixel: G, A, D, R, B e eventos são somas em janela quadrada de meia-largura '
            'r; Gviz, o anel até %dr. r foi escolhido na validação interna.' % FATOR_REGIONAL,
            'Ajuste com todas as presenças e ausências amostradas, ponderadas por 1/q.',
            'Critério de seleção em pixels (área); a captura por sub-bacia é reportada para '
            'comparação com o modelo por sub-bacia.',
            'Desfecho por pixel limitado à área do pixel; alertas sobrepostos não somam acima dela.',
            'socio_pre é constante dentro de cada sub-bacia.',
            'crit = hectares previstos × socio_pre/5 é convenção normativa, não estimativa.',
            'O teste 2024–2025 já foi examinado: os resultados são exploratórios.',
        ])

    log('Gravando GeoTIFFs.')
    ras, cod = res['rasters'], res['codigos']
    tif_prev = pasta / 'previsao_100m.tif'
    gravar_tif(tif_prev, grade, [(k, ras[k]) for k in ('ha_prev', 'p_prev', 'crit_est', 'crit_min',
                                                       'crit_max')])
    tif_cls = pasta / 'classes_criticas_100m.tif'
    gravar_tif(tif_cls, grade, [(k, cod[k]) for k in ('classe', 'topo_crit', 'topo_sens', 'topo_prev')],
               inteiro=True)
    if SALVAR_PREDITORES:
        gravar_tif(pasta / 'preditores_100m.tif', grade, list(res['preditores'].items()))
    pb = res['por_bacia']
    mi.gravar_csv(pasta / 'subbacias_agregado.csv',
                  [{k: mi.valor_saida(v[i]) for k, v in pb.items()} for i in range(len(pb['HYBAS_ID']))])
    mi.gravar_csv(pasta / 'teste_por_origem.csv', res['relatorio']['teste']['por_origem'])
    rel_limpo = mi.json_limpo(rel)
    (pasta / 'relatorio_raster.json').write_text(
        json.dumps(rel_limpo, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    resumo = texto_resumo_raster(rel_limpo)
    (pasta / 'relatorio_raster.txt').write_text(resumo, encoding='utf-8')
    camada = QgsRasterLayer(str(tif_cls), 'Modelo integrado 100 m — classes críticas')
    if camada.isValid():
        estilizar_classes(camada, pasta / 'classes_criticas_100m.qml')
        projeto.addMapLayer(camada)
    prev = QgsRasterLayer(str(tif_prev), 'Modelo integrado 100 m — previsão e prioridade')
    if prev.isValid():
        projeto.addMapLayer(prev)
    print(resumo)
    log('CONCLUÍDO em %.1f min. Saídas: %s' % ((time.time() - t0) / 60, pasta))
    return {'status': 'ok', 'pasta': str(pasta)}


if TEM_QGIS and EXECUTAR_AO_ABRIR:
    main()

# -*- coding: utf-8 -*-
"""Calibração do modelo estático: pesos das evidências e γ.

Para cada combinação de pesos (passo de 0,1, incluindo as que zeram uma ou
duas evidências) e de γ (0,2 a 0,9), gera K numa grade grossa (padrão
150 m) **só com o garimpo da data inicial** — o nível sai dos dados, como no
modelo estático — e mede quanto do garimpo novo até a data final cai no topo
de

    S = P × máx(K − O₀, 0)

em que P é a proximidade ao garimpo da data inicial (núcleo de contágio de
200 m) e O₀ a ocupação local nela, na janela do nível. É a avaliação do
experimento K (``tools/experimento_k.py``): K entra como teto do que ainda
cabe perto do garimpo existente. Referências medidas do mesmo jeito: P
sozinho, K = 1 (sem teto) e o K com os pesos atuais.

Sugestão: entre as combinações a menos de ``TOLERANCIA_PP`` pontos
percentuais da melhor captura nos 5%, a mais simples — a de menos
evidências; no empate, a de maior captura. O K definitivo se gera depois,
na resolução final e com o mapa mais recente, pelo diálogo do modelo
estático.

Como no modelo dinâmico, cada combinação vai para ``resultados.csv`` assim
que termina; rodar de novo retoma de onde parou, e ``PARAR.txt`` na pasta
de saída para com segurança.
"""

import csv
import datetime as _dt
import hashlib
import json
import math
import os
import time
import traceback

import numpy as np

from . import estatico
from .calibracao_dinamica import impedir_suspensao, _normalizar

EVIDENCIAS = ("PGgeom", "Est", "Lt")
CHAVE_DA_EVIDENCIA = {"PGgeom": "PGGEOM", "Est": "EST", "Lt": "LT"}
GAMAS = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
FRACOES = (0.01, 0.05, 0.10)
CRITERIO = "captura_5"

PADROES = dict(
    # evidências (rasters de terreno); ao menos uma
    PGGEOM=None, EST=None, LT=None,
    # estratos do nível (ex.: leito dos rios), como no modelo estático
    AGUA=None,
    # mapas de garimpo (0/1): o K usa só os anteriores à data inicial
    MU_INICIAL=None, MU_ANTIGO=None, MU_FINAL=None,
    # onde o K é gerado (ex.: a Província) e onde as métricas são medidas
    MASCARA=None, MASCARA_AVALIACAO=None,
    RESOLUCAO_M=150.0,
    # o K de referência (os pesos em uso), na ordem PGgeom, Est, Lt
    PESOS_ATUAIS=(0.591, 0.075, 0.334), GAMA_ATUAL=0.4,
    # fixos durante a busca: os do modelo estático
    PISO=0.008, JANELA_M=380.0, QUANTIL=0.99, N_FAIXAS=51,
    MINIMO_POR_FAIXA=200,
    # a busca
    PASSO_PESOS=0.1, GAMAS=GAMAS,
    # a avaliação (a mesma do experimento K)
    ESCALA_P_M=200.0, LIMIAR_NOVO=0.02, BLOCO_M=1000.0,
    TOLERANCIA_PP=1.0,
    PASTA_SAIDA=None, HORAS_MAXIMAS=11.0,
)
# o que define os dados e a busca: mudou, a pasta de saída tem de ser outra
CHAVES_DOS_DADOS = ("PGGEOM", "EST", "LT", "AGUA", "MU_INICIAL", "MU_ANTIGO",
                    "MU_FINAL", "MASCARA", "MASCARA_AVALIACAO", "RESOLUCAO_M",
                    "PISO", "JANELA_M", "QUANTIL", "N_FAIXAS",
                    "MINIMO_POR_FAIXA", "ESCALA_P_M", "LIMIAR_NOVO",
                    "BLOCO_M")

COLUNAS = (["id", "etapa", "nome", "n_evidencias"]
           + ["peso_%s" % e for e in EVIDENCIAS]
           + ["gama", "auc", "captura_1", "captura_5", "captura_10",
              "r_blocos", "teto", "potencial_ha", "segundos", "erro"])


class Cancelada(Exception):
    """O usuário cancelou: a combinação em andamento não é gravada."""


# =========================================================================
# auxiliares numéricos (os mesmos do experimento K)
# =========================================================================
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


def correlacao_em_blocos(pontuacao, observado, universo, fator):
    """Pearson das somas por bloco, só nos blocos com pixels do universo."""
    from .validacao import _pearson, _somar_blocos
    fator = max(int(fator), 1)
    contagem = _somar_blocos(universo.astype(np.float64), fator)
    a = _somar_blocos(np.where(universo, pontuacao, 0.0), fator)
    b = _somar_blocos(np.where(universo, observado, 0.0), fator)
    dentro = contagem > 0
    return _pearson(a[dentro], b[dentro])


# =========================================================================
# plano
# =========================================================================
def composicoes(n, passo=0.1):
    """Pesos de ``n`` evidências em múltiplos de ``passo`` que somam 1."""
    partes = int(round(1.0 / passo))
    if abs(partes * passo - 1.0) > 1e-9:
        raise ValueError("o passo dos pesos precisa dividir 1 (ex.: 0,1)")
    saida = []

    def gerar(restante, faltam, atual):
        if faltam == 1:
            saida.append(tuple(atual + [restante]))
            return
        for k in range(restante, -1, -1):
            gerar(restante - k, faltam - 1, atual + [k])

    gerar(partes, n, [])
    return [tuple(round(k * passo, 6) for k in c) for c in saida]


def identificador(pesos, gama):
    texto = json.dumps({"pesos": pesos, "gama": gama}, sort_keys=True)
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


def plano(nomes, config):
    """Referências e a grade, das combinações mais simples às mais ricas.

    Com uma evidência só, γ não muda nada (o fuzzy gamma de uma camada é a
    própria camada): essas combinações rodam uma vez, sem γ.
    """
    itens = [{"etapa": "referencia", "nome": "P", "pesos": None,
              "gama": None},
             {"etapa": "referencia", "nome": "K_1", "pesos": None,
              "gama": None}]
    atuais = dict(zip(EVIDENCIAS, config["PESOS_ATUAIS"]))
    pesos_atuais = {n: float(atuais.get(n, 0.0)) for n in nomes}
    if sum(pesos_atuais.values()) > 0:
        itens.append({"etapa": "referencia", "nome": "K_atual",
                      "pesos": pesos_atuais,
                      "gama": float(config["GAMA_ATUAL"])})
    grade = []
    for c in composicoes(len(nomes), config["PASSO_PESOS"]):
        pesos = dict(zip(nomes, c))
        n = sum(1 for p in c if p > 0)
        gamas = [None] if n == 1 else [float(g) for g in config["GAMAS"]]
        for gama in gamas:
            grade.append({"etapa": "grade", "nome": "", "pesos": pesos,
                          "gama": gama, "n": n})
    grade.sort(key=lambda it: (it["n"], -1 if it["gama"] is None
                               else it["gama"],
                               [-it["pesos"][n] for n in nomes]))
    itens.extend(grade)
    for it in itens:
        if it["etapa"] == "referencia" and it["nome"] != "K_atual":
            it["id"] = "ref_" + it["nome"]
        else:
            it["id"] = identificador(it["pesos"], it["gama"])
            if it["etapa"] == "referencia":
                it["id"] = "ref_" + it["id"]
        it["n"] = (0 if it["pesos"] is None
                   else sum(1 for p in it["pesos"].values() if p > 0))
    return itens


# =========================================================================
# CSV e sugestão
# =========================================================================
def ler_csv(caminho):
    with open(caminho, encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo, delimiter=";"))
    for linha in linhas:
        for chave, valor in linha.items():
            if chave in ("id", "etapa", "nome", "erro"):
                continue
            try:
                linha[chave] = float(valor) if valor != "" else float("nan")
            except (TypeError, ValueError):
                pass
    return linhas


def _formatar(valor):
    if valor is None:
        return ""
    if isinstance(valor, float):
        return "" if math.isnan(valor) else "%.6g" % valor
    return str(valor)


def _finito(valor):
    return isinstance(valor, float) and math.isfinite(valor)


def _pesos_da_linha(linha):
    return {e: linha.get("peso_%s" % e) for e in EVIDENCIAS
            if _finito(linha.get("peso_%s" % e))}


def _descrever(linha):
    pesos = _pesos_da_linha(linha)
    texto = " ".join("%s %g" % (e, p) for e, p in pesos.items() if p > 0)
    if _finito(linha.get("gama")):
        texto += "  γ %g" % linha["gama"]
    return texto


def sugerir(linhas, tolerancia_pp=1.0):
    """A combinação mais simples a menos de ``tolerancia_pp`` da melhor."""
    ok = [l for l in linhas if not l.get("erro") and _finito(l.get(CRITERIO))]
    grade = [l for l in ok if l["etapa"] == "grade"]
    if not grade:
        return None
    melhor = max(grade, key=lambda l: (l[CRITERIO], l["auc"]))
    limite = melhor[CRITERIO] - tolerancia_pp / 100.0
    empatadas = [l for l in grade if l[CRITERIO] >= limite - 1e-12]
    escolhida = min(empatadas, key=lambda l: (l["n_evidencias"],
                                              -l[CRITERIO], -l["auc"]))
    referencias = {l["nome"]: {c: l.get(c) for c in
                               ("auc", "captura_1", "captura_5",
                                "captura_10", "r_blocos")}
                   for l in ok if l["etapa"] == "referencia"}
    # parecer por evidência: a melhor com ela contra a melhor sem ela
    evidencias = {}
    for e in EVIDENCIAS:
        coluna = "peso_%s" % e
        presentes = [l for l in grade if _finito(l.get(coluna))]
        if not presentes:
            continue
        com = [l for l in presentes if l[coluna] > 0]
        sem = [l for l in presentes if l[coluna] == 0]
        melhor_com = max(com, key=lambda l: l[CRITERIO], default=None)
        melhor_sem = max(sem, key=lambda l: l[CRITERIO], default=None)
        evidencias[e] = {
            "com": None if melhor_com is None else melhor_com[CRITERIO],
            "sem": None if melhor_sem is None else melhor_sem[CRITERIO]}
    gamas = {}
    for l in grade:
        if _finito(l.get("gama")):
            chave = "%.1f" % l["gama"]
            gamas[chave] = max(gamas.get(chave, -1.0), l[CRITERIO])
    return {
        "modelo": "estatico",
        "criterio": CRITERIO,
        "tolerancia_pp": tolerancia_pp,
        "pesos": _pesos_da_linha(escolhida),
        "gama": escolhida["gama"] if _finito(escolhida.get("gama")) else None,
        "n_evidencias": int(escolhida["n_evidencias"]),
        "metricas": {c: escolhida.get(c) for c in
                     ("auc", "captura_1", "captura_5", "captura_10",
                      "r_blocos", "teto")},
        "melhor": {"pesos": _pesos_da_linha(melhor),
                   "gama": melhor["gama"] if _finito(melhor.get("gama"))
                   else None, CRITERIO: melhor[CRITERIO],
                   "auc": melhor["auc"]},
        "empatadas": len(empatadas),
        "combinacoes": len(grade),
        "referencias": referencias,
        "evidencias": evidencias,
        "melhor_por_gama": gamas,
    }


def texto_da_sugestao(sugestao):
    saida = ["0) SUGESTÃO PARA O MODELO ESTÁTICO (critério: captura nos 5%%; "
             "a mais simples a menos de %.1f ponto percentual da melhor)"
             % (sugestao["tolerancia_pp"] if sugestao else 1.0)]
    if sugestao is None:
        saida.append("  ainda não há combinações da grade avaliadas.")
        return saida
    p, m = sugestao["pesos"], sugestao["metricas"]
    saida.append("  pesos: %s%s"
                 % (", ".join("%s %.1f" % kv for kv in p.items()),
                    "" if sugestao["gama"] is None
                    else "; γ = %.1f" % sugestao["gama"]))
    saida.append("  captura nos 1/5/10%%: %.3f/%.3f/%.3f; AUC %.3f; r_blocos "
                 "%.3f; teto de K %.3f"
                 % (m["captura_1"], m["captura_5"], m["captura_10"], m["auc"],
                    m["r_blocos"], m["teto"]))
    melhor = sugestao["melhor"]
    saida.append("  melhor da grade: %s%s (captura nos 5%% %.3f); %d de %d "
                 "combinações ficaram a menos de %.1f pp dela"
                 % (", ".join("%s %.1f" % kv for kv in melhor["pesos"].items()
                              if kv[1] > 0),
                    "" if melhor["gama"] is None
                    else "; γ %.1f" % melhor["gama"],
                    melhor[CRITERIO], sugestao["empatadas"],
                    sugestao["combinacoes"], sugestao["tolerancia_pp"]))
    nomes = {"P": "P sozinho (sem K)", "K_1": "K = 1 (sem teto)",
             "K_atual": "K com os pesos atuais"}
    for nome in ("P", "K_1", "K_atual"):
        ref = sugestao["referencias"].get(nome)
        if ref and _finito(ref.get(CRITERIO)):
            saida.append("  %-24s captura nos 5%% %.3f (sugerida %+.1f pp)"
                         % (nomes[nome], ref[CRITERIO],
                            100 * (m[CRITERIO] - ref[CRITERIO])))
    for e, par in sugestao["evidencias"].items():
        if par["com"] is not None and par["sem"] is not None:
            saida.append("  %-7s melhor com %.3f, melhor sem %.3f (%+.1f pp)"
                         % (e, par["com"], par["sem"],
                            100 * (par["com"] - par["sem"])))
    saida.append("  Retrospectivo, num período: diferenças abaixo da "
                 "tolerância não distinguem combinações. Gere o K definitivo "
                 "no diálogo do modelo estático (\"Usar sugestão\").")
    return saida


# =========================================================================
# o motor
# =========================================================================
class CalibracaoEstatica(object):
    """Uma busca de pesos e γ, numa pasta de saída.

    Uso::

        motor = CalibracaoEstatica(config, registrar=print)
        motor.carregar()       # lê os rasters (pode rodar em segundo plano)
        motor.rodar()          # a grade, até acabar ou HORAS_MAXIMAS
    """

    def __init__(self, config, registrar=None, detalhar=False):
        self.config = dict(PADROES)
        self.config.update(config or {})
        self.registrar = registrar
        self.detalhar = detalhar
        self.dados = None
        self.nomes = [e for e in EVIDENCIAS
                      if self.config.get(CHAVE_DA_EVIDENCIA[e])]

    # ------------------------------------------------------------ registro
    @property
    def pasta(self):
        return self.config["PASTA_SAIDA"]

    def log(self, mensagem, tela=True):
        linha = "%s  %s" % (_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            mensagem)
        try:
            os.makedirs(self.pasta, exist_ok=True)
            with open(os.path.join(self.pasta, "calibracao.log"), "a",
                      encoding="utf-8") as arquivo:
                arquivo.write(linha + "\n")
        except OSError:
            pass
        if self.registrar is not None and (tela or self.detalhar):
            self.registrar(linha)

    def caminho_csv(self):
        return os.path.join(self.pasta, "resultados.csv")

    def ids_feitos(self):
        if not os.path.isfile(self.caminho_csv()):
            return set()
        with open(self.caminho_csv(), encoding="utf-8", newline="") as arquivo:
            return {l["id"] for l in csv.DictReader(arquivo, delimiter=";")
                    if not l.get("erro")}

    def ler(self):
        return ler_csv(self.caminho_csv())

    def gravar_linha(self, linha):
        caminho = self.caminho_csv()
        novo = not os.path.isfile(caminho)
        os.makedirs(self.pasta, exist_ok=True)
        with open(caminho, "a", encoding="utf-8", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS,
                                      delimiter=";", extrasaction="ignore")
            if novo:
                escritor.writeheader()
            escritor.writerow({c: _formatar(linha.get(c)) for c in COLUNAS})
            arquivo.flush()
            os.fsync(arquivo.fileno())

    def plano(self):
        return plano(self.nomes, self.config)

    # ------------------------------------------------------------ preparo
    def validar(self):
        cfg = self.config
        if not self.pasta:
            raise ValueError("informe a pasta de saída")
        if not self.nomes:
            raise ValueError("informe ao menos uma evidência (PGgeom, Est ou "
                             "Lt)")
        for chave, nome in (("MU_INICIAL", "mapa de garimpo da data inicial"),
                            ("MU_FINAL", "mapa de garimpo da data final")):
            if not cfg.get(chave):
                raise ValueError("informe o %s" % nome)
        for chave in ("PGGEOM", "EST", "LT", "AGUA", "MU_INICIAL",
                      "MU_ANTIGO", "MU_FINAL", "MASCARA", "MASCARA_AVALIACAO"):
            if cfg.get(chave) and not os.path.isfile(cfg[chave]):
                raise ValueError("arquivo não encontrado (%s): %s"
                                 % (chave, cfg[chave]))
        if os.path.normcase(os.path.abspath(cfg["MU_INICIAL"])) == \
                os.path.normcase(os.path.abspath(cfg["MU_FINAL"])):
            raise ValueError("os mapas das datas inicial e final são o mesmo "
                             "arquivo")
        composicoes(1, cfg["PASSO_PESOS"])          # confere o passo
        return self

    def conferir_pasta(self):
        """A pasta guarda uma busca só: os mesmos dados, ou pasta nova."""
        os.makedirs(self.pasta, exist_ok=True)
        caminho = os.path.join(self.pasta, "configuracao.json")
        atual = {c: _normalizar(self.config.get(c)) for c in CHAVES_DOS_DADOS}
        if os.path.isfile(caminho):
            with open(caminho, encoding="utf-8") as arquivo:
                gravada = json.load(arquivo)
            if gravada.get("modelo") != "estatico":
                raise ValueError("esta pasta de saída tem uma calibração do "
                                 "modelo dinâmico; escolha outra pasta")
            anterior = gravada.get("dados", {})
            diferentes = [c for c in CHAVES_DOS_DADOS
                          if c in anterior and anterior[c] != atual[c]]
            if diferentes:
                raise ValueError(
                    "esta pasta de saída já tem uma calibração com outros "
                    "dados (%s). Escolha uma pasta nova, ou apague "
                    "configuracao.json se os arquivos só mudaram de lugar."
                    % ", ".join(diferentes))
        with open(caminho, "w", encoding="utf-8") as arquivo:
            json.dump({"modelo": "estatico", "dados": atual}, arquivo,
                      ensure_ascii=False, indent=2)

    def carregar(self):
        """Lê os rasters na grade da calibração. Pode rodar em segundo plano."""
        from .kernels import kernel_contagio
        from .raster import grade_de_raster, grade_reamostrada, ler_alinhado
        cfg = self.config
        self.validar()
        self.conferir_pasta()
        primeira = cfg[CHAVE_DA_EVIDENCIA[self.nomes[0]]]
        grade = grade_reamostrada(grade_de_raster(primeira),
                                  resolucao_m=cfg["RESOLUCAO_M"])
        if grade.geografica:
            raise ValueError("a grade está em coordenadas geográficas; o "
                             "modelo precisa de CRS projetado, em metros")
        pixel = grade.pixel_m
        area_ha = pixel ** 2 / 1e4
        self.log("grade: %d x %d px de %.0f m (%.1f milhões de pixels)"
                 % (grade.nx, grade.ny, pixel, grade.nx * grade.ny / 1e6))
        prontas = []
        for nome in self.nomes:
            camada = ler_alinhado(cfg[CHAVE_DA_EVIDENCIA[nome]], grade,
                                  "bilinear")
            prontas.append(estatico.normalizar(camada, piso=cfg["PISO"]))
        dentro = np.ones(grade.shape, dtype=bool)
        if cfg.get("MASCARA"):
            mascara = ler_alinhado(cfg["MASCARA"], grade, "near")
            dentro = np.isfinite(mascara) & (mascara > 0)
        universo = dentro.copy()
        if cfg.get("MASCARA_AVALIACAO"):
            avaliacao = ler_alinhado(cfg["MASCARA_AVALIACAO"], grade, "near")
            universo &= np.isfinite(avaliacao) & (avaliacao > 0)
        if not universo.any():
            raise ValueError("a área de avaliação ficou vazia na grade")

        def mapa(chave):
            arr = ler_alinhado(cfg[chave], grade, "average")
            if float(np.nanmax(arr)) > 1.0001:
                raise ValueError("o mapa %s tem valores acima de 1; o "
                                 "esperado é 0/1 (1 = garimpo)" % chave)
            return np.clip(np.nan_to_num(arr), 0.0, 1.0).astype(np.float32)

        mu0 = mapa("MU_INICIAL")
        mu1 = mapa("MU_FINAL")
        maduro = dentro.copy()
        if cfg.get("MU_ANTIGO"):
            maduro = dentro & estatico.maduros(mapa("MU_ANTIGO"), pixel,
                                               cfg["JANELA_M"])
        else:
            self.log("AVISO: sem o mapa mais antigo, toda a área conta como "
                     "madura no nível (como no modelo estático)")
        estratos = None
        if cfg.get("AGUA"):
            agua = ler_alinhado(cfg["AGUA"], grade, "near")
            e_agua = dentro & np.isfinite(agua) & (agua > 0)
            estratos = {"agua": e_agua, "terra": dentro & ~e_agua}
        obs = np.where(universo, np.clip(mu1 - mu0, 0.0, None), 0.0)
        if obs.sum() <= 0:
            raise ValueError("não há garimpo novo entre os mapas das duas "
                             "datas na área de avaliação — confira se estão "
                             "na ordem certa")
        lado = estatico.lado_da_janela(pixel, cfg["JANELA_M"])
        nucleo = kernel_contagio(cfg["ESCALA_P_M"], pixel)
        P = proximidade_exata(mu0, nucleo)
        self.log("garimpo na área de avaliação: %.0f ha na data inicial, "
                 "%.0f ha novos até a final; %.1f%% da área madura; P de "
                 "%.0f m, janela de ocupação de %d px"
                 % (mu0[universo].sum() * area_ha, obs.sum() * area_ha,
                    100.0 * maduro.sum() / max(dentro.sum(), 1),
                    cfg["ESCALA_P_M"], lado))
        self.log("evidências: %s; o K de cada combinação usa só o garimpo da "
                 "data inicial (o mapa final só avalia)" % ", ".join(self.nomes))
        self.dados = dict(
            grade=grade, area_ha=area_ha, prontas=prontas, dentro=dentro,
            universo=universo, mu0=mu0, maduro=maduro, estratos=estratos,
            obs=obs, P=P, O0=media_em_janela(mu0, lado),
            fator_bloco=max(int(round(cfg["BLOCO_M"] / pixel)), 1))
        return self.dados

    # ------------------------------------------------------------ avaliação
    def gerar_k(self, pesos, gama):
        d, cfg = self.dados, self.config
        lista = [float(pesos.get(n, 0.0)) for n in self.nomes]
        resultado = estatico.gerar_k(
            d["prontas"], self.nomes, lista,
            0.5 if gama is None else float(gama), d["mu0"],
            d["grade"].pixel_m, d["maduro"],
            pisos=[cfg["PISO"]] * len(self.nomes), janela_m=cfg["JANELA_M"],
            n_faixas=int(cfg["N_FAIXAS"]), quantil=cfg["QUANTIL"],
            minimo_por_faixa=int(cfg["MINIMO_POR_FAIXA"]),
            estratos=d["estratos"], normalizar_entradas=False)
        return np.where(d["dentro"], resultado.K, 0.0)

    def avaliar(self, item):
        """Métricas de S = P × máx(K − O₀, 0) (ou de P, nas referências)."""
        from .validacao import auc, captura
        d = self.dados
        teto = float("nan")
        potencial = float("nan")
        if item["nome"] == "P":
            S = d["P"]
        else:
            K = (np.ones(d["P"].shape) if item["nome"] == "K_1"
                 else self.gerar_k(item["pesos"], item["gama"]))
            S = d["P"] * np.clip(K - d["O0"], 0.0, None)
            teto = float(K[d["universo"]].max())
            potencial = float(K[d["universo"]].sum() * d["area_ha"])
        u = d["universo"]
        s_u, o_u = S[u], d["obs"][u]
        m = {"auc": auc(s_u, o_u > self.config["LIMIAR_NOVO"]),
             "r_blocos": correlacao_em_blocos(S, d["obs"], u,
                                              d["fator_bloco"]),
             "teto": teto, "potencial_ha": potencial}
        for fracao in FRACOES:
            m["captura_%d" % round(100 * fracao)] = captura(s_u, o_u, fracao)
        return m

    def rodar_uma(self, item):
        linha = {"id": item["id"], "etapa": item["etapa"],
                 "nome": item["nome"], "n_evidencias": item["n"],
                 "gama": item["gama"]}
        for e in EVIDENCIAS:
            if item["pesos"] is not None and e in item["pesos"]:
                linha["peso_%s" % e] = float(item["pesos"][e])
        inicio = time.time()
        try:
            linha.update(self.avaliar(item))
        except MemoryError:
            linha["erro"] = "MemoryError"
        except Exception:  # noqa: BLE001 - anota e segue para a próxima
            linha["erro"] = traceback.format_exc(limit=4)
        linha["segundos"] = round(time.time() - inicio, 2)
        return linha

    # ------------------------------------------------------------ o laço
    def rodar(self, cancelado=None, progresso=None):
        """Roda a grade, retomando o que já foi feito. Devolve quantas fez."""
        if self.dados is None:
            raise RuntimeError("carregar() antes de rodar()")
        plano_ = self.plano()
        feitos = self.ids_feitos()
        pendentes = [it for it in plano_ if it["id"] not in feitos]
        limite = time.time() + self.config["HORAS_MAXIMAS"] * 3600
        parar = os.path.join(self.pasta, "PARAR.txt")
        self.log("=== calibração do modelo estático: %d combinações, %d por "
                 "fazer; para até %s ==="
                 % (len(plano_), len(pendentes),
                    _dt.datetime.fromtimestamp(limite).strftime("%d/%m %H:%M")))
        impedir_suspensao(True)
        feitas = 0
        try:
            for n, item in enumerate(pendentes, 1):
                if cancelado is not None and cancelado():
                    raise Cancelada()
                if os.path.exists(parar):
                    self.log("PARAR.txt encontrado: parando.")
                    break
                if time.time() > limite:
                    self.log("limite de horas atingido: parando.")
                    break
                # uma combinação leva segundos e não se interrompe no meio:
                # a que terminou é gravada, e o cancelamento vale na próxima
                linha = self.rodar_uma(item)
                self.gravar_linha(linha)
                feitas += 1
                if linha.get("erro"):
                    self.log("[%d/%d] %s ERRO: %s"
                             % (n, len(pendentes), item["id"],
                                linha["erro"].strip().splitlines()[-1]))
                else:
                    self.log("[%d/%d] %-10s %-32s cap5 %.3f  AUC %.3f  %.1f s"
                             % (n, len(pendentes), item["etapa"],
                                item["nome"] or _descrever(linha),
                                linha["captura_5"], linha["auc"],
                                linha["segundos"]), False)
                    if feitas == 3:
                        segundos = max(linha["segundos"], 0.05)
                        self.log("uma combinação: %.1f s; as %d que faltam: "
                                 "~%.0f min"
                                 % (segundos, len(pendentes) - n,
                                    (len(pendentes) - n) * segundos / 60.0))
                if feitas % 10 == 0:
                    self.resumir_seguro()
                if progresso is not None:
                    progresso(len(plano_) - len(pendentes) + n, len(plano_))
        finally:
            impedir_suspensao(False)
            self.resumir_seguro()
            self.log("=== fim: %d combinações nesta sessão. Veja resumo.txt ==="
                     % feitas)
        return feitas

    # ------------------------------------------------------------ resumo
    def resumir_seguro(self):
        try:
            return self.resumir()
        except Exception:  # noqa: BLE001 - o resumo nunca derruba o laço
            self.log("AVISO: falha ao escrever o resumo:\n"
                     + traceback.format_exc(), False)
            return None

    def sugestao(self):
        if not os.path.isfile(self.caminho_csv()):
            return None
        return sugerir(self.ler(), self.config["TOLERANCIA_PP"])

    def resumir(self):
        """Escreve resumo.txt e sugestao.json a partir do resultados.csv."""
        cfg = self.config
        linhas = self.ler()
        ok = [l for l in linhas if not l.get("erro")]
        grade = [l for l in ok if l["etapa"] == "grade"
                 and _finito(l.get(CRITERIO))]
        sugestao = sugerir(linhas, cfg["TOLERANCIA_PP"])
        saida = []
        escrever = saida.append
        escrever("RESUMO DA CALIBRAÇÃO DO MODELO ESTÁTICO — %s"
                 % _dt.datetime.now().strftime("%d/%m/%Y %H:%M"))
        escrever("%d combinações válidas, %d com erro. Resolução %.0f m; "
                 "P de %.0f m; piso %.3g, janela %.0f m, quantil %.3g."
                 % (len(grade), len(linhas) - len(ok), cfg["RESOLUCAO_M"],
                    cfg["ESCALA_P_M"], cfg["PISO"], cfg["JANELA_M"],
                    cfg["QUANTIL"]))
        escrever("")
        escrever("Cada K usa só o garimpo da data inicial; a avaliação é o "
                 "garimpo novo até a data final, ordenado por S = P × máx(K − "
                 "O₀, 0). cap1/5/10 = fração do garimpo novo nos 1, 5 e 10%% "
                 "da área de maior S; r_blocos = correlação em blocos de "
                 "%.0f m." % cfg["BLOCO_M"])
        escrever("")
        saida.extend(texto_da_sugestao(sugestao))

        def tabela(titulo, grupo, n=15):
            escrever("")
            escrever(titulo)
            escrever("  %-44s %6s %6s %6s %6s %6s %6s"
                     % ("combinação", "cap1", "cap5", "cap10", "AUC", "r_bl",
                        "teto"))
            for l in grupo[:n]:
                escrever("  %-44s %6.3f %6.3f %6.3f %6.3f %6.3f %6s"
                         % ((l["nome"] if l["etapa"] == "referencia"
                             and l["nome"] != "K_atual" else _descrever(l))
                            if l["nome"] != "K_atual"
                            else "atual: " + _descrever(l),
                            l["captura_1"], l["captura_5"], l["captura_10"],
                            l["auc"], l["r_blocos"],
                            "—" if not _finito(l.get("teto"))
                            else "%.3f" % l["teto"]))

        referencias = [l for l in ok if l["etapa"] == "referencia"
                       and _finito(l.get(CRITERIO))]
        tabela("1) REFERÊNCIAS", referencias)
        ordenadas = sorted(grade, key=lambda l: (-l[CRITERIO], -l["auc"]))
        tabela("2) MELHORES DA GRADE POR CAPTURA NOS 5%", ordenadas)
        for n in sorted({int(l["n_evidencias"]) for l in grade}):
            tabela("3) MELHORES COM %d EVIDÊNCIA%s" % (n, "S" if n > 1 else ""),
                   [l for l in ordenadas if int(l["n_evidencias"]) == n], n=5)
        if sugestao and sugestao["melhor_por_gama"]:
            escrever("")
            escrever("4) MELHOR CAPTURA NOS 5% POR γ (combinações com 2 ou "
                     "mais evidências)")
            for gama, valor in sorted(sugestao["melhor_por_gama"].items()):
                escrever("  γ %s  %.3f" % (gama, valor))
        escrever("")
        escrever("Leitura: a avaliação é retrospectiva e num período só; uma "
                 "diferença menor que a tolerância não distingue combinações, "
                 "e a regra prefere a mais simples. K entra como teto do que "
                 "ainda cabe perto do garimpo existente; o teste não mede "
                 "hectares nem valida o teto físico definitivo.")
        texto = "\n".join(saida)
        with open(os.path.join(self.pasta, "resumo.txt"), "w",
                  encoding="utf-8") as arquivo:
            arquivo.write(texto + "\n")
        if sugestao is not None:
            sugestao["gerado_em"] = _dt.datetime.now().isoformat(
                timespec="seconds")
            with open(os.path.join(self.pasta, "sugestao.json"), "w",
                      encoding="utf-8") as arquivo:
                json.dump(sugestao, arquivo, ensure_ascii=False, indent=2)
        return texto

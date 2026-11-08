# -*- coding: utf-8 -*-
"""Calibração do modelo dinâmico contra o garimpo observado (a "noite").

Simula o período entre duas datas com o mapa de garimpo da data inicial e
compara a expansão prevista com a observada na data final, para centenas de
combinações de parâmetros. Feita para rodar sozinha por horas. Responde:

* o modelo ordena melhor que alternativas simples (só K, só o remanescente
  K − μ, só a proximidade ao garimpo existente)?
* que r₀ reproduz a área nova observada?
* que escala de contágio, que fator de K, que platô e alcance da
  fiscalização ajustam melhor?

E sugere r₀ e a escala de contágio para o modelo dinâmico (``sugerir``).

Robustez: cada simulação vai para ``resultados.csv`` assim que termina;
rodar de novo retoma de onde parou, sem repetir. Um erro numa combinação é
anotado e a próxima segue. ``PARAR.txt`` na pasta de saída para com
segurança. O plugin (janela Calibração), ``tools/calibrar_noite.py``
(console) e ``tools/calibrar_paralelo.py`` (vários processos, fora do QGIS)
usam este mesmo motor, o mesmo plano e o mesmo CSV.

Modos:

``reconstrucao``  alertas e autos de todo o período: mede se a dinâmica
                  reproduz o que aconteceu, dados os eventos.
``previsao``      só eventos até a data inicial — o que se saberia no dia da
                  previsão. É a medida honesta de antecipação.
"""

import csv
import datetime as _dt
import hashlib
import json
import math
import os
import random
import time
import traceback

import numpy as np

# =========================================================================
# plano (faixas exploradas)
# =========================================================================
# base dos planos: os padrões do plugin até a 0.3.7 (r0 0,004, contágio
# 380 m). Ficam fixos para que os planos — e os ids das simulações — não
# mudem com os padrões calibrados da 0.4.0 (0,0007 e 100 m): as rodadas já
# feitas continuam retomáveis e comparáveis
BASE_PLANO = dict(r0=0.004, escala_contagio_m=380.0)
# 1º calibra-se r0 para a área nova prevista bater com a observada; as etapas
# seguintes usam esse r0 — senão, com tudo subestimado, o critério premiaria
# a fiscalização mais fraca só por deixar crescer mais
R0 = [0.0001, 0.00015, 0.0002, 0.0003, 0.0004, 0.0005, 0.0007, 0.001, 0.002]
# escalas de proximidade das linhas de base (m): o modelo é comparado com a
# melhor delas. Escalas novas só acrescentam linhas de base: os ids das
# simulações não mudam
ESCALAS_BASE_M = [100.0, 150.0, 200.0, 250.0, 300.0, 380.0, 500.0]
SENSIBILIDADE = {
    "alerta_peso": [0.0, 0.5, 2.0, 4.0],
    "alerta_semeia": [False],
    "alerta_tau_dias": [30.0, 180.0],
    "escala_contagio_m": [200.0, 250.0, 300.0, 500.0],
    "fisc_max": [0.0, 0.4, 1.0],
    "fisc_plato_dias": [60.0, 120.0, 270.0, 365.0, 730.0, 3650.0],
    "fisc_escala_m": [250.0, 500.0, 1000.0, 1500.0],
}
FISCAL = {
    "fisc_plato_dias": [90.0, 180.0, 270.0, 365.0, 3650.0],
    "fisc_escala_m": [400.0, 750.0, 1000.0],
    "fisc_max": [0.5, 0.8, 1.0],
}
# etapa "estrutura": o fator multiplica K (limitado a 1) nos pixels com
# K > 0; 100 equivale a não haver saturação (K = 1)
ESTRUTURA_K = [1.0, 1.5, 2.0, 100.0]
ESTRUTURA_ESCALAS_M = [100.0, 150.0, 200.0, 380.0]
ALEATORIA = {
    "r0": (0.0001, 0.002, "log"),
    "alerta_peso": (0.0, 4.0, "lin"),
    "escala_contagio_m": (200.0, 500.0, "lin"),
    "fisc_max": (0.0, 1.0, "lin"),
    "fisc_plato_dias": (60.0, 730.0, "log"),
    "fisc_escala_m": (250.0, 1500.0, "log"),
}

COLUNAS = ["id", "etapa", "modo", "r0", "alerta_peso", "alerta_escala_m",
           "alerta_tau_dias", "alerta_semeia", "escala_contagio_m", "fisc_max",
           "fisc_escala_m", "fisc_plato_dias", "fisc_k_queda", "passo_dias",
           "auc", "captura_1", "captura_5", "captura_10", "r_blocos",
           "pred_ha", "obs_ha", "razao_area", "perto_autos_pred_ha",
           "perto_autos_obs_ha", "perto_autos_razao", "perto_autos_auc",
           "segundos", "erro", "k_fator"]
PARAMETROS_CSV = COLUNAS[3:14]

# configuração: as mesmas chaves do CONFIG de tools/calibrar_noite.py
PADROES = dict(
    K=None, MU_INICIAL=None, MU_FINAL=None,
    MASCARA=None, MASCARA_AVALIACAO=None,
    ALERTAS=None, CAMPO_DATA_ALERTAS="data",
    AUTOS=None, CAMPO_DATA_AUTOS="data",
    DATA_INICIAL=None, DATA_FINAL=None,
    RESOLUCAO_M=150.0, PASTA_SAIDA=None, HORAS_MAXIMAS=11.0,
    LIMIAR_NOVO=0.02, RAIO_AUTOS_M=1500.0, BLOCO_M=1000.0,
    N_ALEATORIAS=400, SEMENTE=2026, MEMORIA_CAMPOS_GB=1.5,
    ESTRUTURA=True, CONFIGURACOES_EXTRAS=(),
)
# o que define os dados da rodada: mudou, a pasta de saída tem de ser outra
CHAVES_DOS_DADOS = ("K", "MU_INICIAL", "MU_FINAL", "MASCARA",
                    "MASCARA_AVALIACAO", "ALERTAS", "CAMPO_DATA_ALERTAS",
                    "AUTOS", "CAMPO_DATA_AUTOS", "DATA_INICIAL", "DATA_FINAL",
                    "RESOLUCAO_M", "LIMIAR_NOVO", "RAIO_AUTOS_M", "BLOCO_M")
# critério principal da sugestão (o mesmo da calibração estática)
CRITERIO = "captura_5"


class Cancelada(Exception):
    """O usuário cancelou: nada da simulação em andamento é gravado."""


def parametros_base(model=None):
    """Parâmetros da base dos planos (BASE_PLANO), não os padrões do plugin."""
    if model is None:
        from . import model
    return model.Parametros(**BASE_PLANO)


# =========================================================================
# métricas (NumPy puro)
# =========================================================================
def auc(pontuacao, alvo):
    """Área sob a curva ROC (Mann-Whitney), com empates tratados por posto médio."""
    pontuacao = np.asarray(pontuacao, dtype=np.float64).ravel()
    alvo = np.asarray(alvo, dtype=bool).ravel()
    n1 = int(alvo.sum())
    n0 = alvo.size - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    _, inverso, contagens = np.unique(pontuacao, return_inverse=True,
                                      return_counts=True)
    fim = np.cumsum(contagens).astype(np.float64)
    posto_medio = fim - (contagens - 1) / 2.0
    soma = float(posto_medio[inverso.ravel()][alvo].sum())
    return (soma - n1 * (n1 + 1) / 2.0) / (float(n1) * n0)


def captura(pontuacao, observado, fracao_area):
    """Fração do garimpo novo observado nos ``fracao_area`` pixels de maior risco.

    Empates na fronteira entram em proporção (sem favorecer a ordem dos
    pixels): é o que torna a métrica justa com pontuações cheias de zeros.
    """
    pontuacao = np.asarray(pontuacao, dtype=np.float64).ravel()
    observado = np.asarray(observado, dtype=np.float64).ravel()
    total = observado.sum()
    if total <= 0 or pontuacao.size == 0:
        return float("nan")
    k = int(round(fracao_area * pontuacao.size))
    if k <= 0:
        return 0.0
    limite = np.partition(pontuacao, pontuacao.size - k)[pontuacao.size - k]
    acima = pontuacao > limite
    empatados = pontuacao == limite
    faltam = k - int(acima.sum())
    capturado = observado[acima].sum()
    if faltam > 0 and empatados.any():
        capturado += observado[empatados].sum() * faltam / float(empatados.sum())
    return float(capturado / total)


def somar_blocos(arr, fator):
    ny, nx = arr.shape
    py, px = (-ny) % fator, (-nx) % fator
    arr = np.pad(arr, ((0, py), (0, px)))
    return arr.reshape(arr.shape[0] // fator, fator,
                       arr.shape[1] // fator, fator).sum(axis=(1, 3))


def correlacao_blocos(previsto, observado, fator):
    if fator <= 1:
        a, b = previsto.ravel(), observado.ravel()
    else:
        a = somar_blocos(previsto, fator).ravel()
        b = somar_blocos(observado, fator).ravel()
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


# =========================================================================
# plano e ids
# =========================================================================
def identificador(modo, params):
    texto = json.dumps({"modo": modo, "params": params}, sort_keys=True)
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


def com_ids(itens):
    for it in itens:
        it["id"] = identificador(it["modo"], it["params"])
    vistos, unicos = set(), []
    for it in itens:
        if it["id"] not in vistos:
            vistos.add(it["id"])
            unicos.append(it)
    return unicos


def plano_inicial(padrao=None):
    """Padrão e varredura de r0 (não dependem de nada)."""
    padrao = padrao or parametros_base()
    base = {c: getattr(padrao, c) for c in PARAMETROS_CSV}
    itens = []

    def item(etapa, modo, **mudancas):
        params = dict(base)
        params.update(mudancas)
        itens.append({"etapa": etapa, "modo": modo, "params": params})

    item("padrao", "reconstrucao")
    item("padrao", "previsao")
    for valor in R0:
        item("r0", "reconstrucao", r0=valor)
    return com_ids(itens)


def plano_restante(config, r0, padrao=None):
    """Sensibilidade, fiscal, estrutura e aleatórias, com r0 calibrado."""
    padrao = padrao or parametros_base()
    base = {c: getattr(padrao, c) for c in PARAMETROS_CSV}
    base["r0"] = r0
    itens = []

    def item(etapa, modo, **mudancas):
        params = dict(base)
        params.update(mudancas)
        itens.append({"etapa": etapa, "modo": modo, "params": params})

    item("calibrado", "reconstrucao")
    item("calibrado", "previsao")
    padrao_base = {c: getattr(padrao, c) for c in PARAMETROS_CSV}
    for extra in config.get("CONFIGURACOES_EXTRAS") or []:
        mudancas = dict(extra)
        if float(mudancas.get("k_fator", 1.0)) == 1.0:
            mudancas.pop("k_fator", None)     # fator 1 = mesmo id de sempre
        for modo in ("previsao", "reconstrucao"):
            params = dict(padrao_base)        # o r0 vem da extra, não daqui
            params.update(mudancas)
            itens.append({"etapa": "extra", "modo": modo, "params": params})
    for nome, valores in SENSIBILIDADE.items():
        for valor in valores:
            item("sensibilidade", "reconstrucao", **{nome: valor})
    for plato in FISCAL["fisc_plato_dias"]:
        for alcance in FISCAL["fisc_escala_m"]:
            for fmax in FISCAL["fisc_max"]:
                item("fiscal", "reconstrucao", fisc_plato_dias=plato,
                     fisc_escala_m=alcance, fisc_max=fmax)
    if config.get("ESTRUTURA"):
        for fator in ESTRUTURA_K:
            for escala in ESTRUTURA_ESCALAS_M:
                for modo in ("reconstrucao", "previsao"):
                    mudancas = {"escala_contagio_m": escala}
                    if fator != 1.0:          # fator 1 = mesmo id de antes
                        mudancas["k_fator"] = fator
                    item("estrutura", modo, **mudancas)
    sorteio = random.Random(config["SEMENTE"])
    for i in range(int(config["N_ALEATORIAS"])):
        mudancas = {}
        for nome, (minimo, maximo, escala) in ALEATORIA.items():
            u = sorteio.random()
            if escala == "log":
                valor = math.exp(math.log(minimo)
                                 + u * (math.log(maximo) - math.log(minimo)))
            else:
                valor = minimo + u * (maximo - minimo)
            mudancas[nome] = round(valor, 6)
        # um terço das aleatórias no modo previsão
        item("aleatoria", "previsao" if i % 3 == 2 else "reconstrucao",
             **mudancas)
    return com_ids(itens)


def interpolar_r0(pontos, padrao_r0=0.004):
    """r0 em que a razão de áreas cruza 1 (log-log); sem cerco, o mais próximo."""
    pontos = sorted(pontos)
    if not pontos:
        return padrao_r0
    for (r_a, q_a), (r_b, q_b) in zip(pontos, pontos[1:]):
        if (q_a - 1.0) * (q_b - 1.0) <= 0 and q_a != q_b:
            t = (0.0 - math.log(q_a)) / (math.log(q_b) - math.log(q_a))
            valor = math.exp(math.log(r_a) + t * (math.log(r_b) - math.log(r_a)))
            return float("%.4g" % valor)
    return float("%.4g" % min(pontos, key=lambda p: abs(math.log(p[1])))[0])


# =========================================================================
# CSV
# =========================================================================
def ler_csv(caminho):
    with open(caminho, encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo, delimiter=";"))
    for linha in linhas:
        for chave, valor in linha.items():
            if chave in ("id", "etapa", "modo", "erro", "alerta_semeia"):
                continue
            try:
                linha[chave] = float(valor) if valor != "" else float("nan")
            except (TypeError, ValueError):
                pass
        if not isinstance(linha.get("k_fator"), float) or \
                math.isnan(linha["k_fator"]):
            linha["k_fator"] = 1.0
    return linhas


def formatar(valor):
    if isinstance(valor, float):
        return "" if math.isnan(valor) else "%.6g" % valor
    return str(valor)


def _cabecalho(caminho):
    with open(caminho, encoding="utf-8", newline="") as arquivo:
        return next(csv.reader(arquivo, delimiter=";"), [])


def _mediana(valores):
    valores = [v for v in valores if isinstance(v, float) and not math.isnan(v)]
    return float(np.median(valores)) if valores else float("nan")


def _f(valor):
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "—"
    return "%.3g" % valor


def _finito(valor):
    return isinstance(valor, float) and math.isfinite(valor)


def impedir_suspensao(ligar):
    """No Windows, impede a suspensão enquanto roda (sem efeito fora dele)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        continuo, sistema = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            continuo | sistema if ligar else continuo)
    except Exception:  # noqa: BLE001
        pass


# =========================================================================
# sugestão
# =========================================================================
def sugerir(linhas, r0):
    """Parâmetros sugeridos para o modelo dinâmico, a partir do CSV.

    r₀ é o calibrado pela área (modo reconstrução). A escala de contágio é a
    da simulação de maior captura nos 5% no modo previsão entre as que usam
    esse r₀, K sem fator e os demais parâmetros na base do plano (etapas
    calibrado e estrutura): é o que o diálogo do modelo dinâmico consegue
    reproduzir. O resto entra no relatório, sem virar sugestão.
    """
    ok = [l for l in linhas if not l.get("erro")]
    candidatas = [l for l in ok if l["modo"] == "previsao"
                  and l["etapa"] in ("calibrado", "estrutura")
                  and l["k_fator"] == 1.0 and _finito(l.get(CRITERIO))
                  and _finito(l.get("r0"))
                  and math.isclose(l["r0"], r0, rel_tol=1e-6)]
    if not candidatas:
        return None
    escolhida = max(candidatas, key=lambda l: (l[CRITERIO], l["auc"]))
    bases = [l for l in ok if l["etapa"] == "linha_de_base"
             and _finito(l.get(CRITERIO))]
    melhor_base = max(bases, key=lambda l: (l[CRITERIO], l["auc"]),
                      default=None)
    frouxas = [l for l in ok if l["modo"] == "previsao"
               and l["etapa"] == "estrutura" and l["k_fator"] != 1.0
               and _finito(l.get(CRITERIO))]
    melhor_frouxa = max(frouxas, key=lambda l: l[CRITERIO], default=None)
    parametros = {}
    for chave in PARAMETROS_CSV:
        valor = escolhida.get(chave)
        if chave == "alerta_semeia":
            parametros[chave] = str(valor) == "True"
        elif _finito(valor):
            parametros[chave] = valor
    parametros["r0"] = r0
    return {
        "modelo": "dinamico",
        "criterio": CRITERIO,
        "parametros": parametros,
        "metricas": {c: escolhida.get(c) for c in
                     ("auc", "captura_1", "captura_5", "captura_10",
                      "r_blocos", "razao_area")},
        "escalas_testadas": sorted({l["escala_contagio_m"]
                                    for l in candidatas}),
        "melhor_linha_de_base": None if melhor_base is None else {
            "nome": melhor_base["modo"], CRITERIO: melhor_base[CRITERIO],
            "auc": melhor_base["auc"]},
        "supera_linha_de_base": (None if melhor_base is None else
                                 escolhida[CRITERIO] > melhor_base[CRITERIO]),
        "k_frouxo": None if melhor_frouxa is None else {
            "k_fator": melhor_frouxa["k_fator"],
            "escala_contagio_m": melhor_frouxa["escala_contagio_m"],
            CRITERIO: melhor_frouxa[CRITERIO]},
    }


def texto_da_sugestao(sugestao):
    """Bloco do resumo com a sugestão (lista de linhas)."""
    saida = ["0) SUGESTÃO PARA O MODELO DINÂMICO (critério: captura nos 5% "
             "no modo previsão)"]
    if sugestao is None:
        saida.append("  ainda não há simulações no modo previsão com o r0 "
                     "calibrado (etapas calibrado e estrutura).")
        return saida
    p, m = sugestao["parametros"], sugestao["metricas"]
    saida.append("  r0 = %.4g por dia (área nova prevista = observada, modo "
                 "reconstrução)" % p["r0"])
    saida.append("  escala de contágio = %.0f m (testadas: %s m)"
                 % (p["escala_contagio_m"],
                    ", ".join("%.0f" % e for e in sugestao["escalas_testadas"])))
    saida.append("  no modo previsão: AUC %.3f, captura nos 1/5/10%% "
                 "%.3f/%.3f/%.3f, área prevista/observada %s"
                 % (m["auc"], m["captura_1"], m["captura_5"], m["captura_10"],
                    _f(m.get("razao_area"))))
    base = sugestao["melhor_linha_de_base"]
    if base:
        diferenca = 100 * (m[CRITERIO] - base[CRITERIO])
        saida.append("  melhor linha de base: %s (captura nos 5%% %.3f, AUC "
                     "%.3f) — o modelo %s (%+.1f pp)"
                     % (base["nome"], base[CRITERIO], base["auc"],
                        "SUPERA" if sugestao["supera_linha_de_base"]
                        else "NÃO supera", diferenca))
        if not sugestao["supera_linha_de_base"]:
            saida.append("  Neste período a proximidade simples ordena tão "
                         "bem quanto o modelo: os parâmetros sugeridos são os "
                         "melhores do modelo, não uma garantia de ganho.")
    frouxo = sugestao["k_frouxo"]
    if frouxo and frouxo[CRITERIO] > m[CRITERIO] + 0.005:
        saida.append("  K × %.4g (escala %.0f m) daria captura nos 5%% de "
                     "%.3f (%+.1f pp). O plugin não aplica fator de K: um "
                     "ganho grande indica que o nível de K limita cedo demais."
                     % (frouxo["k_fator"], frouxo["escala_contagio_m"],
                        frouxo[CRITERIO],
                        100 * (frouxo[CRITERIO] - m[CRITERIO])))
    saida.append("  Os demais parâmetros ficam como na base do plano "
                 "(α %.3g, F_max %.3g, platô %.0f dias, d0 %.0f m); o botão "
                 "\"Usar sugestão\" leva todos ao modelo dinâmico."
                 % (p.get("alerta_peso", float("nan")),
                    p.get("fisc_max", float("nan")),
                    p.get("fisc_plato_dias", float("nan")),
                    p.get("fisc_escala_m", float("nan"))))
    return saida


# =========================================================================
# o motor
# =========================================================================
class CalibracaoDinamica(object):
    """Uma rodada de calibração, numa pasta de saída.

    Uso (o plugin e as ferramentas fazem o mesmo)::

        motor = CalibracaoDinamica(config, registrar=print)
        motor.preparar_eventos()    # thread principal: lê camadas vetoriais
        motor.carregar()            # lê os rasters e grava as linhas de base
        motor.rodar()               # o plano, até acabar ou HORAS_MAXIMAS
    """

    def __init__(self, config, registrar=None, detalhar=False):
        self.config = dict(PADROES)
        self.config.update(config or {})
        self.registrar = registrar
        # detalhar: cada simulação também vai para a tela (o plugin mostra;
        # o console do QGIS fica sem responder e só a mostraria no fim)
        self.detalhar = detalhar
        self.preparo = None
        self.ctx = None
        self.processar_eventos = None

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
        caminho = self.caminho_csv()
        if not os.path.isfile(caminho):
            return set()
        with open(caminho, encoding="utf-8", newline="") as arquivo:
            return {l["id"] for l in csv.DictReader(arquivo, delimiter=";")
                    if not l.get("erro")}

    def ler(self):
        return ler_csv(self.caminho_csv())

    def gravar_linha(self, linha):
        caminho = self.caminho_csv()
        novo = not os.path.isfile(caminho)
        # um CSV de versão anterior, sem a coluna k_fator, continua válido
        # para o plano antigo; só a etapa estrutura precisa de pasta nova
        colunas = COLUNAS if novo else _cabecalho(caminho)
        fator = linha.get("k_fator", "")
        if "k_fator" not in colunas and fator not in ("", 1, 1.0):
            raise ValueError("o resultados.csv desta pasta de saída é de uma "
                             "versão sem a coluna k_fator; para a etapa "
                             "estrutura, use uma pasta de saída nova")
        os.makedirs(self.pasta, exist_ok=True)
        with open(caminho, "a", encoding="utf-8", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=colunas,
                                      delimiter=";", extrasaction="ignore")
            if novo:
                escritor.writeheader()
            escritor.writerow({c: formatar(linha.get(c, "")) for c in colunas})
            arquivo.flush()
            os.fsync(arquivo.fileno())

    # ------------------------------------------------------------ plano
    def plano_inicial(self):
        return plano_inicial(parametros_base())

    def plano_restante(self, r0):
        return plano_restante(self.config, r0, parametros_base())

    def plano_completo(self):
        return self.plano_inicial() + self.plano_restante(self.r0_calibrado())

    def r0_calibrado(self, padrao_r0=None):
        """r0 em que a área nova prevista iguala a observada (reconstrução).

        Lê sempre do CSV, para que a retomada chegue ao mesmo valor.
        """
        if padrao_r0 is None:
            padrao_r0 = BASE_PLANO["r0"]
        pontos = []
        if os.path.isfile(self.caminho_csv()):
            for l in self.ler():
                if (l["etapa"] == "r0" and not l.get("erro")
                        and l.get("razao_area", 0) > 0):
                    pontos.append((l["r0"], l["razao_area"]))
        return interpolar_r0(pontos, padrao_r0)

    # ------------------------------------------------------------ preparo
    def conferir_pasta(self):
        """A pasta guarda uma rodada só: os mesmos dados, ou pasta nova."""
        os.makedirs(self.pasta, exist_ok=True)
        caminho = os.path.join(self.pasta, "configuracao.json")
        atual = {c: _normalizar(self.config.get(c)) for c in CHAVES_DOS_DADOS}
        if os.path.isfile(caminho):
            with open(caminho, encoding="utf-8") as arquivo:
                gravada = json.load(arquivo)
            if gravada.get("modelo", "dinamico") != "dinamico":
                raise ValueError("esta pasta de saída tem uma calibração do "
                                 "modelo estático; escolha outra pasta")
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
            json.dump({"modelo": "dinamico", "dados": atual}, arquivo,
                      ensure_ascii=False, indent=2)

    def preparar_eventos(self):
        """Confere a configuração e lê os eventos. Thread principal (QGIS)."""
        from . import execucao
        cfg = self.config
        if not self.pasta:
            raise ValueError("informe a pasta de saída")
        for chave, nome in (("K", "K"), ("MU_INICIAL", "mapa de garimpo da "
                                         "data inicial"),
                            ("MU_FINAL", "mapa de garimpo da data final")):
            if not cfg.get(chave) or not os.path.isfile(cfg[chave]):
                raise ValueError("%s não encontrado: %s" % (nome, cfg.get(chave)))
        for chave in ("MASCARA", "MASCARA_AVALIACAO"):
            if cfg.get(chave) and not os.path.isfile(cfg[chave]):
                raise ValueError("%s não encontrada: %s" % (chave, cfg[chave]))
        t0, t1 = _data(cfg["DATA_INICIAL"]), _data(cfg["DATA_FINAL"])
        if t1 <= t0:
            raise ValueError("a data final deve ser posterior à inicial")
        alertas = _camada(cfg.get("ALERTAS"), "alertas",
                          cfg["CAMPO_DATA_ALERTAS"])
        autos = _camada(cfg.get("AUTOS"), "autos", cfg["CAMPO_DATA_AUTOS"])
        configuracao = execucao.Configuracao(
            caminho_k=cfg["K"], caminho_mu0=cfg["MU_INICIAL"],
            caminho_mascara=cfg.get("MASCARA"),
            resolucao_m=cfg["RESOLUCAO_M"], camada_alertas=alertas,
            campo_data_alertas=cfg["CAMPO_DATA_ALERTAS"],
            camada_fiscalizacao=autos,
            campo_data_fiscalizacao=cfg["CAMPO_DATA_AUTOS"],
            data_inicio=t0, data_fim=t1, pasta_saida=self.pasta,
            prefixo="calibracao")
        self.preparo = execucao.preparar(configuracao,
                                         log=lambda m: self.log("   " + m))
        self.t0, self.t1 = t0, t1
        return self.preparo

    def carregar(self, linhas_de_base=True, gravar=True):
        """Lê os rasters na grade e monta o contexto das simulações.

        Pode rodar em segundo plano. ``gravar=False`` (os trabalhadores do
        calibrar_paralelo) não toca na pasta de saída.
        """
        from . import kernels, model, numerico
        from . import raster as _raster
        from .eventos import CampoEventos
        if self.preparo is None:
            raise RuntimeError("preparar_eventos() antes de carregar()")
        cfg = self.config
        if gravar:
            self.conferir_pasta()
        grade = self.preparo.grade
        pixels = grade.nx * grade.ny
        self.log("grade: %d x %d = %.1f milhões de pixels de %.0f m"
                 % (grade.nx, grade.ny, pixels / 1e6, grade.pixel_m))
        if pixels > 25e6:
            raise ValueError("grade grande demais (%.0f milhões de pixels) "
                             "para rodar centenas de simulações: aumente a "
                             "resolução ou use uma máscara menor"
                             % (pixels / 1e6))
        K = _raster.ler_alinhado(cfg["K"], grade, "bilinear")
        mu0 = _raster.ler_alinhado(cfg["MU_INICIAL"], grade, "media")
        mu1 = _raster.ler_alinhado(cfg["MU_FINAL"], grade, "media")
        for nome, arr in (("o mapa de garimpo da data inicial", mu0),
                          ("o mapa de garimpo da data final", mu1),
                          ("K", K)):
            maximo = float(np.nanmax(arr)) if arr.size else 0.0
            if maximo > 1.0001:
                raise ValueError("%s tem valores até %.3g; o esperado é "
                                 "fração 0–1 (1 = garimpo). Se for o raster "
                                 "de classes do MapBiomas, extraia antes só a "
                                 "classe de garimpo como 0/1."
                                 % (nome, maximo))
        K = np.nan_to_num(K).astype(np.float32)
        mu0 = np.clip(np.nan_to_num(mu0), 0, 1).astype(np.float32)
        mu1 = np.clip(np.nan_to_num(mu1), 0, 1).astype(np.float32)
        mascara = None
        if cfg.get("MASCARA"):
            mascara = _raster.ler_alinhado(cfg["MASCARA"], grade,
                                           "vizinho") > 0
        # universo = onde as métricas são medidas. A simulação roda em
        # MASCARA (área + entorno); a avaliação, em MASCARA_AVALIACAO
        universo = mascara if mascara is not None else np.ones(K.shape, bool)
        if cfg.get("MASCARA_AVALIACAO"):
            universo = universo & (_raster.ler_alinhado(
                cfg["MASCARA_AVALIACAO"], grade, "vizinho") > 0)
        self.log("domínio da simulação: %.0f km² | universo de avaliação: "
                 "%.0f km²"
                 % ((mascara.sum() if mascara is not None else K.size)
                    * grade.pixel_m ** 2 / 1e6,
                    universo.sum() * grade.pixel_m ** 2 / 1e6))
        area_ha = grade.pixel_m ** 2 / 10000.0
        obs_novo = np.where(universo, np.clip(mu1 - mu0, 0.0, None), 0.0)
        self.log("garimpo observado: %.0f ha em %s, %.0f ha em %s; novo: "
                 "%.0f ha" % (mu0[universo].sum() * area_ha, self.t0,
                              mu1[universo].sum() * area_ha, self.t1,
                              obs_novo.sum() * area_ha))
        if obs_novo.sum() <= 0:
            raise ValueError("não há garimpo novo entre os mapas das duas "
                             "datas — confira se estão na ordem certa")
        novos_fora_k = obs_novo[(K <= 0) & universo].sum() / obs_novo.sum()
        if novos_fora_k > 0.05:
            self.log("AVISO: %.0f%% do garimpo novo observado caiu onde K = 0 "
                     "— o modelo não pode prever ali (limite do K, não da "
                     "dinâmica)" % (100 * novos_fora_k))

        # zona dos autos do período, para as métricas locais da fiscalização
        perto = None
        autos_periodo = [(d, idx) for d, idx in
                         self.preparo.janelas_fiscalizacao
                         if self.t0 <= d <= self.t1]
        if autos_periodo:
            marcados = np.zeros(K.shape, dtype=bool)
            for _d, (linhas, colunas) in autos_periodo:
                marcados[linhas, colunas] = True
            distancia = numerico.distancia_euclidiana(marcados, grade.pixel_m)
            perto = (distancia <= cfg["RAIO_AUTOS_M"]) & universo
            self.log("autos no período: %d janelas; zona de %.0f m em volta: "
                     "%.0f km²" % (len(autos_periodo), cfg["RAIO_AUTOS_M"],
                                   perto.sum() * grade.pixel_m ** 2 / 1e6))
        else:
            self.log("AVISO: nenhum auto no período; o platô e o alcance da "
                     "fiscalização não serão identificáveis")

        self.ctx = dict(
            model=model, kernels=kernels, numerico=numerico,
            CampoEventos=CampoEventos, preparo=self.preparo, grade=grade,
            K=K, mu0=mu0, mascara=mascara, universo=universo,
            obs_novo=obs_novo, perto_autos=perto, area_ha=area_ha,
            t0=self.t0, t1=self.t1, campos={},
            fator_bloco=max(int(round(cfg["BLOCO_M"] / grade.pixel_m)), 1))
        if linhas_de_base and gravar:
            for linha in self.calcular_linhas_de_base(self.ids_feitos()):
                self.gravar_linha(linha)
                self.log("linha de base %-28s AUC %.3f  captura 5%% %.0f%%"
                         % (linha["modo"], linha["auc"],
                            100 * linha["captura_5"]))
        return self.ctx

    def verificar(self):
        """Prepara, roda uma simulação e estima o tempo (o verificar() do console)."""
        inicio = time.time()
        self.log("=== verificar() ===")
        self.preparar_eventos()
        self.carregar()
        plano = self.plano_completo()
        feitos = self.ids_feitos()
        self.log("simulação de teste com a base dos planos (padrão até a "
                 "0.3.7)…")
        linha = self.rodar_uma(plano[0])
        if linha["erro"]:
            raise RuntimeError("a simulação de teste falhou:\n" + linha["erro"])
        if linha["id"] not in feitos:
            self.gravar_linha(linha)
        memoria = sum(_bytes_do_campo(c) for c in self.ctx["campos"].values())
        self.log("campos de eventos da simulação de teste: %.2f GB (limite do "
                 "cache %.1f GB)" % (memoria / 1e9,
                                     self.config["MEMORIA_CAMPOS_GB"]))
        self.log("uma simulação: %.0f s (AUC %.3f, captura nos 5%% de maior "
                 "risco %.0f%%, área prevista / observada %.2f)"
                 % (max(linha["segundos"], 0.1), linha["auc"],
                    100 * linha["captura_5"], linha["razao_area"]))
        self._estimar(plano, max(linha["segundos"], 0.1))
        self.log("verificar() levou %.0f s. Tudo certo: rode rodar_noite()."
                 % (time.time() - inicio))
        return True

    def _estimar(self, plano, segundos):
        etapas = {}
        for item in plano:
            etapas[item["etapa"]] = etapas.get(item["etapa"], 0) + 1
        cabem = int(self.config["HORAS_MAXIMAS"] * 3600 / segundos)
        feitos = self.ids_feitos()
        faltam = len([it for it in plano if it["id"] not in feitos])
        self.log("plano: %s — %d simulações (%d por fazer, ~%.1f h); em %.0f h "
                 "cabem ~%d" % (", ".join("%s %d" % kv for kv in etapas.items()),
                                len(plano), faltam, faltam * segundos / 3600.0,
                                self.config["HORAS_MAXIMAS"], cabem))
        if cabem < sum(n for e, n in etapas.items() if e != "aleatoria"):
            self.log("AVISO: não cabem nem as etapas fixas. Considere uma "
                     "resolução maior ou uma máscara menor.")

    # ------------------------------------------------------------ simulações
    def calcular_linhas_de_base(self, feitos=()):
        """Linhas do CSV das linhas de base ainda não feitas (não grava nada)."""
        ctx = self.ctx
        linhas = []
        folga = np.clip(ctx["K"] - ctx["mu0"], 0.0, None)
        candidatos = [("so_K", ctx["K"]), ("remanescente", folga)]
        for escala in ESCALAS_BASE_M:
            nucleo = ctx["kernels"].kernel_contagio(escala, ctx["grade"].pixel_m)
            proximidade = ctx["numerico"].convolver(ctx["mu0"], nucleo)
            candidatos.append(("proximidade_%dm" % escala, proximidade))
            candidatos.append(("proximidade_x_remanescente_%dm" % escala,
                               proximidade * folga))
        for nome, pontuacao in candidatos:
            ident = "base_" + nome
            if ident in feitos:
                continue
            linha = dict.fromkeys(COLUNAS, "")
            linha.update(id=ident, etapa="linha_de_base", modo=nome)
            linha.update(self.avaliar(pontuacao, magnitude=False))
            linha["segundos"] = 0.0
            linhas.append(linha)
        return linhas

    def avaliar(self, pontuacao, magnitude=True):
        """Todas as métricas de uma pontuação (Δμ previsto ou linha de base)."""
        ctx = self.ctx
        limiar = self.config["LIMIAR_NOVO"]
        universo = ctx["universo"]
        obs = ctx["obs_novo"]
        pont = np.where(universo, pontuacao, 0.0).astype(np.float32)
        p_u, o_u = pont[universo], obs[universo]
        area_ha = ctx["area_ha"]
        m = {
            "auc": auc(p_u, o_u > limiar),
            "captura_1": captura(p_u, o_u, 0.01),
            "captura_5": captura(p_u, o_u, 0.05),
            "captura_10": captura(p_u, o_u, 0.10),
            "r_blocos": correlacao_blocos(pont, obs, ctx["fator_bloco"]),
            "obs_ha": float(o_u.sum() * area_ha),
        }
        perto = ctx["perto_autos"]
        if perto is not None and perto.any():
            m["perto_autos_obs_ha"] = float(obs[perto].sum() * area_ha)
            m["perto_autos_auc"] = auc(pont[perto], obs[perto] > limiar)
        if magnitude:
            m["pred_ha"] = float(p_u.sum() * area_ha)
            m["razao_area"] = (m["pred_ha"] / m["obs_ha"]
                               if m["obs_ha"] > 0 else float("nan"))
            if perto is not None and perto.any():
                m["perto_autos_pred_ha"] = float(pont[perto].sum() * area_ha)
                m["perto_autos_razao"] = (
                    m["perto_autos_pred_ha"] / m["perto_autos_obs_ha"]
                    if m["perto_autos_obs_ha"] > 0 else float("nan"))
        return m

    def _campo(self, tipo, modo, escala_m):
        """Campos de eventos em cache (o termo espacial só depende da escala).

        O cache descarta o menos usado quando passa de MEMORIA_CAMPOS_GB:
        numa província inteira, o retângulo de uma janela de autos pode
        cobrir quase a grade toda.
        """
        ctx = self.ctx
        chave = (tipo, modo, round(float(escala_m), 3))
        campos = ctx["campos"]
        if chave in campos:
            campos[chave] = campos.pop(chave)       # vira o mais recente
            return campos[chave]
        preparo = ctx["preparo"]
        janelas = (preparo.janelas_alertas if tipo == "alertas"
                   else preparo.janelas_fiscalizacao)
        limite = ctx["t0"] if modo == "previsao" else ctx["t1"]
        janelas = [(d, idx) for d, idx in janelas if d <= limite]
        campo = None
        if janelas:
            campo = ctx["CampoEventos"](ctx["grade"].shape,
                                        ctx["grade"].pixel_m, janelas,
                                        escala_m,
                                        guardar_posicoes=(tipo == "alertas"))
        limite = self.config["MEMORIA_CAMPOS_GB"] * 1e9
        while campos and (sum(_bytes_do_campo(c) for c in campos.values())
                          + _bytes_do_campo(campo) > limite):
            campos.pop(next(iter(campos)))           # o menos usado
        campos[chave] = campo
        return campo

    def rodar_uma(self, item, cancelado=None):
        """Uma simulação; devolve a linha do CSV (não grava)."""
        ctx = self.ctx
        linha = dict.fromkeys(COLUNAS, "")
        linha.update(id=item["id"], etapa=item["etapa"], modo=item["modo"])
        linha.update(item["params"])
        inicio = time.time()
        progresso = None
        if cancelado is not None:
            progresso = lambda *_a: not cancelado()  # noqa: E731
        try:
            valores = dict(item["params"])
            fator = float(valores.pop("k_fator", 1.0))
            params = ctx["model"].Parametros(**valores).validar()
            K = ctx["K"]
            if fator != 1.0:
                K = np.where(K > 0, np.minimum(K * fator, 1.0),
                             0.0).astype(np.float32)
            alertas = self._campo("alertas", item["modo"],
                                  params.alerta_escala_m)
            autos = self._campo("autos", item["modo"], params.fisc_escala_m)
            resultado = ctx["model"].simular(
                K, ctx["mu0"], ctx["grade"].pixel_m, ctx["t0"], ctx["t1"],
                params, campo_alertas=alertas, campo_fiscalizacao=autos,
                mascara=ctx["mascara"], progresso=progresso)
            linha.update(self.avaliar(resultado.delta_mu))
        except ctx["model"].SimulacaoCancelada:
            raise Cancelada()
        except MemoryError:
            ctx["campos"].clear()
            linha["erro"] = "MemoryError (cache de campos esvaziado)"
        except Exception:  # noqa: BLE001 - anota e segue para a próxima
            linha["erro"] = traceback.format_exc(limit=4)
        linha["segundos"] = round(time.time() - inicio, 1)
        return linha

    # ------------------------------------------------------------ o laço
    def rodar(self, cancelado=None, progresso=None):
        """Roda o plano em ordem de prioridade, retomando o que já foi feito.

        Devolve quantas simulações fez. ``cancelado()`` → True interrompe
        (levanta Cancelada); ``progresso(feitas, total)`` acompanha.
        """
        if self.ctx is None:
            raise RuntimeError("carregar() antes de rodar()")
        limite = time.time() + self.config["HORAS_MAXIMAS"] * 3600
        self.log("=== calibração: para até %s ==="
                 % _dt.datetime.fromtimestamp(limite).strftime("%d/%m %H:%M"))
        impedir_suspensao(True)
        contagem = {"feitas": 0, "primeira": None}
        try:
            if self._rodar_lista(self.plano_inicial(), limite, contagem,
                                 cancelado, progresso):
                r0 = self.r0_calibrado()
                self.log("r0 calibrado (área prevista = observada): %.4g" % r0)
                self._rodar_lista(self.plano_restante(r0), limite, contagem,
                                  cancelado, progresso)
        finally:
            impedir_suspensao(False)
            self.resumir_seguro()
            self.log("=== fim: %d simulações nesta sessão. Veja resumo.txt ==="
                     % contagem["feitas"])
        return contagem["feitas"]

    def _rodar_lista(self, plano, limite, contagem, cancelado, progresso):
        """Roda o que falta do plano. Devolve False se mandaram parar."""
        feitos = self.ids_feitos()
        pendentes = [it for it in plano if it["id"] not in feitos]
        parar = os.path.join(self.pasta, "PARAR.txt")
        # o total (para o progresso) é o do plano completo estimado agora
        total = len(self.plano_inicial()) + len(self.plano_restante(
            self.r0_calibrado()))
        self.log("%d pendentes de %d nesta fase" % (len(pendentes), len(plano)),
                 False)
        for n, item in enumerate(pendentes, 1):
            if cancelado is not None and cancelado():
                raise Cancelada()
            if os.path.exists(parar):
                self.log("PARAR.txt encontrado: parando.")
                return False
            if time.time() > limite:
                self.log("limite de horas atingido: parando.")
                return False
            linha = self.rodar_uma(item, cancelado)
            self.gravar_linha(linha)
            contagem["feitas"] += 1
            if linha["erro"]:
                self.log("[%d/%d] %s %s ERRO: %s"
                         % (n, len(pendentes), item["etapa"], item["id"],
                            linha["erro"].strip().splitlines()[-1]), False)
            else:
                self.log("[%d/%d] %-13s %-12s AUC %.3f  cap5 %.0f%%  área "
                         "%.2f  %.0f s"
                         % (n, len(pendentes), item["etapa"], item["modo"],
                            linha["auc"], 100 * linha["captura_5"],
                            linha["razao_area"], linha["segundos"]), False)
                if contagem["primeira"] is None:
                    contagem["primeira"] = linha["segundos"]
                    self._estimar(self.plano_completo(),
                                  max(linha["segundos"], 0.1))
            if contagem["feitas"] % 5 == 0:
                self.resumir_seguro()
            if progresso is not None:
                progresso(len(self.ids_feitos() & _ids(self.plano_completo())),
                          total)
            if self.processar_eventos is not None:
                self.processar_eventos()
        return True

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
        return sugerir(self.ler(), self.r0_calibrado())

    def resumir(self):
        """Escreve resumo.txt e sugestao.json a partir do resultados.csv."""
        cfg = self.config
        linhas = self.ler()
        ok = [l for l in linhas if not l.get("erro")]
        erros = len(linhas) - len(ok)
        sims = [l for l in ok if l["etapa"] != "linha_de_base"]
        r0 = self.r0_calibrado()
        sugestao = sugerir(linhas, r0)
        saida = []
        escrever = saida.append
        escrever("RESUMO DA CALIBRAÇÃO — %s" % _dt.datetime.now().strftime(
            "%d/%m/%Y %H:%M"))
        escrever("%d simulações válidas, %d com erro. Período %s a %s, "
                 "resolução %.0f m." % (len(sims), erros, cfg["DATA_INICIAL"],
                                        cfg["DATA_FINAL"], cfg["RESOLUCAO_M"]))
        escrever("")
        escrever("Métricas: AUC (0,5 = acaso, 1 = perfeito) ordena o risco; "
                 "cap5 = fração do garimpo novo observado nos 5%% da área de "
                 "maior risco; área = área prevista / observada (1 = certo); "
                 "r_blocos = correlação em blocos de %.0f m; perto_autos = o "
                 "mesmo, só a até %.0f m dos autos do período."
                 % (cfg["BLOCO_M"], cfg["RAIO_AUTOS_M"]))
        escrever("")
        saida.extend(texto_da_sugestao(sugestao))

        def tabela(titulo, grupo, chave_ordem, n=10, reverso=True):
            escrever("")
            escrever(titulo)
            escrever("  %-13s %-27s %6s %6s %6s %6s %6s  %s"
                     % ("etapa", "modo", "AUC", "cap5", "área", "r_bl",
                        "p_aut", "parâmetros"))
            validos = [l for l in grupo
                       if not math.isnan(l.get(chave_ordem, float("nan")))]
            validos.sort(key=lambda l: l[chave_ordem], reverse=reverso)
            for l in validos[:n]:
                params = ("" if l["etapa"] == "linha_de_base" else
                          "r0=%.4g α=%.2g Fmax=%.2g platô=%.0f d0=%.0f "
                          "esc=%.0f%s"
                          % (l["r0"], l["alerta_peso"], l["fisc_max"],
                             l["fisc_plato_dias"], l["fisc_escala_m"],
                             l["escala_contagio_m"],
                             "" if l["k_fator"] == 1.0
                             else " K×%.4g" % l["k_fator"]))
                escrever("  %-13s %-27s %6.3f %6.3f %6s %6.3f %6s  %s"
                         % (l["etapa"], l["modo"], l["auc"], l["captura_5"],
                            _f(l.get("razao_area")), l["r_blocos"],
                            _f(l.get("perto_autos_razao")), params))

        escrever("")
        escrever("r0 calibrado (área prevista = observada, modo "
                 "reconstrução): %.4g — usado como base nas etapas "
                 "sensibilidade, fiscal, estrutura e calibrado." % r0)
        bases = [l for l in ok if l["etapa"] == "linha_de_base"]
        padrao = [l for l in ok if l["etapa"] in ("padrao", "calibrado")]
        tabela("1) LINHAS DE BASE × MODELO (base do plano e com r0 calibrado; "
               "o modelo precisa superar as linhas de base)", bases + padrao,
               "auc", n=20)
        for modo in ("reconstrucao", "previsao"):
            tabela("2) MELHORES POR AUC — modo %s" % modo,
                   [l for l in sims if l["modo"] == modo], "auc")
        for modo in ("previsao", "reconstrucao"):
            tabela("3) MELHORES POR CAPTURA NOS 5%% — modo %s" % modo,
                   [l for l in sims if l["modo"] == modo], "captura_5")
        calibradas = [dict(l, erro_area=abs(math.log(l["razao_area"])))
                      for l in sims if l["modo"] == "reconstrucao"
                      and l.get("razao_area", 0) > 0]
        tabela("4) ÁREA NOVA MAIS PRÓXIMA DA OBSERVADA (calibra r0)",
               calibradas, "erro_area", reverso=False)

        rec = [dict(l, p_aut_rel=(l.get("perto_autos_razao", float("nan"))
                                  / l["razao_area"]
                                  if l.get("razao_area", 0) > 0
                                  else float("nan")))
               for l in sims if l["modo"] == "reconstrucao"
               and l["etapa"] in ("fiscal", "aleatoria", "sensibilidade")]
        for chave, nome in (("fisc_plato_dias", "PLATÔ DA FISCALIZAÇÃO (dias)"),
                            ("fisc_escala_m", "ALCANCE d0 DA FISCALIZAÇÃO (m)"),
                            ("fisc_max", "F_max")):
            escrever("")
            escrever("5) EFEITO DE %s — medianas, etapa fiscal (fatorial) e, "
                     "entre parênteses, aleatória por faixa" % nome)
            escrever("  %-10s %4s %7s %7s %9s %9s %9s"
                     % ("valor", "n", "AUC", "cap5", "p_aut_AUC", "p_aut_área",
                        "p_aut_rel"))
            fiscal = [l for l in rec if l["etapa"] == "fiscal"]
            for valor in sorted({l[chave] for l in fiscal}):
                grupo = [l for l in fiscal if l[chave] == valor]
                escrever("  %-10s %4d %7.3f %7.3f %9.3f %9s %9s"
                         % ("%.4g" % valor, len(grupo),
                            _mediana([l["auc"] for l in grupo]),
                            _mediana([l["captura_5"] for l in grupo]),
                            _mediana([l.get("perto_autos_auc", float("nan"))
                                      for l in grupo]),
                            _f(_mediana([l.get("perto_autos_razao",
                                               float("nan"))
                                         for l in grupo])),
                            _f(_mediana([l["p_aut_rel"] for l in grupo]))))
            aleat = [l for l in rec if l["etapa"] == "aleatoria"
                     and not math.isnan(l.get(chave, float("nan")))]
            if len(aleat) >= 12:
                valores = sorted(l[chave] for l in aleat)
                cortes = [valores[int(len(valores) * q)]
                          for q in (0.25, 0.5, 0.75)]
                faixas = [(-math.inf, cortes[0]), (cortes[0], cortes[1]),
                          (cortes[1], cortes[2]), (cortes[2], math.inf)]
                for a, b in faixas:
                    grupo = [l for l in aleat if a < l[chave] <= b]
                    rotulo = "(%s–%s)" % (_f(max(a, valores[0])),
                                          _f(min(b, valores[-1])))
                    escrever("  %-10s %4d %7.3f %7.3f %9.3f"
                             % (rotulo, len(grupo),
                                _mediana([l["auc"] for l in grupo]),
                                _mediana([l["captura_5"] for l in grupo]),
                                _mediana([l.get("perto_autos_auc",
                                                float("nan"))
                                          for l in grupo])))
        melhor_auc = max([b["auc"] for b in bases] or [float("nan")])
        melhor_cap = max([b["captura_5"] for b in bases] or [float("nan")])
        extras = [l for l in sims if l["etapa"] == "extra"]
        if extras:
            escrever("")
            escrever("7) CONFIGURAÇÕES EXTRAS (escolhidas fora deste período, "
                     "rodadas como vieram) — compare com a melhor linha de "
                     "base (AUC %.3f, cap5 %.3f)" % (melhor_auc, melhor_cap))
            escrever("  %-12s %9s %7s %7s %7s %7s %7s"
                     % ("modo", "r0", "k_fator", "escala", "AUC", "cap5",
                        "área"))
            for l in sorted(extras, key=lambda l: (l["modo"], l["r0"],
                                                   l["escala_contagio_m"])):
                escrever("  %-12s %9.4g %7.4g %7.0f %7.3f %7.3f %7s"
                         % (l["modo"], l["r0"], l["k_fator"],
                            l["escala_contagio_m"], l["auc"], l["captura_5"],
                            _f(l.get("razao_area"))))
        estrutura = [l for l in sims if l["etapa"] == "estrutura"
                     or _so_muda_a_escala(l, sims)]
        if any(l["etapa"] == "estrutura" for l in estrutura):
            escrever("")
            escrever("6) ESTRUTURA — saturação por K × fator (100 = sem "
                     "saturação) e escala de contágio; compare com a melhor "
                     "linha de base (AUC %.3f, cap5 %.3f)"
                     % (melhor_auc, melhor_cap))
            escrever("  %-12s %7s %7s %7s %7s %7s"
                     % ("modo", "k_fator", "escala", "AUC", "cap5", "área"))
            for l in sorted(estrutura, key=lambda l: (l["modo"], l["k_fator"],
                                                      l["escala_contagio_m"])):
                escrever("  %-12s %7.4g %7.0f %7.3f %7.3f %7s"
                         % (l["modo"], l["k_fator"], l["escala_contagio_m"],
                            l["auc"], l["captura_5"], _f(l.get("razao_area"))))
        escrever("")
        escrever("Leitura: compare primeiro (0) e (1). Em (5), diferenças de "
                 "AUC menores que ~0,005 entre valores não distinguem nada; "
                 "as colunas p_aut, perto dos autos, são as mais sensíveis à "
                 "fiscalização. p_aut_rel = (área prevista/observada perto "
                 "dos autos) ÷ (a mesma razão na área toda): perto de 1, a "
                 "fiscalização simulada reproduz o entorno dos autos tão bem "
                 "quanto o resto; abaixo de 1, suprime demais; acima, de "
                 "menos. A sugestão vale para este período: o ritmo muda "
                 "entre períodos, e r0 deve ser recalibrado.")
        texto = "\n".join(saida)
        with open(os.path.join(self.pasta, "resumo.txt"), "w",
                  encoding="utf-8") as arquivo:
            arquivo.write(texto + "\n")
        if sugestao is not None:
            sugestao.update(periodo=[str(cfg["DATA_INICIAL"]),
                                     str(cfg["DATA_FINAL"])],
                            gerado_em=_dt.datetime.now().isoformat(
                                timespec="seconds"))
            with open(os.path.join(self.pasta, "sugestao.json"), "w",
                      encoding="utf-8") as arquivo:
                json.dump(sugestao, arquivo, ensure_ascii=False, indent=2)
        return texto


def _ids(plano):
    return {it["id"] for it in plano}


def _so_muda_a_escala(linha, sims):
    """Linha com K × 1 que difere da calibrada só na escala de contágio.

    Essas combinações já existem nas etapas calibrado e sensibilidade, com o
    mesmo id; entram na tabela da estrutura como referência de K × 1.
    """
    if linha["etapa"] not in ("calibrado", "sensibilidade") or \
            linha["k_fator"] != 1.0:
        return False
    referencia = [l for l in sims if l["etapa"] == "calibrado"
                  and l["modo"] == linha["modo"]]
    if not referencia:
        return False
    return all(linha[c] == referencia[0][c] or
               (c == "escala_contagio_m")
               for c in PARAMETROS_CSV if c != "alerta_semeia") and \
        linha["alerta_semeia"] == referencia[0]["alerta_semeia"]


def _bytes_do_campo(campo):
    if campo is None:
        return 0
    return sum(sub.nbytes for _d, _sy, _sx, sub in campo.itens)


def _data(texto):
    if isinstance(texto, _dt.date):
        return texto
    return _dt.date.fromisoformat(str(texto))


def _normalizar(valor):
    """Valor comparável entre rodadas: caminho normalizado, sem "|layername=".

    A janela do plugin passa camadas (fonte "arquivo.gpkg|layername=x"); o
    CONFIG do paralelo, o caminho do arquivo. Os dois são a mesma rodada.
    """
    if valor is None or isinstance(valor, (int, float, bool)):
        return valor
    if hasattr(valor, "source"):                  # QgsVectorLayer
        valor = valor.source()
    texto = str(valor)
    if os.path.sep in texto or "/" in texto or "\\" in texto:
        texto = os.path.normcase(os.path.abspath(texto.partition("|")[0]))
    return texto


def _camada(fonte, nome, campo):
    """Camada vetorial de eventos: um caminho (abre) ou uma camada já aberta."""
    if not fonte:
        return None
    if isinstance(fonte, str):
        from qgis.core import QgsVectorLayer
        camada = QgsVectorLayer(fonte, nome, "ogr")
        if not camada.isValid():
            raise ValueError("não consegui abrir %s: %s" % (nome, fonte))
    else:
        camada = fonte
    if camada.fields().indexFromName(campo) < 0:
        raise ValueError("a camada %s não tem o campo de data %r. Campos: %s"
                         % (nome, campo,
                            ", ".join(f.name() for f in camada.fields())))
    return camada


# =========================================================================
# teste fora da amostra (lê dois resultados.csv; não simula nada)
# =========================================================================
def _postos(valores):
    valores = np.asarray(valores, dtype=np.float64)
    _, inverso, contagens = np.unique(valores, return_inverse=True,
                                      return_counts=True)
    fim = np.cumsum(contagens).astype(np.float64)
    return (fim - (contagens - 1) / 2.0)[inverso.ravel()]


def _spearman(a, b):
    if len(a) < 3:
        return float("nan")
    pa, pb = _postos(a), _postos(b)
    if pa.std() == 0 or pb.std() == 0:
        return float("nan")
    return float(np.corrcoef(pa, pb)[0, 1])


def fora_da_amostra(pasta_treino, pasta_teste, n=10):
    """Escolhe parâmetros num período e mede no outro, sem simular nada.

    Rode o mesmo plano (mesma semente, número de aleatórias e faixas) em
    dois períodos, cada um na sua pasta — por exemplo 2020→2022 (treino) e
    2022→2024 (teste). As combinações aleatórias têm o mesmo id nos dois.
    Aqui as ``n`` melhores no treino, por AUC, são lidas no teste; a linha
    de base de comparação também é escolhida no treino. É a medida honesta:
    o teste não participa de nenhuma escolha. Grava fora_da_amostra.txt na
    pasta de teste e devolve o texto.
    """
    treino = [l for l in ler_csv(os.path.join(pasta_treino, "resultados.csv"))
              if not l.get("erro")]
    teste = [l for l in ler_csv(os.path.join(pasta_teste, "resultados.csv"))
             if not l.get("erro")]
    por_id = {l["id"]: l for l in teste}
    saida = []
    escrever = saida.append
    escrever("TESTE FORA DA AMOSTRA — %s" % _dt.datetime.now().strftime(
        "%d/%m/%Y %H:%M"))
    escrever("treino: %s" % pasta_treino)
    escrever("teste:  %s" % pasta_teste)

    bases_treino = [l for l in treino if l["etapa"] == "linha_de_base"]
    base_escolhida = None
    if bases_treino:
        melhor = max(bases_treino, key=lambda l: l["auc"])
        base_escolhida = por_id.get(melhor["id"])
        escrever("")
        escrever("Linha de base escolhida no treino: %s (AUC treino %.3f)"
                 % (melhor["modo"], melhor["auc"]))
        if base_escolhida:
            escrever("   no teste: AUC %.3f  cap5 %.3f"
                     % (base_escolhida["auc"], base_escolhida["captura_5"]))

    for modo in ("previsao", "reconstrucao"):
        comuns = [(l, por_id[l["id"]]) for l in treino
                  if l["etapa"] == "aleatoria" and l["modo"] == modo
                  and l["id"] in por_id]
        escrever("")
        escrever("MODO %s — %d combinações aleatórias nos dois períodos"
                 % (modo.upper(), len(comuns)))
        if not comuns:
            escrever("   nenhuma em comum: os planos dos dois períodos são "
                     "iguais? (semente, número de aleatórias e faixas)")
            continue
        escrever("   estabilidade (Spearman treino × teste): AUC %.2f  "
                 "cap5 %.2f"
                 % (_spearman([a["auc"] for a, _ in comuns],
                              [b["auc"] for _, b in comuns]),
                    _spearman([a["captura_5"] for a, _ in comuns],
                              [b["captura_5"] for _, b in comuns])))
        escolhidas = sorted(comuns, key=lambda par: -par[0]["auc"])[:n]
        melhor_teste = max(b["auc"] for _, b in comuns)
        escrever("   as %d melhores no treino, medidas no teste "
                 "(melhor possível no teste: AUC %.3f):" % (len(escolhidas),
                                                             melhor_teste))
        escrever("   %7s %7s %7s %7s %7s  %s" % ("AUC_tr", "AUC_te", "cap5_te",
                                                 "área_te", "r_bl_te",
                                                 "parâmetros"))
        for a, b in escolhidas:
            escrever("   %7.3f %7.3f %7.3f %7s %7.3f  r0=%.4g α=%.2g "
                     "Fmax=%.2g platô=%.0f d0=%.0f esc=%.0f"
                     % (a["auc"], b["auc"], b["captura_5"],
                        _f(b.get("razao_area")), b["r_blocos"], b["r0"],
                        b["alerta_peso"], b["fisc_max"], b["fisc_plato_dias"],
                        b["fisc_escala_m"], b["escala_contagio_m"]))
        mediana_auc = _mediana([b["auc"] for _, b in escolhidas])
        mediana_cap = _mediana([b["captura_5"] for _, b in escolhidas])
        escrever("   mediana no teste: AUC %.3f  cap5 %.3f"
                 % (mediana_auc, mediana_cap))
        if base_escolhida:
            escrever("   %s a linha de base escolhida no treino "
                     "(AUC %+.3f, cap5 %+.3f)"
                     % ("SUPERA" if mediana_auc > base_escolhida["auc"]
                        and mediana_cap > base_escolhida["captura_5"]
                        else "NÃO supera",
                        mediana_auc - base_escolhida["auc"],
                        mediana_cap - base_escolhida["captura_5"]))
    escrever("")
    escrever("Leitura: o modo previsão é o que vale como antecipação. "
             "Estabilidade baixa (< 0,5) indica que o ranking de parâmetros "
             "de um período não se repete no outro: os 'melhores' "
             "parâmetros refletem só aquele período.")
    texto = "\n".join(saida)
    with open(os.path.join(pasta_teste, "fora_da_amostra.txt"), "w",
              encoding="utf-8") as arquivo:
        arquivo.write(texto + "\n")
    return texto

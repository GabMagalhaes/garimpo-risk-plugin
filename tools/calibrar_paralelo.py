# -*- coding: utf-8 -*-
"""Roda o calibrar_noite.py em PARALELO, fora do QGIS.

Usa o mesmo CONFIG, o mesmo plano, o mesmo resultados.csv e o mesmo
calibracao.log do calibrar_noite.py — só distribui as simulações entre
vários processos. Retoma de onde parou (inclusive o que o QGIS já gravou).

COMO USAR
---------
1. Salve este arquivo na MESMA pasta do calibrar_noite.py (com o CONFIG
   já preenchido).
2. Feche o QGIS (ou pare o rodar_noite() com PARAR.txt). Dois programas
   escrevendo no mesmo CSV ao mesmo tempo estragam o arquivo.
3. Abra o "OSGeo4W Shell" (menu Iniciar) e rode:

       python-qgis-ltr "C:\\caminho\\calibrar_paralelo.py" 12

   (no QGIS 4, ``python-qgis`` em vez de ``python-qgis-ltr``). O plugin
   é procurado no perfil do QGIS que roda o script (QGIS3 ou QGIS4).

   O número no fim é a quantidade de processos (padrão 12). Um terceiro
   argumento troca o arquivo de CONFIG (o padrão é o calibrar_noite.py da
   mesma pasta), para manter uma cópia por período:

       python-qgis-ltr "C:\\caminho\\calibrar_paralelo.py" 12 calibrar_teste.py
4. Acompanhe calibracao.log na PASTA_SAIDA. Para parar com segurança,
   crie PARAR.txt na PASTA_SAIDA: as simulações em andamento terminam e
   são gravadas; nenhuma nova começa.

Lógica: cada processo prepara os dados uma vez (lê camadas e rasters,
~1 min) e depois recebe simulações uma a uma. Só o processo principal
escreve no CSV, então não há disputa pelo arquivo. A ordem de prioridade
é a mesma do rodar_noite(): linhas de base, padrão + varredura de r0; o r0
calibrado só é calculado quando essa fase termina; depois o resto.

Memória: no máximo PREPARANDO_JUNTOS processos preparam os dados ao mesmo
tempo (a preparação é o pico de memória; com 12 de uma vez, um processo
morreu na primeira rodada real), e cada processo guarda no máximo
MEMORIA_POR_PROCESSO_GB de campos de eventos. Se ainda faltar memória,
use menos processos.
"""
import os
import sys

# cada processo usa 1 thread de NumPy/BLAS: o paralelismo vem dos processos,
# e threads extras só disputariam os mesmos núcleos
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import collections
import contextlib
import io
import time
import traceback
import datetime as _dt
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from concurrent.futures.process import BrokenProcessPool

PASTA = os.path.dirname(os.path.abspath(__file__))
# o calibrar_noite.py (com o CONFIG) que este processo usa; o principal o
# troca pelo 2º argumento e o repassa aos trabalhadores
SCRIPT = os.path.join(PASTA, "calibrar_noite.py")
# onde o QGIS instala os plugins (o Risco Garimpo está aqui): o perfil
# do QGIS que roda o script, QGIS3 ou QGIS4 (ver _pastas_de_plugins)
PERFIS = os.path.join(os.environ.get("APPDATA", ""), "QGIS")

# processos que preparam os dados ao mesmo tempo (o pico de memória)
PREPARANDO_JUNTOS = 2
# campos de eventos guardados por processo entre simulações; o campo dos
# alertas (escala fixa) cabe e é reaproveitado, o dos autos muda com d0
MEMORIA_POR_PROCESSO_GB = 0.6

_APP = None          # QgsApplication do processo (precisa ficar vivo)
_NS = None           # o calibrar_noite.py carregado, em cada trabalhador
_ERRO_PREPARO = ""   # se a preparação de um trabalhador falhar


# -------------------------------------------------------------------------
# comum ao principal e aos trabalhadores
# -------------------------------------------------------------------------
def _iniciar_qgis():
    """QGIS sem interface: necessário para abrir as camadas (provedor ogr)."""
    global _APP
    for pasta in reversed(_pastas_de_plugins()):
        if pasta not in sys.path:
            sys.path.insert(0, pasta)
    from qgis.core import QgsApplication
    _APP = QgsApplication([], False)
    _APP.initQgis()


def _pastas_de_plugins():
    """Pastas de plugins a procurar, a do QGIS em uso primeiro.

    O QGIS 4 instala os plugins em ``QGIS4``; o 3, em ``QGIS3``. Com os
    dois instalados, cada perfil pode ter uma versão diferente do plugin.
    """
    from qgis.core import Qgis
    atual = "QGIS%d" % (Qgis.QGIS_VERSION_INT // 10000)
    perfis = [atual] + [p for p in ("QGIS4", "QGIS3") if p != atual]
    return [os.path.join(PERFIS, p, "profiles", "default", "python",
                         "plugins") for p in perfis]


def _versoes(model):
    """QGIS em uso e plugin carregado: a rodada deve usar o plugin atual."""
    from qgis.core import Qgis
    from risco_garimpo.core import registro
    return ("QGIS %s; plugin Risco Garimpo %s em %s"
            % (Qgis.QGIS_VERSION, registro.versao_plugin(),
               os.path.dirname(os.path.dirname(os.path.dirname(
                   os.path.abspath(model.__file__))))))


def _carregar():
    """Executa o calibrar_noite.py num dicionário próprio (sem o aviso inicial)."""
    ns = {"__name__": "calibrar_noite", "__file__": SCRIPT}
    with open(SCRIPT, encoding="utf-8") as arquivo:
        codigo = compile(arquivo.read(), SCRIPT, "exec")
    with contextlib.redirect_stdout(io.StringIO()):
        exec(codigo, ns)
    return ns


# -------------------------------------------------------------------------
# trabalhadores
# -------------------------------------------------------------------------
def _iniciar_trabalhador(semaforo=None, script=None):
    """Prepara o contexto (dados alinhados na grade) uma vez por processo.

    Reaproveita o verificar() do calibrar_noite.py, mas sem as partes que
    não fazem sentido aqui: não escreve no log nem no CSV, não refaz as
    linhas de base e não roda a simulação de teste (que custaria ~4 min
    em cada processo).
    """
    global _NS, _ERRO_PREPARO, SCRIPT
    if script:
        SCRIPT = script
    if semaforo is not None:
        semaforo.acquire()
    try:
        _iniciar_qgis()
        ns = _carregar()
        ns["CONFIG"]["MEMORIA_CAMPOS_GB"] = min(
            ns["CONFIG"].get("MEMORIA_CAMPOS_GB", 1.5),
            MEMORIA_POR_PROCESSO_GB)
        nada = lambda *a, **k: None  # noqa: E731
        ns["_log"] = nada
        ns["_gravar_linha"] = nada
        ns["_linhas_de_base"] = nada
        rodar_original = ns["_rodar_uma"]
        # simulação de teste falsa: verificar() só usa id e as métricas no log
        ns["_rodar_uma"] = lambda ctx, item: {
            "id": item["id"], "erro": "", "segundos": 1.0,
            "auc": float("nan"), "captura_5": float("nan"),
            "razao_area": float("nan")}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ns["verificar"]()
        finally:
            ns["_rodar_uma"] = rodar_original
        _NS = ns
    except Exception:  # noqa: BLE001 - devolvido ao principal na 1ª tarefa
        _ERRO_PREPARO = traceback.format_exc(limit=6)
    finally:
        if semaforo is not None:
            semaforo.release()


def _linhas_de_base(feitos):
    """Resumo dos dados e linhas de base que faltam (num trabalhador)."""
    if _NS is None:
        raise RuntimeError("PREPARO: " + _ERRO_PREPARO)
    ctx = _NS["_CONTEXTO"]
    universo, area_ha = ctx["universo"], ctx["area_ha"]
    resumo = ("garimpo no universo de avaliação: %.0f ha em %s; novo até %s: "
              "%.0f ha (%.0f km² avaliados)"
              % (ctx["mu0"][universo].sum() * area_ha, ctx["t0"], ctx["t1"],
                 ctx["obs_novo"].sum() * area_ha,
                 universo.sum() * area_ha / 100.0))
    return resumo, _NS["calcular_linhas_de_base"](ctx, feitos)


def _trabalhar(item):
    """Uma simulação; devolve a linha do CSV (quem grava é o principal)."""
    if _NS is None:
        linha = {"id": item["id"], "etapa": item["etapa"],
                 "modo": item["modo"], "erro": "PREPARO: " + _ERRO_PREPARO}
        linha.update(item["params"])
        return linha
    return _NS["_rodar_uma"](_NS["_CONTEXTO"], item)


# -------------------------------------------------------------------------
# processo principal
# -------------------------------------------------------------------------
def _fase(ns, pool, n, plano, limite, parar):
    """Roda o que falta de uma fase, mantendo n simulações em andamento.

    Devolve (terminou_inteira, quantas_foram_feitas).
    """
    log = ns["_log"]
    feitos = ns["_ids_feitos"]()
    pendentes = collections.deque(it for it in plano if it["id"] not in feitos)
    total = len(pendentes)
    log("%d pendentes de %d nesta fase" % (total, len(plano)))
    em_andamento = {}
    seguir = True
    feitas = 0
    while (pendentes and seguir) or em_andamento:
        # completa a fila de trabalho até n simulações simultâneas
        while pendentes and seguir and len(em_andamento) < n:
            if os.path.exists(parar):
                log("PARAR.txt encontrado: terminando as que estão rodando.")
                seguir = False
                break
            if time.time() > limite:
                log("HORAS_MAXIMAS atingido: terminando as que estão rodando.")
                seguir = False
                break
            item = pendentes.popleft()
            em_andamento[pool.submit(_trabalhar, item)] = item
        if not em_andamento:
            break
        prontos, _ = wait(list(em_andamento), return_when=FIRST_COMPLETED)
        for futuro in prontos:
            item = em_andamento.pop(futuro)
            try:
                linha = futuro.result()
            except BrokenProcessPool:
                log("Um processo morreu (quase sempre falta de memória). O que "
                    "já foi gravado fica; rode de novo com menos processos.")
                raise
            except Exception:  # noqa: BLE001
                linha = {"id": item["id"], "etapa": item["etapa"],
                         "modo": item["modo"],
                         "erro": traceback.format_exc(limit=3)}
                linha.update(item["params"])
            if str(linha.get("erro", "")).startswith("PREPARO"):
                log("Falha ao preparar os dados num processo:\n"
                    + linha["erro"])
                log("PARADO: %s" % linha["erro"].strip().splitlines()[-1])
                raise _Parado()
            ns["_gravar_linha"](linha)
            feitas += 1
            if linha.get("erro"):
                log("[%d/%d] %s %s ERRO: %s"
                    % (feitas, total, item["etapa"], item["id"],
                       str(linha["erro"]).strip().splitlines()[-1]))
            else:
                log("[%d/%d] %-13s %-12s AUC %.3f  cap5 %.0f%%  área %.2f  "
                    "%.0f s" % (feitas, total, item["etapa"], item["modo"],
                                linha["auc"], 100 * linha["captura_5"],
                                linha["razao_area"], linha["segundos"]))
            if feitas % 10 == 0:
                ns["_resumir_seguro"]()
    return seguir and not pendentes, feitas


def principal(n):
    _iniciar_qgis()
    ns = _carregar()
    log = ns["_log"]
    cfg = ns["CONFIG"]
    os.makedirs(cfg["PASTA_SAIDA"], exist_ok=True)
    parar = os.path.join(cfg["PASTA_SAIDA"], "PARAR.txt")
    if os.path.exists(parar):
        os.remove(parar)
        log("PARAR.txt de uma rodada anterior removido.")
    model = ns["_plugin"]()[1]
    padrao = ns["parametros_base"](model)
    limite = time.time() + cfg["HORAS_MAXIMAS"] * 3600
    log("=== calibrar_paralelo: %d processos, para até %s ==="
        % (n, _dt.datetime.fromtimestamp(limite).strftime("%d/%m %H:%M")))
    log(_versoes(model))
    log("Preparando os dados em cada processo, %d de cada vez (alguns "
        "minutos antes da 1ª linha)…" % PREPARANDO_JUNTOS)
    ns["_impedir_suspensao"](True)
    total = 0
    # "spawn" em qualquer sistema: cada processo começa limpo, como no Windows
    contexto = multiprocessing.get_context("spawn")
    semaforo = contexto.Semaphore(PREPARANDO_JUNTOS)
    try:
        with ProcessPoolExecutor(max_workers=n, mp_context=contexto,
                                 initializer=_iniciar_trabalhador,
                                 initargs=(semaforo, SCRIPT)) as pool:
            feitos = ns["_ids_feitos"]()
            try:
                resumo, bases = pool.submit(_linhas_de_base, feitos).result()
            except BrokenProcessPool:
                log("Um processo morreu (quase sempre falta de memória). Rode "
                    "de novo com menos processos.")
                raise
            except RuntimeError as erro:
                log("Falha ao preparar os dados num processo:\n%s" % erro)
                log("PARADO: %s" % str(erro).strip().splitlines()[-1])
                raise _Parado()
            log(resumo)
            for linha in bases:
                ns["_gravar_linha"](linha)
                log("linha de base %-28s AUC %.3f  captura 5%% %.0f%%"
                    % (linha["modo"], linha["auc"], 100 * linha["captura_5"]))
            completa, feitas = _fase(ns, pool, n, ns["_plano_inicial"](padrao),
                                     limite, parar)
            total += feitas
            if completa:
                # só agora todas as varreduras de r0 estão no CSV
                r0 = ns["r0_calibrado"](padrao.r0)
                log("r0 calibrado (área prevista = observada): %.4g" % r0)
                _completa, feitas = _fase(
                    ns, pool, n, ns["_plano_restante"](padrao, r0),
                    limite, parar)
                total += feitas
    finally:
        ns["_impedir_suspensao"](False)
        ns["_resumir_seguro"]()
        log("=== fim: %d simulações nesta sessão. Veja resumo.txt ===" % total)


class _Parado(Exception):
    """Erro já explicado no log: sai sem repetir o traceback."""


if __name__ == "__main__":
    if len(sys.argv) > 2:
        SCRIPT = os.path.abspath(os.path.join(PASTA, sys.argv[2]))
        if not os.path.isfile(SCRIPT):
            sys.exit("arquivo de CONFIG não encontrado: %s" % SCRIPT)
    try:
        principal(int(sys.argv[1]) if len(sys.argv) > 1 else 12)
    except _Parado:
        sys.exit(1)
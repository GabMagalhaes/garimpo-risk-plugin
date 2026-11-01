# -*- coding: utf-8 -*-
"""Motor da calibração do modelo dinâmico (core/calibracao_dinamica.py).

Métricas, plano e ids, CSV retomável, sugestão e uma rodada inteira com os
dados sintéticos; e os invólucros tools/calibrar_noite.py e
tools/calibrar_paralelo.py, que usam o mesmo motor.
"""

import datetime as _dt
import itertools
import json
import os
import sys

import numpy as np
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from risco_garimpo.core import calibracao_dinamica as cd  # noqa: E402
from risco_garimpo.core import model  # noqa: E402
from risco_garimpo.core.model import Parametros  # noqa: E402


@pytest.fixture
def motor(tmp_path):
    return cd.CalibracaoDinamica({"PASTA_SAIDA": str(tmp_path),
                                  "ESTRUTURA": False})


def _linha(**valores):
    linha = dict.fromkeys(cd.COLUNAS, "")
    linha.update(valores)
    return linha


def _auc_forca_bruta(pontuacao, alvo):
    pos = [p for p, a in zip(pontuacao, alvo) if a]
    neg = [p for p, a in zip(pontuacao, alvo) if not a]
    pares = [(1.0 if p > n else 0.5 if p == n else 0.0)
             for p, n in itertools.product(pos, neg)]
    return sum(pares) / len(pares)


# ----------------------------------------------------------------- métricas
def test_auc_bate_com_a_definicao_inclusive_com_empates():
    rng = np.random.default_rng(3)
    pontuacao = rng.integers(0, 4, 60).astype(float)     # muitos empates
    alvo = rng.random(60) < 0.3
    assert cd.auc(pontuacao, alvo) == \
        pytest.approx(_auc_forca_bruta(pontuacao, alvo))
    assert np.isnan(cd.auc(pontuacao, np.zeros(60, bool)))


def test_captura_reparte_empates_na_fronteira():
    pontuacao = np.array([3.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    observado = np.array([1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    # 30% da área: os dois primeiros e 1 de 8 empatados em zero (3/8 com garimpo)
    esperado = (1.0 + 3.0 / 8.0) / 4.0
    assert cd.captura(pontuacao, observado, 0.3) == pytest.approx(esperado)
    assert cd.captura(pontuacao, observado, 1.0) == pytest.approx(1.0)


# -------------------------------------------------------------- plano e ids
def test_r0_interpolado_onde_a_area_prevista_iguala_a_observada(motor):
    for r0, razao in ((0.002, 0.25), (0.004, 0.5), (0.008, 2.0)):
        motor.gravar_linha(_linha(id="r%s" % r0, etapa="r0",
                                  modo="reconstrucao", r0=r0,
                                  razao_area=razao))
    # entre 0,004 (0,5) e 0,008 (2,0), em log-log: raiz geométrica
    assert motor.r0_calibrado() == pytest.approx(0.005657, rel=1e-3)


def test_plano_nao_repete_combinacoes_e_tem_ids_estaveis():
    config = dict(cd.PADROES, ESTRUTURA=False)
    plano = (cd.plano_inicial(Parametros())
             + cd.plano_restante(config, 0.01, Parametros()))
    ids = [it["id"] for it in plano]
    assert len(ids) == len(set(ids))
    de_novo = cd.plano_restante(config, 0.01, Parametros())
    assert [it["id"] for it in de_novo] == \
        [it["id"] for it in plano if it["etapa"] not in ("r0", "padrao")]
    fiscal = [it for it in plano if it["etapa"] == "fiscal"]
    assert all(it["params"]["r0"] == 0.01 for it in fiscal)


def test_etapa_estrutura_nao_muda_os_ids_do_plano_antigo():
    antes = {it["id"] for it in cd.plano_restante(
        dict(cd.PADROES, ESTRUTURA=False), 0.01, Parametros())}
    depois = cd.plano_restante(dict(cd.PADROES, ESTRUTURA=True), 0.01,
                               Parametros())
    assert antes <= {it["id"] for it in depois}
    estrutura = [it for it in depois if it["etapa"] == "estrutura"]
    assert estrutura
    # fator 1 não entra nos parâmetros: o id é o mesmo da versão anterior
    assert all("k_fator" not in it["params"] or it["params"]["k_fator"] != 1.0
               for it in estrutura)


def test_os_ids_sao_os_do_calibrar_noite_ate_a_0_4_0():
    """Ids fixados de uma rodada da 0.4.0: a mudança para o core não os muda."""
    base = cd.parametros_base(model)
    assert (base.r0, base.escala_contagio_m) == (0.004, 380.0)
    padrao = model.Parametros()
    assert (padrao.r0, padrao.escala_contagio_m) == (0.0007, 100.0)
    inicial = cd.plano_inicial(base)
    assert [it["id"] for it in inicial] == [it["id"] for it in cd.plano_inicial(
        model.Parametros(r0=0.004, escala_contagio_m=380.0))]
    params = {c: getattr(base, c) for c in cd.PARAMETROS_CSV}
    assert inicial[0]["id"] == cd.identificador("reconstrucao", params)
    assert inicial[0]["params"] == params


def test_csv_antigo_aceita_o_plano_antigo_e_recusa_k_fator(motor, tmp_path):
    antigo = [c for c in cd.COLUNAS if c != "k_fator"]
    caminho = tmp_path / "resultados.csv"
    caminho.write_text(";".join(antigo) + "\n", encoding="utf-8")
    motor.gravar_linha(_linha(id="a", etapa="aleatoria", modo="previsao",
                              auc=0.8))
    assert caminho.read_text(encoding="utf-8").count("\n") == 2
    with pytest.raises(ValueError):
        motor.gravar_linha(_linha(id="b", etapa="estrutura", k_fator=1.5))


def test_configuracoes_extras_rodam_como_vieram():
    """Escolhidas em outro período: o r0 delas vale, não o calibrado aqui."""
    config = dict(cd.PADROES, CONFIGURACOES_EXTRAS=[
        {"r0": 0.0007, "escala_contagio_m": 100.0, "k_fator": 1.5},
        {"r0": 0.0007, "escala_contagio_m": 100.0, "k_fator": 1.0}])
    extras = [it for it in cd.plano_restante(config, 0.01, Parametros())
              if it["etapa"] == "extra"]
    assert len(extras) == 4
    assert {it["modo"] for it in extras} == {"previsao", "reconstrucao"}
    assert all(it["params"]["r0"] == 0.0007 for it in extras)
    assert [it["params"].get("k_fator") for it in extras] == [1.5, 1.5,
                                                               None, None]
    outro_r0 = [it["id"] for it in cd.plano_restante(config, 0.0003,
                                                     Parametros())
                if it["etapa"] == "extra"]
    assert outro_r0 == [it["id"] for it in extras]


# ------------------------------------------------------- fora da amostra
def test_fora_da_amostra_escolhe_no_treino_e_mede_no_teste(tmp_path):
    def periodo(nome, aucs, base_auc):
        pasta = tmp_path / nome
        m = cd.CalibracaoDinamica({"PASTA_SAIDA": str(pasta)})
        m.gravar_linha(_linha(id="base_proximidade", etapa="linha_de_base",
                              modo="proximidade", auc=base_auc,
                              captura_5=0.30))
        for i, valor in enumerate(aucs):
            m.gravar_linha(_linha(
                id="c%d" % i, etapa="aleatoria", modo="previsao", auc=valor,
                captura_5=valor / 2, razao_area=1.0, r_blocos=0.5, r0=0.001,
                alerta_peso=1.0, fisc_max=0.5, fisc_plato_dias=180.0,
                fisc_escala_m=750.0, escala_contagio_m=200.0))
        return str(pasta)

    # no treino c3 e c4 são as melhores; no teste, c3 é ótima e c4 péssima
    treino = periodo("treino", [0.70, 0.71, 0.72, 0.90, 0.89], 0.80)
    teste = periodo("teste", [0.70, 0.71, 0.72, 0.88, 0.60], 0.80)
    texto = cd.fora_da_amostra(treino, teste, n=2)
    assert "mediana no teste: AUC 0.740" in texto     # média de 0,88 e 0,60
    assert "NÃO supera" in texto
    assert os.path.isfile(os.path.join(teste, "fora_da_amostra.txt"))


# ------------------------------------------------------------------ sugestão
def _simulacao(ident, etapa, modo, escala, cap5, auc=0.9, r0=0.0006,
               k_fator=1.0):
    params = {c: getattr(cd.parametros_base(), c) for c in cd.PARAMETROS_CSV}
    params.update(r0=r0, escala_contagio_m=escala)
    return _linha(id=ident, etapa=etapa, modo=modo, auc=auc, captura_1=0.1,
                  captura_5=cap5, captura_10=0.7, r_blocos=0.5,
                  razao_area=0.6, k_fator=k_fator, **params)


def _com_r0(motor, r0=0.0006):
    """Varredura de r0 que cerca a razão 1 exatamente em ``r0``."""
    motor.gravar_linha(_linha(id="ra", etapa="r0", modo="reconstrucao",
                              r0=r0 / 2, razao_area=0.5))
    motor.gravar_linha(_linha(id="rb", etapa="r0", modo="reconstrucao",
                              r0=r0 * 2, razao_area=2.0))


def test_sugestao_escolhe_a_escala_de_maior_captura_no_modo_previsao(motor):
    _com_r0(motor)
    for linha in (
            _linha(id="base_prox", etapa="linha_de_base",
                   modo="proximidade_100m", auc=0.9, captura_5=0.45),
            _simulacao("a", "calibrado", "previsao", 380.0, 0.30),
            _simulacao("b", "estrutura", "previsao", 100.0, 0.47),
            _simulacao("c", "estrutura", "previsao", 150.0, 0.40),
            # melhor, mas em reconstrução, com outro r0 ou com K frouxo
            _simulacao("d", "estrutura", "reconstrucao", 200.0, 0.60),
            _simulacao("e", "aleatoria", "previsao", 250.0, 0.55, r0=0.002),
            _simulacao("f", "estrutura", "previsao", 100.0, 0.50,
                       k_fator=1.5)):
        motor.gravar_linha(linha)
    sugestao = motor.sugestao()
    assert sugestao["parametros"]["r0"] == pytest.approx(0.0006)
    assert sugestao["parametros"]["escala_contagio_m"] == 100.0
    assert sugestao["escalas_testadas"] == [100.0, 150.0, 380.0]
    assert sugestao["supera_linha_de_base"] is True
    assert sugestao["k_frouxo"]["k_fator"] == 1.5
    texto = "\n".join(cd.texto_da_sugestao(sugestao))
    assert "SUPERA" in texto and "K × 1.5" in texto


def test_sugestao_avisa_quando_nao_supera_a_linha_de_base(motor):
    _com_r0(motor)
    motor.gravar_linha(_linha(id="base_prox", etapa="linha_de_base",
                              modo="proximidade_x_remanescente_100m",
                              auc=0.9, captura_5=0.49))
    motor.gravar_linha(_simulacao("b", "estrutura", "previsao", 100.0, 0.47))
    sugestao = motor.sugestao()
    assert sugestao["supera_linha_de_base"] is False
    texto = "\n".join(cd.texto_da_sugestao(sugestao))
    assert "NÃO supera" in texto and "-2.0 pp" in texto


def test_sem_simulacao_no_modo_previsao_nao_ha_sugestao(motor):
    _com_r0(motor)
    motor.gravar_linha(_simulacao("d", "estrutura", "reconstrucao", 200.0,
                                  0.60))
    assert motor.sugestao() is None
    assert "ainda não há" in cd.texto_da_sugestao(None)[1]


def test_resumo_comeca_pela_sugestao_e_grava_o_json(motor, tmp_path):
    _com_r0(motor)
    motor.gravar_linha(_simulacao("b", "estrutura", "previsao", 100.0, 0.47))
    texto = motor.resumir()
    assert [l for l in texto.splitlines() if l][3].startswith("0) SUGESTÃO")
    with open(tmp_path / "sugestao.json", encoding="utf-8") as arquivo:
        gravada = json.load(arquivo)
    assert gravada["modelo"] == "dinamico"
    assert gravada["parametros"]["escala_contagio_m"] == 100.0
    assert (tmp_path / "resumo.txt").read_text(encoding="utf-8").startswith(
        "RESUMO DA CALIBRAÇÃO")


# ------------------------------------------------------------- uma pasta só
def test_a_pasta_recusa_outra_rodada_com_outros_dados(tmp_path):
    config = {"PASTA_SAIDA": str(tmp_path), "K": str(tmp_path / "K.tif"),
              "DATA_INICIAL": "2020-12-31", "DATA_FINAL": "2022-12-31"}
    cd.CalibracaoDinamica(config).conferir_pasta()
    cd.CalibracaoDinamica(dict(config)).conferir_pasta()       # a mesma: ok
    # o mesmo arquivo escrito de outro jeito também é o mesmo
    cd.CalibracaoDinamica(dict(config, K=str(tmp_path / "." / "K.tif"))
                          ).conferir_pasta()
    # a camada do plugin ("|layername=") e o caminho do CONFIG são o mesmo
    com_eventos = dict(config, PASTA_SAIDA=str(tmp_path / "eventos"),
                       ALERTAS=str(tmp_path / "ev.gpkg") + "|layername=a")
    cd.CalibracaoDinamica(com_eventos).conferir_pasta()
    cd.CalibracaoDinamica(dict(com_eventos, ALERTAS=str(tmp_path / "ev.gpkg"))
                          ).conferir_pasta()
    with pytest.raises(ValueError, match="DATA_FINAL"):
        cd.CalibracaoDinamica(dict(config, DATA_FINAL="2024-12-31")
                              ).conferir_pasta()


# ------------------------------------------------------ rodada de ponta a ponta
def _gdal():
    return pytest.importorskip("osgeo.gdal")


def _dados(pasta):
    """K, μ₀ e máscara sintéticos e um μ₁ com os focos crescidos."""
    gdal = _gdal()
    espaco = {"__name__": "gerador"}
    caminho = os.path.join(RAIZ, "tools", "gerar_dados_teste.py")
    exec(compile(open(caminho, encoding="utf-8").read(), caminho, "exec"),
         espaco)
    dados = espaco["gerar"](str(pasta))
    fonte = gdal.Open(dados["K"])
    K = fonte.GetRasterBand(1).ReadAsArray()
    fonte_mu0 = gdal.Open(dados["mu0"])
    mu0 = fonte_mu0.GetRasterBand(1).ReadAsArray()
    fonte_mu0 = None
    mu1 = mu0.copy()
    for linha, coluna in ((60, 70), (130, 150), (95, 45)):
        mu1[linha - 4:linha + 5, coluna - 4:coluna + 5] = 0.9
    mu1 = np.minimum(np.maximum(mu1, mu0), np.maximum(K, mu0))
    destino = gdal.GetDriverByName("GTiff").Create(
        str(pasta / "teste_mu1.tif"), fonte.RasterXSize, fonte.RasterYSize, 1,
        gdal.GDT_Float32)
    destino.SetGeoTransform(fonte.GetGeoTransform())
    destino.SetProjection(fonte.GetProjection())
    destino.GetRasterBand(1).WriteArray(mu1)
    destino = None
    dados["mu1"] = str(pasta / "teste_mu1.tif")
    return dados


@pytest.fixture
def plano_curto(monkeypatch):
    """Um plano de poucas simulações, com a mesma estrutura do real."""
    monkeypatch.setattr(cd, "R0", [0.0005, 0.004, 0.03])
    monkeypatch.setattr(cd, "SENSIBILIDADE", {"alerta_peso": [0.0]})
    monkeypatch.setattr(cd, "FISCAL", {"fisc_plato_dias": [180.0],
                                       "fisc_escala_m": [750.0],
                                       "fisc_max": [0.5]})
    monkeypatch.setattr(cd, "ESTRUTURA_K", [1.0, 2.0])
    monkeypatch.setattr(cd, "ESTRUTURA_ESCALAS_M", [200.0, 380.0])
    monkeypatch.setattr(cd, "ESCALAS_BASE_M", [200.0, 380.0])


def _config(dados, pasta):
    return {"K": dados["K"], "MU_INICIAL": dados["mu0"],
            "MU_FINAL": dados["mu1"], "MASCARA": dados["mascara"],
            "DATA_INICIAL": "2025-01-01", "DATA_FINAL": "2026-12-31",
            "RESOLUCAO_M": 100.0, "PASTA_SAIDA": str(pasta),
            "N_ALEATORIAS": 2, "ESTRUTURA": True}


def test_rodada_completa_sugere_e_retoma_sem_repetir(tmp_path, plano_curto):
    dados = _dados(tmp_path / "dados")
    pasta = tmp_path / "saida"
    mensagens = []
    motor = cd.CalibracaoDinamica(_config(dados, pasta),
                                  registrar=mensagens.append, detalhar=True)
    motor.preparar_eventos()
    motor.carregar()
    progresso = []
    feitas = motor.rodar(progresso=lambda a, b: progresso.append((a, b)))
    plano = motor.plano_completo()
    assert feitas == len(plano)
    linhas = motor.ler()
    assert not [l for l in linhas if l.get("erro")]
    bases = [l for l in linhas if l["etapa"] == "linha_de_base"]
    assert len(bases) == 2 + 2 * len(cd.ESCALAS_BASE_M)
    assert {l["id"] for l in linhas} - {l["id"] for l in bases} == \
        {it["id"] for it in plano}
    assert progresso[-1] == (len(plano), len(plano))
    assert any("plano:" in m for m in mensagens)       # estimativa de tempo

    sugestao = json.loads((pasta / "sugestao.json").read_text("utf-8"))
    assert sugestao["parametros"]["r0"] == pytest.approx(motor.r0_calibrado())
    assert sugestao["parametros"]["escala_contagio_m"] in (200.0, 380.0)
    assert "0) SUGESTÃO" in (pasta / "resumo.txt").read_text("utf-8")

    # rodar de novo, com outro motor, não repete nada
    outro = cd.CalibracaoDinamica(_config(dados, pasta))
    outro.preparar_eventos()
    outro.carregar()
    assert outro.rodar() == 0
    assert len(outro.ler()) == len(linhas)


def test_cancelar_nao_grava_a_simulacao_em_andamento(tmp_path, plano_curto):
    dados = _dados(tmp_path / "dados")
    motor = cd.CalibracaoDinamica(_config(dados, tmp_path / "saida"))
    motor.preparar_eventos()
    motor.carregar()
    n_bases = len(motor.ids_feitos())
    chamadas = []

    def cancelado():
        # chamado a cada passo das simulações: depois de duas prontas,
        # cancela no meio de uma (ou no começo da seguinte)
        chamadas.append(1)
        prontas = len(motor.ids_feitos()) - n_bases
        return prontas >= 2 and len(chamadas) % 25 == 0

    with pytest.raises(cd.Cancelada):
        motor.rodar(cancelado=cancelado)
    feitas = [l for l in motor.ler() if l["etapa"] != "linha_de_base"]
    assert 0 < len(feitas) < len(motor.plano_completo())
    assert not [l for l in feitas if l.get("erro")]


def test_mapa_fora_de_0_1_e_periodo_invertido_sao_recusados(tmp_path):
    dados = _dados(tmp_path / "dados")
    config = _config(dados, tmp_path / "saida")
    with pytest.raises(ValueError, match="posterior"):
        cd.CalibracaoDinamica(dict(config, DATA_FINAL="2024-01-01")
                              ).preparar_eventos()
    with pytest.raises(ValueError, match="não encontrado"):
        cd.CalibracaoDinamica(dict(config, MU_FINAL=str(tmp_path / "x.tif"))
                              ).preparar_eventos()


# ------------------------------------------------------------- os invólucros
def _executar(nome, **extra):
    caminho = os.path.join(RAIZ, "tools", nome)
    espaco = {"__name__": "__console__", "__file__": caminho}
    espaco.update(extra)
    exec(compile(open(caminho, encoding="utf-8").read(), caminho, "exec"),
         espaco)
    return espaco


def test_o_config_do_console_tem_as_chaves_do_motor():
    espaco = _executar("calibrar_noite.py")
    assert set(espaco["CONFIG"]) == set(cd.PADROES)
    assert espaco["CONFIG"]["ESTRUTURA"] is True


def test_o_console_usa_o_motor_do_plugin(tmp_path):
    espaco = _executar("calibrar_noite.py")
    espaco["CONFIG"]["PASTA_SAIDA"] = str(tmp_path)
    motor = espaco["_motor"]()
    assert isinstance(motor, cd.CalibracaoDinamica)
    assert motor is espaco["_motor"]()                 # o mesmo, na sessão
    espaco["CONFIG"]["N_ALEATORIAS"] = 3
    assert motor is not espaco["_motor"]()             # CONFIG mudou
    motor = espaco["_motor"]()
    motor.gravar_linha(_linha(id="ra", etapa="r0", modo="reconstrucao",
                              r0=0.0003, razao_area=0.5))
    texto = espaco["resumir"](imprimir=False)
    assert "0) SUGESTÃO" in texto


def test_o_paralelo_usa_o_motor_do_plugin():
    espaco = _executar("calibrar_paralelo.py")
    fonte = open(os.path.join(RAIZ, "tools", "calibrar_paralelo.py"),
                 encoding="utf-8").read()
    assert "CalibracaoDinamica" in fonte and "_NS" not in fonte
    assert espaco["MEMORIA_POR_PROCESSO_GB"] < cd.PADROES["MEMORIA_CAMPOS_GB"]


def test_datas_aceitam_texto_ou_date():
    assert cd._data("2022-12-31") == _dt.date(2022, 12, 31)
    assert cd._data(_dt.date(2022, 12, 31)) == _dt.date(2022, 12, 31)

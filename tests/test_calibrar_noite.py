# -*- coding: utf-8 -*-
"""Métricas e calibração de r0 do roteiro tools/calibrar_noite.py (sem QGIS)."""

import itertools
import os
import sys

import numpy as np
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from risco_garimpo.core.model import Parametros  # noqa: E402


@pytest.fixture
def roteiro(tmp_path):
    caminho = os.path.join(RAIZ, "tools", "calibrar_noite.py")
    espaco = {"__name__": "__console__"}
    exec(compile(open(caminho, encoding="utf-8").read(), caminho, "exec"),
         espaco)
    espaco["CONFIG"]["PASTA_SAIDA"] = str(tmp_path)
    return espaco


def _auc_forca_bruta(pontuacao, alvo):
    pos = [p for p, a in zip(pontuacao, alvo) if a]
    neg = [p for p, a in zip(pontuacao, alvo) if not a]
    pares = [(1.0 if p > n else 0.5 if p == n else 0.0)
             for p, n in itertools.product(pos, neg)]
    return sum(pares) / len(pares)


def test_auc_bate_com_a_definicao_inclusive_com_empates(roteiro):
    rng = np.random.default_rng(3)
    pontuacao = rng.integers(0, 4, 60).astype(float)     # muitos empates
    alvo = rng.random(60) < 0.3
    assert roteiro["auc"](pontuacao, alvo) == \
        pytest.approx(_auc_forca_bruta(pontuacao, alvo))
    assert np.isnan(roteiro["auc"](pontuacao, np.zeros(60, bool)))


def test_captura_reparte_empates_na_fronteira(roteiro):
    pontuacao = np.array([3.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    observado = np.array([1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    # 30% da área: os dois primeiros e 1 de 8 empatados em zero (3/8 com garimpo)
    esperado = (1.0 + 3.0 / 8.0) / 4.0
    assert roteiro["captura"](pontuacao, observado, 0.3) == \
        pytest.approx(esperado)
    assert roteiro["captura"](pontuacao, observado, 1.0) == pytest.approx(1.0)


def test_r0_interpolado_onde_a_area_prevista_iguala_a_observada(roteiro):
    gravar = roteiro["_gravar_linha"]
    for r0, razao in ((0.002, 0.25), (0.004, 0.5), (0.008, 2.0)):
        linha = dict.fromkeys(roteiro["COLUNAS"], "")
        linha.update(id="r%s" % r0, etapa="r0", modo="reconstrucao", r0=r0,
                     razao_area=razao)
        gravar(linha)
    # entre 0,004 (0,5) e 0,008 (2,0), em log-log: raiz geométrica
    assert roteiro["r0_calibrado"]() == pytest.approx(0.005657, rel=1e-3)


def test_plano_nao_repete_combinacoes_e_tem_ids_estaveis(roteiro):
    plano = (roteiro["_plano_inicial"](Parametros())
             + roteiro["_plano_restante"](Parametros(), 0.01))
    ids = [it["id"] for it in plano]
    assert len(ids) == len(set(ids))
    de_novo = roteiro["_plano_restante"](Parametros(), 0.01)
    assert [it["id"] for it in de_novo] == \
        [it["id"] for it in plano if it["etapa"] != "r0"
         and it["etapa"] != "padrao"]
    fiscal = [it for it in plano if it["etapa"] == "fiscal"]
    assert all(it["params"]["r0"] == 0.01 for it in fiscal)


def test_etapa_estrutura_nao_muda_os_ids_do_plano_antigo(roteiro):
    antes = {it["id"] for it in roteiro["_plano_restante"](Parametros(), 0.01)}
    roteiro["CONFIG"]["ESTRUTURA"] = True
    depois = roteiro["_plano_restante"](Parametros(), 0.01)
    assert antes <= {it["id"] for it in depois}
    estrutura = [it for it in depois if it["etapa"] == "estrutura"]
    assert estrutura
    # fator 1 não entra nos parâmetros: o id é o mesmo da versão anterior
    assert all("k_fator" not in it["params"] or it["params"]["k_fator"] != 1.0
               for it in estrutura)


def test_csv_antigo_aceita_o_plano_antigo_e_recusa_k_fator(roteiro, tmp_path):
    antigo = [c for c in roteiro["COLUNAS"] if c != "k_fator"]
    caminho = tmp_path / "resultados.csv"
    caminho.write_text(";".join(antigo) + "\n", encoding="utf-8")
    linha = dict.fromkeys(roteiro["COLUNAS"], "")
    linha.update(id="a", etapa="aleatoria", modo="previsao", auc=0.8)
    roteiro["_gravar_linha"](linha)
    assert caminho.read_text(encoding="utf-8").count("\n") == 2
    linha.update(id="b", etapa="estrutura", k_fator=1.5)
    with pytest.raises(ValueError):
        roteiro["_gravar_linha"](linha)


def test_fora_da_amostra_escolhe_no_treino_e_mede_no_teste(roteiro, tmp_path):
    def periodo(nome, aucs, base_auc):
        pasta = tmp_path / nome
        pasta.mkdir()
        roteiro["CONFIG"]["PASTA_SAIDA"] = str(pasta)
        base = dict.fromkeys(roteiro["COLUNAS"], "")
        base.update(id="base_proximidade", etapa="linha_de_base",
                    modo="proximidade", auc=base_auc, captura_5=0.30)
        roteiro["_gravar_linha"](base)
        for i, valor in enumerate(aucs):
            linha = dict.fromkeys(roteiro["COLUNAS"], "")
            linha.update(id="c%d" % i, etapa="aleatoria", modo="previsao",
                         auc=valor, captura_5=valor / 2, razao_area=1.0,
                         r_blocos=0.5, r0=0.001, alerta_peso=1.0,
                         fisc_max=0.5, fisc_plato_dias=180.0,
                         fisc_escala_m=750.0, escala_contagio_m=200.0)
            roteiro["_gravar_linha"](linha)
        return str(pasta)

    # no treino c3 e c4 são as melhores; no teste, c3 é ótima e c4 péssima
    treino = periodo("treino", [0.70, 0.71, 0.72, 0.90, 0.89], 0.80)
    teste = periodo("teste", [0.70, 0.71, 0.72, 0.88, 0.60], 0.80)
    texto = roteiro["fora_da_amostra"](treino, teste, n=2, imprimir=False)
    assert "mediana no teste: AUC 0.740" in texto     # média de 0,88 e 0,60
    assert "NÃO supera" in texto
    assert os.path.isfile(os.path.join(teste, "fora_da_amostra.txt"))


def test_configuracoes_extras_rodam_como_vieram(roteiro):
    """Escolhidas em outro período: o r0 delas vale, não o calibrado aqui."""
    roteiro["CONFIG"]["CONFIGURACOES_EXTRAS"] = [
        {"r0": 0.0007, "escala_contagio_m": 100.0, "k_fator": 1.5},
        {"r0": 0.0007, "escala_contagio_m": 100.0, "k_fator": 1.0}]
    extras = [it for it in roteiro["_plano_restante"](Parametros(), 0.01)
              if it["etapa"] == "extra"]
    assert len(extras) == 4
    assert {it["modo"] for it in extras} == {"previsao", "reconstrucao"}
    assert all(it["params"]["r0"] == 0.0007 for it in extras)
    assert [it["params"].get("k_fator") for it in extras] == [1.5, 1.5,
                                                               None, None]
    outro_r0 = [it["id"] for it in roteiro["_plano_restante"](
        Parametros(), 0.0003) if it["etapa"] == "extra"]
    assert outro_r0 == [it["id"] for it in extras]


def test_a_base_dos_planos_nao_segue_os_padroes_do_plugin(roteiro):
    """Os ids das rodadas já feitas dependem da base dos planos, fixada nos
    padrões até a 0.3.7; os padrões do plugin mudaram na 0.4.0."""
    from risco_garimpo.core import model
    base = roteiro["parametros_base"](model)
    assert (base.r0, base.escala_contagio_m) == (0.004, 380.0)
    padrao = model.Parametros()
    assert (padrao.r0, padrao.escala_contagio_m) == (0.0007, 100.0)
    ids = {it["id"] for it in roteiro["_plano_inicial"](base)}
    assert ids == {it["id"] for it in roteiro["_plano_inicial"](
        model.Parametros(r0=0.004, escala_contagio_m=380.0))}

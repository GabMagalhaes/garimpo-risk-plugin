# -*- coding: utf-8 -*-
"""Gera um conjunto sintético de insumos para testar o plugin.

Serve para verificar que o plugin roda ponta a ponta **antes** de ligar os
dados reais: se falhar aqui, o problema é o plugin; se funcionar aqui e
falhar com os seus dados, o problema é projeção, campo de data ou extensão.

Como usar, dentro do QGIS (Complementos → Console Python), duas linhas:

    exec(open(r"C:\\Plugin\\tools\\gerar_dados_teste.py", encoding="utf-8").read())
    gerar_e_carregar(r"C:\\Plugin\\teste")

A primeira linha define as funções; a segunda gera os arquivos e já carrega
as camadas no projeto aberto.

Para testar o plugin instalado de ponta a ponta, sem abrir os diálogos, troque
a segunda linha por:

    testar_ponta_a_ponta(r"C:\\Plugin\\teste")

Isso gera os dados, roda o modelo dinâmico e o estático com o código do
plugin instalado, grava as saídas em ``<pasta>\\saidas`` e confere o que cada
registro de execução (JSON) contém.

Cria, na pasta indicada:

    teste_K.tif        favorabilidade sintética (0–1), com áreas de K = 0
    teste_mu0.tif      três focos de garimpo já instalados
    teste_mascara.tif  área de estudo
    teste_eventos.gpkg camadas 'alertas' (6 frentes) e 'autos' (12 pontos)

Área de 20 × 20 km em SIRGAS 2000 / UTM 21S (EPSG:31981), pixel de 100 m —
pequena de propósito: roda em segundos.
"""

import datetime as _dt
import os

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()

EPSG = 31981
XMIN = 600000.0
YMAX = 9400000.0
RES = 100.0
NX = 200
NY = 200
SEMENTE = 20250921


def _referencia():
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    try:
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:
        pass
    return srs


def _gravar(caminho, arr, nodata=-9999.0):
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(caminho, NX, NY, 1, gdal.GDT_Float32,
                       options=["COMPRESS=DEFLATE", "TILED=YES"])
    ds.SetGeoTransform((XMIN, RES, 0.0, YMAX, 0.0, -RES))
    ds.SetProjection(_referencia().ExportToWkt())
    banda = ds.GetRasterBand(1)
    banda.SetNoDataValue(float(nodata))
    banda.WriteArray(np.asarray(arr, dtype=np.float32))
    banda.FlushCache()
    ds = None
    return caminho


def _suavizar(arr, passos=6, raio=3):
    """Média móvel repetida — aproxima um borrão gaussiano sem SciPy."""
    saida = np.asarray(arr, dtype=np.float64)
    for _ in range(passos):
        acumulado = np.zeros_like(saida)
        contagem = 0
        for dy in range(-raio, raio + 1):
            for dx in range(-raio, raio + 1):
                acumulado += np.roll(np.roll(saida, dy, axis=0), dx, axis=1)
                contagem += 1
        saida = acumulado / contagem
    return saida


def _normalizar(arr):
    minimo, maximo = float(arr.min()), float(arr.max())
    if maximo <= minimo:
        return np.zeros_like(arr)
    return (arr - minimo) / (maximo - minimo)


def _pixel_para_coord(linha, coluna):
    return (XMIN + (coluna + 0.5) * RES, YMAX - (linha + 0.5) * RES)


def _camada_pontos(fonte, nome, pontos, datas):
    camada = fonte.CreateLayer(nome, _referencia(), ogr.wkbPoint)
    camada.CreateField(ogr.FieldDefn("data", ogr.OFTDate))
    camada.CreateField(ogr.FieldDefn("obs", ogr.OFTString))
    definicao = camada.GetLayerDefn()
    for (x, y), data in zip(pontos, datas):
        feicao = ogr.Feature(definicao)
        geometria = ogr.Geometry(ogr.wkbPoint)
        geometria.AddPoint(float(x), float(y))
        feicao.SetGeometry(geometria)
        feicao.SetField("data", data.year, data.month, data.day, 0, 0, 0, 0)
        feicao.SetField("obs", "%s sintético" % nome)
        camada.CreateFeature(feicao)
        feicao = None
    return camada


def gerar(pasta=None, prefixo="teste"):
    pasta = pasta or os.getcwd()
    os.makedirs(pasta, exist_ok=True)
    rng = np.random.default_rng(SEMENTE)

    # ------------------------------------------------------ K e área válida
    K = _normalizar(_suavizar(rng.random((NY, NX)), passos=8, raio=4))
    K = np.clip((K - 0.25) / 0.75, 0.0, 1.0)      # parte do território sem aptidão

    mascara = np.ones((NY, NX), dtype=np.float32)
    mascara[:, :10] = 0.0                          # borda fora da área de estudo
    mascara[:10, :] = 0.0
    K = K * mascara

    # ------------------------------------------------------------ focos de μ
    mu0 = np.zeros((NY, NX), dtype=np.float32)
    focos = [(60, 70), (130, 150), (95, 45)]
    for linha, coluna in focos:
        mu0[linha - 1:linha + 2, coluna - 1:coluna + 2] = 0.9
    mu0 = np.minimum(mu0, K)                       # μ nunca excede K

    caminho_k = _gravar(os.path.join(pasta, "%s_K.tif" % prefixo), K)
    caminho_mu = _gravar(os.path.join(pasta, "%s_mu0.tif" % prefixo), mu0)
    caminho_mascara = _gravar(os.path.join(pasta, "%s_mascara.tif" % prefixo),
                              mascara)

    # ---------------------------------------------------------------- eventos
    caminho_gpkg = os.path.join(pasta, "%s_eventos.gpkg" % prefixo)
    if os.path.exists(caminho_gpkg):
        os.remove(caminho_gpkg)
    fonte = ogr.GetDriverByName("GPKG").CreateDataSource(caminho_gpkg)

    # seis frentes de alerta, cada uma um aglomerado de fragmentos a < 100 m
    pontos_alerta, datas_alerta = [], []
    centros = [(55, 75), (65, 62), (128, 158), (140, 145), (100, 40), (88, 52)]
    base = _dt.date(2025, 1, 15)
    for i, (linha, coluna) in enumerate(centros):
        cx, cy = _pixel_para_coord(linha, coluna)
        chegada = base + _dt.timedelta(days=45 * i)
        for _ in range(25):                        # fragmentos da mesma detecção
            pontos_alerta.append((cx + rng.normal(0, 30.0),
                                  cy + rng.normal(0, 30.0)))
            datas_alerta.append(chegada
                                + _dt.timedelta(days=int(rng.integers(0, 20))))
    _camada_pontos(fonte, "alertas", pontos_alerta, datas_alerta)

    # autos de infração: alguns sobre os focos, outros dispersos
    pontos_auto, datas_auto = [], []
    for i, (linha, coluna) in enumerate(focos + centros[:3]):
        cx, cy = _pixel_para_coord(linha, coluna)
        for j in range(2):
            pontos_auto.append((cx + rng.normal(0, 200.0),
                                cy + rng.normal(0, 200.0)))
            datas_auto.append(_dt.date(2025, 3, 1)
                              + _dt.timedelta(days=60 * i + 30 * j))
    _camada_pontos(fonte, "autos", pontos_auto, datas_auto)
    fonte = None

    print("Insumos sintéticos gravados em:", pasta)
    print("  K            ", caminho_k)
    print("  μ₀           ", caminho_mu)
    print("  máscara      ", caminho_mascara)
    print("  eventos      ", caminho_gpkg, "(camadas 'alertas' e 'autos')")
    print("")
    print("Alertas: %d fragmentos em 6 frentes — o plugin deve reportar "
          "6 frentes ao agrupar." % len(pontos_alerta))
    print("Autos:   %d pontos." % len(pontos_auto))
    print("")
    print("Período sugerido na aba Parâmetros: 01/01/2025 a 31/12/2026.")
    return {"K": caminho_k, "mu0": caminho_mu, "mascara": caminho_mascara,
            "eventos": caminho_gpkg}


def carregar_no_qgis(saidas):
    """Carrega os arquivos gerados no projeto aberto (só dentro do QGIS)."""
    from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

    projeto = QgsProject.instance()
    for rotulo, chave in (("teste K", "K"), ("teste μ₀", "mu0"),
                          ("teste máscara", "mascara")):
        camada = QgsRasterLayer(saidas[chave], rotulo)
        if camada.isValid():
            projeto.addMapLayer(camada)
    for nome in ("alertas", "autos"):
        camada = QgsVectorLayer("%s|layername=%s" % (saidas["eventos"], nome),
                                "teste %s" % nome, "ogr")
        if camada.isValid():
            projeto.addMapLayer(camada)
    print("Camadas carregadas no projeto.")


def _gravar_subbacias(pasta, prefixo="teste"):
    """Quatro quadrantes de 10 × 10 km, com o campo ``nome``."""
    caminho = os.path.join(pasta, "%s_subbacias.gpkg" % prefixo)
    if os.path.exists(caminho):
        os.remove(caminho)
    fonte = ogr.GetDriverByName("GPKG").CreateDataSource(caminho)
    camada = fonte.CreateLayer("subbacias", _referencia(), ogr.wkbPolygon)
    camada.CreateField(ogr.FieldDefn("nome", ogr.OFTString))
    lado = NX * RES / 2.0
    for i, (linha, coluna) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
        x0 = XMIN + coluna * lado
        y0 = YMAX - linha * lado
        anel = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in ((x0, y0), (x0 + lado, y0), (x0 + lado, y0 - lado),
                     (x0, y0 - lado), (x0, y0)):
            anel.AddPoint_2D(x, y)
        poligono = ogr.Geometry(ogr.wkbPolygon)
        poligono.AddGeometry(anel)
        feicao = ogr.Feature(camada.GetLayerDefn())
        feicao.SetGeometry(poligono)
        feicao.SetField("nome", "SB%d" % (i + 1))
        camada.CreateFeature(feicao)
        feicao = None
    fonte = None
    return caminho


def testar_ponta_a_ponta(pasta=None, prefixo="teste"):
    """Gera os dados e roda os dois modelos com o plugin instalado.

    Só dentro do QGIS. Não usa os diálogos: chama o núcleo do plugin na
    mesma ordem em que a interface o chama. O modelo estático usa o K
    sintético no lugar do PGgeom, só para exercitar o fluxo. Devolve os
    caminhos dos dois registros de execução e o número de verificações que
    falharam.
    """
    import json
    import sys

    from qgis.core import QgsVectorLayer

    # descarta módulos do plugin já importados nesta sessão, para usar a
    # versão instalada agora
    for nome in [m for m in sys.modules if m.split(".")[0] == "risco_garimpo"]:
        del sys.modules[nome]
    try:
        from risco_garimpo.core import execucao, execucao_estatica, registro
    except ImportError as erro:
        print("ERRO: não achei o plugin Risco Garimpo com o registro de "
              "execução (%s). Instale o ZIP novo em Complementos → "
              "Gerenciar e instalar complementos → Instalar a partir do ZIP "
              "e rode de novo." % erro)
        return None
    if not hasattr(registro, "remover"):
        print("ERRO: o plugin instalado é anterior às últimas correções. "
              "Instale o ZIP novo e reinicie o QGIS.")
        return None

    pasta = pasta or os.getcwd()
    dados = gerar(pasta, prefixo)
    dados["subbacias"] = _gravar_subbacias(pasta, prefixo)
    pasta_saida = os.path.join(pasta, "saidas")

    def camada(caminho, nome):
        resultado = QgsVectorLayer("%s|layername=%s" % (caminho, nome), nome,
                                   "ogr")
        if not resultado.isValid():
            raise RuntimeError("camada inválida: %s" % nome)
        return resultado

    alertas = camada(dados["eventos"], "alertas")
    autos = camada(dados["eventos"], "autos")
    subbacias = camada(dados["subbacias"], "subbacias")

    def log(mensagem):
        print("   " + str(mensagem))

    print("\n=== Modelo dinâmico ===")
    cfg = execucao.Configuracao(
        caminho_k=dados["K"], caminho_mu0=dados["mu0"],
        caminho_mascara=dados["mascara"],
        camada_alertas=alertas, campo_data_alertas="data",
        camada_fiscalizacao=autos, campo_data_fiscalizacao="data",
        camada_subbacias=subbacias, campo_id_subbacias="nome",
        data_inicio=_dt.date(2025, 1, 1), data_fim=_dt.date(2026, 12, 31),
        pasta_saida=pasta_saida, prefixo=prefixo)
    preparo = execucao.preparar(cfg, log=log)
    saida = execucao.executar(cfg, preparo, log=log)
    execucao.agregar(cfg, saida["grade"], saida["resultado"], log=log,
                     registro=saida["registro"])

    print("\n=== Modelo estático ===")
    cfg_e = execucao_estatica.ConfiguracaoEstatica(
        caminho_ibx=dados["K"], caminho_mu=dados["mu0"],
        caminho_mascara=dados["mascara"], pasta_saida=pasta_saida,
        prefixo=prefixo + "_estatico", resolucao_m=RES,
        camada_subbacias=subbacias, campo_id_subbacia="nome", carregar=False)
    calculado = execucao_estatica.executar(
        execucao_estatica.preparar(cfg_e, registrar=log), registrar=log)
    execucao_estatica.escrever(calculado, registrar=log)

    registros = {
        "dinamico": saida["registro"],
        "estatico": execucao_estatica.caminho_de_saida(cfg_e, "registro",
                                                       "json"),
    }
    falhas = 0
    for rotulo, caminho in registros.items():
        falhas += _conferir_registro(rotulo, caminho, json)
    print("\n%s" % ("TUDO CERTO: os dois registros têm o esperado."
                     if not falhas else
                     "%d verificação(ões) falharam — veja acima." % falhas))
    registros["falhas"] = falhas
    return registros


def _conferir_registro(rotulo, caminho, json):
    """Imprime o essencial de um registro e confere o esperado."""
    print("\n=== Registro do modelo %s ===" % rotulo)
    if not caminho or not os.path.isfile(caminho):
        print("   FALHOU: registro não gravado (%s)" % caminho)
        return 1
    with open(caminho, encoding="utf-8") as arquivo:
        dados = json.load(arquivo)
    print("   arquivo:      %s" % caminho)
    print("   plugin:       %s   |   QGIS %s, GDAL %s, Python %s"
          % (dados["versao_plugin"], dados["ambiente"]["qgis"],
             dados["ambiente"]["gdal"], dados["ambiente"]["python"]))
    print("   início/fim:   %s  →  %s" % (dados["inicio"], dados["gravado_em"]))
    print("   insumos:")
    for insumo in dados["insumos"]:
        print("     %-12s %s  %s"
              % (insumo["papel"], (insumo.get("sha256") or "sem SHA-256")[:16],
                 insumo.get("caminho")))
    for tipo, info in sorted(dados["eventos"].items()):
        print("   eventos %-13s %s" % (tipo + ":", info))
    print("   saídas:       %s" % ", ".join(sorted(dados["saidas"])))

    insumos = {i["papel"]: i for i in dados["insumos"]}
    if rotulo == "dinamico":
        esperado = [
            ("K, μ₀ e máscara identificados por SHA-256",
             all(insumos.get(p, {}).get("sha256")
                 for p in ("K", "mu0", "mascara"))),
            ("alertas e autos identificados por SHA-256",
             all(insumos.get(p, {}).get("sha256")
                 for p in ("alertas", "fiscalizacao"))),
            ("150 alertas agrupados em 6 frentes",
             dados["eventos"].get("alertas", {}).get("lidos") == 150
             and dados["eventos"]["alertas"].get("frentes") == 6),
            ("12 autos lidos",
             dados["eventos"].get("fiscalizacao", {}).get("lidos") == 12),
            ("parâmetros do modelo registrados",
             dados["parametros"].get("fisc_plato_dias") is not None),
            ("GeoPackage por sub-bacia acrescentado",
             "subbacias" in dados["saidas"]
             and dados["resultados"].get("subbacias") == 4),
        ]
    else:
        esperado = [
            ("PGgeom, μ e máscara identificados por SHA-256",
             all(insumos.get(p, {}).get("sha256")
                 for p in ("PGgeom", "mu", "mascara"))),
            ("sub-bacias identificadas por SHA-256",
             bool(insumos.get("subbacias", {}).get("sha256"))),
            ("K gravado e teto registrado",
             "K" in dados["saidas"]
             and dados["resultados"].get("teto_estimado") is not None),
            ("GeoPackage por sub-bacia gravado",
             "subbacias" in dados["saidas"]),
        ]
    falhas = 0
    for descricao, ok in esperado:
        print("   %s  %s" % ("OK     " if ok else "FALHOU ", descricao))
        falhas += 0 if ok else 1
    return falhas


def gerar_e_carregar(pasta=None, prefixo="teste"):
    """Atalho para o console do QGIS: gera os arquivos e já os carrega."""
    saidas = gerar(pasta, prefixo)
    carregar_no_qgis(saidas)
    return saidas


if __name__ == "__main__":
    import sys

    gerar(sys.argv[1] if len(sys.argv) > 1 else None)
else:
    # Mesmo motivo do gerar_K: o editor do QGIS executa o arquivo com
    # __name__ != "__main__", e sem este aviso o script devolve o prompt em
    # silêncio, parecendo que falhou.
    print("gerar_dados_teste carregado — nada foi gerado ainda.")
    print("  Rode:  gerar_e_carregar(r'C:\\Users\\<voce>\\Output\\teste')")
    print("  ou, para testar o plugin de ponta a ponta:")
    print("         testar_ponta_a_ponta(r'C:\\Users\\<voce>\\Output\\teste')")

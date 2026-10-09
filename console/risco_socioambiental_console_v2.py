# -*- coding: utf-8 -*-
"""RISCO SOCIOAMBIENTAL PREVENTIVO — console Python do QGIS 4.2.

Abra este arquivo no EDITOR do console Python e clique em Executar.
As configurações ficam abaixo. Pode também executar com exec(compile(...)).
Arquivo independente: não precisa importar o Python entregue anteriormente.

Usa as camadas já carregadas. Se faltar um campo/camada ou houver ambiguidade,
salva o diagnóstico e abre uma janela para selecionar os inputs.
Conserva a matriz 1–5 e o nível 2 para ausência química, identificado por flag.
Gera uma camada nova, CSV, relatório e estilo QML; os inputs não são editados.

População: soma já agregada, densidade em hab/km² ou WorldPop de CONTAGEM.
Ligação química por HYBAS_ID. No modo raster, usa a grade original, banda 1,
centros de pixels e cobertura válida completa, sem reamostrar contagens.
Combinação F*S/5 opcional: prioridade de preservação, não probabilidade.
Com ela, o topo de 1/5/10% é comparado nos cenários min/est/max e as
sub-bacias com pertinência instável recebem topo_inst.
As camadas são lidas com seus filtros. Seleções ativas limitam a execução se
RESPEITAR_SELECAO = True, como na janela de Processamento; sempre há aviso.
O estilo distingue o nível 2 imputado (hachura) do nível 2 calculado.
"""
import csv
import datetime as dt
import json
import math
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path


# CONFIGURAÇÕES — ALTERE SOMENTE ESTE BLOCO, SE NECESSÁRIO.
CAMADA_SUBBACIAS = 'PMT_v2_subbacias'
ID_CAMADA_SUBBACIAS = None       # alternativa: ID da camada, se o nome se repete
CAMPO_ID_SUBBACIAS = 'HYBAS_ID'

CAMADA_MERCURIO = None           # None: mesma sub-bacia se tem Rsc_RbM; senão candidata única
ID_CAMADA_MERCURIO = None        # resolve nomes repetidos, inclusive Recortado/Recortado_2
CAMPO_ID_MERCURIO = 'HYBAS_ID'
CAMPO_MERCURIO = 'Rsc_RbM'        # indicador BRUTO, não classe já reclassificada

MODO_POPULACAO = 'contagem'      # 'contagem', 'densidade' ou 'raster'
CAMPO_POPULACAO = None           # soma/densidade nas sub-bacias; None tenta nomes exatos usuais
CAMADA_POPULACAO = None          # nome exato do WorldPop de contagem, se modo='raster'
ID_CAMADA_POPULACAO = None       # alternativa para nome repetido
ANO_POPULACAO = '2025'

CAMPO_AREA_KM2 = None            # None: área da geometria; ou campo em km² compatível com a soma
GERAR_PRIORIDADE_INTEGRADA = False
CAMPO_FAVORABILIDADE = None      # F em [0,1], SEM população/mercúrio já incluídos
REFERENCIA_MERCURIO = 'Domingues et al. (2024) — camada usada no projeto'

RESPEITAR_SELECAO = True         # True: só feições selecionadas, se houver seleção
PASTA_SAIDA = Path.home() / 'Etapa1_risco_socioambiental'
ABRIR_JANELA_SE_FALTAR_CONFIGURACAO = True
EXECUTAR_AO_ABRIR = True
# FIM DAS CONFIGURAÇÕES.

VERSAO = 'socio_pre_console_v2_20261006'
MATRIZ = ((3, 4, 5), (2, 3, 4), (1, 2, 3))
NIVEL_SEM_HG = 2
FRACOES_TOPO = (.01, .05, .10)
CENARIOS_TOPO = ('prio_min', 'prio_est', 'prio_max')
COLUNAS = (
    'HYBAS_ID', 'pop_sum', 'area_km2', 'dens_pop', 'Rsc_RbM',
    'cl_pop', 'cl_hg', 'socio_pre', 'hg_imput', 'hg_status',
    'pop_status', 'pop_cob', 'area_geom', 'area_razao', 'fav_base',
    'prio_est', 'socio_min', 'socio_max', 'prio_min', 'prio_max',
    'topo_inst',
)


def ausente(v):
    if v is None or (hasattr(v, 'isNull') and v.isNull()):
        return True
    if isinstance(v, str):
        return v.strip().lower() in ('', 'null', 'none', 'nan')
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def numero(v, nome):
    """NULL/NaN são ausentes; valores negativos/malformados são erros."""
    if ausente(v):
        return None
    try:
        x = float(v)
    except (ValueError, TypeError):
        raise ValueError('%s não é numérico: %r.' % (nome, v)) from None
    if not math.isfinite(x) or x < 0:
        raise ValueError('%s deve ser finito e não negativo: %r.' % (nome, v))
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


def inserir_unico(indice, identificador, valor, origem):
    k = chave(identificador)
    if k in indice:
        raise ValueError('HYBAS_ID repetido em %s: %s. Dissolva as geometrias '
                         'e verifique os atributos antes de continuar.' % (origem, k))
    indice[k] = valor
    return k


def classe_pop(d):
    return 1 if d < 1 else (2 if d <= 10 else 3)


def classe_hg(h):
    return 1 if h < 0.01 else (2 if h < 2 else 3)


def registro(identificador, pop_val, modo_pop, area, hg, hg_status,
             pop_status='informado', cobertura=None, area_geom=None,
             favor=None, integrar=False):
    """Núcleo numérico, independente do QGIS; classes de saída começam em 1."""
    k = chave(identificador)
    a = numero(area, 'Área de %s' % k)
    if a is None or a <= 0:
        raise ValueError('Área de %s deve ser maior que zero.' % k)
    p = numero(pop_val, 'População/densidade de %s' % k)
    h = numero(hg, 'Rsc_RbM de %s' % k)
    f = numero(favor, 'Favorabilidade de %s' % k) if integrar else None
    if f is not None and f > 1:
        raise ValueError('Favorabilidade de %s fora de [0,1]. Não use K em '
                         'hectares nem normalize automaticamente.' % k)
    dens = p / a if p is not None and modo_pop != 'densidade' else p
    pop = dens * a if p is not None and modo_pop == 'densidade' else p
    cp = classe_pop(dens) if dens is not None else None
    ch = classe_hg(h) if h is not None else None
    imput = int(h is None)
    if cp is None:
        s = baixo = alto = None
        pop_status = pop_status if pop_status != 'informado' else 'ausente'
    elif ch is None:
        s = NIVEL_SEM_HG
        # Cenários extremos mais o próprio nível imputado; não são ICs.
        possiveis = [linha[cp - 1] for linha in MATRIZ] + [NIVEL_SEM_HG]
        baixo, alto = min(possiveis), max(possiveis)
    else:
        s = MATRIZ[ch - 1][cp - 1]
        baixo = alto = s
    prio = f * s / 5 if f is not None and s is not None else None
    ag = numero(area_geom, 'Área geométrica')
    return dict(zip(COLUNAS, (
        k, pop, a, dens, h, cp, ch, s, imput, hg_status, pop_status,
        cobertura, ag, a / ag if ag and ag > 0 else None, f, prio,
        baixo, alto,
        f * baixo / 5 if f is not None and baixo is not None else None,
        f * alto / 5 if f is not None and alto is not None else None,
        None,  # topo_inst: preenchido por topo_cenarios
    )))


def topo_cenarios(resultados, fracoes=FRACOES_TOPO):
    """Compara o topo nos cenários min/est/max e marca pertinência instável.

    Cada cenário ordena todas as sub-bacias com prioridade integrada pelo
    respectivo campo; empates no corte entram. topo_inst recebe o menor topo
    (em %) cuja inclusão da sub-bacia muda entre os cenários. Isso identifica
    tanto quem depende da imputação para entrar quanto quem pode ser deslocado.
    """
    for r in resultados:
        r['topo_inst'] = None
    conhecidos = [r for r in resultados if r['prio_est'] is not None]
    relatorio = {}
    if not conhecidos:
        return relatorio
    for frac in sorted(fracoes):
        n = max(1, math.ceil(frac * len(conhecidos)))
        grupos, por_cenario = {}, {}
        for campo in CENARIOS_TOPO:
            corte = sorted((r[campo] for r in conhecidos), reverse=True)[n - 1]
            grupos[campo] = {i for i, r in enumerate(conhecidos) if r[campo] >= corte}
            por_cenario[campo] = {
                'corte': corte, 'n_com_empates': len(grupos[campo]),
                'fracao_com_empates': len(grupos[campo]) / len(conhecidos),
                'n_hg_imputados': sum(conhecidos[i]['hg_imput'] for i in grupos[campo]),
            }
        uniao = set().union(*grupos.values())
        estaveis = set.intersection(*grupos.values())
        instaveis = uniao - estaveis
        for i in instaveis:
            if conhecidos[i]['topo_inst'] is None:
                conhecidos[i]['topo_inst'] = int(round(frac * 100))
        extremos = grupos['prio_min'] | grupos['prio_max']
        relatorio[str(frac)] = {
            'n_base': len(conhecidos), 'n_alvo': n, 'cenarios': por_cenario,
            'n_estaveis': len(estaveis), 'n_instaveis': len(instaveis),
            'n_instaveis_hg_imputado': sum(conhecidos[i]['hg_imput'] for i in instaveis),
            'n_instaveis_hg_observado': sum(1 - conhecidos[i]['hg_imput'] for i in instaveis),
            'jaccard_min_max': (len(grupos['prio_min'] & grupos['prio_max']) / len(extremos)
                                if extremos else None),
        }
    return relatorio


def suspeita_classe(valores):
    """True se todos os valores parecem classes ordinais 1–5, não dose bruta."""
    vals = [v for v in valores if v is not None]
    return bool(vals) and all(v == int(v) and 1 <= v <= 5 for v in vals)


def somar_grade(dados, dentro, validos, cobertura_min=1.0):
    """Soma contagens originais em centros de pixels; não reamostra o raster."""
    import numpy as np
    dentro = np.asarray(dentro, dtype=bool)
    validos = np.asarray(validos, dtype=bool) & np.isfinite(dados)
    n = int(dentro.sum())
    if n == 0:
        return None, None, 'sem_centro_de_pixel'
    ok = dentro & validos
    cob = float(ok.sum()) / n
    if np.any(np.asarray(dados)[ok] < 0):
        raise ValueError('Contagem populacional negativa em pixel válido.')
    if cob + 1e-12 < cobertura_min:
        return None, cob, 'NoData_parcial'
    return float(np.asarray(dados)[ok].sum(dtype=np.float64)), cob, 'raster_contagem'


try:
    from qgis.PyQt.QtCore import QMetaType
    from qgis.core import (
        Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea, QgsFeature,
        QgsFeatureSink, QgsFields, QgsField, QgsGeometry,
        QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingFeatureSourceDefinition,
        QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
        QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource,
        QgsProcessingParameterField, QgsProcessingParameterFileDestination,
        QgsProcessingParameterRasterLayer, QgsProcessingParameterString,
    )
    TEM_QGIS = True
except ModuleNotFoundError as e:
    if e.name != 'qgis':
        raise
    TEM_QGIS = False


if TEM_QGIS:
    class PopulacaoRaster:
        """GeoTIFF de contagem, grade original; janelas por sub-bacia."""
        def __init__(self, raster, crs_bacias, contexto):
            import numpy as np
            from osgeo import gdal, ogr, osr
            self.np, self.gdal, self.ogr, self.osr = np, gdal, ogr, osr
            self.ds = gdal.Open(raster.source(), gdal.GA_ReadOnly)
            if self.ds is None:
                raise QgsProcessingException('Não consegui abrir o raster pelo GDAL. '
                                             'Selecione o GeoTIFF WorldPop original.')
            if not raster.crs().isValid():
                raise QgsProcessingException('Raster de população sem CRS válido.')
            if not self.ds.GetProjection():
                raise QgsProcessingException('GeoTIFF sem CRS gravado. Defina o CRS '
                                             'correto no arquivo, sem reamostrar contagens.')
            if QgsCoordinateReferenceSystem(self.ds.GetProjection()) != raster.crs():
                raise QgsProcessingException('CRS do arquivo difere do CRS atribuído no QGIS. '
                                             'Corrija essa divergência antes de calcular população.')
            self.gt = self.ds.GetGeoTransform()
            if (self.gt[2] != 0 or self.gt[4] != 0 or
                    self.gt[1] <= 0 or self.gt[5] >= 0):
                raise QgsProcessingException('Raster deve ter grade sem rotação, '
                                             'com orientação norte. Use a fonte original.')
            self.band = self.ds.GetRasterBand(1)
            self.nodata = self.band.GetNoDataValue()
            self.scale = self.band.GetScale()
            self.offset = self.band.GetOffset()
            self.transform = QgsCoordinateTransform(crs_bacias, raster.crs(), contexto)
            self.srs = osr.SpatialReference()
            self.srs.ImportFromWkt(self.ds.GetProjection())

        def extrair(self, geometria):
            np, gdal, ogr = self.np, self.gdal, self.ogr
            geom = QgsGeometry(geometria)
            geom.transform(self.transform)
            bb, gt = geom.boundingBox(), self.gt
            nx, ny = self.ds.RasterXSize, self.ds.RasterYSize
            xmax, ymin = gt[0] + nx * gt[1], gt[3] + ny * gt[5]
            # Cobertura parcial de extensão não vira população zero.
            tol = 1e-7 * max(gt[1], -gt[5])
            if (bb.xMinimum() < gt[0] - tol or bb.xMaximum() > xmax + tol or
                    bb.yMinimum() < ymin - tol or bb.yMaximum() > gt[3] + tol):
                return None, None, 'fora_extensao_raster'
            x0 = max(0, math.floor((bb.xMinimum() - gt[0]) / gt[1]))
            x1 = min(nx, math.ceil((bb.xMaximum() - gt[0]) / gt[1]))
            y0 = max(0, math.floor((bb.yMaximum() - gt[3]) / gt[5]))
            y1 = min(ny, math.ceil((bb.yMinimum() - gt[3]) / gt[5]))
            largura, altura = x1 - x0, y1 - y0
            if largura <= 0 or altura <= 0:
                return None, None, 'sem_centro_de_pixel'
            if largura * altura > 25_000_000:
                raise QgsProcessingException('Janela populacional maior que 25 milhões '
                                             'de pixels. Use a soma zonal já calculada '
                                             'para essa camada, na mesma geometria.')
            mascara = gdal.GetDriverByName('MEM').Create('', largura, altura, 1,
                                                        gdal.GDT_Byte)
            mascara.SetGeoTransform((gt[0] + x0 * gt[1], gt[1], 0,
                                    gt[3] + y0 * gt[5], 0, gt[5]))
            mascara.SetProjection(self.ds.GetProjection())
            mascara.GetRasterBand(1).Fill(0)
            vetor = ogr.GetDriverByName('Memory').CreateDataSource('')
            camada = vetor.CreateLayer('bacia', self.srs, ogr.wkbUnknown)
            feat = ogr.Feature(camada.GetLayerDefn())
            feat.SetGeometry(ogr.CreateGeometryFromWkt(geom.asWkt()))
            if camada.CreateFeature(feat) != 0:
                raise QgsProcessingException('Falha ao preparar a geometria da bacia.')
            if gdal.RasterizeLayer(mascara, [1], camada, burn_values=[1]) != 0:
                raise QgsProcessingException('Falha na máscara da população.')
            dentro = mascara.GetRasterBand(1).ReadAsArray().astype(bool)
            brutos = self.band.ReadAsArray(x0, y0, largura, altura)
            validos = self.band.GetMaskBand().ReadAsArray(x0, y0, largura, altura) != 0
            if self.nodata is not None and math.isfinite(self.nodata):
                validos &= brutos != self.nodata
            dados = brutos.astype(np.float64)
            if self.scale is not None:
                dados *= self.scale
            if self.offset is not None:
                dados += self.offset
            return somar_grade(dados, dentro, validos)


    class RiscoSocioambientalPreventivo(QgsProcessingAlgorithm):
        def name(self):
            return 'risco_socioambiental_preventivo'

        def displayName(self):
            return 'Risco socioambiental — prioridade preventiva'

        def group(self):
            return 'Modelo de garimpo'

        def groupId(self):
            return 'modelo_garimpo'

        def createInstance(self):
            return type(self)()

        def shortHelpString(self):
            return (
                'Preserva a matriz preventiva da dissertação (1 a 5). Maior nota '
                '= menor exposição química modelada e maior densidade populacional. '
                'Não estima adoecimento. Selecione as sub-bacias HydroBASINS nível 12 '
                'e a camada/tabela com Rsc_RbM, ligadas por HYBAS_ID. Se o campo já '
                'estiver nas sub-bacias, selecione a mesma camada nas duas entradas. '
                'População: soma por sub-bacia, densidade em hab/km² ou GeoTIFF '
                'original WorldPop de habitantes por pixel. NoData químico recebe '
                '2 com flag; falta de população deixa o índice vazio. No modo raster, '
                'qualquer NoData nos pixels incluídos deixa a população vazia. '
                'A integração opcional requer favorabilidade 0–1 sem componente '
                'socioambiental. Ela gera F × S/5 como escore de decisão. '
                'Salve a nova camada em GeoPackage. As entradas não são editadas.'
            )

        def initAlgorithm(self, config=None):
            p = Qgis.ProcessingSourceType.VectorPolygon
            t = Qgis.ProcessingSourceType.Vector
            num = Qgis.ProcessingFieldParameterDataType.Numeric
            self.addParameter(QgsProcessingParameterFeatureSource(
                'BASINS', 'Sub-bacias HydroBASINS nível 12', [p]))
            self.addParameter(QgsProcessingParameterField(
                'BASIN_ID', 'Identificador das sub-bacias', defaultValue='HYBAS_ID',
                parentLayerParameterName='BASINS'))
            self.addParameter(QgsProcessingParameterFeatureSource(
                'HG', 'Camada ou tabela de exposição ao mercúrio (Rsc_RbM)', [t]))
            self.addParameter(QgsProcessingParameterField(
                'HG_ID', 'Identificador na camada de mercúrio', defaultValue='HYBAS_ID',
                parentLayerParameterName='HG'))
            self.addParameter(QgsProcessingParameterField(
                'HG_FIELD', 'Dose/indicador Rsc_RbM, sem reclassificar',
                defaultValue='Rsc_RbM', parentLayerParameterName='HG', type=num))
            self.addParameter(QgsProcessingParameterEnum(
                'POP_MODE', 'Entrada de população', options=[
                    'Campo com soma de habitantes por sub-bacia',
                    'Campo com densidade já calculada (hab/km²)',
                    'Raster WorldPop original de contagem (habitantes/pixel)',
                ], defaultValue=0))
            self.addParameter(QgsProcessingParameterField(
                'POP_FIELD', 'Campo de população ou densidade nas sub-bacias',
                parentLayerParameterName='BASINS', type=num, optional=True))
            self.addParameter(QgsProcessingParameterRasterLayer(
                'POP_RASTER', 'GeoTIFF WorldPop de contagem — só para modo raster',
                optional=True))
            self.addParameter(QgsProcessingParameterField(
                'AREA_FIELD', 'Área em km² — vazio calcula a área da geometria',
                parentLayerParameterName='BASINS', type=num, optional=True))
            self.addParameter(QgsProcessingParameterString(
                'POP_YEAR', 'Ano de referência da população', defaultValue='2025'))
            self.addParameter(QgsProcessingParameterString(
                'HG_REFERENCE', 'Referência/versão da camada de mercúrio',
                defaultValue='Domingues et al. (2024) — camada usada no projeto'))
            self.addParameter(QgsProcessingParameterBoolean(
                'INTEGRATE', 'Gerar também prioridade estática F × S/5',
                defaultValue=False))
            self.addParameter(QgsProcessingParameterField(
                'FAVOR', 'Favorabilidade/potencial remanescente em [0,1], sem S',
                parentLayerParameterName='BASINS', type=num, optional=True))
            pasta = Path.home() / 'Etapa1_risco_socioambiental'
            marca = dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            self.addParameter(QgsProcessingParameterFeatureSink(
                'OUTPUT', 'Nova camada de prioridade preventiva', type=p,
                defaultValue=str(pasta / ('socio_pre_%s.gpkg' % marca))))
            self.addParameter(QgsProcessingParameterFileDestination(
                'TABLE', 'Tabela de resultados', 'CSV (*.csv)',
                defaultValue=str(pasta / ('socio_pre_%s.csv' % marca))))
            self.addParameter(QgsProcessingParameterFileDestination(
                'REPORT', 'Relatório de cobertura e método', 'JSON (*.json)',
                defaultValue=str(pasta / ('socio_pre_%s.json' % marca))))

        def processAlgorithm(self, parameters, context, feedback):
            try:
                return self._processar(parameters, context, feedback)
            except ValueError as e:
                raise QgsProcessingException(str(e)) from e

        def _processar(self, par, context, feedback):
            bacias = self.parameterAsSource(par, 'BASINS', context)
            fonte_hg = self.parameterAsSource(par, 'HG', context)
            if bacias is None or fonte_hg is None:
                raise QgsProcessingException('Selecione as sub-bacias e a camada de mercúrio.')
            if not bacias.sourceCrs().isValid():
                raise QgsProcessingException('Sub-bacias sem CRS válido.')
            def campo(param, fonte, obrigatorio=True):
                nome = self.parameterAsString(par, param, context)
                if not nome and not obrigatorio:
                    return None
                if fonte.fields().indexFromName(nome) < 0:
                    raise QgsProcessingException('Campo %s não encontrado. Campos: %s.' %
                                                 (nome or param, ', '.join(fonte.fields().names())))
                return nome
            cid = campo('BASIN_ID', bacias)
            hid = campo('HG_ID', fonte_hg)
            hf = campo('HG_FIELD', fonte_hg)
            modo = self.parameterAsEnum(par, 'POP_MODE', context)
            pf = campo('POP_FIELD', bacias, obrigatorio=(modo != 2)) if modo != 2 else None
            af = campo('AREA_FIELD', bacias, obrigatorio=False)
            integrar = self.parameterAsBool(par, 'INTEGRATE', context)
            ff = campo('FAVOR', bacias) if integrar else None
            raster = None
            pop_raster = None
            if modo == 2:
                raster = self.parameterAsRasterLayer(par, 'POP_RASTER', context)
                if raster is None or not raster.isValid():
                    raise QgsProcessingException('Selecione o raster original WorldPop de contagem.')
                pop_raster = PopulacaoRaster(raster, bacias.sourceCrs(), context.transformContext())
                if af:
                    feedback.pushWarning('Raster usa a geometria das sub-bacias. Confira se '
                                         'a área informada corresponde à mesma geometria.')
            ind_hg = {}
            for ft in fonte_hg.getFeatures():
                if feedback.isCanceled():
                    raise QgsProcessingException('Processamento cancelado; resultado incompleto.')
                k = chave(ft[hid])
                inserir_unico(ind_hg, k, numero(ft[hf], 'Rsc_RbM de %s' % k), 'mercúrio')
            if not ind_hg:
                raise QgsProcessingException('Fonte de mercúrio vazia; verifique filtro/seleção.')
            valores_hg = sorted(v for v in ind_hg.values() if v is not None)
            hg_suspeito = suspeita_classe(valores_hg)
            if hg_suspeito:
                feedback.pushWarning('%s contém apenas inteiros entre 1 e 5 (%s). Isso parece '
                                     'uma classe já reclassificada, não o indicador bruto. '
                                     'Confira a camada antes de usar o resultado.' %
                                     (hf, ', '.join(str(int(v)) for v in sorted(set(valores_hg)))))
            da = QgsDistanceArea()
            da.setSourceCrs(bacias.sourceCrs(), context.transformContext())
            da.setEllipsoid('WGS84')
            vistos, resultados, geometrias = {}, [], []
            total = max(bacias.featureCount(), 1)
            for i, ft in enumerate(bacias.getFeatures()):
                if feedback.isCanceled():
                    raise QgsProcessingException('Processamento cancelado; resultado incompleto.')
                k = inserir_unico(vistos, ft[cid], True, 'sub-bacias')
                geom = ft.geometry()
                if geom.isNull() or geom.isEmpty() or not geom.isGeosValid():
                    raise QgsProcessingException('Geometria vazia/inválida na sub-bacia %s. '
                                                 'Corrija a fonte antes de processar.' % k)
                ag = abs(da.measureArea(geom)) / 1_000_000
                a = ft[af] if af else ag
                if k not in ind_hg:
                    h, hs = None, 'sem_correspondencia'
                else:
                    h = ind_hg[k]
                    hs = 'disponivel_na_fonte' if h is not None else 'nulo_na_fonte'
                if pop_raster:
                    pv, cob, ps = pop_raster.extrair(geom)
                else:
                    pv, cob, ps = ft[pf], None, 'informado'
                r = registro(k, pv, 'densidade' if modo == 1 else 'contagem',
                             a, h, hs, ps, cob, ag, ft[ff] if ff else None, integrar)
                resultados.append(r)
                geometrias.append(QgsGeometry(geom))
                feedback.setProgress(85 * (i + 1) / total)
            if not resultados:
                raise QgsProcessingException('Nenhuma sub-bacia na entrada.')
            correspondencias = sum(r['hg_status'] != 'sem_correspondencia' for r in resultados)
            if correspondencias == 0:
                raise QgsProcessingException('Nenhum HYBAS_ID corresponde à camada de mercúrio. '
                                             'Verifique chaves, nível HydroBASINS e filtros. '
                                             'A imputação não será usada para esconder erro de ligação.')
            topo = topo_cenarios(resultados) if integrar else None
            campos = QgsFields()
            textos = {'HYBAS_ID', 'hg_status', 'pop_status'}
            inteiros = {'cl_pop', 'cl_hg', 'socio_pre', 'hg_imput', 'socio_min', 'socio_max',
                        'topo_inst'}
            for c in COLUNAS:
                tipo = (QMetaType.Type.QString if c in textos else
                        QMetaType.Type.Int if c in inteiros else QMetaType.Type.Double)
                campos.append(QgsField(c, tipo))
            destino = self.parameterAsOutputLayer(par, 'OUTPUT', context)
            tab = self.parameterAsFileOutput(par, 'TABLE', context)
            rel = self.parameterAsFileOutput(par, 'REPORT', context)
            for arquivo in (destino, tab, rel):
                if arquivo and arquivo != 'TEMPORARY_OUTPUT' and '|' not in arquivo:
                    Path(arquivo).parent.mkdir(parents=True, exist_ok=True)
            sink, destino_id = self.parameterAsSink(
                par, 'OUTPUT', context, campos, bacias.wkbType(), bacias.sourceCrs())
            if sink is None:
                raise QgsProcessingException('Não foi possível criar a camada de saída.')
            for r, geom in zip(resultados, geometrias):
                if feedback.isCanceled():
                    raise QgsProcessingException('Processamento cancelado; saída parcial, descarte-a.')
                novo = QgsFeature(campos)
                novo.setGeometry(geom)
                novo.setAttributes([r[c] for c in COLUNAS])
                if not sink.addFeature(novo, QgsFeatureSink.Flag.FastInsert):
                    raise QgsProcessingException('Falha ao gravar a sub-bacia %s.' % r['HYBAS_ID'])
            sink.finalize()
            with open(tab, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=COLUNAS, delimiter=';')
                writer.writeheader()
                writer.writerows(resultados)
            resumo = {
                'versao': VERSAO, 'data_execucao': dt.datetime.now().astimezone().isoformat(),
                'qgis': Qgis.QGIS_VERSION, 'finalidade': 'preservacao_preventiva',
                'matriz': MATRIZ, 'imputacao_sem_hg': NIVEL_SEM_HG,
                'populacao_ano': self.parameterAsString(par, 'POP_YEAR', context),
                'mercurio_referencia': self.parameterAsString(par, 'HG_REFERENCE', context),
                'entrada_bacias': bacias.sourceName(), 'entrada_hg': fonte_hg.sourceName(),
                'campos': {'id_bacias': cid, 'id_hg': hid, 'hg': hf,
                           'pop': pf, 'area_km2': af, 'favorabilidade': ff},
                'crs_bacias': bacias.sourceCrs().authid(),
                'area_metodo': 'campo_km2' if af else 'geometria_elipsoide_WGS84',
                'populacao_modo': ('contagem_campo', 'densidade_campo', 'contagem_raster')[modo],
                'raster_metodo': 'soma_grade_original_centros_pixels_banda1' if modo == 2 else None,
                'raster_crs': raster.crs().authid() if raster else None,
                'cobertura_raster_minima': 1.0 if modo == 2 else None,
                'rsc_rbm_fonte': {
                    'n_valores': len(valores_hg),
                    'min': valores_hg[0] if valores_hg else None,
                    'mediana': valores_hg[len(valores_hg) // 2] if valores_hg else None,
                    'max': valores_hg[-1] if valores_hg else None,
                    'suspeita_classe_reclassificada': hg_suspeito,
                },
                'n_bacias': len(resultados), 'n_fonte_hg': len(ind_hg),
                'n_hg_ligados': correspondencias,
                'n_hg_imputados': sum(r['hg_imput'] for r in resultados),
                'n_sem_populacao': sum(r['dens_pop'] is None for r in resultados),
                'n_classificados': sum(r['socio_pre'] is not None for r in resultados),
                'n_prioridade_integrada': sum(r['prio_est'] is not None for r in resultados),
                'contagens_hg_status': dict(Counter(r['hg_status'] for r in resultados)),
                'contagens_pop_status': dict(Counter(r['pop_status'] for r in resultados)),
                'contagens_socio_pre': dict(Counter(str(r['socio_pre']) for r in resultados)),
                'n_area_diferenca_maior_5pct': sum(
                    r['area_razao'] is not None and abs(r['area_razao'] - 1) > .05
                    for r in resultados),
                'integracao': 'F*S/5; convencao normativa, nao probabilidade' if integrar else None,
                'sensibilidade_hg': 'min/max das tres classes possiveis e nivel imputado 2; nao IC',
                'avisos': [
                    'Rsc_RbM modelado não é medida clínica local nem probabilidade.',
                    'Densidade total não identifica a população ribeirinha masculina.',
                    'WorldPop 2025 não deve ser retroativamente tratado como disponível em 2021.',
                    'Filtros/seleções das entradas de Processamento definem as feições lidas.',
                    'Contagem raster usa centros de pixels; bordas são aproximadas.',
                    'No modo de campo agregado, a cobertura populacional não é verificável pelo script.',
                    'Áreas informadas e soma populacional devem referir-se à mesma geometria.',
                    'Ausência química imputada não significa baixa contaminação observada.',
                ],
            }
            if hg_suspeito:
                resumo['avisos'].append('Rsc_RbM só tem inteiros 1–5: possível classe '
                                        'reclassificada em vez do indicador bruto.')
            if integrar:
                resumo['sensibilidade_topo'] = topo
                resumo['topo_inst'] = ('menor topo (%) cuja inclusão da sub-bacia muda entre '
                                       'prio_min, prio_est e prio_max; NULL = estável')
                instaveis = {f: t['n_instaveis'] for f, t in topo.items() if t['n_instaveis']}
                if instaveis:
                    feedback.pushWarning('A composição do topo depende da imputação química '
                                         '(sub-bacias instáveis por fração: %s). Use topo_inst '
                                         'e o relatório antes de afirmar prioridades.' %
                                         ', '.join('%s: %d' % kv for kv in instaveis.items()))
            with open(rel, 'w', encoding='utf-8') as f:
                json.dump(resumo, f, ensure_ascii=False, indent=2, allow_nan=False)
            feedback.pushInfo('%d sub-bacias; %d classificadas; %d com química imputada; '
                              '%d sem população.' % (len(resultados), resumo['n_classificados'],
                                                     resumo['n_hg_imputados'], resumo['n_sem_populacao']))
            if resumo['n_hg_imputados']:
                feedback.pushWarning('Nível 2 inclui ausência de dados químicos. '
                                     'Use hg_imput e os cenários min/max na leitura do mapa.')
            if resumo['n_area_diferenca_maior_5pct']:
                feedback.pushWarning('Há áreas informadas com diferença >5% da geometria. '
                                     'Verifique recortes e a unidade do denominador.')
            feedback.setProgress(100)
            return {'OUTPUT': destino_id, 'TABLE': tab, 'REPORT': rel}

# Entrada de console; incorporada ao arquivo independente de entrega.
def log_console(mensagem):
    print(dt.datetime.now().strftime('%H:%M:%S'), mensagem, flush=True)


def configuracao_console():
    return {
        'bacias': CAMADA_SUBBACIAS, 'bacias_id': ID_CAMADA_SUBBACIAS,
        'chave_bacias': CAMPO_ID_SUBBACIAS,
        'hg': CAMADA_MERCURIO, 'hg_id': ID_CAMADA_MERCURIO,
        'chave_hg': CAMPO_ID_MERCURIO, 'campo_hg': CAMPO_MERCURIO,
        'modo_pop': MODO_POPULACAO, 'campo_pop': CAMPO_POPULACAO,
        'raster_pop': CAMADA_POPULACAO, 'raster_pop_id': ID_CAMADA_POPULACAO,
        'area': CAMPO_AREA_KM2, 'integrar': GERAR_PRIORIDADE_INTEGRADA,
        'favor': CAMPO_FAVORABILIDADE, 'respeitar_selecao': RESPEITAR_SELECAO,
    }


def campo_console(camada, nome, problemas, papel):
    if camada is None:
        return None
    nomes = camada.fields().names()
    if nome in nomes:
        return nome
    encontrados = [n for n in nomes if nome and n.casefold() == nome.casefold()]
    if len(encontrados) == 1:
        return encontrados[0]
    problemas.append('%s: campo %r não encontrado de modo inequívoco em %s. '
                     'Campos: %s.' % (papel, nome, camada.name(), ', '.join(nomes)))
    return None


def camada_console(projeto, nome, identificador, problemas, papel, vetor=True):
    if identificador:
        camada = projeto.mapLayer(identificador)
        encontradas = [camada] if camada else []
    else:
        encontradas = projeto.mapLayersByName(nome) if nome else []
    if len(encontradas) != 1:
        problemas.append('%s: %d camadas correspondem ao nome/ID configurado. '
                         'Selecione a camada na janela ou configure seu nome/ID.' %
                         (papel, len(encontradas)))
        return None
    camada = encontradas[0]
    eh_vetor = hasattr(camada, 'fields') and hasattr(camada, 'getFeatures')
    eh_raster = hasattr(camada, 'bandCount') and hasattr(camada, 'rasterUnitsPerPixelX')
    if not camada.isValid() or (vetor and not eh_vetor) or (not vetor and not eh_raster):
        problemas.append('%s: camada inválida ou de tipo incompatível.' % papel)
        return None
    return camada


def preparar_console(projeto, cfg):
    """Resolve apenas nomes/campos inequívocos; devolve parâmetros e pendências."""
    problemas, par = [], {}
    bacias = camada_console(projeto, cfg['bacias'], cfg['bacias_id'], problemas, 'Sub-bacias')
    if bacias:
        tipo_geometria = bacias.geometryType()
        if getattr(tipo_geometria, 'value', tipo_geometria) != 2:
            problemas.append('Sub-bacias: a camada precisa ser poligonal.')
        par['BASINS'] = bacias
        cid = campo_console(bacias, cfg['chave_bacias'], problemas, 'Chave das sub-bacias')
        if cid:
            par['BASIN_ID'] = cid
    if cfg['hg'] or cfg['hg_id']:
        hg = camada_console(projeto, cfg['hg'], cfg['hg_id'], problemas, 'Mercúrio')
    else:
        def candidata(c):
            return (c.isValid() and hasattr(c, 'getFeatures') and hasattr(c, 'fields')
                    and cfg['campo_hg'] in c.fields().names()
                    and cfg['chave_hg'] in c.fields().names())
        if bacias and candidata(bacias):
            hg = bacias
        else:
            candidatas = [c for c in projeto.mapLayers().values() if candidata(c)]
            hg = candidatas[0] if len(candidatas) == 1 else None
            if hg is None:
                nomes = ', '.join('%s [ID=%s]' % (c.name(), c.id()) for c in candidatas)
                problemas.append('Mercúrio: %d candidatas com %s e %s%s. '
                                 'Selecione na janela ou configure CAMADA_MERCURIO/ID.' %
                                 (len(candidatas), cfg['campo_hg'], cfg['chave_hg'],
                                  ': ' + nomes if nomes else ''))
    if hg:
        par['HG'] = hg
        for param, configurado, papel in (
            ('HG_ID', cfg['chave_hg'], 'Chave do mercúrio'),
            ('HG_FIELD', cfg['campo_hg'], 'Exposição química'),
        ):
            c = campo_console(hg, configurado, problemas, papel)
            if c:
                par[param] = c
    modos = {'contagem': 0, 'densidade': 1, 'raster': 2}
    modo = cfg['modo_pop']
    if modo not in modos:
        problemas.append("MODO_POPULACAO precisa ser 'contagem', 'densidade' ou 'raster'.")
    else:
        par['POP_MODE'] = modos[modo]
    if modo == 'raster':
        raster = camada_console(projeto, cfg['raster_pop'], cfg['raster_pop_id'],
                                problemas, 'WorldPop de contagem', vetor=False)
        if raster:
            par['POP_RASTER'] = raster
    elif modo in ('contagem', 'densidade') and bacias:
        if cfg['campo_pop']:
            pop = campo_console(bacias, cfg['campo_pop'], problemas, 'População')
        else:
            aceitos = ({'pop_sum', 'wp_sum', 'worldpop_sum'} if modo == 'contagem' else
                       {'dens_pop', 'pop_dens', 'densidade_pop', 'densidade_populacional'})
            opcoes = [c for c in bacias.fields().names() if c.casefold() in aceitos]
            pop = opcoes[0] if len(opcoes) == 1 else None
            if pop is None:
                problemas.append('População: selecione o campo de %s ou configure '
                                 'CAMPO_POPULACAO. Candidatos: %s. Um campo genérico '
                                 'SUM ou SAMPLE_1 não é identificado automaticamente.' %
                                 (modo, ', '.join(opcoes) if opcoes else 'nenhum'))
        if pop:
            par['POP_FIELD'] = pop
    if cfg['area'] and bacias:
        area = campo_console(bacias, cfg['area'], problemas, 'Área em km²')
        if area:
            par['AREA_FIELD'] = area
    par['INTEGRATE'] = bool(cfg['integrar'])
    if cfg['integrar']:
        if not cfg['favor']:
            problemas.append('Integração: configure CAMPO_FAVORABILIDADE em [0,1], '
                             'sem o componente socioambiental já incluído.')
        elif bacias:
            favor = campo_console(bacias, cfg['favor'], problemas, 'Favorabilidade')
            if favor:
                par['FAVOR'] = favor
    avisos = aplicar_selecao(par, cfg['respeitar_selecao'])
    return par, problemas, avisos


def aplicar_selecao(par, respeitar):
    """Seleção ativa: limita a leitura (como na janela) ou é ignorada com aviso."""
    avisos = []
    for param, papel in (('BASINS', 'Sub-bacias'), ('HG', 'Mercúrio')):
        camada = par.get(param)
        if camada is None or not hasattr(camada, 'selectedFeatureCount'):
            continue
        n_sel = camada.selectedFeatureCount()
        if not n_sel:
            continue
        if respeitar:
            par[param] = QgsProcessingFeatureSourceDefinition(camada.id(), True)
            avisos.append('%s: %d de %d feições selecionadas em %s; SÓ elas serão lidas '
                          '(RESPEITAR_SELECAO = True).' %
                          (papel, n_sel, camada.featureCount(), camada.name()))
        else:
            avisos.append('%s: há %d feições selecionadas em %s, mas a seleção será '
                          'IGNORADA (RESPEITAR_SELECAO = False).' %
                          (papel, n_sel, camada.name()))
    return avisos


def inventario_console(projeto, pasta, problemas, avisos=()):
    """Registra metadados, não atributos individuais de moradores ou feições."""
    camadas = []
    for camada in projeto.mapLayers().values():
        vetor = hasattr(camada, 'fields') and hasattr(camada, 'getFeatures')
        r = {'nome': camada.name(), 'id': camada.id(), 'valida': camada.isValid(),
             'crs': camada.crs().authid(), 'tipo': 'vetor' if vetor else 'outro'}
        if vetor:
            r.update({'feicoes_visiveis': camada.featureCount(),
                      'filtro_ativo': bool(camada.subsetString()),
                      'selecionadas': camada.selectedFeatureCount(),
                      'campos': camada.fields().names()})
        camadas.append(r)
    arquivo = pasta / 'diagnostico_inputs.json'
    arquivo.write_text(json.dumps({'versao': VERSAO, 'pendencias': problemas,
                                   'avisos_selecao': list(avisos),
                                   'camadas': camadas}, ensure_ascii=False, indent=2),
                       encoding='utf-8')
    for a in avisos:
        log_console('ATENÇÃO: ' + a)
    for p in problemas:
        log_console('CONFIGURAR: ' + p)
    if problemas:
        for r in camadas:
            if r.get('campos'):
                log_console('%s [ID=%s] | campos: %s' %
                            (r['nome'], r['id'], ', '.join(r['campos'])))
    return arquivo


if TEM_QGIS:
    from qgis.core import (
        QgsProject, QgsProcessingContext, QgsProcessingFeedback,
        QgsVectorLayer, QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsFillSymbol,
        QgsSimpleFillSymbolLayer,
    )

    def criar_algoritmo_console(_classe=RiscoSocioambientalPreventivo):
        return _classe().create()

    # O editor do QGIS também inspeciona subclasses para abrir algoritmos.
    # Mantê-la na fábrica evita uma segunda execução/janela após main().
    del RiscoSocioambientalPreventivo

    class FeedbackConsole(QgsProcessingFeedback):
        def __init__(self):
            super().__init__()
            self.ultimo_progresso = -1
            self.ultimo_erro = None

        def pushInfo(self, texto):
            log_console(texto)
            super().pushInfo(texto)

        def pushWarning(self, texto):
            log_console('ATENÇÃO: ' + texto)
            super().pushWarning(texto)

        def reportError(self, texto, fatalError=False):
            self.ultimo_erro = texto
            log_console('ERRO: ' + texto)
            super().reportError(texto, fatalError)

        def setProgress(self, valor):
            super().setProgress(valor)
            etapa = int(valor) // 10
            if etapa > self.ultimo_progresso:
                self.ultimo_progresso = etapa
                log_console('Processamento: %d%%' % int(valor))


def carregar_resultado_console(projeto, resultado, pasta):
    saida = resultado.get('OUTPUT')
    if not saida:
        raise RuntimeError('O algoritmo não devolveu a camada de saída.')
    if isinstance(saida, QgsVectorLayer):
        camada = saida
    else:
        # A janela de Processamento pode ter carregado a camada previamente.
        camada = projeto.mapLayer(str(saida))
        if camada is None:
            camada = next((c for c in projeto.mapLayers().values()
                           if hasattr(c, 'source') and c.source() == str(saida)), None)
        if camada is None:
            camada = QgsVectorLayer(str(saida), 'Risco socioambiental — prioridade preventiva', 'ogr')
    if not camada.isValid():
        raise RuntimeError('Saída gravada, mas não foi possível carregar a camada: %s.' % saida)
    cores = ['#ffffb2', '#fed976', '#feb24c', '#f03b20', '#bd0026']

    def simbolo(cor):
        return QgsFillSymbol.createSimple({'color': cor, 'outline_color': '#666666',
                                           'outline_width': '0.15'})
    categorias = []
    for nivel, cor in enumerate(cores, 1):
        rotulo = 'Prioridade preventiva %d' % nivel
        if nivel == NIVEL_SEM_HG:
            rotulo += ' (Rsc_RbM observado)'
        categorias.append(QgsRendererCategory(str(nivel), simbolo(cor), rotulo))
    # Nível 2 imputado: mesma cor do 2, com hachura, para não parecer dado observado.
    imputado = simbolo(cores[NIVEL_SEM_HG - 1])
    imputado.appendSymbolLayer(QgsSimpleFillSymbolLayer.create({
        'color': '#404040', 'style': 'b_diagonal', 'outline_style': 'no'}))
    categorias.append(QgsRendererCategory('%di' % NIVEL_SEM_HG, imputado,
                                          'Prioridade preventiva %d — química imputada '
                                          '(sem Rsc_RbM)' % NIVEL_SEM_HG))
    simbolo_nulo = QgsFillSymbol.createSimple({'color': '#d9d9d9', 'outline_color': '#666666'})
    categorias.append(QgsRendererCategory(None, simbolo_nulo, 'Sem classificação populacional'))
    classe = ('if("hg_imput" = 1 AND "socio_pre" IS NOT NULL, '
              'to_string("socio_pre") || \'i\', to_string("socio_pre"))')
    camada.setRenderer(QgsCategorizedSymbolRenderer(classe, categorias))
    if camada.id() not in projeto.mapLayers():
        projeto.addMapLayer(camada)
    camada.triggerRepaint()
    mensagem, salvo = camada.saveNamedStyle(str(pasta / 'socio_pre.qml'))
    if not salvo:
        log_console('Camada calculada. Não consegui salvar o estilo QML: %s.' % mensagem)
    return camada


def main():
    if not TEM_QGIS:
        raise RuntimeError('Execute este arquivo no editor do console Python do QGIS 4.2.')
    projeto = QgsProject.instance()
    pasta = Path(PASTA_SAIDA) / dt.datetime.now().strftime('socio_pre_%Y%m%d_%H%M%S_%f')
    pasta.mkdir(parents=True, exist_ok=False)
    log_console('Versão %s | preservação preventiva | inputs existentes.' % VERSAO)
    parametros, problemas, avisos = preparar_console(projeto, configuracao_console())
    parametros.update({'POP_YEAR': ANO_POPULACAO, 'HG_REFERENCE': REFERENCIA_MERCURIO,
                       'OUTPUT': str(pasta / 'socio_pre.gpkg'),
                       'TABLE': str(pasta / 'socio_pre.csv'),
                       'REPORT': str(pasta / 'relatorio_socio_pre.json')})
    diagnostico = inventario_console(projeto, pasta, problemas, avisos)
    algoritmo = criar_algoritmo_console()
    if problemas:
        log_console('Inputs pendentes. Diagnóstico: %s.' % diagnostico)
        if not ABRIR_JANELA_SE_FALTAR_CONFIGURACAO:
            log_console('CÁLCULO NÃO EXECUTADO. Ajuste o bloco de configurações e execute novamente.')
            return {'status': 'configurar', 'diagnostico': str(diagnostico)}
        from qgis.processing import execAlgorithmDialog
        log_console('Selecione os inputs na janela de Processamento e clique em Executar.')
        resultado = execAlgorithmDialog(algoritmo, parametros)
        if not resultado or not resultado.get('OUTPUT'):
            log_console('CÁLCULO NÃO CONCLUÍDO. Janela fechada sem resultado; confira o diagnóstico.')
            return {'status': 'sem_resultado', 'diagnostico': str(diagnostico)}
    else:
        contexto = QgsProcessingContext()
        contexto.setProject(projeto)
        contexto.setTransformContext(projeto.transformContext())
        feedback = FeedbackConsole()
        valido, mensagem = algoritmo.checkParameterValues(parametros, contexto)
        if not valido:
            raise RuntimeError('Parâmetros inválidos: %s.' % mensagem)
        resultado, ok = algoritmo.run(parametros, contexto, feedback)
        if not ok:
            raise RuntimeError(feedback.ultimo_erro or 'Processamento não concluído; verifique o console.')
    carregar_resultado_console(projeto, resultado, pasta)
    relatorio = resultado.get('REPORT')
    if relatorio and Path(relatorio).is_file():
        resumo = json.loads(Path(relatorio).read_text(encoding='utf-8'))
        log_console('RESULTADO: %d sub-bacias; %d classificadas; %d com química imputada; '
                    '%d sem população.' % (resumo['n_bacias'], resumo['n_classificados'],
                                           resumo['n_hg_imputados'], resumo['n_sem_populacao']))
    log_console('CONCLUÍDO.')
    for chave_saida, rotulo in (('OUTPUT', 'Camada'), ('TABLE', 'CSV'), ('REPORT', 'Relatório')):
        log_console('%s: %s.' % (rotulo, resultado.get(chave_saida)))
    log_console('Diagnóstico e estilo: %s.' % pasta)
    return resultado


if TEM_QGIS and EXECUTAR_AO_ABRIR:
    main()

# Risco Garimpo — plugin QGIS

Plugin desenvolvido no âmbito da dissertação sobre a expansão do garimpo ilegal na Província Mineral do Tapajós. Disponibiliza três ferramentas no menu **Raster → Risco Garimpo**:

| Ferramenta | Função | Produto principal |
|---|---|---|
| **Modelo estático — gerar K** | Estimar a capacidade de ocupação por garimpo a partir das condições do terreno e de uma calibração com dados observados. | Raster `K.tif`, com a fração máxima estimada de ocupação de cada pixel. |
| **Modelo dinâmico de risco** | Simular a expansão no período escolhido, considerando ocupação inicial, contágio, alertas e fiscalização. | Raster de expansão `Δμ`, correspondente ao acréscimo de ocupação simulado. |
| **Validar uma execução** | Comparar a expansão prevista por uma execução do modelo dinâmico com o garimpo novo observado, e com alternativas sem dinâmica. | Tabela de métricas, curva de captura e comparação por sub-bacia. |

O modelo estático grava a capacidade estimada em arquivo. O modelo dinâmico recebe esse raster como insumo e o mantém fixo durante a simulação. Também pode receber um raster de capacidade produzido por outro procedimento, desde que compatível com a ocupação inicial. A validação lê o registro e as saídas de uma execução do modelo dinâmico.

As equações abaixo descrevem a formulação documentada pelo projeto [P1]. As legendas informam o significado, a unidade e a origem dos símbolos. As referências dos métodos estão reunidas ao final.

## Instalação e teste inicial

Requisito indicado pelo projeto: **QGIS 3.22 ou posterior, inclusive o QGIS 4** (Qt6); os testes rodam no QGIS 3.34 e no 4.2. NumPy e GDAL são utilizados no processamento. SciPy acelera a convolução e a transformada de distância; o projeto prevê alternativas em NumPy para ambientes sem SciPy.

1. Na pasta do projeto, execute `python tools/empacotar.py`.
2. No QGIS, abra **Complementos → Gerenciar e instalar complementos → Instalar a partir do ZIP** e selecione `dist/risco_garimpo-<versão>.zip`.
3. Acesse as ferramentas em **Raster → Risco Garimpo** ou na barra de ferramentas.

Para pastas sincronizadas pelo OneDrive, use a instalação pelo ZIP. Isso evita interferências da sincronização sobre os arquivos temporários de Python.

### Dados sintéticos

O script `tools/gerar_dados_teste.py` produz uma área de 20 × 20 km, com resolução de 100 m, em EPSG:31981. Inclui capacidade, ocupação inicial, máscara e um GeoPackage com 150 fragmentos de alerta distribuídos em seis frentes e 12 autos de infração [P1].

No **Console Python do QGIS**, ajuste os caminhos e execute:

```python
exec(open(r"C:\Plugin\tools\gerar_dados_teste.py", encoding="utf-8").read())
gerar_e_carregar(r"C:\Plugin\teste")
```

Selecione as camadas e os campos de data, defina o período de **01/01/2025 a 31/12/2026** e execute o modelo dinâmico. A mensagem **“Agrupados em 6 frentes”** é o resultado esperado para esse conjunto de teste.

Para testar o plugin instalado sem abrir os diálogos, troque a segunda linha por `testar_ponta_a_ponta(r"C:\Plugin\teste")`. A função gera os dados, cria quatro sub-bacias de teste, roda os dois modelos com o código do plugin instalado, grava as saídas em `teste\saidas` e confere os registros de execução. A última linha deve ser **“TUDO CERTO: os dois registros têm o esperado.”**

Se houver falha, consulte o registro de execução. Nos dados reais, confira inicialmente projeção, extensão, campos de data e alinhamento das camadas.

## Insumos e configuração

### Aba Insumos do modelo dinâmico

| Campo | Conteúdo | Origem |
|---|---|---|
| `K` | Raster de capacidade de ocupação. O modelo estático produz valores entre 0 e 1. | Saída do modelo estático ou raster externo fornecido pelo usuário [P1]. |
| Mapa de garimpo (0/1) — data inicial | Mapa anual do garimpo na data inicial, sem suavizar: 1 = garimpo, 0 = não. Na grade de trabalho vira `μ₀`, a fração ocupada de cada pixel. | Mapeamento histórico selecionado pelo usuário; o capítulo de metodologia utiliza a série MapBiomas [P2]. |
| Máscara | Raster opcional cujos valores maiores que zero delimitam a área simulada. | Recorte territorial definido pelo usuário [P1]. |
| Grade de trabalho | Sistema de referência, extensão e resolução da simulação. Deve utilizar coordenadas projetadas em metros. | Configuração da execução [P1]. |
| Alertas | Camada de pontos ou polígonos e respectivo campo de data. | Camada selecionada; na pesquisa, alertas Brasil Mais, com dados SCCON/Planet [P2]. |
| Fiscalização | Camada de autos de infração e respectivo campo de data. | Camada selecionada; na pesquisa, registros do IBAMA e do ICMBio [P2]. |
| Sub-bacias | Polígonos utilizados para resumir as saídas. | Camada selecionada; na pesquisa, HydroBASINS nível 12 [P2]. |

O plugin leva as camadas à mesma projeção, origem e resolução antes dos cálculos. Essa preparação permite integrar, por exemplo, dados em SIRGAS 2000 / UTM 21S, EPSG:31981, e dados em coordenadas geográficas, EPSG:4674.

Confira o tamanho da **Grade resultante** e a memória estimada por camada antes de executar uma área extensa.

### Aba Parâmetros

**Parametrizar** consiste em atribuir valores aos coeficientes e às escalas que controlam o modelo. **Calibrar** consiste em ajustar esses valores com base na comparação entre resultados simulados e observados.

| Parâmetro | Efeito no cálculo | Valor ou procedimento documentado | Fonte |
|---|---|---|---|
| Escala de contágio | Controla a distribuição espacial da influência da ocupação existente. | Aproximadamente 380 m. | Resultado da pesquisa citado em [P1] e [P2]. |
| Alcance da fiscalização | Controla a redução da influência repressiva com a distância ao evento. | 750 m. | Referência na faixa de 0,5–1 km, sem ajuste formal [P3]. |
| Platô da fiscalização | Define a referência temporal para a dissipação do efeito repressivo. | 180 dias. | Comparação antes/depois e event-study [P3]. |
| `r₀` | Controla a velocidade básica de expansão. | Ajuste externo com a expansão observada em período conhecido. | Formulação do projeto [P1]. |
| `α` | Controla a intensidade da ativação por alertas. | Ajuste externo. | Formulação do projeto [P1]. |
| `F_max` | Controla a redução máxima da taxa de expansão por fiscalização. | Ajuste externo, entre 0 e 1. | Formulação do projeto [P1]. |
| Piso das variáveis | Define o menor valor admitido nas evidências usadas pelo modelo estático. | Valor configurado na ferramenta estática. | Formulação do projeto [P1]. |
| Teto fixo | Permite atribuir um nível de capacidade escolhido pelo usuário. | Opção da ferramenta estática; o README de origem menciona o valor 0,70 usado em versão anterior. | Documentação do projeto [P1]. |

Os valores da fiscalização vêm do artigo [P3]:

- **Platô de 180 dias.** Janela escolhida pela comparação da área de alertas antes e depois dos autos de infração, em janelas cumulativas de 30 a 360 dias, contra um placebo (Seções 3.6 e 4.2, Figura 1). Nessa janela, a redução é significativa em quatro testes: três não paramétricos e um t pareado (Tabela 1). O event-study em intervalos de 30 dias (Seção 4.5, Figura 3) mostra queda praticamente imediata, patamar no semestre seguinte e perda de distinção em relação ao placebo perto de 180–195 dias. As inclinações `k_subida` e `k_queda` não foram ajustadas.
- **Alcance de 750 m.** Valor de referência dentro da faixa de 0,5–1 km, lido do gráfico e sem ajuste formal. Com janela fixa de 180 dias e buffers cumulativos de 500 m (Seção 4.4, Figura 2), o efeito se distingue do placebo no buffer de 500 m e deixa de se distinguir a partir de 1 km. O artigo propõe `S(d) = exp(−d/d₀)` (Seção 5.2), mas não estima `d₀`.

As referências bibliográficas deste documento fundamentam os métodos.

## Modelo estático: construção de K

A construção da capacidade tem duas etapas: combinar as evidências de terreno em um escore de favorabilidade e converter esse escore em uma fração estimada de ocupação.

### Evidências de terreno

O capítulo de metodologia descreve os seguintes indicadores [P2]:

| Sigla | Significado e função | Fonte dos dados ou procedimento |
|---|---|---|
| `IBx` | **Índice de Baixão**: indicador de superfícies topográficas deprimidas ou aplainadas. | Modelo de elevação SRTM de 30 m e máscara hídrica do Projeto Tapajós, do SGB; processamento descrito em [P2]. |
| `PGgeom` | **Potencial Geomorfológico de Garimpo**: favorabilidade associada à posição no baixão e às suas bordas. | Transformação do `IBx` definida e conferida com dados observados na pesquisa [P2]. |
| `Est` | **Favorabilidade estrutural**: influência da proximidade a lineamentos geológicos. | Lineamentos do SGB e função de distância descrita em [P2], onde o indicador também aparece como `Est2`. |
| `Lt` | **Favorabilidade litológica**: indicador do potencial associado às unidades litológicas. | Unidades litoestratigráficas do SGB e classificação adotada na pesquisa [P2]. |

Essas siglas identificam indicadores da pesquisa. Na combinação matemática, cada evidência transformada é representada por `u_j`.

### Combinação fuzzy gamma

Cada evidência é convertida para uma escala de favorabilidade entre 0 e 1. A formulação ponderada descrita no projeto é:

$$
P(x)=\prod_{j=1}^{n}u_j(x)^{w_j}
$$

$$
S(x)=1-\prod_{j=1}^{n}\left[1-u_j(x)\right]^{w_j}
$$

$$
G(x)=S(x)^\gamma P(x)^{1-\gamma}
$$

| Símbolo | Significado | Unidade ou intervalo | Fonte |
|---|---|---|---|
| `x` | Posição do pixel na grade. | Coordenadas do sistema de referência adotado. | Notação espacial utilizada neste documento. |
| `j` | Índice que identifica uma evidência de terreno. | Inteiro, de 1 a `n`. | Notação da combinação. |
| `n` | Número de evidências incluídas. | Contagem. | Configuração do modelo estático [P1]. |
| `u_j(x)` | Favorabilidade da evidência `j` no pixel `x`. | Adimensional, entre 0 e 1. | Transformações do projeto [P1, P2]; combinação fuzzy [1]. |
| `w_j` | Peso da evidência `j`. | Adimensional. | Pesos obtidos pelo AHP no projeto [P1]; método de Saaty [2]. |
| `P(x)` | Produto fuzzy ponderado das evidências. | Adimensional, entre 0 e 1. | Forma ponderada do projeto [P1]; produto fuzzy [1]. |
| `S(x)` | Soma fuzzy ponderada das evidências. | Adimensional, entre 0 e 1. | Forma ponderada do projeto [P1]; soma fuzzy [1]. |
| `γ`, gama | Parâmetro que controla a combinação entre produto e soma fuzzy. | Adimensional, entre 0 e 1. | Operador fuzzy gamma [1]; aplicação no projeto [P1]. |
| `G(x)` | Escore combinado de favorabilidade do terreno. | Adimensional, entre 0 e 1. | Notação adotada aqui para o resultado do operador do projeto [P1]. |
| `Π` ou `\prod` | Produto dos termos indicados pelo índice. | Operador matemático. | Definição das equações. |

Com `γ = 0`, o escore corresponde ao produto fuzzy; com `γ = 1`, corresponde à soma fuzzy. Os pesos nos expoentes seguem a formulação específica do projeto. A referência [1] descreve o operador gamma convencional.

Uma evidência igual a zero, com peso positivo, anula o produto. O **Piso das variáveis** reduz esse efeito de veto. O valor do piso deve ser registrado junto aos pesos e às transformações, pois influencia o resultado.

### Conversão do escore em capacidade

O projeto estima o nível de `K` por **substituição espaço-tempo**: utiliza locais com maior tempo de exposição ao processo como referência para a ocupação que terrenos semelhantes podem alcançar.

Para cada classe de favorabilidade, calcula-se um quantil alto da ocupação local, medida em uma janela associada à escala de contágio. Entram só os pixels de exposição longa: aqueles cuja janela tem garimpo no mapa do ano mais antigo, ao menos meio pixel. Até a versão 0.3.5, o ruído numérico da convolução fazia cerca de metade da área contar como madura, o que puxava o nível para baixo; um K gerado com o mapa do ano mais antigo antes da 0.3.6 deve ser gerado de novo. Um quantil alto é um valor abaixo do qual se encontra uma parcela elevada das observações. O ajuste por **PAVA** reúne classes adjacentes que violam a ordem crescente e produz uma curva monotônica entre escore e capacidade [P1, 3].

| Símbolo | Significado | Unidade ou intervalo | Fonte |
|---|---|---|---|
| `K(x)` | Capacidade de ocupação por garimpo estimada para o pixel `x`. | Fração da área do pixel, entre 0 e 1, na saída do modelo estático. | Calibração do projeto [P1]. |
| `μ(x)`, mu | Ocupação observada por garimpo, usada na calibração ou no diagnóstico estático. A ferramenta a calcula a partir do **mapa de garimpo (0/1)** do ano de referência; o nível usa a média de `μ` na janela de ocupação. | Fração da área do pixel. | Mapeamento histórico [P1, P2]. |

Os três insumos de ocupação da ferramenta — **mapa de garimpo (0/1)** do ano de referência, do ano mais antigo e do ano anterior — são os mapas anuais como vêm do MapBiomas (só a classe de garimpo), **sem suavizar**: a ferramenta calcula a vizinhança. Um mapa já suavizado seria suavizado de novo, e o nível de K sairia mais baixo e num suporte maior que a janela declarada. A ferramenta avisa quando o mapa tem muitos valores entre 0 e 1 na própria resolução.

A ocupação observada participa da calibração da curva que converte o escore em capacidade. As evidências de terreno determinam a ordenação espacial. A avaliação deve usar períodos distintos para calibrar a curva e verificar seu desempenho.

A capacidade estimada pode ficar abaixo da ocupação potencial de locais que ainda estão em expansão. Sua interpretação depende do tempo de exposição, da cobertura dos dados e da qualidade do ajuste. O projeto trata o leito dos rios como um estrato com curva própria, para representar as condições do garimpo em embarcações [P1]. Com a máscara de água, a ferramenta grava também `<prefixo>_saturacao_agua.csv` e `<prefixo>_saturacao_terra.csv`, com o nível que cada estrato recebeu, e o registro informa, por estrato, a área, a área madura, se a curva própria foi usada e o teto. Um estrato sem pixels maduros suficientes para estimar a própria curva fica com a curva geral; o registro e o registro de execução dizem por quê.

**Área de cálculo e recortes.** A curva escore → nível é estimada com os pixels de exposição longa de toda a área da máscara. Por isso, o projeto gera K para a Província Mineral do Tapajós inteira e só depois o recorta para cada uso: a área de estudo nas análises e a área de estudo com a faixa de entorno nas simulações do modelo dinâmico. O recorte não altera o valor de K em nenhum pixel, porque cada pixel recebe o nível da curva estimada na Província, aplicada ao seu escore. Calibrar na área ampla aproveita toda a exposição observada e mantém a mesma curva em todos os recortes; a hipótese implícita é que a relação entre terreno e saturação é a mesma em toda a Província. Gerar K só na área de estudo teria dois efeitos indesejados: menos pixels maduros por faixa de escore, com faixas inteiras sem dado e de nível extrapolado; e K = 0 fora dela, o que, no modelo dinâmico, zera a ocupação da faixa de entorno e corta o contágio vindo de fora. Os totais do registro e da agregação por sub-bacia se referem à área da máscara usada na geração; para a área de estudo, recorte K ou filtre as sub-bacias.

### Potencial remanescente e agregação

O potencial remanescente corresponde à fração de área disponível segundo a capacidade estimada:

$$
R(x)=\max\left[K(x)-\mu(x),\,0\right]
$$

| Símbolo | Significado | Unidade | Fonte |
|---|---|---|---|
| `R(x)` | Potencial remanescente do pixel. | Fração da área do pixel. | Definição do projeto [P1]. |
| `K(x)` e `μ(x)` | Capacidade estimada e ocupação observada, definidas acima. | Fração da área do pixel. | [P1, P2]. |
| `max` | Operador que seleciona o maior valor; nesta expressão, limita o remanescente inferiormente a zero. | Operador matemático. | Definição da equação. |

Para obter hectares, multiplica-se cada fração pela área do respectivo pixel antes da soma:

$$
\mathrm{Área\ de\ capacidade}=\sum_i a_iK_i,\qquad
\mathrm{Área\ ocupada}=\sum_i a_i\mu_i,\qquad
\mathrm{Área\ remanescente}=\sum_i a_iR_i
$$

Aqui, `i` identifica um pixel da sub-bacia; `a_i` é sua área em hectares; `K_i`, `μ_i` e `R_i` são os valores nesse pixel; e `Σ`, ou `\sum`, representa a soma. A conversão decorre das unidades das variáveis e da agregação por sub-bacia descrita no projeto [P1].

Com a camada de sub-bacias informada, a saída `<prefixo>_subbacias.gpkg` contém:

| Campo | Significado | Unidade |
|---|---|---|
| `pa_k_ha` | Área correspondente à capacidade total estimada. | ha |
| `pa_mu_ha` | Área observada de garimpo. | ha |
| `pa_rem_ha` | Área potencial remanescente. | ha |
| `pa_expl` | Grau de ocupação em relação à capacidade, documentado no intervalo de 0 a 1. | Adimensional |
| `pa_exc_ha` | Área excedente, calculada onde a ocupação observada supera a capacidade estimada. | ha |
| `pa_var_ha` | Variação da área ocupada no último ano. | ha |
| `pa_anos` | Tempo estimado para ocupar o remanescente ao ritmo do último ano. | anos |
| `pa_rank` | Posição da sub-bacia na ordenação por potencial remanescente. | Posição |

Fonte dos campos: documentação do projeto [P1]; rotina indicada: `core/agregacao.py`. A estimativa de tempo depende de um incremento anual positivo e da manutenção desse ritmo.

O excedente ajuda a identificar locais em que a capacidade pode estar subestimada ou em que o mapeamento de ocupação inclui processos pouco representados pelas evidências de terreno. As operações de combinação e subtração são feitas no pixel; a soma por sub-bacia resume o resultado.

## Modelo dinâmico: simulação da expansão

A ocupação evolui em uma grade raster. O modelo combina contágio, capacidade disponível, ativação por alertas e supressão por fiscalização [P1]:

$$
\frac{d\mu}{dt}
=
r_0\,C(\mu)
\left(1-\frac{\mu}{K}\right)
(1+\alpha A)
(1-F_{\max}F)
$$

| Símbolo ou termo | Significado | Unidade ou intervalo | Fonte |
|---|---|---|---|
| `μ(x,t)`, mu | Ocupação simulada por garimpo no pixel `x` e no tempo `t`. O limite modelado é `K`. | Na formulação fracionária, entre 0 e `K`. | Variável de estado do projeto [P1]. |
| `x` | Posição do pixel na grade. | Coordenadas projetadas em metros. | Grade de trabalho [P1]. |
| `t` | Tempo da simulação. | Dias, quando os parâmetros temporais são expressos em dias. | Convenção temporal deste documento. |
| `dμ/dt` | Taxa instantânea de mudança da ocupação. | Fração de área por dia, nessa convenção. | Equação do projeto [P1]. |
| `r₀` | Coeficiente básico de expansão. | Dia⁻¹, para contágio adimensional e tempo em dias. | Parâmetro do projeto, sujeito a calibração [P1]. |
| `C(μ)` | Campo de contágio: influência espacial da ocupação, calculada por convolução com um núcleo. | Adimensional na formulação com núcleo normalizado. | Formulação do projeto; `core/kernels.py` [P1]. |
| `K(x)` | Capacidade de ocupação, mantida fixa durante a execução. | Mesma unidade de `μ`. | Raster de entrada [P1]. |
| `1 − μ/K` | Fator de saturação: reduz o crescimento à medida que a ocupação se aproxima da capacidade. | Adimensional. | Forma logística [4], adaptada no projeto [P1]. |
| `A(x,t)` | Campo de ativação produzido pelos alertas datados. | Adimensional. | Formulação do projeto; `core/eventos.py` [P1]. |
| `α`, alfa | Coeficiente que dimensiona a contribuição dos alertas. | Adimensional. | Parâmetro do projeto, sujeito a calibração [P1]. |
| `F(x,t)` | Campo de influência da fiscalização no espaço e no tempo. | Adimensional, entre 0 e 1. | Formulação do projeto; `core/eventos.py` [P1]. |
| `F_max` | Intensidade máxima de supressão da taxa de crescimento. | Adimensional, entre 0 e 1. | Parâmetro do projeto, sujeito a calibração [P1]. |

A rotina utiliza **Euler explícito**: em cada passo, calcula a taxa no estado atual e a aplica ao intervalo seguinte [P1, 5]:

$$
\mu(x,t+h)\approx\mu(x,t)+h\,\frac{d\mu}{dt}(x,t)
$$

O símbolo `h` representa o passo de integração, em dias nesta convenção; `≈` indica uma aproximação numérica. Fonte: método de Euler [5], utilizado pelo integrador `core/model.py` segundo [P1].

### Propagação e eventos

O contágio utiliza distância euclidiana e uma escala local próxima de 380 m. A sub-bacia recebe os resultados agregados após a simulação. Esse arranjo conserva a resolução espacial dos processos durante o cálculo.

Quando `C(μ) = 0`, a taxa de expansão calculada pela equação é zero. A opção de **semeadura por alertas** introduz ocupação a partir de detecções de garimpo novo, conforme a configuração do projeto.

Os fragmentos de alerta são agrupados em frentes antes da geração dos campos. A rotina indicada é `core/frentes.py`, com agrupamento por ligação simples: elementos próximos se conectam em um mesmo grupo [P1].

Pixels com `K = 0` são tratados como excluídos. A documentação informa que sua ocupação inicial é zerada na preparação [P1].

### Efeito temporal dos eventos

O projeto representa a fiscalização por uma função com subida inicial, patamar e dissipação. Para tempo posterior ao evento:

$$
T_{\mathrm{bruto}}(\Delta t)
=
\left(1-e^{-k_{\mathrm{subida}}\Delta t}\right)
\sigma\left[-k_{\mathrm{queda}}(\Delta t-p)\right]
$$

$$
\sigma(z)=\frac{1}{1+e^{-z}},\qquad
T(\Delta t)=\frac{T_{\mathrm{bruto}}(\Delta t)}{T_{\max}}
$$

Antes do evento, `T = 0`. A normalização faz o pico de `T` corresponder a 1. Fora dos primeiros dias, essa curva coincide com a forma `T(Δt) = 1/(1 + e^{(Δt − c)/k})` proposta em [P3], com `c = p` e `k = 1/k_queda`; nos valores padrão, `k = 20` dias. Para os alertas, o componente temporal documentado é:

$$
A_{\mathrm{temporal}}(\Delta t)=e^{-\Delta t/\tau},
\qquad \Delta t\geq 0
$$

| Símbolo | Significado | Unidade | Fonte |
|---|---|---|---|
| `Δt`, delta t | Tempo transcorrido desde a data do evento. | Dias. | Formulação temporal do projeto [P1]. |
| `T_bruto` | Função temporal da fiscalização antes da normalização. | Adimensional. | Formulação do projeto; `core/temporal.py` [P1]. |
| `T` | Função temporal normalizada da fiscalização. | Adimensional, entre 0 e 1. | Formulação do projeto [P1]. |
| `T_max` | Valor máximo de `T_bruto` utilizado na normalização. | Adimensional. | Procedimento documentado em [P1]. |
| `k_subida` | Coeficiente que controla a velocidade de subida do efeito. | Dia⁻¹. | Parâmetro da função do projeto [P1]. |
| `k_queda` | Coeficiente que controla a velocidade de dissipação do efeito. | Dia⁻¹. | Parâmetro da função do projeto [P1]. |
| `p` | Referência temporal da queda, correspondente ao parâmetro **platô**. | Dias. | Notação adotada aqui para o parâmetro do projeto [P1]. |
| `σ`, sigma | Função sigmoide logística definida na equação. | Adimensional. | Definição matemática; uso temporal do projeto [P1]. |
| `z` | Argumento da função `σ`; nesta aplicação, `−k_queda(Δt − p)`. | Adimensional. | Notação da equação. |
| `e` | Base da função exponencial, aproximadamente 2,71828. | Constante adimensional. | Notação matemática. |
| `A_temporal` | Componente temporal da ativação por alertas. | Adimensional. | Formulação do projeto [P1]. |
| `τ`, tau | Tempo característico de decaimento dos alertas; após esse intervalo, o componente temporal vale `e⁻¹`, aproximadamente 0,368. | Dias. | Parâmetro da função do projeto [P1]. |
| `Δ`, delta | Indica uma diferença: tempo decorrido em `Δt` e acréscimo de ocupação em `Δμ`. | Depende da grandeza. | Convenção das equações. |

Os componentes temporais são combinados com a influência espacial dos eventos para formar `A` e `F`. A documentação prevê combinação da fiscalização por máximo e a opção `soma_saturada` para comparação [P1].

### Aba Saídas do modelo dinâmico

A saída principal é `*_expansao_*.tif`:

$$
\Delta\mu(x)=\mu_f(x)-\mu_0(x)
$$

| Símbolo | Significado | Unidade | Fonte |
|---|---|---|---|
| `μ₀(x)` | Ocupação inicial, na data `t₀`. | Mesma unidade de `K`. | Raster inicial [P1]. |
| `μ_f(x)` | Ocupação simulada na data final `t_f`. | Mesma unidade de `K`. | Resultado do integrador [P1]. |
| `t₀` e `t_f` | Datas inicial e final da simulação. | Datas do período escolhido. | Configuração da execução [P1]. |
| `Δμ(x)` | Acréscimo de ocupação entre o início e o fim da execução. | Fração de área, na formulação fracionária. | Definição da saída do projeto [P1]. |

Também podem ser gravados a ocupação final, a taxa instantânea, os campos de ativação e supressão, a série temporal e o GeoPackage por sub-bacia.

| Campo agregado | Significado | Unidade |
|---|---|---|
| `rg_exp_ha` | Área de expansão simulada. | ha |
| `rg_mu0_ha` | Área de ocupação inicial. | ha |
| `rg_muf_ha` | Área de ocupação final. | ha |
| `rg_exp_rel` | Expansão relativa ao estoque inicial: `rg_exp_ha / rg_mu0_ha`. Vale `−1` sempre que a sub-bacia não tinha ocupação inicial, com ou sem expansão; nesse caso, a expansão é chegada em área virgem, por contágio de sub-bacias vizinhas ou por semeadura de alertas. Filtre esse valor antes de ordenar ou classificar o campo. | Adimensional |
| `rg_px` | Pixels da grade de trabalho atribuídos à sub-bacia, incluindo os excluídos da simulação por `K = 0` ou pela máscara. | Pixels |
| `rg_rank` | Posição na ordenação por `rg_exp_ha` decrescente; 1 é a maior expansão. Empates mantêm a ordem das feições na camada. | Posição |

Um pixel pertence à sub-bacia que contém o seu centro; onde polígonos se sobrepõem, vale o último. A ocupação inicial de `rg_mu0_ha` é a que a simulação usou: truncada em `K` e zerada onde `K = 0` ou fora da máscara. A área de cada pixel é a da grade de trabalho. Rotina: `core/agregacao.py`.

### Registro da execução

Cada execução grava, na pasta de saída, um arquivo JSON que permite identificar e repetir o que foi rodado:

| Ferramenta | Arquivo |
|---|---|
| Modelo dinâmico | `<prefixo>_registro_<data final>.json` |
| Modelo estático | `<prefixo>_registro.json` |

O registro contém a versão do plugin e das bibliotecas (Python, NumPy, SciPy, GDAL e QGIS), a configuração da interface, os parâmetros do modelo, a grade de trabalho e os arquivos gravados. Cada insumo em arquivo local é identificado pelo caminho, pelo tamanho, pela data de modificação e pelo SHA-256 do conteúdo; um shapefile inclui também `.dbf`, `.shx`, `.prj` e `.cpg`. Fontes que não são arquivo local, como PostGIS, WFS, camadas em memória e arquivos compactados (`/vsizip/`), aparecem com `"existe": false` e sem SHA-256. Camadas vetoriais registram ainda o nome, a fonte sem senhas, o CRS, o número de feições, o campo de data e se havia edições não salvas; nesse caso, a interface avisa que o SHA-256 identifica o arquivo em disco, e não as feições usadas. No modelo dinâmico entram as contagens de alertas e autos lidos, descartados e fora da grade, as frentes e o período dos eventos; com o agrupamento ligado, as contagens fora da grade e o período se referem às frentes. No estático, entram o teto de `K`, os totais da área de estudo e o diagnóstico de veto.

O registro descreve os arquivos listados em `saidas`. Antes da primeira saída, o registro anterior com o mesmo nome é apagado: se a execução falhar no meio, a pasta fica sem registro, em vez de manter um que descreve outra execução. Saídas opcionais de execuções anteriores que esta não regravou continuam na pasta, e a pasta da série temporal (`<prefixo>_serie`) não leva data. Para preservar uma execução, use outro prefixo ou outra pasta. Uma falha ao gravar o registro aparece no registro de execução da interface e não interrompe a gravação das saídas. Rotina: `core/registro.py`.

## Calibração e verificação

- **Passo de integração:** compare uma execução com outra de passo menor. Diferenças relevantes indicam sensibilidade à discretização temporal [5].
- **Janelas de eventos:** eventos agrupados na mesma janela compartilham uma data de referência. Escolha uma janela compatível com a duração dos efeitos; o projeto exemplifica 30 dias para um platô de 180 dias [P1].
- **Intensidade dos efeitos:** ajuste `r₀`, `α` e `F_max` com dados observados. A forma das funções temporais e espaciais constitui uma parte da parametrização.
- **Memória:** muitas janelas em rasters extensos aumentam a demanda. Avalie a resolução e o número de janelas em conjunto com as escalas dos processos.
- **Validação temporal:** compare a expansão prevista com o garimpo novo observado em período reservado para teste [P1, P2]. A ferramenta **Validar uma execução** faz essa comparação; as métricas e o procedimento estão descritos abaixo.
- **Calibração em lote:** `tools/calibrar_noite.py` roda, no Console Python do QGIS, centenas de simulações entre duas datas com mapas de garimpo e compara a expansão prevista com a observada. Primeiro calibra `r₀` para a área nova prevista igualar a observada; depois varia os demais parâmetros, com um fatorial de platô, alcance e `F_max` da fiscalização, e compara o modelo com alternativas sem dinâmica (só `K`, remanescente, proximidade ao garimpo existente). Grava cada simulação num CSV assim que termina, retoma de onde parou e escreve `resumo.txt`. `tools/calibrar_paralelo.py` roda o mesmo plano em vários processos, fora do QGIS. `fora_da_amostra(treino, teste)` compara dois períodos rodados com o mesmo plano: escolhe os parâmetros num período e mede o desempenho no outro, sem simular de novo. As instruções estão no início de cada arquivo.
- **K acrescenta informação?** `tools/experimento_k.py` compara, em cada período e em recortes pela distância ao garimpo de `t₀` (geral, até 1 km, 1–5 km, mais de 5 km), a proximidade ao garimpo existente `P` com `S = P × máx(K − O₀, 0)` para o K atual, o K sem Est e K = 1. O K sem Est é gerado pelo modelo estático com a mesma configuração do registro do K atual, sem Est; antes, o K atual é regerado e conferido contra o arquivo. `O₀` é a ocupação na janela de ocupação, o mesmo suporte em que K é calibrado, e `P` é calculado por soma direta, sem o ruído da FFT, de modo que é zero fora do alcance do núcleo: onde `P = 0` em todo o recorte, a comparação é registrada como redundante. Mede captura nos 1, 5 e 10% da área, AUC e `r_blocos`, com intervalos de 95% das diferenças por bootstrap pareado por sub-bacia, e reproduz as linhas de base da calibração do mesmo período. Roda em modo `piloto` (um período, poucas réplicas) e `completo`; o segundo reaproveita o K sem Est e a conferência do primeiro.

A documentação cita o teste `test_supressao_temporaria_desloca_a_trajetoria_permanentemente`. Ele examina, em um cenário sintético, a persistência de diferenças de trajetória após a fiscalização perder intensidade. A duração e a magnitude dessas diferenças dependem dos parâmetros, da saturação e do horizonte analisado. A interpretação territorial exige comparação com dados observados.

### Métricas de validação

A rotina `core/validacao.py` compara a expansão prevista `Δμ` com o garimpo novo observado no mesmo período, `máx(μ_final − μ_inicial, 0)`, num mapa que o modelo não usou. As métricas respondem a três perguntas:

| Métrica | Pergunta | Leitura | Fonte |
|---|---|---|---|
| `auc` | O risco ordena os pixels com garimpo novo acima dos demais? | Probabilidade de um pixel com garimpo novo, acima de um limiar, ter risco maior que um pixel sem. 0,5 é o acaso; 1, a ordenação perfeita. | Curva ROC [6], aplicada a mudança de uso da terra [7]. |
| `captura_<x>` e curva de captura | Quanto do garimpo novo cai nos `x`% da área de maior risco? | Fração da área nova observada; pesa pelo tamanho do garimpo novo, ao contrário da AUC. | Curva de taxa de sucesso [8]. |
| `area_sob_captura` | Resumo da curva de captura. | 0,5 é o acaso; tende a 1 quando o garimpo novo se concentra no topo do risco. | [8]. |
| `razao_area` | O modelo prevê a quantidade certa? | Área nova prevista ÷ observada; 1 é o certo. Calibra `r₀`. | Definição deste documento. |
| `r_blocos` | O padrão bate em escala mais grossa? | Correlação de Pearson entre as somas em blocos de 1 km, que tolera erros de posição menores que o bloco. | Definição deste documento. |
| `spearman` e `topo_comum` por sub-bacia | A priorização das sub-bacias acerta? | Correlação de postos entre as áreas previstas e observadas por sub-bacia, e fração das 10% sub-bacias de maior expansão observada que também estão entre as de maior expansão prevista. | Definição deste documento. |

Empates, inevitáveis porque o risco é zero em boa parte da área, são tratados por posto médio na AUC e por rateio proporcional na captura; assim, nenhuma métrica depende da ordem dos pixels no raster. O universo da avaliação é a área de estudo inteira, inclusive onde `K = 0`: garimpo novo fora de `K` é erro do modelo e deve contar.

**Validar uma execução.** Em **Raster → Risco Garimpo → Validar uma execução…**, escolha o registro da execução (o `<prefixo>_registro_<data>.json` gravado pelo modelo dinâmico) e o garimpo observado na data final; os demais campos são preenchidos pelo registro. A mesma rotina, `core/execucao_validacao.py`, pode ser chamada no Console Python do QGIS:

```python
from risco_garimpo.core import execucao_validacao as v
config = v.de_registro(r"C:\saida\PMT_registro_20241231.json",
                       r"C:\dados\garimpo_2024.tif",
                       caminho_mascara_avaliacao=r"C:\dados\mascara_area.tif")
v.validar(config)
```

Do registro saem a expansão prevista, `μ₀`, `K`, a máscara e as datas. O SHA-256 de cada insumo é conferido de novo, e a validação avisa se algum arquivo mudou desde a execução. Ela avisa também quando a execução usou alertas ou autos posteriores à data inicial: nesse caso, mede a reconstrução do período, e não a antecipação. As saídas, com o prefixo da execução seguido de `_validacao`, são:

| Arquivo | Conteúdo |
|---|---|
| `_metricas.csv` | Uma linha para o modelo e uma para cada linha de base (proximidade em 100, 150, 200, 300, 380 e 500 m e, com `K`, as que usam o potencial), com todas as métricas da tabela acima. |
| `_curva_captura.csv` e `_curva_captura.png` | Curva de captura do modelo e das linhas de base; a figura traz o modelo, a melhor linha de base e o acaso. A figura exige matplotlib, que acompanha o QGIS. |
| `_subbacias.gpkg` | Com uma camada de sub-bacias: `va_prev_ha` e `va_obs_ha`, as áreas novas prevista e observada, e `va_rank_prev` e `va_rank_obs`, as posições nas duas ordenações. |
| `_registro.json` | Registro da validação, com as métricas, os avisos e o SHA-256 dos insumos. |

Para que a comparação tenha sentido, o modelo precisa superar alternativas sem dinâmica, calculadas por `linhas_de_base`: só `K`, o remanescente `K − μ`, a proximidade ao garimpo existente e a proximidade onde ainda há remanescente. A capacidade de antecipação só é medida honestamente com eventos até a data inicial; com os eventos do período inteiro, a avaliação mede a reconstrução do que aconteceu.

## Desenvolvimento

| Arquivo ou pasta | Responsabilidade |
|---|---|
| `core/numerico.py` | Convolução por FFT, transformada de distância e alternativas em NumPy. |
| `core/kernels.py` | Núcleo de contágio. |
| `core/temporal.py` | Funções temporais de fiscalização e alertas. |
| `core/frentes.py` | Agrupamento dos alertas em frentes. |
| `core/eventos.py` | Campos espaciais e temporais dos eventos. |
| `core/model.py` | Integração da dinâmica. |
| `core/raster.py` | Grade, leitura alinhada e escrita de rasters com GDAL. |
| `core/vetor.py` | Extração dos eventos das camadas do QGIS. |
| `core/agregacao.py` | Resumo das saídas por sub-bacia. |
| `core/estatico.py` | Combinação fuzzy e estimação do nível de capacidade. |
| `core/execucao.py` | Orquestração do modelo dinâmico. |
| `core/execucao_estatica.py` | Orquestração do modelo estático. |
| `core/registro.py` | Registro da execução em JSON: configuração, identificação dos insumos e saídas. |
| `core/validacao.py` | Métricas de validação da expansão prevista contra o garimpo novo observado. |
| `core/execucao_validacao.py` | Orquestração da validação de uma execução: leitura alinhada, métricas, linhas de base, curva de captura, sub-bacias e registro. |
| `compat.py` | Diferenças entre o QGIS 3 (Qt5) e o QGIS 4 (Qt6): nomes de enum com escopo, tipos de geometria e de campo. |
| `ui/` | Diálogos, arquivos `.ui` e tarefas do QGIS. |
| `tools/` | Geração de interfaces, empacotamento e dados de teste. |
| `tests/` | Testes do núcleo e contratos de interface. |

Os caminhos do núcleo são relativos à pasta `risco_garimpo/`. As responsabilidades acima seguem a documentação original [P1].

Cada ferramenta organiza o trabalho em três fases: **preparar**, na thread principal, acessa as camadas do projeto; **executar**, em segundo plano, realiza os cálculos; **agregar/escrever**, na thread principal, finaliza as saídas.

A cada push, o GitHub Actions (`.github/workflows/testes.yml`) roda os testes em Python 3.9, a versão do QGIS 3.22, e em 3.12, com e sem SciPy. Um segundo job instala o QGIS 3.34 do Ubuntu, e um terceiro o QGIS 4.2 do conda-forge; os dois rodam também `tests/test_qgis_ponta_a_ponta.py`, que executa os dois modelos com a GDAL e a API vetorial do QGIS reais e abre o plugin e os três diálogos; fora desses ambientes, esse teste é pulado.

O arquivo `tests/test_contrato_ui.py` verifica os componentes utilizados pelos diálogos e a correspondência entre os arquivos de interface e seus geradores.

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
python tools/gerar_ui.py
python tools/gerar_ui_estatico.py
python tools/empacotar.py
```

## Siglas e convenções

| Termo | Significado | Fonte |
|---|---|---|
| **AHP** | *Analytic Hierarchy Process*, processo de análise hierárquica: método de atribuição de prioridades por comparações par a par. | Saaty [2]. |
| **PAVA** | *Pool-Adjacent-Violators Algorithm*: algoritmo de ajuste monotônico por agrupamento de classes adjacentes que violam a ordem. | De Leeuw, Hornik e Mair [3]. |
| **CRS** | *Coordinate Reference System*, sistema de referência de coordenadas. | Configuração espacial do projeto [P1]. |
| **EPSG** | Registro de sistemas de referência e seus identificadores, como 31981 e 4674. | Códigos empregados na documentação [P1]. |
| **SIRGAS** | Sistema de Referência Geocêntrico para as Américas. | Sistema adotado na pesquisa [P2]. |
| **UTM** | *Universal Transverse Mercator*, projeção cartográfica; “21S” indica a zona 21 do hemisfério sul. | Grade adotada na pesquisa [P2]. |
| **SRTM** | *Shuttle Radar Topography Mission*, missão que forneceu o modelo de elevação utilizado. | Fonte de dados identificada em [P2]. |
| **SGB** | Serviço Geológico do Brasil. | Fonte de dados geológicos e hidrográficos identificada em [P2]. |
| **IBAMA** | Instituto Brasileiro do Meio Ambiente e dos Recursos Naturais Renováveis. | Fonte dos autos de infração identificada em [P2]. |
| **ICMBio** | Instituto Chico Mendes de Conservação da Biodiversidade. | Fonte dos autos de infração identificada em [P2]. |
| **GDAL** | *Geospatial Data Abstraction Library*, biblioteca para leitura, processamento e escrita de dados geoespaciais. | [Documentação oficial](https://gdal.org/en/stable/). |
| **FFT** | *Fast Fourier Transform*, transformada rápida de Fourier, utilizada na convolução. | [Documentação do SciPy](https://docs.scipy.org/doc/scipy/reference/fft.html); aplicação indicada em [P1]. |
| **UI** | *User Interface*, interface do usuário. | Pasta e arquivos de interface do projeto [P1]. |
| **m**, **km**, **ha** | Metro; quilômetro, equivalente a 1.000 m; hectare, equivalente a 10.000 m². | Unidades de distância e área usadas no projeto. |
| `pa_` e `rg_` | `pa_` identifica os campos do potencial remanescente; `rg_`, as saídas agregadas do modelo dinâmico Risco Garimpo. | Convenção dos campos listados em [P1]. |
| `_ha`, `_rel`, `_px`, `_rank` | Sufixos dos campos: hectares, valor relativo, contagem de pixels e posição na ordenação. | Convenção dos campos do projeto [P1]. |
| `[0,1]` | Intervalo que inclui os valores de zero a um. | Convenção matemática das legendas. |

QGIS, NumPy, SciPy, MapBiomas e HydroBASINS são nomes dos softwares ou produtos mencionados. SCCON/Planet identifica a origem dos dados de imagem e alertas citada no capítulo de metodologia [P2].

## Fontes

**[P1] Documentação do projeto.** README original “Risco Garimpo (plugin QGIS)”, fornecido para esta revisão. Fonte das equações implementadas, parâmetros informados, opções de interface, arquivos de saída, comandos e organização do código. Os caminhos de código citados neste documento foram extraídos dessa documentação.

**[P2] Documentos da pesquisa.** “Capítulo de Metodologia — Risco de expansão do garimpo no Tapajós”, especialmente as seções de dados e pré-processamento, evidências geomorfológica e estrutural e validação; e capítulo de sistemas complexos da qualificação. Fontes das definições dos indicadores, das bases de dados e da escala local de contágio. As versões das bases devem acompanhar os registros de cada execução.

**[P3] Artigo da pesquisa.** Magalhães, G. C. “Efeito temporal da fiscalização ambiental sobre a expansão do garimpo ilegal: evidências para a parametrização de um modelo dinâmico de risco na Província Mineral do Tapajós”. Artigo submetido ao SICOD 2026. Fonte do platô e do alcance da fiscalização e da forma das funções temporal e espacial.

1. **Esri.** *How Fuzzy Overlay works*. Documentação do ArcGIS Pro. Referência para produto, soma e operador fuzzy gamma. [Texto](https://doc.esri.com/en/arcgis-pro/latest/tool-reference/spatial-analyst/how-fuzzy-overlay-works.html).
2. **Saaty, T. L. (2008).** Decision making with the analytic hierarchy process. *International Journal of Services Sciences*, 1(1), 83–98. [DOI: 10.1504/IJSSCI.2008.017590](https://doi.org/10.1504/IJSSCI.2008.017590).
3. **De Leeuw, J.; Hornik, K.; Mair, P. (2009).** Isotone Optimization in R: Pool-Adjacent-Violators Algorithm (PAVA) and Active Set Methods. *Journal of Statistical Software*, 32(5), 1–24. [DOI: 10.18637/jss.v032.i05](https://doi.org/10.18637/jss.v032.i05).
4. **Edelstein-Keshet, L. (2005).** *Mathematical Models in Biology*. SIAM, capítulo 4, “An Introduction to Continuous Models”. Referência para modelos de crescimento e saturação. [DOI do capítulo: 10.1137/1.9780898719147.ch4](https://doi.org/10.1137/1.9780898719147.ch4).
5. **Driscoll, T. A.; Braun, R. J.** *Fundamentals of Numerical Computation*, seção “Euler’s method”, edição on-line dos autores. Referência para integração explícita e erro numérico. [Texto](https://fncbook.github.io/v1.0/ivp/euler.html).
6. **Fawcett, T. (2006).** An introduction to ROC analysis. *Pattern Recognition Letters*, 27(8), 861–874. [DOI: 10.1016/j.patrec.2005.10.010](https://doi.org/10.1016/j.patrec.2005.10.010).
7. **Pontius, R. G.; Schneider, L. C. (2001).** Land-cover change model validation by an ROC method for the Ipswich watershed, Massachusetts, USA. *Agriculture, Ecosystems & Environment*, 85(1–3), 239–248. [DOI: 10.1016/S0167-8809(01)00187-6](https://doi.org/10.1016/S0167-8809(01)00187-6).
8. **Chung, C.-J. F.; Fabbri, A. G. (2003).** Validation of spatial prediction models for landslide hazard mapping. *Natural Hazards*, 30(3), 451–472. [DOI: 10.1023/B:NHAZ.0000007172.62651.2b](https://doi.org/10.1023/B:NHAZ.0000007172.62651.2b).

## Licença

MIT. Consulte `LICENSE` para os termos de uso e distribuição.

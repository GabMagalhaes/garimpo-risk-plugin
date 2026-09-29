# Risco Garimpo (plugin QGIS)

Produto da dissertação sobre antecipação da expansão do garimpo ilegal na
Província Mineral do Tapajós. O plugin traz **duas ferramentas**, em
**Raster → Risco Garimpo**:

| ferramenta | pergunta | saída |
|---|---|---|
| **Modelo estático — gerar K** | quanto cabe aqui? | `K` em [0, 1]: a proporção da área do pixel que se espera explorada **no máximo** |
| **Modelo dinâmico de risco** | onde vai expandir a seguir? | `Δμ`: a expansão prevista no horizonte escolhido |

As duas são independentes, e a separação é **de arquivo, não de chamada**: o
estático grava `K.tif`, o dinâmico o lê como qualquer raster. Nenhum dos dois
importa o código do outro, e nada reprocessa o estático a cada simulação.

---

## O modelo estático

O resultado não é um índice adimensional de favorabilidade: é uma
**proporção**. E uma proporção tem duas propriedades independentes, cada uma
com o seu método.

**Ordenação** — quem é mais favorável que quem. Sai do fuzzy gamma ponderado
sobre as variáveis de terreno:

```
γ = S^gamma · P^(1−gamma)        P = Π xᵢ^wᵢ        S = 1 − Π (1−xᵢ)^wᵢ
```

com os pesos AHP nos expoentes. **μ não entra aqui** — nem como variável, nem
pixel a pixel.

**Nível** — quanto, de fato, cabe no terreno mais favorável. Curvas de captura
e AUC são **cegas a isso**: multiplicar K inteiro por 0,5 não muda nenhuma
delas (há teste que fixa essa propriedade). O nível é estimado por
substituição espaço-tempo: para cada classe de terreno, um quantil alto da
ocupação local observada entre os pixels de **exposição longa** ao processo,
medida em janela da escala de contágio. Ajuste monótono por
pool-adjacent-violators, sem assumir forma funcional.

Três consequências que valem registrar:

- **O quantil observado é limite inferior do teto.** Nada garante que algum
  lugar já tenha saturado, então K estimado assim erra para baixo — o que é
  preferível a um teto decretado. Os 0,70 da versão anterior embutiam margem
  de expansão de ≈6,5× sobre a ocupação observada; isso era hipótese, não
  medida, e agora é opcional (`Teto fixo`).
- **O piso é obrigatório, não cosmético.** No produto ponderado `0^w = 0` para
  qualquer peso: uma única variável zerada anula o pixel. Com IBx zerando em
  ~30% da área, o veto apagaria território real. O campo `Piso das variáveis`
  preserva a ordem e impede que a variável mais fraca do modelo decida sozinha
  onde não pode haver garimpo.
- **O leito de rio é estrato, não máscara.** Garimpo de balsa não obedece à
  geomorfologia de barranco, então a água recebe curva de nível própria em vez
  de herdar um PGgeom que ali não significa nada.

### Potencial Aurífero Remanescente

O ramo `(−)` do fluxograma. A favorabilidade diz quanto o terreno comporta; o
garimpo pretérito diz quanto já foi tomado. A diferença é o que resta:

```
R(x) = máx(K(x) − μ(x), 0)
```

Informando a camada de sub-bacias, sai `<prefixo>_subbacias.gpkg` com:

| coluna | o que é |
|---|---|
| `pa_k_ha` | potencial total do terreno (Σ K) |
| `pa_mu_ha` | área total garimpada na sub-bacia (Σ μ) |
| `pa_rem_ha` | **potencial remanescente** |
| `pa_expl` | grau de explotação garimpeira: μ/K, em [0, 1] |
| `pa_exc_ha` | excedente, onde μ passou de K |
| `pa_var_ha` | variação no último ano |
| `pa_anos` | anos até esgotar o remanescente no ritmo do último ano |
| `pa_rank` | posição por potencial remanescente |

**Agregar aqui é legítimo, e antes não era** — e a diferença não é de gosto,
é de tipo de grandeza. PGgeom, Est e Lt são **intensivas**: a média por
sub-bacia as dilui abaixo do ruído, porque borda de baixão ocupa fração
pequena de uma sub-bacia de mais de 100 km² (foi o que matou o PGgeom
agregado, ρ = +0,017 contra concentração de 90× por pixel, e o `Lt_local`).
K, μ e R em hectares são **extensivas**: a soma conserva a grandeza. Por isso
a ordem é combinar no pixel, subtrair no pixel, **somar** por sub-bacia —
nunca o contrário. É a mesma regra do modelo dinâmico, em que a sub-bacia é
unidade de saída e jamais de propagação.

O **excedente** (`pa_exc_ha`, onde μ > K) não é erro a esconder: é
diagnóstico. Ou K está subestimado ali, ou μ inclui o que o modelo de terreno
não cobre — balsa no leito, rejeito, retrabalho. O total dele mede a aderência
do estático aos dados.

Sobre a fronteira "K exclui μ": ela continua valendo onde importa. O que μ faz
no estático é calibrar uma curva **global unidimensional** escore → nível, do
mesmo tipo da transformação IBx → PGgeom. A dependência é atenuada, não
eliminada, e a validação honesta é calibrar num recorte temporal e verificar
no seguinte.

---

## O modelo

A dinâmica é resolvida em superfície contínua (raster), por Euler explícito:

```
dμ/dt = r₀ · C(μ) · (1 − μ/K) · (1 + α·A) · (1 − F_max·F)
```

| símbolo | significado |
|---|---|
| `μ(x,t)` | variável de estado — grau de ocupação por garimpo no pixel, em [0, K] |
| `K(x)`   | capacidade de suporte — favorabilidade do modelo estático |
| `C(μ)`   | pressão de contágio — convolução de μ por núcleo de escala ~380 m |
| `(1−μ/K)`| saturação logística |
| `A(x,t)` | ativação por alertas — exponencial no espaço e no tempo |
| `F(x,t)` | supressão por fiscalização — platô logístico no tempo, exponencial no espaço |

Decisões que o código assume e que **não** são ajustáveis por engano:

- **K exclui μ.** O garimpo pretérito entra na variável de estado e no termo de
  contágio, nunca na capacidade de suporte — sob pena de dupla contagem e de
  K endógeno.
- **A propagação é euclidiana, não hidrológica.** O contágio local está ligado
  a ramais e logística terrestre; a hipótese de litologia a montante não se
  sustentou na escala HydroBASINS nível 12.
- **A sub-bacia é unidade de saída, nunca de propagação.** A agregação ocorre
  depois de simular. Agregar antes dilui o efeito repressivo (raio de 0,5–1 km
  sobre sub-bacias de mediana > 100 km²) abaixo do ruído e destrói as escalas
  de 380 m e 3.500 m.
- **Sem vizinho ocupado não há crescimento.** O contágio é a única porta de
  entrada — coerente com o achado de que PGgeom prediz taxa de crescimento
  condicionada à presença, e não chegada em área virgem. A chegada em área
  virgem só ocorre por semeadura de alerta (opcional), porque um alerta é, ele
  próprio, detecção de garimpo novo.
- **Alertas são agrupados em frentes antes de qualquer coisa.** ~85% dos
  alertas têm vizinho a menos de 100 m: o alerta isolado é fragmento de
  detecção, não evento independente.

### Forma das funções temporais

A fiscalização **não** é um decaimento exponencial a partir de um pico. O
event-study mostra início quase imediato, patamar estável por ~180 dias e
dissipação posterior — daí o platô logístico:

```
T(Δt) = (1 − e^(−k_subida·Δt)) · σ(−k_queda·(Δt − platô)),   T(Δt<0) = 0
```

normalizado para pico 1, de modo que `F_max` permaneça legível como "supressão
máxima". Os alertas, ao contrário, decaem continuamente desde o dia zero:
`A(Δt) = e^(−Δt/τ)`.

### Uma consequência do modelo que vale registrar

Num sistema logístico com contágio, um pulso repressivo **temporário** não
devolve o sistema à trajetória original depois de dissipado: ele atrasa a
frente, e o atraso é amplificado pelo crescimento posterior. A diferença
acumulada entre o cenário com e sem fiscalização continua crescendo mesmo
quando o campo repressivo já é nulo. Isso está coberto por teste
(`test_supressao_temporaria_desloca_a_trajetoria_permanentemente`) e merece
discussão na dissertação — é um argumento a favor de fiscalização recorrente
sobre fiscalização pontual, mas também um resultado do qual não se pode
extrapolar sem validação empírica.

---

## Instalação

1. `python tools/empacotar.py` → gera `dist/risco_garimpo-<versão>.zip`
2. No QGIS: **Complementos → Gerenciar e instalar complementos → Instalar a
   partir do ZIP**
3. As duas ferramentas aparecem em **Raster → Risco Garimpo** e na barra de
   ferramentas: *Modelo estático — gerar K…* e *Modelo dinâmico de risco…*

Requisitos: QGIS 3.22+. NumPy e GDAL já vêm com o QGIS; SciPy é usado quando
existe (acelera convolução e transformada de distância) e há alternativa em
NumPy puro quando não existe.

> **OneDrive:** se for testar apontando o QGIS para uma pasta sincronizada, a
> sincronização vai brigar com os `__pycache__`. Instale pelo `.zip`.

---

## De onde vem o K

O K é **dado de entrada** do modelo dinâmico, não algo que ele calcule. Chega
pronto — da ferramenta estática deste mesmo plugin ou de qualquer outro
processo — e é consumido como está, sem reprocessar, sem recalibrar, sem
opinar sobre como foi construído.

Essa fronteira é deliberada, e por isso ela é de arquivo. O modelo estático
responde "quanto cabe"; o dinâmico responde "onde a seguir". São perguntas
distintas, com métodos distintos, e resolvê-las no mesmo passo faria o
dinâmico herdar decisões de calibração que não são dele — e convidaria a
reprocessar o estático a cada simulação, que é exatamente o que não deve
acontecer. O material **exploratório** do estático — tabulação de variáveis
contra garimpo observado, ajuste de transformações, comparação entre métodos,
busca de pesos — vive fora deste repositório, com os dados da pesquisa.

O que o modelo dinâmico exige do K é apenas isto:

- raster contínuo, valores ≥ 0, na mesma unidade em que μ₀ é expresso;
- **sem μ embutido** — o garimpo pretérito entra pela variável de estado e
  pelo termo de contágio, nunca pela capacidade de suporte;
- CRS projetado, em metros.

Pixels com `K = 0` são exclusão permanente: nunca crescem, e o μ inicial deles
é zerado na inicialização. Quem produz o K decide se isso é o desejado.

## Primeiro teste (antes de ligar os dados reais)

`tools/gerar_dados_teste.py` cria uma província sintética de 20 × 20 km em
EPSG:31981 a 100 m — K, μ₀, máscara e um GeoPackage com 150 fragmentos de
alerta em 6 frentes e 12 autos de infração. No **Console Python** do QGIS:

```python
exec(open(r"C:\Plugin\tools\gerar_dados_teste.py", encoding="utf-8").read())
gerar_e_carregar(r"C:\Plugin\teste")
```

As camadas já aparecem no projeto. Abra o plugin, aponte os campos, período de
01/01/2025 a 31/12/2026, e execute. O registro deve dizer **"Agrupados em 6
frentes"** — é o sinal de que o pré-processamento está correto. Se falhar aqui,
o problema é o plugin; se funcionar aqui e falhar com os seus dados, o problema
é projeção, campo de data ou extensão.

---

## Uso

### Aba Insumos

| campo | o que é |
|---|---|
| `K` | raster de favorabilidade estática (capacidade de suporte) |
| `μ₀` | raster do garimpo existente na data inicial, mesma unidade de K |
| Máscara | opcional; valores > 0 delimitam a área simulada |
| Grade de trabalho | CRS e resolução da simulação — **precisa ser projetada** |
| Alertas | camada de pontos ou polígonos + campo de data |
| Fiscalização | camada de autos de infração + campo de data |
| Sub-bacias | camada de agregação da saída |

Todas as camadas são levadas à grade de trabalho antes de qualquer cálculo:
mesma projeção, mesma origem, mesma resolução. É isso que resolve de uma vez o
problema de misturar SIRGAS 2000 / UTM 21S (EPSG:31981) com coordenadas
geográficas (EPSG:4674).

A caixa "Grade resultante" mostra o tamanho em pixels e a memória estimada por
camada — vale conferir antes de rodar a província inteira a 30 m.

### Aba Parâmetros

Os padrões refletem o que foi estimado empiricamente: contágio de 380 m,
alcance repressivo de 750 m, platô de 180 dias. `r₀` é o parâmetro livre — é
ele que se calibra contra a expansão observada em um período conhecido.

### Aba Saídas

Sempre gerado: **`*_expansao_*.tif`** — Δμ, a expansão prevista no horizonte.
Essa é a camada que responde à pergunta do modelo.

Opcionais: μ final, taxa instantânea, campos de ativação e supressão
(diagnóstico), série temporal de μ, e o GeoPackage agregado por sub-bacia com
`rg_exp_ha`, `rg_mu0_ha`, `rg_muf_ha`, `rg_exp_rel`, `rg_px` e `rg_rank`.

---

## Limitações conhecidas

- **Euler explícito.** Passos longos com `r₀` alto superestimam o crescimento.
  Na dúvida, reduza o passo e compare — se o resultado muda muito, o passo era
  grande demais.
- **Janelas de eventos.** Eventos da mesma janela compartilham data de
  referência. Janela de 30 dias com platô de 180 dias é aceitável; janela de
  180 dias não seria.
- **Combinação por máximo.** Vários autos próximos não somam supressão, por
  padrão. É uma escolha conservadora e discutível; `soma_saturada` existe para
  comparação.
- **Memória.** O campo de cada janela de eventos é pré-calculado no retângulo
  em que é significativo. Muitas janelas sobre raster grande custa memória —
  use resolução de trabalho mais grossa ou janelas mais largas.
- **Calibração.** `r₀`, `α` e `F_max` não são estimados pelo plugin. O
  event-study fornece a *forma* das funções temporais e espaciais; a amplitude
  precisa ser ajustada contra dados observados.
- **Sem validação embutida.** Comparar Δμ previsto com expansão observada é
  passo externo, ainda a fazer.

---

## Desenvolvimento

```
risco_garimpo/
  core/          núcleo numérico — sem QGIS, testável como script
    numerico.py    convolução FFT e transformada de distância (+ alternativas sem SciPy)
    kernels.py     núcleo de contágio
    temporal.py    platô logístico e decaimentos
    frentes.py     agrupamento de alertas por ligação simples
    eventos.py     campos espaço-temporais de eventos datados
    model.py       integrador do modelo
    raster.py      grade de trabalho, leitura alinhada e escrita (GDAL)
    vetor.py       extração de eventos de camadas do QGIS
    agregacao.py   zonal por sub-bacia
    execucao.py    orquestração em três fases
    estatico.py    modelo estático: fuzzy gamma, saturação e nível de K
    execucao.py    orquestração do dinâmico, em três fases
    execucao_estatica.py  orquestração do estático, em três fases
  ui/            dois diálogos (.ui editáveis no Qt Designer) e seus QgsTask
tools/           geradores dos .ui, empacotador e gerador de dados de teste
tests/           testes do núcleo (rodam sem QGIS)
```

Cada ferramenta é partida em três fases por causa das regras de *thread* do
QGIS: `preparar` (thread principal, toca camadas do projeto) → `executar`
(segundo plano, só NumPy e GDAL) → `agregar`/`escrever` (thread principal,
grava).

`tests/test_contrato_ui.py` confere que cada `self.<widget>` usado nos
diálogos existe mesmo no `.ui`, e que o gerador reproduz o arquivo
versionado. Existe por causa de um erro real: uma função foi listada como
dependência sem nunca ter existido, e os testes a validavam contra dublês
montados a partir da mesma lista — a lista concordava consigo mesma.

```bash
python -m pytest tests -q          # 123 testes, sem QGIS
python tools/gerar_ui.py           # regenera o .ui do modelo dinâmico
python tools/gerar_ui_estatico.py  # regenera o .ui do modelo estático
python tools/empacotar.py          # gera o .zip instalável
```

---

## Licença

MIT — ver `LICENSE`.


# 📘 **Pipeline DECIDE (Classificação de Queries com LLMs)**

Este repositório contém a pipeline desenvolvida no âmbito do projeto **DECIDE**, com o objetivo de **replicar e estender** a metodologia descrita no estudo AIM (Artificial Intelligence Supported Development of Health Guidelines) para a análise em larga escala de queries, recorrendo a **Large Language Models (LLMs)**.

A pipeline está organizada em **duas fases principais**:

* **Parte 1** – Identificação de queries que transmitem uma pergunta explícita
* **Parte 2 + 3** – Triagem ARIA e formulação de perguntas de guideline em formato GRADE

---

## 📂 **Estrutura do Projeto**

```
agreement/                      Validação e processamento agreement
archived_results/               Resultados de runs anteriores ou descartados
logs/                           Logs detalhados de execução

results_part1/                  Outputs finais da Parte 1
results_part2_3/                Outputs finais da Parte 2 + 3
xlsx_intermed/                  Ficheiros intermédios (debug, retries, merges)

pipeline_groupA_part1.py        Pipeline da Parte 1 (classificação de queries)
pipeline_groupA_part2_3.py      Pipeline da Parte 2 + 3 (ARIA + GRADE)

queries_middle_east.xlsx        Dataset de input inicial
README.md
.env
.gitignore
```
---

## 🔹 Parte 1 — Identificação de Queries com Pergunta Explícita

A Parte 1 tem como objetivo identificar se uma query **transmite explicitamente uma pergunta**, seguindo a metodologia descrita no **Supplement Box 2A e 2B** do artigo AIM.

### ✔ Normalização e deduplicação

* Normalização Unicode e limpeza de whitespace
* Remoção de duplicados por texto
* Atribuição de um `UniqueID` estável a cada query única

---

### ✔ Classificação baseada em LLMs

Cada query única é classificada usando **três modelos**, com **duas runs independentes por modelo**:

* **Perplexity (sonar)**
* **OpenAI GPT-4o-mini**
* **Gemini 2.5 Flash**

Características principais:

* Processamento em **batches de 50 queries**
* Prompt baseado no **Supplement Box 2A**, adaptado e estendido
* Output **forçado a JSON**
* Parsing robusto para lidar com respostas parcialmente mal-formatadas
* Logs e exportação de respostas falhadas para ficheiros de debug

Para replicar fielmente o método do artigo:

> *“Different query combinations were used in each round.”*

As queries são:

* embaralhadas com `.sample(frac=1)`
* reagrupadas em batches diferentes em cada run

---

### ✔ Classificação por regras linguísticas (determinística)

Em paralelo, é aplicada uma classificação baseada em regras multilingues, inspirada no **Supplement Box 2B**, incluindo:

* palavras interrogativas (EN, ES, PT, FR, DE, NL, RU, AR, FA, TR)
* padrões sintáticos
* partículas interrogativas
* deteção de pedidos implícitos

Prompt usado (**ChatGPT 5.1**):
* [https://chatgpt.com/share 6931b30c-4208-8004-9e29-98037d1dc763](https://chatgpt.com/share/6931b30c-4208-8004-9e29-98037d1dc763)

---

### ✔ Merge final da Parte 1

Os resultados são integrados usando o `UniqueID`, garantindo:

* consistência entre runs
* tolerância a pequenas variações de texto
* ausência de conflitos

O output final da Parte 1 completo é:

```
LLM_complete_classification_PERP_GPT_GEM.xlsx
```

que contém:

* Query
* UniqueID
* Classificações LLM (runs 1 e 2)
* Classificação por regras
* Colunas originais do dataset

O output final da Parte 1 apenas com queriess unicas é:

```
LLM_class_unique_PERP_GPT_GEM.xlsx
```

---

## 🔹 Parte 2 + 3 — Triagem ARIA e Perguntas GRADE

A Parte 2 + 3 parte **exclusivamente do output da Parte 1**.

### Critério de elegibilidade

Uma query é processada se **pelo menos um método da Parte 1** indicar que transmite uma pergunta explícita:

* `Rules == YES`
  **ou**
* qualquer coluna `LLM_run* == YES`

Este critério privilegia **sensibilidade máxima**.

---

### ✔ Parte 2 — Classificação ARIA

Cada query elegível é processada **independentemente** por:

* GPT-4o
* Perplexity Sonar-Pro
* Gemini-2.5-Pro

Cada modelo classifica a query como:

* **Unrelated**
* **Background**
* **Foreground**

Acompanhado de uma justificação textual para a sua classificação.

Os prompts utilizados correspondem integralmente aos prompts longos definidos a priori.

---

### ✔ Parte 3 — Formulação de Perguntas GRADE

Para queries classificadas como **Foreground**, cada LLM gera **independentemente** uma pergunta estruturada no formato GRADE:

```
Should [Intervention] vs [Comparator] be used in [Population]?
```

Não é aplicado qualquer mecanismo de consenso ou voting:

* cada LLM é tratado como **pipeline analítico independente**
* divergências são consideradas objeto de análise

Quando a intervenção é demasiado vaga, o output é explicitamente:

```
Error: Intervention too vague.
```

---

## 📊 Outputs

* Resultados em formato **wide** (uma linha por `UniqueID`, colunas por modelo)
* Valores explícitos `N/A` distinguem claramente:

  * queries não processadas
  * queries não aplicáveis

Os resultados da Parte 2 + 3 são posteriormente **integrados no dataset completo da Parte 1** através de merge por `UniqueID`.

O resultado das queries classificadas na Parte 2 e 3:

```
PART2_3_queries_class.xlsx
```

O output final da Parte 2 e 3 para queries únicas:

```
PART2_3_final_unique.xlsx
```

---

## ⚠️ Limitações e Notas

### Robustez do parsing

* Outputs JSON podem falhar quando o modelo adiciona texto extra
* O código inclui:

  * mecanismos de fallback
  * logging detalhado
  * exportação de respostas problemáticas
* A execução nunca é interrompida por estas falhas

---

## 🔁 Reprodutibilidade

* A pipeline é determinística dado o mesmo input e respostas das APIs
* Logs, ficheiros intermédios e resultados arquivados garantem rastreabilidade total
* O uso de `UniqueID` assegura consistência entre fases

---

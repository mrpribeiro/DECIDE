
# 📘 **Pipeline DECIDE (Classificação de Queries com LLMs)**

Este repositório contém a implementação da primeira parte do trabalho DECIDE, cujo objetivo é classificar queries provenientes do Google Trends segundo o método descrito no artigo de referência (Supplement Box 2).

O pipeline aplica **três classificações por query**:

1. **Run 1 – Classificação LLM (Prompt do Supplement Box 2A)**
2. **Run 2 – Classificação LLM com batches reorganizados (para replicar o método do artigo)**
3. **Classificação baseada em regras sintáticas (Supplement Box 2B)**

As duas primeiras utilizam um modelo LLM e a terceira utiliza heurísticas linguísticas.

---

## 🧩 **1. Ambiente – Instalação**

Recomenda-se criar um ambiente dedicado:

```bash
mamba create -n decide_env python=3.10
mamba activate decide_env
```

Instalar dependências:

```bash
pip install groq pandas python-dotenv openpyxl
```

---

## 🔑 **2. API Key**

Criar um ficheiro `.env` na raiz do projeto contendo:

```
GROQ_API_KEY=INSERIR_AQUI_A_CHAVE_DA_GROQ
```

A Groq API foi usada numa fase inicial por ser gratuita e rápida. Contudo, devido ao limite diário de 100 000 tokens, pode ser necessário migrar futuramente para a API da OpenAI.

---

## 📂 **3. Estrutura do Projeto**

```
📁 DECIDE/
 ├── pipeline_groupA.py           # pipeline completo em Python
 ├── pipeline_groupA_test.ipynb   # notebook com passos testáveis
 ├── queries_middle_east.xlsx     # dataset original
 ├── pipeline_decide.log          # ficheiro de logs (gerado automaticamente)
 ├── .env                         # chave da API (não partilhar)
 └── README.md                    # este documento
```

---

## ▶️ **4. Como correr o pipeline**

### **Opção A — Script Python**

```bash
python pipeline_decide.py
```

### **Opção B — Notebook**

Abrir:

```
pipeline_decide.ipynb
```

e executar célula a célula para testar e ajustar parâmetros.

---

## 🔍 **5. Passos realizados pelo pipeline**

### ✔ **1) Ler o ficheiro `.xlsx`**

* remoção de linhas vazias
* normalização Unicode e limpeza do texto

---

### ✔ **2) Deduplicação**

* criação de um `UniqueID` por query única
* evita classificações repetidas
* garante merges seguros

---

### ✔ **3) Run 1 — Classificação LLM**

* modelo usado: **LLaMA 3.3 70B (Groq API)**
* batches de 50 queries
* prompt igual ao do Supplement Box 2A (extendido)

---

### ✔ **4) Run 2 — Classificação LLM com batches diferentes**

Para replicar fielmente o método do artigo:

> “Different query combinations were used in each round.”

* queries embaralhadas com `.sample(frac=1)`
* batches novos → contexto diferente

---

### ✔ **5) Classificação por regras sintáticas**

Baseada no Supplement Box 2B:

* identificação de palavras interrogativas (EN, ES, PT, FR, AR, FA, TR, UR)
* padrões sintáticos
* partículas interrogativas
* pontuação
* método totalmente determinístico

Resultado guardado em `rules.xlsx`.

---

### ✔ **6) Merge final**

Merge realizado por `UniqueID`, garantindo:

* consistência entre runs
* tolerância a alterações mínimas do texto
* ausência de conflitos

O ficheiro final:

```
queries_classificadas_llm.xlsx
```

contém:

* Query
* Classificação Run 1
* Classificação Run 2
* Classificação por Regras

E mantém as colunas originais do dataset.

---

## 📊 **7. Limitações da Groq API**

A Groq é:

* gratuita
* extremamente rápida
* compatível com modelos fortes (LLaMA 70B)

Mas possui um limite diário de **100 000 tokens**, o que pode impedir o processamento completo do dataset sem pausas.


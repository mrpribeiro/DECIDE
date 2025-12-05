
# 📘 **Pipeline DECIDE (Classificação de Queries com LLMs)**

Este repositório contém a implementação da primeira parte do trabalho DECIDE, cujo objetivo é classificar queries do Google Trends segundo a metodologia apresentada no artigo AIM – Artificial Intelligence Supported Development of Health Guidelines (em particular o Supplement Box 2).

O pipeline aplica **três classificações independentes por query**:

1. **Run 1 – Classificação LLM (Prompt do Supplement Box 2A)**
2. **Run 2 – Classificação LLM com batches reorganizados (para replicar o método do artigo)**
3. **Classificação baseada em regras sintáticas (Supplement Box 2B expandido)**

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
pip install groq pandas python-dotenv openpyxl perplexityai
```

---

## 🔑 **2. API Key**

Criar um ficheiro `.env` na raiz do projeto contendo:

```
PERPLEXITY_API_KEY=INSERIR_AQUI_A_CHAVE
```

Nota: Inicialmente testou-se Groq API por ser gratuita, mas devido ao limite diário de tokens, o pipeline foi migrado para Perplexity API, especificamente o modelo sonar, utilizado como LLM de classificação.

---

## 📂 **3. Estrutura do Projeto**

```
📁 DECIDE/
 ├── pipeline_groupA.py           # pipeline completo (versão final)
 ├── pipeline_groupA_teste.ipynb  # notebook para testes passo a passo
 ├── queries_middle_east.xlsx     # dataset original
 ├── df_unique.xlsx               # queries únicas com UniqueID
 ├── df_run1.xlsx                 # classificações da Run 1
 ├── df_run2.xlsx                 # classificações da Run 2
 ├── df_rules.xlsx                # classificações por regras
 ├── queries_classificadas_COMPLETO.xlsx   # output final
 ├── pipeline_run_YYYY-MM-DD.log  # logs gerados automaticamente
 └── README.md                    # este documento

```

---

## ▶️ **4. Como correr o pipeline**

### **Opção A — Script Python**

```bash
python3 pipeline_decide.py
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

* modelo usado: **sonar (Perplexity)**
* batches de 50 queries
* prompt igual ao do Supplement Box 2A (adaptado e extendido)
* output forçado a JSON
* parsing robusto para lidar com respostas não formatadas

---

### ✔ **4) Run 2 — Classificação LLM com batches diferentes**

Para replicar fielmente o método do artigo:

> “Different query combinations were used in each round.”

* queries embaralhadas com `.sample(frac=1)`
* batches novos → contexto diferente

---

### ✔ **5) Classificação por regras sintáticas**

Baseada no Supplement Box 2B:

* identificação de palavras interrogativas (EN, ES, PT, FR, DE, NL, RU, AR, FA, TR)
* padrões sintáticos
* partículas interrogativas
* detecção de pedidos implícitos
* método totalmente determinístico

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
* UniqueID
* Classificação Run 1
* Classificação Run 2
* Classificação por Regras

E mantém as colunas originais do dataset.

---

## 📊 **7. Limitações e Notas**

⚠️ Limite da Perplexity API (PRO)

O modelo sonar funciona bem, mas:
* se o utilizador não tiver plano PRO, há limites fortes
* cada batch consome tokens rapidamente
* recomendamos correr apenas uma vez sobre o dataset final

⚠️ JSON pode falhar quando o modelo inclui texto extra

O código possui:
* mecanismo de fallback
* logger + exportação de respostas falhadas para failed_batch.txt
* Isto permite depurar problemas sem interromper a execução.


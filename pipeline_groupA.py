# ===============================================================
# DECIDE – PIPELINE LLM PARA CLASSIFICAÇÃO DE QUERIES (GROQ API)
# ===============================================================
#
# INSTRUÇÕES INICIAIS:
#
# Antes de correr este script, devem criar um ambiente Python.
# Sugestão (usando conda/mamba):
#
#   > mamba create -n decide_env python=3.10
#   > mamba activate decide_env
#
# Depois instalar os pacotes necessários:
#
#   > pip install groq pandas python-dotenv openpyxl
#
# Criar um ficheiro .env na mesma pasta, contendo (seguir tutorial: https://console.groq.com/docs/quickstart):
#
#   GROQ_API_KEY= A_TUA_CHAVE_AQUI
#
# Este script:
#   1) lê o ficheiro queries_middle_east.xlsx
#   2) normaliza o texto das queries
#   3) remove duplicados e cria UniqueID para cada query única
#   4) faz RUN 1 e RUN 2 usando Groq API (modelo: llama-3.3-70b-versatile)
#   5) aplica regras multilingues baseadas no Supplement Box 2B (expandido)
#   6) faz merge usando UniqueID (evitando problemas de inconsistências)
#   7) exporta: queries_classificadas_llm.xlsx
#
# ===============================================================
# Groq API. gratuita, extremamente rápida e suporta modelos
# open-source com qualidade suficiente para a nossa tarefa.
#
# Para classificar centenas de queries em Run 1 e Run 2, a Groq oferece maior velocidade e custo zero,
# ao contrário da OpenAI API que é paga por token. Além disso, a API da Groq tem baixa latência,
# é fácil de integrar e fornece modelos modernos como LLaMA 3.3 70B que são mais do que adequados para
# a classificação simples de YES/NO usada neste projeto.
# PROBLEMA: token rate limit (limite de taxa de tokens) na Groq API.
# ===============================================================

# =======================
# IMPORTS
# =======================

import os
import time
import json
import re
import unicodedata
import pandas as pd

from dotenv import load_dotenv
from groq import Groq
from datetime import datetime

# ============================================================
# LOGGING CONFIGURATION
# ============================================================

import logging
from logging.handlers import RotatingFileHandler

log_filename = datetime.now().strftime("pipeline_run_%Y-%m-%d_%H-%M-%S.log")

# Criar handler para ficheiro (com rotação a 5MB)
log_handler = RotatingFileHandler(
    log_filename,
    maxBytes=5_000_000,
    backupCount=3,
    encoding="utf-8"
)

# Formato do log
formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

log_handler.setFormatter(formatter)

# Criar logger global
logger = logging.getLogger("DECIDE_PIPELINE")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# Adicionar também output para consola
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# =======================
# 0. CONFIGURAR GROQ API
# =======================

load_dotenv()

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    raise ValueError("Variável de ambiente GROQ_API_KEY não definida.")

client = Groq(api_key=api_key)

MODEL_NAME = "llama-3.3-70b-versatile"
BATCH_SIZE = 50   # número de queries enviadas por batch ao LLM

logger.info("Ambiente carregado e cliente Groq configurado!")


# ============================================================
# NORMALIZAÇÃO DE QUERIES
# ============================================================

def normalize_query(q):
    """Remove espaços invisíveis + normaliza Unicode + limpa whitespace."""
    if pd.isna(q):
        return ""
    q = str(q)

    q = unicodedata.normalize("NFKC", q)    # normalização Unicode
    q = q.replace("\u200b", "")             # zero-width space
    q = q.replace("\xa0", " ")              # NBSP
    q = " ".join(q.split())                 # remover múltiplos espaços / trim

    return q


# ============================================================
# 1. LER FICHEIRO EXCEL
# ============================================================

def load_queries(path="queries_middle_east.xlsx"):
    df = pd.read_excel(path)
    # df = df.tail(20)  # PARA TESTES RÁPIDOS — REMOVER ESTA LINHA PARA CORRER COM O FICHEIRO COMPLETO

    if "Query" not in df.columns:
        raise ValueError("A coluna 'Query' não existe no ficheiro.")

    # Remover linhas com Query vazia
    df = df.dropna(subset=["Query"]).copy()

    # Normalização
    df["Query"] = df["Query"].apply(normalize_query)

    # Adicionar QueryID (opcional) a cada entrada para tracking
    # df["QueryID"] = range(1, len(df)+1)
    return df


# ============================================================
# 2. REMOVER DUPLICADOS POR TEXTO
# ============================================================

def deduplicate_queries(df):
    df_unique = df[["Query"]].drop_duplicates().reset_index(drop=True)
    df_unique["UniqueID"] = range(1, len(df_unique)+1)
    return df_unique


# ============================================================
# 3. BATCHING
# ============================================================

def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]


# ============================================================
# 4. PROMPT — SUPPLEMENT BOX 2A (COM JSON)
# ============================================================

def build_prompt_for_batch(batch):
    text = (
        "We have extracted several queries from GoogleTrends from the following "
        "nine countries: Algeria, Egypt, Iran, Iraq, Morocco, Pakistan, Saudi Arabia, "
        "Turkey and the United Arab Emirates.\n\n"
        "We want to identify which queries explicitly convey a question. "
        "These queries can be in English, Portuguese, French, Spanish or any language "
        "spoken in the aforementioned countries.\n\n"
        "Below, you can find the list of queries.\n\n"
        "For each query, return a JSON array where each element has the form:\n"
        "{ \"query\": \"<query text>\", \"explicit_question\": \"YES\" or \"NO\" }\n\n"
        "Write \"YES\" only if the query explicitly conveys a question. "
        "Otherwise, write \"NO\".\n\n"
        "List of queries to be classified:\n"
    )

    for q in batch:
        text += f"- {q}\n"

    return text


# ============================================================
# 5. FUNÇÃO PARA EXTRAIR JSON DA RESPOSTA DO LLM
# ============================================================

def safe_json_extract(text):
    """
    Extrai JSON mesmo que exista texto antes/depois do array.
    Encontra o bloco entre '[' e ']' e tenta json.loads().
    """
    if not text:
        return None

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return None

    candidate = match.group(0).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


# ============================================================
# 6. FUNÇÃO PARA CLASSIFICAR UM BATCH COM O LLM
# ============================================================

def classify_batch_with_llm(batch):
    """
    Devolve lista de dicts: { "query": ..., "explicit_question": "YES"/"NO" }
    """
    prompt = build_prompt_for_batch(batch)

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0  # respostas mais determinísticas, menos aleatórias
    )

    raw = response.choices[0].message.content

    data = safe_json_extract(raw)

    if data is None:
        logger.warning(f"Falha ao extrair JSON. Resposta bruta: {raw}")
        return []

    cleaned = []
    for item in data:
        cleaned.append({
            "query": item.get("query", "").strip(),
            "explicit_question": item.get("explicit_question", "NO").strip().upper()
        })

    return cleaned


# ============================================================
# 7. FUNÇÃO: EXECUTAR UMA RUN COMPLETA (RUN 1 / RUN 2)
# ============================================================

def run_llm_classification(df_unique, run_name):
    rows = df_unique[["UniqueID", "Query"]]
    results = []

    for batch_df in chunk_list(rows, BATCH_SIZE):
        logger.info(f"{run_name} - batch com {len(batch_df)} queries...")

        batch_queries = batch_df["Query"].tolist()
        batch_ids = batch_df["UniqueID"].tolist()

        out = classify_batch_with_llm(batch_queries)

        for uid, item in zip(batch_ids, out):
            results.append({
                "UniqueID": uid,
                run_name: item["explicit_question"]
            })

        time.sleep(1)

    return pd.DataFrame(results)


# ============================================================
# 8. CLASSIFICAÇÃO POR REGRAS MULTILINGUE
# ============================================================

# QUESTION_WORDS, PATTERNS e is_question_multilingual()
# CHAT GPT 5.1 PROMPT USADO: https://chatgpt.com/share/692f15a6-b298-8004-b46d-95d5157cb5cb
# PROMPT:
# "Please generate Python with a set of rules allowing to identify:
# - keywords that typically allow to identify a sentence as a question;
# - patterns or phrasal structures implicitly suggesting that a sentence corresponds to a question.
# Please note that these rules should be able to identify questions in English, Spanish, Portuguese, French
# or in any language from the following countries: Algeria, Egypt, Iran, Iraq, Morocco, Pakistan, Saudi Arabia, Turkey and the United Arab Emirates."

QUESTION_WORDS = {
    "en": ["what","why","how","when","where","which","who","whom","whose",
           "is","are","am","do","does","did","can","could","should","would",
           "may","might","will","shall","have","has","had"],
    "es": ["qué","que","cómo","cuando","dónde","cual","cuál","quién","quien",
           "por qué","porque?","puedo","puede","pueden","debo","debe","deben"],
    "pt": ["o que","que","porquê","porque?","como","quando","onde","qual",
           "quais","quem","pode","podemos","devo","deves","devemos"],
    "fr": ["quoi","pourquoi","comment","quand","où","quel","quelle","quels",
           "que","qui","est-ce que","peux-tu","pouvez-vous"],
    "ar": ["ما","ماذا","كيف","لماذا","متى","أين","هل","كم"],
    "fa": ["چی","چه","چرا","چطور","کجا","کی","آیا"],
    "tr": ["ne","neden","nasıl","ne zaman","nerede","hangi","kim","mı","mi","mu","mü"],
    "ur": ["کیا","کیوں","کیسے","کب","کہاں","کون","آیا"]
}

ALL_QUESTION_WORDS = list({kw for kws in QUESTION_WORDS.values() for kw in kws})

PATTERNS = [
    r".*\? *$",
    r"^(\s*)(is|are|am|do|does|did|can|could|should|would|have|has|had)\b",
    r"^(\s*)est-ce que\b",
    r"\b(mı|mi|mu|mü)\?$",
    r"^(.*?)\b(há|há alguma|será que)\b",
    r"^(.*?)\b(acaso|será que)\b",
    r"^(\s*)آیا\b",
    r"^(\s*)هل\b",
]


def is_question_multilingual(text):
    text = (text or "").strip().lower()

    if text.endswith("?"):
        return True

    for kw in ALL_QUESTION_WORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", text):
            return True

    for pattern in PATTERNS:
        if re.search(pattern, text):
            return True

    return False


def apply_multilingual_rules(df_unique):
    df_unique["Rules"] = df_unique["Query"].apply(
        lambda x: "YES" if is_question_multilingual(x) else "NO"
    )
    return df_unique


# ============================================================
# 9. MERGE FINAL
# ============================================================

def merge_results(df_original, df_unique, df_run1, df_run2):
    temp = df_unique.merge(df_run1, on="UniqueID", how="left")
    temp = temp.merge(df_run2, on="UniqueID", how="left")

    # Merge final com df original usando Query (agora seguro)
    df_final = df_original.merge(
        temp[["Query", "Rules", "LLM_run1", "LLM_run2"]],
        on="Query",
        how="left"
    )

    return df_final


# ============================================================
# 10. MAIN
# ============================================================

def main():
    logger.info("=== 1) A LER O FICHEIRO ===")
    df = load_queries()
    logger.info(f"Total de linhas: {len(df)}")

    logger.info("=== 2) DEDUPLICAÇÃO ===")
    df_unique = deduplicate_queries(df)
    logger.info(f"Queries únicas: {len(df_unique)}")
    df_unique.to_excel("df_unique.xlsx", index=False)  # TEMP

    logger.info("=== 3) RUN 1 ===")
    df_run1 = run_llm_classification(df_unique, "LLM_run1")
    df_run1.to_excel("df_run1_int.xlsx", index=False)  # TEMP

    logger.info("\n=== 4) RUN 2 (com batches diferentes) ===")
    df_unique_shuffled = df_unique.sample(frac=1, random_state=None).reset_index(drop=True)  # sample() função pandas usada para escolher linhas de forma aleatória
    df_run2 = run_llm_classification(df_unique_shuffled, "LLM_run2")

    # logger.info("=== 4) RUN 2 ===")
    # df_run2 = run_llm_classification(df_unique, "LLM_run2")
    # df_run2.to_excel("df_run2_int.xlsx", index=False)  # TEMP

    logger.info("=== 5) CLASSIFICAÇÃO POR REGRAS ===")
    df_unique = apply_multilingual_rules(df_unique)
    df_unique.to_excel("df_rules.xlsx", index=False)  # TEMP

    logger.info("=== 6) MERGE FINAL ===")
    df_final = merge_results(df, df_unique, df_run1, df_run2)
    logger.info(f"Merge final concluído: {len(df_final)} linhas totais.")

    logger.info("=== 7) EXPORTAR ===")
    output = "queries_classificadas_llm.xlsx"
    df_final.to_excel(output, index=False)
    logger.info(f"Concluído! Ficheiro gravado como: {output}\n")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    logger.info("====================================================")
    logger.info("🚀 Início da execução do pipeline DECIDE")
    logger.info("====================================================")
    start_timestamp = datetime.now()
    start_time = time.time()

    main()

    end_time = time.time()
    elapsed = int(end_time - start_time)
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60

    logger.info("====================================================")
    logger.info("🏁 Fim da execução do pipeline DECIDE")
    logger.info(f"⏱ Tempo total: {hours:02d}:{minutes:02d}:{seconds:02d}")
    logger.info("====================================================")
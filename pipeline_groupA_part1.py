# ==========================================================================
# DECIDE – PIPELINE LLM PARA CLASSIFICAÇÃO DE QUERIES
#                 Perplexity (sonar) + OpenAI GPT-4o-mini + Gemini 2.5 Flash
# ==========================================================================
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
#   > pip install pandas python-dotenv openpyxl perplexityai openai google-generativeai
#
# Criar um ficheiro .env na mesma pasta, contendo:
#
#   PERPLEXITY_API_KEY= "A_TUA_CHAVE_AQUI"
#   OPENAI_API_KEY= "A_TUA_CHAVE_OPENAI_AQUI"
#   GEMINI_API_KEY= "A_TUA_CHAVE_GEMINI_AQUI"
#
# Este script:
#   1) lê o ficheiro queries_middle_east.xlsx
#   2) normaliza o texto das queries
#   3) remove duplicados e cria UniqueID para cada query única
#   4) faz RUN 1 e RUN 2 usando:
#        - Perplexity (sonar)  → colunas LLM_run1_Perplexity / LLM_run2_Perplexity
#        - OpenAI GPT-4o-mini  → colunas LLM_run1_GPT        / LLM_run2_GPT
#        - Gemini 2.5 Flash    → colunas LLM_run1_Gemini     / LLM_run2_Gemini
#   5) aplica regras multilingues baseadas no Supplement Box 2B (expandido)
#   6) faz merge usando UniqueID (evitando problemas de inconsistências)
#   7) exporta: queries_classificadas_COMPLETO.xlsx

# =======================
# FLAGS PARA CADA LLM
# =======================
USE_PERPLEXITY = True  # muda para True se quiseres usar Perplexity (sonar)
USE_GPT = True        # muda para True se quiseres usar OpenAI GPT-4o-mini
USE_GEMINI = True     # muda para True se quiseres usar Gemini 2.5 Flash

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
from datetime import datetime

# Perplexity
from perplexity import Perplexity

# OpenAI (GPT-4o-mini)
from openai import OpenAI

# Google Gemini
import google.generativeai as genai

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

# ============================
# 0. CONFIGURAR APIs + .env
# ============================

load_dotenv()

# --- Perplexity ---
perplexity_client = None
PERPLEXITY_MODEL_NAME = "sonar"
BATCH_SIZE = 50

if USE_PERPLEXITY:
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
    if not perplexity_key:
        raise ValueError("Variável de ambiente PERPLEXITY_API_KEY não definida.")
    perplexity_client = Perplexity(api_key=perplexity_key)
    logger.info("Cliente Perplexity SDK configurado (sonar).")

# --- OpenAI GPT-4o-mini ---
gpt_client = None
GPT_MODEL_NAME = "gpt-4o-mini"

if USE_GPT:
    gpt_api_key = os.environ.get("OPENAI_API_KEY")
    if not gpt_api_key:
        raise ValueError("Variável de ambiente OPENAI_API_KEY não definida.")
    gpt_client = OpenAI(api_key=gpt_api_key)
    logger.info("Cliente OpenAI GPT configurado (gpt-4o-mini).")

# --- Gemini 2.5 Flash ---
gemini_model = None
GEMINI_MODEL_NAME = "gemini-2.5-flash"

if USE_GEMINI:
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_api_key:
        raise ValueError("Variável de ambiente GEMINI_API_KEY não definida.")
    genai.configure(api_key=gemini_api_key)
    gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)
    logger.info("Cliente Gemini configurado (gemini-2.5-flash).")


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
    # df = df.tail(75)  # PARA TESTES RÁPIDOS

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
        "We have extracted several queries from GoogleTrends.\n\n"
        "We want to identify what are the queries which explicitly convey a question. "
        "These queries can be in different languages, including English, Arabic, "
        "French, Persian (Farsi), Turkish, Russian, Spanish, German or Dutch.\n\n"
        "Below, you can find the list of queries.\n\n"
        "Return ONLY a JSON array, with no explanations or additional text. "
        "Each element must have the form:\n"
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
    if not text:
        return None

    cleaned = text.strip()

    # 1) Strip leading markdown fence like ```json
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)

    # 2) Strip trailing fence ```
    if cleaned.endswith("```"):
        cleaned = re.sub(r"\s*```$", "", cleaned)

    # 3) Extract the first JSON array
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return None

    candidate = match.group(0).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


# =======================================
# 6A. CLASSIFICAR UM BATCH COM PERPLEXITY
# =======================================

def classify_batch_perplexity(batch):
    """Classificação de um batch com Perplexity (sonar)."""
    prompt = build_prompt_for_batch(batch)

    response = perplexity_client.chat.completions.create(
        model=PERPLEXITY_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0  # respostas mais determinísticas, menos aleatórias
    )

    raw = response.choices[0].message.content
    data = safe_json_extract(raw)

    if data is None:
        logger.error("Falha ao extrair JSON (Perplexity); a guardar resposta bruta para debug.")
        with open("failed_batch_perplexity.txt", "a", encoding="utf-8") as f:
            f.write(raw + "\n\n" + "="*80 + "\n\n")
        return []

    cleaned = []
    for item in data:
        cleaned.append({
            "query": item.get("query", "").strip(),
            "explicit_question": item.get("explicit_question", "NO").strip().upper()
        })

    return cleaned


# ============================================================
# 6B. CLASSIFICAR UM BATCH – GPT-4o-mini
# ============================================================

def classify_batch_gpt(batch):
    """Classificação de um batch com OpenAI GPT-4o-mini."""
    prompt = build_prompt_for_batch(batch)

    response = gpt_client.chat.completions.create(
        model=GPT_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    raw = response.choices[0].message.content
    data = safe_json_extract(raw)

    if data is None:
        logger.error("Falha ao extrair JSON (GPT); a guardar resposta bruta para debug.")
        with open("failed_batch_gpt.txt", "a", encoding="utf-8") as f:
            f.write(raw + "\n\n" + "="*80 + "\n\n")
        return []

    cleaned = []
    for item in data:
        cleaned.append({
            "query": item.get("query", "").strip(),
            "explicit_question": item.get("explicit_question", "NO").strip().upper()
        })

    return cleaned


# ============================================================
# 6C. CLASSIFICAR UM BATCH – GEMINI 2.5 FLASH
# ============================================================

def classify_batch_gemini(batch):
    """Classificação de um batch com Gemini 2.5 Flash."""
    prompt = build_prompt_for_batch(batch)

    response = gemini_model.generate_content(prompt)
    raw = response.text

    data = safe_json_extract(raw)

    if data is None:
        logger.error("Falha ao extrair JSON (Gemini); a guardar resposta bruta para debug.")
        with open("failed_batch_gemini.txt", "a", encoding="utf-8") as f:
            f.write(raw + "\n\n" + "="*80 + "\n\n")
        return []

    cleaned = []
    for item in data:
        cleaned.append({
            "query": item.get("query", "").strip(),
            "explicit_question": item.get("explicit_question", "NO").strip().upper()
        })

    return cleaned


# ============================================================
# 7. EXECUTAR UMA RUN COMPLETA PARA QUALQUER LLM (RUN 1 / RUN 2)
# ============================================================

def run_llm_classification(df_unique, run_name, classify_fn):
    """
    df_unique: DataFrame com colunas [UniqueID, Query]
    run_name: nome da coluna a criar (ex: 'LLM_run1_Perplexity')
    classify_fn: função que recebe uma lista de queries e devolve:
                 [ { 'query': ..., 'explicit_question': 'YES'/'NO' }, ... ]
    """

    rows = df_unique[["UniqueID", "Query"]]
    results = []

    for batch_df in chunk_list(rows, BATCH_SIZE):
        logger.info(f"{run_name} - batch com {len(batch_df)} queries...")

        batch_queries = batch_df["Query"].tolist()
        batch_ids = batch_df["UniqueID"].tolist()

        out = classify_fn(batch_queries)

        # zip assume que o LLM devolve N items pelo menos para as N queries
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
# CHAT GPT 5.1 PROMPT USADO: https://chatgpt.com/share/6931b30c-4208-8004-9e29-98037d1dc763
# PROMPT:
# "Please generate Python code with a set of rules allowing to identify:
# - keywords that typically allow to identify a sentence as a question;
# - patterns or phrasal structures implicitly suggesting that a sentence corresponds to a question.
# Please note that these rules should be able to identify questions in different languages,
# including English, Arabic, French, Persian (Farsi), Turkish, Russian, Spanish, German or Dutch"

# 1) Interrogative keywords
QUESTION_KEYWORDS = [
    # English
    "what", "when", "where", "why", "how", "who", "whom", "which",
    "do", "did", "does", "are", "is", "can", "could", "should", "would",

    # French
    "quoi", "quand", "où", "pourquoi", "comment", "qui", "lequel",
    "est-ce", "peux-tu", "pourrais-tu",

    # Spanish
    "qué", "cuándo", "dónde", "por qué", "cómo", "quién", "cuál", "puedes", "podrías",

    # German
    "was", "wann", "wo", "warum", "wie", "wer", "welche", "kann", "könnte",

    # Dutch
    "wat", "wanneer", "waar", "waarom", "hoe", "wie", "welke", "kan", "zou",

    # Russian
    "что", "когда", "где", "почему", "как", "кто", "который", "может", "могли бы",

    # Arabic
    "ماذا", "متى", "أين", "لماذا", "كيف", "من", "هل", "أيمكن",

    # Persian
    "چه", "کی", "کجا", "چرا", "چطور", "کیست", "آیا",

    # Turkish
    "ne", "ne zaman", "nerede", "neden", "nasıl", "kim", "hangi", "mı", "mi", "mu", "mü"
]

# 2) Structural patterns
STRUCTURAL_PATTERNS = [
    r".*\?\s*$",                                  # explicit '?'
    r"^(can|could|should|would|do|did|does)\b",   # English inversion
    r"^(is|are|was|were|am)\b",                   # English BE inversion
    r".*\b(mı|mi|mu|mü)\?$",                      # Turkish question particle
    r"^[^.!?]*\b(est-ce que)\b",                  # French
    r"^[^.!?]*\b(هل)\b",                          # Arabic
    r"^[^.!?]*\b(آیا)\b",                         # Persian
]

# 3) Implicit question markers
IMPLICIT_PATTERNS = [
    r"could you\b.*",
    r"would you\b.*",
    r"can you\b.*",
    r"please explain\b.*",
    r"i wonder if\b.*",
    r"i would like to know\b.*"
]


def is_question_multilingual(sentence: str) -> bool:
    if not sentence:
        return False

    s = sentence.strip().lower()

    # Rule 1: punctuation
    if re.search(r".*\?\s*$", s):
        return True

    # Rule 2: structural patterns
    for p in STRUCTURAL_PATTERNS:
        if re.search(p, s):
            return True

    # Rule 3: keyword-based detection
    for kw in QUESTION_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", s):
            return True

    # Rule 4: implicit patterns
    for p in IMPLICIT_PATTERNS:
        if re.search(p, s):
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

def merge_results(df_original, df_unique, run_dfs):
    """
    df_original: DataFrame completo (com duplicados)
    df_unique: DataFrame de queries únicas (UniqueID, Query, Rules, ...)
    run_dfs: lista de DataFrames com colunas [UniqueID, <run_name>]
    """
    temp = df_unique.copy()

    for df_run in run_dfs:
        if df_run is not None and not df_run.empty:
            temp = temp.merge(df_run, on="UniqueID", how="left")

    df_final = df_original.merge(temp, on="Query", how="left")
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

    # # Criar uma amostra aleatória de n linhas (random_state para reprodutibilidade = seed, gera sempre a mesma amostra)
    # n = 246
    # df_sample = df_unique.sample(n=n, random_state=42)
    # df_sample.to_excel("df_sample.xlsx", index=False)
    # logger.info(f"Criada amostra aleatória de {n} queries, guardada em df_sample.xlsx")

    run_dfs = []

    # -----------------------
    # 3) Perplexity (2 runs)
    # -----------------------
    if USE_PERPLEXITY:
        logger.info("=== 3) RUN 1 - Perplexity ===")
        df_run1_perp = run_llm_classification(df_unique, "LLM_run1_Perplexity", classify_batch_perplexity)
        df_run1_perp.to_excel("df_run1_perplexity.xlsx", index=False)
        run_dfs.append(df_run1_perp)

        logger.info("=== 4) RUN 2 - Perplexity (batches diferentes) ===")
        df_unique_shuffled = df_unique.sample(frac=1, random_state=None).reset_index(drop=True)
        df_run2_perp = run_llm_classification(df_unique_shuffled, "LLM_run2_Perplexity", classify_batch_perplexity)
        df_run2_perp.to_excel("df_run2_perplexity.xlsx", index=False)
        run_dfs.append(df_run2_perp)
    else:
        df_run1_perp = None
        df_run2_perp = None

    # -----------------------
    # 5) GPT-4o-mini (2 runs)
    # -----------------------
    if USE_GPT:
        logger.info("=== 5) RUN 1 - GPT-4o-mini ===")
        df_run1_gpt = run_llm_classification(df_unique, "LLM_run1_GPT", classify_batch_gpt)
        df_run1_gpt.to_excel("df_run1_gpt.xlsx", index=False)
        run_dfs.append(df_run1_gpt)

        logger.info("=== 6) RUN 2 - GPT-4o-mini (batches diferentes) ===")
        df_unique_shuffled_gpt = df_unique.sample(frac=1, random_state=None).reset_index(drop=True)
        df_run2_gpt = run_llm_classification(df_unique_shuffled_gpt, "LLM_run2_GPT", classify_batch_gpt)
        df_run2_gpt.to_excel("df_run2_gpt.xlsx", index=False)
        run_dfs.append(df_run2_gpt)
    else:
        df_run1_gpt = None
        df_run2_gpt = None

    # -----------------------
    # 7) Gemini 2.5 Flash (2 runs)
    # -----------------------
    if USE_GEMINI:
        logger.info("=== 7) RUN 1 - Gemini 2.5 Flash ===")
        df_run1_gem = run_llm_classification(df_unique, "LLM_run1_Gemini", classify_batch_gemini)
        df_run1_gem.to_excel("df_run1_gemini.xlsx", index=False)
        run_dfs.append(df_run1_gem)

        logger.info("=== 8) RUN 2 - Gemini 2.5 Flash (batches diferentes) ===")
        df_unique_shuffled_gem = df_unique.sample(frac=1, random_state=None).reset_index(drop=True)
        df_run2_gem = run_llm_classification(df_unique_shuffled_gem, "LLM_run2_Gemini", classify_batch_gemini)
        df_run2_gem.to_excel("df_run2_gemini.xlsx", index=False)
        run_dfs.append(df_run2_gem)
    else:
        df_run1_gem = None
        df_run2_gem = None

    # -----------------------
    # 9) CLASSIFICAÇÃO POR REGRAS
    # -----------------------
    logger.info("=== 9) CLASSIFICAÇÃO POR REGRAS ===")
    df_unique = apply_multilingual_rules(df_unique)
    df_unique.to_excel("df_rules.xlsx", index=False)  # TEMP

    # -----------------------
    # 10) MERGE FINAL
    # -----------------------
    logger.info("=== 10) MERGE FINAL ===")
    df_final = merge_results(df, df_unique, run_dfs)

    # # --- COLUNAS FIXAS WANTED ---
    # base_cols = ["UniqueID", "Query", "Rules"]

    # # --- DETECTAR TODAS AS COLUNAS DE RUN (qualquer modelo) ---
    # llm_cols = [c for c in df_final.columns if c.startswith("LLM_run")]

    # # --- COLUNAS FINAIS A EXPORTAR ---
    # desired_cols = base_cols + llm_cols

    # # --- FILTRAR DATAFRAME ---
    # df_final = df_final[[c for c in desired_cols if c in df_final.columns]]

    logger.info(f"Merge final concluído: {len(df_final)} linhas totais.")

    logger.info("=== 11) EXPORTAR ===")
    output = "LLM_complete_classification_PERP_GPT_GEM.xlsx"
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

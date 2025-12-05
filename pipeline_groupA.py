# ==========================================================================
# DECIDE – PIPELINE LLM PARA CLASSIFICAÇÃO DE QUERIES (SONAR PERPLEXITY API)
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
#
#   > pip install groq pandas python-dotenv openpyxl perplexityai
#
# Criar um ficheiro .env na mesma pasta, contendo (seguir tutorial: https://docs.perplexity.ai/getting-started/quickstart):
#
#   PERPLEXITY_API_KEY= "A_TUA_CHAVE_AQUI"
#
# Este script:
#   1) lê o ficheiro queries_middle_east.xlsx
#   2) normaliza o texto das queries
#   3) remove duplicados e cria UniqueID para cada query única
#   4) faz RUN 1 e RUN 2 usando perplexity API (modelo: sonar)
#   5) aplica regras multilingues baseadas no Supplement Box 2B (expandido)
#   6) faz merge usando UniqueID (evitando problemas de inconsistências)
#   7) exporta: queries_classificadas_llm.xlsx

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
from perplexity import Perplexity

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
# 0. CONFIGURAR PERPLEXITY API
# ============================
load_dotenv()

api_key = os.environ.get("PERPLEXITY_API_KEY")
if not api_key:
    raise ValueError("Variável de ambiente PERPLEXITY_API_KEY não definida.")

client = Perplexity(api_key=api_key)

MODEL_NAME = "sonar"   # ou "sonar-pro"
BATCH_SIZE = 50

logger.info("Ambiente carregado e cliente Perplexity SDK configurado!")


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

def load_queries(path="queries_middle_east_teste.xlsx"):
    df = pd.read_excel(path)
    # df = df.tail(100)  # PARA TESTES RÁPIDOS

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
        logger.error("Falha ao extrair JSON; a guardar resposta bruta para debug.")
        with open("failed_batch.txt", "a", encoding="utf-8") as f:
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

def merge_results(df_original, df_unique, df_run1, df_run2):
    temp = df_unique.merge(df_run1, on="UniqueID", how="left")
    temp = temp.merge(df_run2, on="UniqueID", how="left")

    # Merge final com df original usando Query (agora seguro)
    df_final = df_original.merge(
        temp[["UniqueID","Query", "Rules", "LLM_run1", "LLM_run2"]],
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
    # df_unique.to_excel("df_unique.xlsx", index=False)  # TEMP

    # # Criar uma amostra aleatória de n linhas (random_state para reprodutibilidade = seed, gera sempre a mesma amostra)
    # n = 246
    # df_sample = df_unique.sample(n=n, random_state=42)
    # df_sample.to_excel("df_sample.xlsx", index=False)
    # logger.info(f"Criada amostra aleatória de {n} queries, guardada em df_sample.xlsx")

    logger.info("=== 3) RUN 1 ===")
    df_run1 = run_llm_classification(df_unique, "LLM_run1")
    df_run1.to_excel("df_run1_test.xlsx", index=False)  # TEMP

    logger.info("\n=== 4) RUN 2 (com batches diferentes) ===")
    df_unique_shuffled = df_unique.sample(frac=1, random_state=None).reset_index(drop=True)  # sample() função pandas usada para escolher linhas de forma aleatória
    df_run2 = run_llm_classification(df_unique_shuffled, "LLM_run2")
    df_run2.to_excel("df_run2_test.xlsx", index=False)  # TEMP

    logger.info("=== 5) CLASSIFICAÇÃO POR REGRAS ===")
    df_unique = apply_multilingual_rules(df_unique)
    df_unique.to_excel("df_rules_test.xlsx", index=False)  # TEMP

    logger.info("=== 6) MERGE FINAL ===")
    df_final = merge_results(df, df_unique, df_run1, df_run2)
    logger.info(f"Merge final concluído: {len(df_final)} linhas totais.")

    logger.info("=== 7) EXPORTAR ===")
    output = "queries_classificadas_llm_test.xlsx"
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
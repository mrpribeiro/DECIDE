# ==========================================================================
# DECIDE – PARTE 2 + 3 (GPT-4o, OpenAI API) – EXTRA
# ==========================================================================
#
# Este script parte do OUTPUT da PARTE 1:
#   - ficheiro de input (exemplo): LLM_complete_classification_PERP_GPT_GEM.xlsx
#   - colunas necessárias (mínimo): UniqueID, Query, Rules, LLM_run*...
#
# Objetivos:
#   ✅ PARTE 2 (PROMPT_PARTE2_SYSTEM):
#       - Classificar cada Query elegível em:
#         ARIA_Category: [Unrelated | Background | Foreground]
#         ARIA_Reasoning: justificação textual
#
#   ✅ PARTE 3 (PROMPT_PARTE3_SYSTEM):
#       - Apenas se ARIA_Category == "Foreground"
#       - Gerar uma Guideline Question em formato GRADE:
#           "Should [Intervention] vs [Comparator] be used in [Population]?"
#       - OU, se a intervenção for demasiado vaga:
#           "Error: Intervention too vague."
#
# Critério de elegibilidade (parte 2):
#   - Só processamos UNIQUE QUERIES cujo UniqueID tenha sido classificado como
#     "explicit question" na PARTE 1:
#       Rules == "YES"  OU  qualquer coluna LLM_run* == "YES"
#
# Outputs:
#   1) PART2_3_unique_results.xlsx
#       - 1 linha por UniqueID (queries únicas)
#       - colunas: UniqueID, Query, ARIA_Category, ARIA_Reasoning, GRADE_Question
#
#   2) PART2_3_full_dataset.xlsx
#       - mesmas linhas e colunas do ficheiro de input
#       - + colunas novas: ARIA_Category, ARIA_Reasoning, GRADE_Question
#
# Pré-requisitos:
#   Ambiente Python (exemplo com mamba/conda):
#
#       mamba create -n decide_env python=3.10
#       mamba activate decide_env
#
#   Pacotes:
#
#       pip install pandas python-dotenv openpyxl openai
#
#   Ficheiro .env na raiz:
#
#       OPENAI_API_KEY="A_TUA_CHAVE_AQUI"
#
# ==========================================================================

import os
import time
import re
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

# ============================
# CONFIGURAÇÕES GERAIS
# ============================

INPUT_FILE = "LLM_complete_classification_PERP_GPT_GEM.xlsx"
UNIQUE_OUTPUT_FILE = "PART2_3_unique_results.xlsx"
FULL_OUTPUT_FILE = "PART2_3_full_dataset.xlsx"

GPT_MODEL = "gpt-4o"
SLEEP_BETWEEN_CALLS = 0.3  # segundos entre chamadas à API (prudência)


# ============================
# LOGGING
# ============================

log_filename = datetime.now().strftime("pipeline_part2_3_%Y-%m-%d_%H-%M-%S.log")

log_handler = RotatingFileHandler(
    log_filename,
    maxBytes=5_000_000,
    backupCount=3,
    encoding="utf-8"
)

formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

log_handler.setFormatter(formatter)

logger = logging.getLogger("DECIDE_PART2_3")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


# ============================
# OPENAI CLIENT (GPT-4o)
# ============================

load_dotenv()

api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise ValueError("Variável de ambiente OPENAI_API_KEY não definida.")

client = OpenAI(api_key=api_key)

logger.info("Ambiente carregado e cliente OpenAI (GPT-4o) configurado!")


# ============================
# PROMPTS – PARTE 2 & 3
# ============================

PROMPT_PARTE2_SYSTEM = """
You are an expert Clinical AI Assistant specialized in the ARIA (Allergic Rhinitis and its Impact on Asthma) 2024 guidelines.
Your task is to triage user queries to determine if they can be formulated into specific GRADE guideline questions.

The user queries may appear in different languages, including English, Arabic, French, Persian (Farsi), Turkish, Russian, Spanish, German, or Dutch.

You are bound by the scope of the ARIA 2024 guidelines (Pharmacological and Non-pharmacological management).

1. IN SCOPE Domains:
- Conditions: Allergic Rhinitis (AR), Asthma comorbidities, Allergic Conjunctivitis.
- Interventions: Pharmacotherapy (Antihistamines, INCS, LTRA, Decongestants), Immunotherapy (AIT), Non-pharmacological (avoidance, saline).

2. OUT OF SCOPE (Strict Exclusion):
- Nutritional Interventions: Diet, supplements, vitamins.
- Complementary/Alternative Medicine: Homeopathy, acupuncture, herbal remedies (e.g., "bee pollen" is Unrelated).
- General Medical: Conditions unrelated to respiratory allergy (e.g., hypertension).

Classification Categories:
1. UNRELATED
   - The query falls into the "OUT OF SCOPE" list above or is completely disconnected from the clinical domain.

2. BACKGROUND (Learning / Vague Mode)
   - Definition: Questions seeking definitions, pathophysiology, epidemiology, or general knowledge.
   - Vague Interventions: If the user asks generally about "treatment" or "medication" WITHOUT specifying a drug class or intervention type
     (e.g., "What is the treatment for AR?"), classify as BACKGROUND.
   - Symptoms/Etiology: Queries like "Is hayfever contagious?" or "Can hayfever cause headaches?".

3. FOREGROUND (Action Mode)
   - Definition: Questions conveying a clinical action regarding a specific intervention or class of interventions.
   - Criteria: The query allows the formulation of a recommendation: "Should [Specific Intervention] vs [Comparator] be used?".
   - Includes:
       - Efficacy ("Does X work?").
       - Safety/Side Effects ("Is X safe?" / "Side effects of X").
       - Comparison ("Is X better than Y?").
       - Dosing/Administration ("Can I take two pills?").

Decision Logic:
1. Scope Check: Is it about bee pollen, homeopathy, or non-allergy topics? -> Unrelated.
2. Specificity Check: Does the query mention a specific drug (e.g., "Avamys", "Ibuprofen") or a drug class (e.g., "Antihistamine", "Nasal Spray")?
   - If NO (e.g., "how to cure rhinitis") -> Background.
   - If YES -> Proceed to intent.
3. Intent Check: Does it imply a decision about using that specific intervention? -> Foreground.

Output Format (STRICT):
Category: [Unrelated | Background | Foreground]
Reasoning: [Brief justification based on specificity and scope]
"""

PROMPT_PARTE3_SYSTEM = """
You are an expert Guideline Methodologist using the GRADE approach for ARIA 2024.
Your task is to translate natural language queries into structured Guideline Questions
in the format: "Should [Intervention] vs [Comparator] be used in [Population]?".

User queries may be expressed in multiple languages,
including English, Arabic, French, Persian (Farsi), Turkish, Russian,
Spanish, German, or Dutch. Interpret them consistently regardless of language.

Logic for Transformation (Based on ARIA Methodology):

1. Population (P):
- Default: "patients with allergic rhinitis" (or specify 'ocular symptoms', 'asthma' if mentioned).

2. Intervention (I) & Terminology Mapping:
- Map lay terms to ARIA classes:
    - "Allergy pill" -> "Oral H1-antihistamines".
    - "Nasal spray" -> "Intranasal corticosteroids" or "Intranasal antihistamines" (context dependent).
    - "Ibuprofen" -> "Nonsteroidal anti-inflammatory drugs (NSAIDs)".
    - "Fluticasone" -> "Intranasal glucocorticosteroids".

3. Comparator (C) Rules:
- Efficacy/Safety Queries:
    If the user asks "Does X work?" or "Side effects of X", the comparator is "no treatment".
- Comparative Queries:
    If the user asks "Best X", the comparison is "other individual [class of X]".
- Dosing Queries:
    If asking about quantity (e.g., "two pills"), compare "more than one" vs "one single".

Few-Shot Examples (Patterns):

Example 1:
Query: "antihistamine for runny nose"
Reasoning: User wants effective antihistamines for rhinorrhoea.
Output: "Should H1-antihistamines vs no treatment be used for the treatment of allergic rhinitis?"

Example 2:
Query: "best nasal decongestant"
Reasoning: User implies comparison between agents to find the superior one.
Output: "Should any specific individual intranasal decongestant vs other individual intranasal decongestants be used for the treatment of allergic rhinitis?"

Example 3:
Query: "fluticasone nasal spray side effects"
Reasoning: Safety query.
Output: "Should intranasal glucocorticosteroids vs no treatment be used for the treatment of allergic rhinitis?"

Example 4:
Query: "does ibuprofen help with nasal congestion"
Reasoning: Map specific drug to class (NSAIDs) and impute comparator.
Output: "Should nonsteroidal anti-inflammatory drugs vs no treatment be used for the treatment of allergic rhinitis?"

Example 5:
Query: "can i take two antihistamines"
Reasoning: Dosing frequency question.
Output: "Should more than one daily oral H1-antihistamine vs one single daily oral H1-antihistamine be used for the treatment of allergic rhinitis?"

Output Rules (STRICT):
- Output ONLY the structured question.
- If the query lacks a specific intervention to map (e.g., "rhinitis medication"),
  output exactly: "Error: Intervention too vague."
"""


# ============================
# FUNÇÕES AUXILIARES GPT
# ============================

def call_gpt(system_msg: str, user_msg: str) -> str:
    """
    Wrapper simples para chamar o GPT-4o com mensagens system + user.
    Devolve o conteúdo textual (string) já stripado.
    """
    try:
        resp = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0
        )
        text = resp.choices[0].message.content
        if text is None:
            return ""
        return text.strip()
    except Exception as e:
        logger.error(f"Erro na chamada à API GPT-4o: {e}")
        return ""


def parse_parte2_output(text: str):
    """
    Espera algo do género:
        Category: Foreground
        Reasoning: ...
    Faz parsing robusto e devolve (category, reasoning).
    """
    if not text:
        return "", ""

    # remove aspas exteriores, se vier tipo "..." ou '...'
    text_clean = text.strip().strip('"').strip("'")

    cat = ""
    reason = ""

    # Procurar linhas tipo "Category: X"
    cat_match = re.search(r"Category:\s*(.+)", text_clean, re.IGNORECASE)
    if cat_match:
        cat = cat_match.group(1).strip()

    reason_match = re.search(r"Reasoning:\s*(.+)", text_clean, re.IGNORECASE | re.DOTALL)
    if reason_match:
        reason = reason_match.group(1).strip()

    # Se não encontrou nada, fallback: tudo dentro de reasoning
    if not cat and not reason:
        reason = text_clean

    # Normalizar categoria
    cat_norm = cat.lower()
    if "unrelated" in cat_norm:
        cat = "Unrelated"
    elif "background" in cat_norm:
        cat = "Background"
    elif "foreground" in cat_norm:
        cat = "Foreground"

    return cat, reason


def classify_parte2(query: str):
    """
    Aplica o PROMPT_PARTE2_SYSTEM a uma query e devolve:
        (ARIA_Category, ARIA_Reasoning)
    """
    user_msg = f"Query to classify:\n{query}"
    raw = call_gpt(PROMPT_PARTE2_SYSTEM, user_msg)
    category, reasoning = parse_parte2_output(raw)
    return category, reasoning


def build_grade_question_parte3(query: str) -> str:
    """
    Aplica o PROMPT_PARTE3_SYSTEM e devolve a string final.
    Pode ser:
        - "Should ... be used ...?"
        - "Error: Intervention too vague."
    """
    user_msg = f"Query to transform into a GRADE question:\n{query}"
    raw = call_gpt(PROMPT_PARTE3_SYSTEM, user_msg)
    if not raw:
        return ""

    # remover aspas exteriores, se existirem
    q = raw.strip().strip('"').strip("'")
    return q


# ============================
# PIPELINE PRINCIPAL
# ============================

def main():
    logger.info("=== 1) A LER INPUT ===")
    df_full = pd.read_excel(INPUT_FILE)
    # df_full = df_full.tail(200)  # PARA TESTES RÁPIDOS
    logger.info(f"Ficheiro lido: {INPUT_FILE} com {len(df_full)} linhas.")

    if "UniqueID" not in df_full.columns or "Query" not in df_full.columns:
        raise ValueError("Necessário ter colunas 'UniqueID' e 'Query' no ficheiro de input.")

    # Detectar colunas LLM_run* da parte 1
    llm_cols = [c for c in df_full.columns if c.startswith("LLM_run")]
    if "Rules" not in df_full.columns:
        logger.warning("Coluna 'Rules' não encontrada - será ignorado o critério baseado em Rules.")
        rules_col = None
    else:
        rules_col = "Rules"

    # ============================
    # 2) REDUZIR A QUERIES ÚNICAS
    # ============================
    # Uma linha por UniqueID
    df_unique = df_full.drop_duplicates(subset=["UniqueID"]).copy()
    logger.info(f"Queries únicas (por UniqueID): {len(df_unique)}")

    # ============================
    # 3) DEFINIR QUEM É 'PERGUNTA EXPLÍCITA'
    # ============================
    logger.info("=== 3) SELECIONAR QUERIES EXPLÍCITAS ===")

    mask_rules = (df_unique[rules_col] == "YES") if rules_col else False
    mask_llm = False
    if llm_cols:
        mask_llm = df_unique[llm_cols].eq("YES").any(axis=1)

    mask_explicit = mask_rules | mask_llm

    df_explicit = df_unique[mask_explicit].copy()
    logger.info(f"Queries explícitas a processar (Parte 2 + 3): {len(df_explicit)}")

    # ============================
    # 4) LOOP SOBRE QUERIES EXPLÍCITAS
    # ============================

    results = []  # lista de dicts: {UniqueID, ARIA_Category, ARIA_Reasoning, GRADE_Question}

    for idx, row in df_explicit.iterrows():
        uid = row["UniqueID"]
        query = row["Query"]

        logger.info(f"PROCESSAR UniqueID={uid} | Query='{query}'")

        # ---- PARTE 2 ----
        category, reasoning = classify_parte2(query)
        logger.info(f" -> ARIA_Category={category}")

        grade_q = ""

        # ---- PARTE 3 (só se Foreground) ----
        if category.lower() == "foreground":
            grade_q = build_grade_question_parte3(query)
            logger.info(f" -> GRADE_Question gerada.")

        results.append({
            "UniqueID": uid,
            "ARIA_Category": category,
            "ARIA_Reasoning": reasoning,
            "GRADE_Question": grade_q
        })

        # pequena pausa por prudência (rate limits)
        time.sleep(SLEEP_BETWEEN_CALLS)

    # Converter resultados em DataFrame
    df_res = pd.DataFrame(results)

    # ============================
    # 5) MERGE COM df_unique
    # ============================

    logger.info("=== 5) MERGE COM QUERIES ÚNICAS ===")

    df_unique_out = df_unique.merge(df_res, on="UniqueID", how="left")

    # Guardar resultados únicos
    df_unique_out[["UniqueID", "Query", "ARIA_Category", "ARIA_Reasoning", "GRADE_Question"]] \
        .to_excel(UNIQUE_OUTPUT_FILE, index=False)
    logger.info(f"Resultados por UniqueID guardados em: {UNIQUE_OUTPUT_FILE}")

    # ============================
    # 6) MERGE COM DATASET COMPLETO
    # ============================

    logger.info("=== 6) MERGE COM DATASET COMPLETO ===")

    df_full_out = df_full.merge(
        df_unique_out[["UniqueID", "ARIA_Category", "ARIA_Reasoning", "GRADE_Question"]],
        on="UniqueID",
        how="left"
    )

    df_full_out.to_excel(FULL_OUTPUT_FILE, index=False)
    logger.info(f"Dataset completo com colunas extra guardado em: {FULL_OUTPUT_FILE}")


# ============================
# ENTRY POINT
# ============================

if __name__ == "__main__":
    logger.info("====================================================")
    logger.info("🚀 Início da execução do pipeline DECIDE – PARTE 2 + 3 (GPT-4o)")
    logger.info("====================================================")
    t0 = time.time()

    main()

    elapsed = int(time.time() - t0)
    h = elapsed // 3600
    m = (elapsed % 3600) // 60
    s = elapsed % 60

    logger.info("====================================================")
    logger.info("🏁 Fim da execução do pipeline DECIDE – PARTE 2 + 3")
    logger.info(f"⏱ Tempo total: {h:02d}:{m:02d}:{s:02d}")
    logger.info("====================================================")

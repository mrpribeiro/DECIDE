# ===========================================================================
# DECIDE – PARTE 2 + 3 (GPT-4o | Perplexity Sonar-Pro | Gemini-2.5-Pro) EXTRA
# ===========================================================================
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
#   1) PART2_3_final_unique.xlsx
#       - 1 linha por UniqueID (queries únicas)
#
#   2) PART2_3_queries_class.xlsx
#       - apenas queries que passaram no filtro de elegibilidade
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

# Perplexity
from perplexity import Perplexity

# OpenAI (GPT-4o-mini)
from openai import OpenAI

# Google Gemini
import google.generativeai as genai

# ============================
# CONFIGURAÇÕES GERAIS
# ============================

INPUT_FILE = "LLM_complete_classification_PERP_GPT_GEM.xlsx"
OUTPUT_CLASS_FILE = "PART2_3_queries_class.xlsx"
OUTPUT_UNIQUE_FILE = "PART2_3_final_unique.xlsx"

SLEEP_BETWEEN_CALLS = 0.3
GPT_MODEL = "gpt-4o"
PERPLEXITY_MODEL = "sonar-pro"
GEMINI_MODEL = "gemini-2.5-pro"


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
# LOAD ENV + CLIENTES
# ============================

load_dotenv()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

perplexity_client = erplexity_client = Perplexity(api_key=os.environ.get("PERPLEXITY_API_KEY"))

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel(GEMINI_MODEL)

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
# FUNÇÕES LLM
# ============================

def call_openai(system_msg, user_msg):
    try:
        r = openai_client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            temperature=0
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return ""


def call_perplexity(system_msg, user_msg):
    try:
        r = perplexity_client.chat.completions.create(
            model=PERPLEXITY_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            temperature=0
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Perplexity error: {e}")
        return ""


def call_gemini(system_msg, user_msg):
    try:
        prompt = f"{system_msg}\n\n{user_msg}"
        r = gemini_model.generate_content(prompt)
        return r.text.strip()
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return ""


# ============================
# PARSING
# ============================

def parse_parte2_output(text):
    if not text:
        return "", ""

    text = text.strip().strip('"').strip("'")

    cat_match = re.search(r"Category:\s*(.+)", text, re.IGNORECASE)
    reason_match = re.search(r"Reasoning:\s*(.+)", text, re.IGNORECASE | re.DOTALL)

    category = cat_match.group(1).strip() if cat_match else ""
    reasoning = reason_match.group(1).strip() if reason_match else ""

    category_low = category.lower()
    if "foreground" in category_low:
        category = "Foreground"
    elif "background" in category_low:
        category = "Background"
    elif "unrelated" in category_low:
        category = "Unrelated"

    return category, reasoning


# ============================
# PIPELINE POR LLM
# ============================

def run_part2_part3(query, llm_call):
    raw_p2 = llm_call(
        PROMPT_PARTE2_SYSTEM,
        f"Query to classify:\n{query}"
    )
    category, reasoning = parse_parte2_output(raw_p2)

    grade_q = ""
    if category == "Foreground":
        raw_p3 = llm_call(
            PROMPT_PARTE3_SYSTEM,
            f"Query to transform into a GRADE question:\n{query}"
        )
        grade_q = raw_p3.strip().strip('"').strip("'")

    return category, reasoning, grade_q


# ============================
# PIPELINE PRINCIPAL
# ============================

def main():
    logger.info("=== LER INPUT ===")
    df = pd.read_excel(INPUT_FILE)
    # df = df.head(200)  # Para testes rápidos; remover em produção

    llm_cols = [c for c in df.columns if c.startswith("LLM_run")]
    rules_col = "Rules" if "Rules" in df.columns else None

    df_unique = df.drop_duplicates(subset=["UniqueID"]).copy()

    mask_rules = (df_unique[rules_col] == "YES") if rules_col else False
    mask_llm = df_unique[llm_cols].eq("YES").any(axis=1)

    df_explicit = df_unique[mask_rules | mask_llm].copy()
    logger.info(f"Queries explícitas: {len(df_explicit)}")

    LLM_PIPELINES = {
        "gpt4o": call_openai,
        "sonar_pro": call_perplexity,
        "gemini_2_5_pro": call_gemini
    }

    results = []

    for _, row in df_explicit.iterrows():
        uid = row["UniqueID"]
        query = row["Query"]

        record = {
            "UniqueID": uid,
            "Query": query
        }

        for llm_name in LLM_PIPELINES.keys():
            record[f"ARIA_Category_{llm_name}"] = "N/A"
            record[f"ARIA_Reasoning_{llm_name}"] = "N/A"
            record[f"GRADE_Question_{llm_name}"] = "N/A"

        for llm_name, llm_call in LLM_PIPELINES.items():
            logger.info(f"[{llm_name}] UniqueID={uid}")

            cat, reason, grade = run_part2_part3(query, llm_call)

            record[f"ARIA_Category_{llm_name}"] = cat if cat else "N/A"
            record[f"ARIA_Reasoning_{llm_name}"] = reason if reason else "N/A"
            record[f"GRADE_Question_{llm_name}"] = grade if grade else "N/A"

            time.sleep(SLEEP_BETWEEN_CALLS)

        results.append(record)

    df_resultados = pd.DataFrame(results)

    # Segurança adicional (caso algum NaN tenha escapado)
    df_resultados = df_resultados.fillna("N/A")
    df_resultados.to_excel(OUTPUT_CLASS_FILE, index=False)

    logger.info(f"Output all classified queries guardado: {OUTPUT_CLASS_FILE}")

    df_input_unique = pd.read_excel("LLM_class_unique_PERP_GPT_GEM.xlsx")

    df_full_unique = df_input_unique.merge(
        df_resultados,
        on=["UniqueID"],
        how="left"
    )

    df_full_unique = df_full_unique.fillna("N/A")

    df_full_unique.to_excel("PART2_3_FINAL_unique.xlsx", index=False)

    logger.info(f"Output all unique queries guardado: {OUTPUT_UNIQUE_FILE}")

# ============================
# ENTRY POINT
# ============================

if __name__ == "__main__":
    logger.info("🚀 Início pipeline DECIDE Parte 2 + 3 (Multi-LLM independente)")
    t0 = time.time()
    main()
    elapsed = int(time.time() - t0)
    logger.info(f"🏁 Fim | Tempo total: {elapsed//60:02d}:{elapsed%60:02d}")
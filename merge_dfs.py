import pandas as pd

df_sample_JS = pd.read_excel("df_sample_JOAO.xlsx")
df_LLMs = pd.read_excel("queries_classificadas_S.DUPLICADOS.xlsx")

cols_to_add = ["Rules", "LLM_run1", "LLM_run2"]

df_merged = df_sample_JS.merge(
    df_LLMs[["UniqueID"] + cols_to_add],
    on="UniqueID",
    how="left"
)

df_merged.to_excel("df_samples_wLLM.xlsx", index=False)

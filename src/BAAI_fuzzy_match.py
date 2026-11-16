# BAAI_fuzzy_match.py

from huggingface_hub import snapshot_download
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import torch
import pandas as pd
import time

start_time = time.time()

# Download the model files to a local directory
model_path = snapshot_download(repo_id="BAAI/bge-reranker-large")
print(f"Model downloaded to: {model_path}")

# Load the model and tokenizer from the local directory
model = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True)
tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)

final_drug_details = pd.read_excel("inputs/Final_Drug_details_fuzzy.xlsx")
clinical_details = pd.read_excel("inputs/Clinical_details_fuzzy.xlsx")

merged = pd.merge(clinical_details, final_drug_details, left_on="NDC_Code", right_on="NDC", how="inner")
print(f"Shape of DataFrame: {merged.shape}")

merged["Sub-Indication"] = merged["Sub-Indication"].fillna("") # empty values are replaced with empty string
merged["clinical_comb_col"] = merged["Indication"] + merged["Sub-Indication"].apply(lambda x: f" {x}" if x else "")


merged["final_drug_comb_col"] = merged["conditions"] + " " + merged["subconditions"] + " " + merged["usage"]
merged["final_drug_comb_col"] = merged["final_drug_comb_col"].str.replace("none", "", regex=False).str.strip() # remove "none" word from the column
merged["final_drug_comb_col"] = merged["final_drug_comb_col"].str.split().str.join(" ") # removing extra spaces

merged_data = merged.drop_duplicates(subset=['clinical_comb_col', 'final_drug_comb_col'])

#merged_data.to_excel("outputs/merged_data.xlsx", index=False)
print(f"Shape of DataFrame After Cleaning/Merging: {merged_data.shape}")

def rerank_excel(merged, model, tokenizer, query_col, doc_col, max_length=512):
    merged_results = []

    for _, row in merged.iterrows():
        query = row[query_col]
        document = row[doc_col]

        # Tokenize and get similarity score
        inputs = tokenizer(
            [[query, document]],
            padding=True,
            truncation=True,
            return_tensors="pt",
            max_length=max_length
        )

        with torch.no_grad(): # Generating scores
            scores = model(**inputs).logits.squeeze(-1)

        score = scores.item()
        print(f"Query: {query}, Document: {document}, Score: {score}") # debugging purpose

        # Add similarity details to the list
        merged_data = {
            **row.to_dict(),
            "Similarity_Score": score
        }
        merged_results.append(merged_data)

    return pd.DataFrame(merged_results)

query_column = "clinical_comb_col"
document_columns = "final_drug_comb_col"

merged_data_results = rerank_excel(merged_data, model, tokenizer, query_column, document_columns)
merged_data_results.to_excel("outputs/merged_results_df.xlsx", index=False)
print(f"Shape of Merged Data Results file: {merged_data_results.shape}")

df_max_similarity = merged_data_results.loc[merged_data_results.groupby(['NDC', 'clinical_comb_col'])['Similarity_Score'].idxmax()]

max_similarity = pd.DataFrame(df_max_similarity)
max_similarity.to_excel("outputs/merged_data_similarity_results.xlsx", index=False)
print(f"Shape of Merged Data Results file: {max_similarity.shape}")

print("Similarity Search Completed")

cols_to_merge = [
    "NDC_Code", "Indication", "Sub-Indication",  # for join
    "conditions", "subconditions", "usage", "lineoftreatment",
    "typeofTherapy", "combinationdrug", "Similarity_Score"
]

clinical_details["Sub-Indication"] = clinical_details["Sub-Indication"].fillna("") # empty values are replaced with empty string

final_merged = pd.merge(
    clinical_details,
    df_max_similarity[cols_to_merge],
    on=["NDC_Code", "Indication", "Sub-Indication"],
    how="left"
)

final_merged["Sub-Indication"] = final_merged["Sub-Indication"].replace("", "NA")

output_columns = [
    "Drug", "NDC_Code", "Indication", "Study ID", "Dosage", "Patient Selection",
    "Sub-Indication", "ORR", "PFS", "Response Duration",
    "conditions", "subconditions", "usage", "lineoftreatment",
    "typeofTherapy", "combinationdrug", "Similarity_Score"
]

final_output = final_merged[output_columns]
final_output = final_output.fillna("NA").replace("", "NA")
final_output.to_excel("outputs/final_matched_reranked.xlsx", index=False)
print(f"Saved final matched data with shape: {final_output.shape}")

# Reading the first Excel file
Final_matched_reranked_details = pd.read_excel("outputs/final_matched_reranked.xlsx")
col_name1 = ["Indication", "Patient Selection", "Dosage"]
Final_matched_reranked_details[col_name1] = Final_matched_reranked_details[col_name1].fillna("")

Final_matched_reranked_details["matched_comb_col"] = (Final_matched_reranked_details["Indication"] + " " + Final_matched_reranked_details["Patient Selection"] + " " + Final_matched_reranked_details["Dosage"]).str.strip()

Final_matched_reranked_details["matched_comb_col"] = Final_matched_reranked_details["matched_comb_col"].replace("", "NA")

Final_matched_reranked_details.drop(Final_matched_reranked_details[Final_matched_reranked_details['matched_comb_col'] == "NA"].index, inplace=True)
Final_matched_reranked_details.columns = Final_matched_reranked_details.columns.str.strip()

Final_matched_reranked_details.to_excel("outputs/Final_matched_reranked_merged_details.xlsx", index=False)

# Reading the second Excel file
dosage_administration_details = pd.read_excel("inputs/Final_dosage_administration_details.xlsx")
col_name2 = ["Indication", "Patient selection", "Strength of tablet for frequency"]
dosage_administration_details[col_name2] = dosage_administration_details[col_name2].fillna("")

dosage_administration_details["dosage_comb_col"] = (dosage_administration_details["Indication"] + " " + dosage_administration_details["Patient selection"] + " " + dosage_administration_details["Strength of tablet for frequency"]).str.strip()

dosage_administration_details["dosage_comb_col"] = dosage_administration_details["dosage_comb_col"].replace("", "NA")

dosage_administration_details.drop(dosage_administration_details[dosage_administration_details['dosage_comb_col'] == "NA"].index, inplace=True)

dosage_administration_details.to_excel("outputs/dosage_administration_details_file.xlsx", index=False)

merged2 = pd.merge(
    Final_matched_reranked_details,
    dosage_administration_details,
    on="NDC_Code",
    how="inner"
)

merged2.to_excel("outputs/merged2.xlsx", index=False)

merged_data2 = merged2.drop_duplicates(subset=['matched_comb_col', 'dosage_comb_col'])
merged_data2.to_excel("outputs/merged_data_file.xlsx", index=False)

query_column1 = "matched_comb_col"
document_columns1 = "dosage_comb_col"

merged_rerank_dosage_results = rerank_excel(merged_data2, model, tokenizer, query_column1, document_columns1)
merged_rerank_dosage_results.to_excel("outputs/merged_rerank_dosage_results.xlsx", index=False)
print(f"Shape of Merged Reranked with Dosage part Data Results file: {merged_rerank_dosage_results.shape}")

df_max_similarity_rerank_dosage = merged_rerank_dosage_results.loc[merged_rerank_dosage_results.groupby(['NDC_Code', 'matched_comb_col'])['Similarity_Score'].idxmax()]
max_similarity_rerank_dosage = pd.DataFrame(df_max_similarity_rerank_dosage)

max_similarity_rerank_dosage.rename(columns={"Similarity_Score": "Dosage_Similarity_Score"}, inplace=True)
max_similarity_rerank_dosage.rename(columns={"Indication_y": "Dosage_Indication"}, inplace=True)
max_similarity_rerank_dosage.rename(columns={"Indication_x": "Indication"}, inplace=True)
max_similarity_rerank_dosage.rename(columns={"Patient selection": "Dosage Patient Selection"}, inplace=True)

max_similarity_rerank_dosage.columns = max_similarity_rerank_dosage.columns.str.strip()

max_similarity_rerank_dosage.to_excel("outputs/merged_rerank_dosage_similarity_results.xlsx", index=False)
print(f"Shape of Merged Data Results file: {max_similarity_rerank_dosage.shape}")

print("Similarity Search Completed between Rerank and dosage file")

cols_to_merge1 = ["NDC_Code", "Indication", "Patient Selection", "Dosage", "Dosage_Indication", "Dosage Patient Selection",
                 "Total Recommended Dosage", "Strength of tablet for frequency", "Frequency",
                 "Administration Method", "Dosage_Similarity_Score"]

final_rerank_dosage_merged = pd.merge(
    Final_matched_reranked_details,
    max_similarity_rerank_dosage[cols_to_merge1],
    on=["NDC_Code", "Indication", "Patient Selection", "Dosage"],
    how="left"
)

output_columns = [
    "Drug", "NDC_Code", "Indication", "Study ID", "Dosage", "Patient Selection",
    "Sub-Indication", "ORR", "PFS", "Response Duration",
    "conditions", "subconditions", "usage", "lineoftreatment",
    "typeofTherapy", "combinationdrug", "Similarity_Score", "Dosage_Indication", "Dosage Patient Selection",
    "Total Recommended Dosage", "Strength of tablet for frequency", "Frequency",
    "Administration Method", "Dosage_Similarity_Score"
]

final_rerank_dosage_merged = final_rerank_dosage_merged[output_columns]
final_rerank_dosage_merged = final_rerank_dosage_merged.fillna("NA").replace("", "NA")
final_rerank_dosage_merged.to_excel("outputs/rerank_clinical_drug_dosage_merged_file.xlsx", index=False)
print(f"Saved final Clinical Drug Dosage Matched Data with Shape: {final_rerank_dosage_merged.shape}")

end_time = time.time()
elapsed_time = end_time - start_time
print(f"The program took {elapsed_time:.4f} seconds to run.")

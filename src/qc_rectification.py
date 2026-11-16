#qc_rectification.py code file

import os
import re
import json
import time
import logging
import asyncio
import dotenv
import pandas as pd
from datetime import datetime, timezone
import yaml
from tenacity import retry, stop_after_attempt, wait_exponential
from main import get_chat_access_token, limits  # Your existing LLM auth, client limits

from db_connection import TrinoConnection
from db_queries import dbQueryClient
from langchain_anthropic import ChatAnthropic

dotenv.load_dotenv("config/.env")

logger = logging.getLogger("rectification")
logger.setLevel(logging.INFO)

now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
now_str = now.replace('-', '').replace(':', '').replace(' ', '_')

# Config and Constants
SCHEMA = 'z_abv_cws_bts_ps_data_analytics'
INPUT_TABLE = 'dailymed_dosing_qc'
OUTPUT_TABLE = 'dailymed_dosing_qc_rectified'
Q_CONFIG_PATH = 'config/qc_config2.json'
CONFIG_JSON_PATH = 'config/config.json'  # For prompt location

# Prompt config loader (from main.py style)
async def load_prompt(config_path, category_key, prompt_key):
    with open(config_path, 'r', encoding='utf-8') as config_file:
        config_data = json.load(config_file)
    yaml_file_path = config_data[category_key][prompt_key]
    with open(yaml_file_path, 'r', encoding='utf-8') as yaml_stream:
        yaml_content = yaml.safe_load(yaml_stream)
    first_key = list(yaml_content.keys())[0]
    prompt_text = yaml_content[first_key]
    return prompt_text

def load_qc_config(path):
    with open(path, 'r') as f:
        return json.load(f).get('qc_checks', {})

def extract_validation_rules(qc_config, check_name):
    rules = qc_config.get(check_name)
    if not rules:
        return "No detailed rule found."
    return json.dumps(rules, indent=2)

async def correction_task(idx, row, qc_config, data_col_names, llm_anthropic, prompt_template):
    record = row[data_col_names].to_dict()

    def serialize_for_json(obj):
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    record_json = json.dumps(record, indent=2, default=serialize_for_json)

    error_checks = [e.strip() for e in str(row.get('error_message_query', '')).split(';') if e.strip()]
    check_errors_text = '\n'.join(f"- Check Name: {err}\n  Error Message: See QC config" for err in error_checks)
    val_rules = ""
    for err in error_checks:
        val_rules += f"Rule for {err}:\n{extract_validation_rules(qc_config, err)}\n\n"
    prompt_text = prompt_template.format(
        record_json=record_json,
        check_errors=check_errors_text,
        validation_rules=val_rules
    )
    try:
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt_text}]}]
        response = await llm_anthropic.ainvoke(messages)
        llm_response = response.content
        match = re.search(r"\{.*\}", llm_response, re.DOTALL)
        if match:
            correction_dict = json.loads(match.group().replace("'", '"'))
            if not isinstance(correction_dict, dict):
                correction_dict = {}
        else:
            correction_dict = {}
    except Exception as exc:
        logger.error(f"LLM rectification failed for row {idx}: {exc}")
        correction_dict = {}
    return {'index': idx, 'corrections': correction_dict}

async def generate_corrections_for_errors(error_df, qc_config, data_col_names, llm_anthropic, prompt_template, batch_size=16):
    tasks = []
    batch_results = []
    for i, (idx, row) in enumerate(error_df.iterrows()):
        tasks.append(correction_task(idx, row, qc_config, data_col_names, llm_anthropic, prompt_template))
        if len(tasks) == batch_size:
            res = await asyncio.gather(*tasks)
            batch_results.extend(res)
            tasks = []
    if tasks:
        res = await asyncio.gather(*tasks)
        batch_results.extend(res)
    return batch_results

def apply_corrections(df, corrections):
    rectification_log = []
    df_updated = df.copy()
    for correction in corrections:
        idx = correction['index']
        corr_dict = correction['corrections']
        if not corr_dict:
            continue
        for field, corrected_value in corr_dict.items():
            if field not in df_updated.columns:
                continue
            original_value = df_updated.at[idx, field]
            df_updated.at[idx, field] = corrected_value
            rectification_log.append({
                'row_index': idx,
                'field_name': field,
                'original_value': original_value,
                'corrected_value': corrected_value
            })
    return df_updated, rectification_log

async def run_rectification(job_id):
    start_time = time.time()
    logger.info("[INFO] Rectification Orchestration Started")

    RECTIFIED_OUTPUT_PATH = f'output/qc_rectified_data_final_{job_id}_{now_str}.xlsx'
    RECTIFICATION_LOG_PATH = f'output/qc_rectification_log_{job_id}_{now_str}.xlsx'

    if not job_id:
        raise ValueError("job_id parameter must be provided in function argument qc rectification!")

    # Load QC config
    qc_config = load_qc_config(Q_CONFIG_PATH)

    # Get LLM Auth token
    iliad_url = os.getenv("endpoint")
    AUTH_TOKEN = await get_chat_access_token()
    llm_anthropic = ChatAnthropic(
        base_url = iliad_url + "/anthropic",
        model="claude-3-7-sonnet-20250219",
        api_key= f"{AUTH_TOKEN}",
        default_headers={"Authorization": f"{AUTH_TOKEN}","anthropic-beta":"tools-2024-04-04"},
        model_kwargs={"extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31"}},
        temperature=0.0,
        max_tokens=4096
    )

    # Load the LLM correction prompt from config (YAML path from main.py logic)
    prompt_template = await load_prompt(CONFIG_JSON_PATH, 'llm_qc_rectification_prompt', 'path')

    # Setup DB connections
    trino_conn = TrinoConnection()
    client = dbQueryClient(trino_conn)

    latest_mod_df = client.execute_query(
        SCHEMA,
        f"SELECT MAX(modified_date) as latest_mod FROM {INPUT_TABLE} WHERE job_id = '{job_id}'"
    )
    if latest_mod_df.empty or latest_mod_df['latest_mod'].isnull().all():
        raise ValueError(f"No data found for job_id {job_id} in table {INPUT_TABLE} to rectify.")

    latest_mod = latest_mod_df['latest_mod'].iloc[0]

    input_query = f"""
        SELECT *
        FROM {INPUT_TABLE}
        WHERE job_id = '{job_id}' AND modified_date = TIMESTAMP '{latest_mod}'
    """

    df = client.execute_query(SCHEMA, input_query)
    logger.info(f"[INFO] Loaded {df.shape[0]} rows from {INPUT_TABLE} for job_id {job_id}, modified_date {latest_mod}.")

    # Filter rows needing rectification based on pass_fail
    error_rows = df[df['pass_fail'].isin(['Fail', 'Warning'])].copy()

    if error_rows.empty:
        logger.info("[INFO] No error rows found requiring rectification.")
        return {
            "message": "No records found requiring rectification.",
            "rectified_output": None,
            "rect_log": None,
            "runtime_seconds": 0,
            "rectified_rows": 0
        }

    data_col_names = [col for col in df.columns if col not in ['pass_fail', 'error_message_query', 'empty_columns_silent_warning']]

    corrections = await generate_corrections_for_errors(error_rows, qc_config, data_col_names, llm_anthropic, prompt_template)

    corrected_df, rect_log = apply_corrections(df, corrections)

    # Drop QC indicator columns before output
    qc_columns_to_drop = ['pass_fail', 'error_message_query', 'empty_columns_silent_warning']
    corrected_df_final = corrected_df.drop(columns=[col for col in qc_columns_to_drop if col in corrected_df.columns])

    # Convert date columns to datetime without tz
    for dt_col in ['created_date', 'modified_date']:
        if dt_col in corrected_df_final.columns:
            corrected_df_final[dt_col] = pd.to_datetime(corrected_df_final[dt_col], errors='coerce').dt.tz_localize(None)

    # Cast some columns to numeric as needed
    int_cols = ['derived_duration_of_treatment']
    for col in int_cols:
        if col in corrected_df_final.columns:
            corrected_df_final[col] = pd.to_numeric(corrected_df_final[col], errors='coerce').astype('Int64')

    float_cols = [
        'adverse_reaction_median_duration_of_treatment_(weeks)',
        'adverse_reaction_max_duration_of_treatment_(weeks)',
    ]
    for col in float_cols:
        if col in corrected_df_final.columns:
            corrected_df_final[col] = pd.to_numeric(corrected_df_final[col], errors='coerce')

    # Replace NaN/NA with None for DB insertion
    corrected_df_final = corrected_df_final.where(pd.notnull(corrected_df_final), None)

    # Insert rectified data into DB
    client.insert_dataframe(corrected_df_final, SCHEMA, OUTPUT_TABLE, batch_size=100)

    # Save output Excel files
    os.makedirs(os.path.dirname(RECTIFIED_OUTPUT_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(RECTIFICATION_LOG_PATH), exist_ok=True)

    corrected_df_final.to_excel(RECTIFIED_OUTPUT_PATH, index=False)
    logger.info(f"[INFO] Rectified dataset saved to: {RECTIFIED_OUTPUT_PATH}")

    pd.DataFrame(rect_log).to_excel(RECTIFICATION_LOG_PATH, index=False)
    logger.info(f"[INFO] Rectification log saved to: {RECTIFICATION_LOG_PATH}")

    end_time = time.time()
    runtime_seconds = end_time - start_time
    logger.info(f"[INFO] Rectification process finished. Runtime: {runtime_seconds:.2f} seconds.")

    return {
        "rectified_output": RECTIFIED_OUTPUT_PATH,
        "rect_log": RECTIFICATION_LOG_PATH,
        "runtime_seconds": runtime_seconds,
        "rectified_rows": len(corrected_df_final)
    }

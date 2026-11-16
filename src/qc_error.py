#qc_error.py code file

def run_qc(job_id):
    import pandas as pd
    import json
    import re
    import os
    from datetime import datetime, timezone

    from db_connection import TrinoConnection
    from db_queries import dbQueryClient

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    now_str = now.replace('-', '').replace(':', '').replace(' ', '_')

    # Paths
    #excel_file_path = 'input/Async_Final_Dosage_details_test_job.xlsx'
    output_excel = f'output/query_qc_results_{job_id}_{now_str}.xlsx'
    config_file_path = 'config/qc_config2.json'

    # Load Data
    #df = pd.read_excel(excel_file_path)

    # job_id = os.environ.get("JOB_ID")   # Can be set in your .env or passed directly
    # if not job_id:
    #     raise Exception("JOB_ID must be set in your environment or script.")

    if not job_id:
        raise ValueError("JOB_ID must be provided as function argumnet in QC")

    schema = 'z_abv_cws_bts_ps_data_analytics'
    input_table = 'dailymed_dosing_raw'

    # Step 1: Find latest modified_date for the job_id
    connection = TrinoConnection()
    client = dbQueryClient(connection)
    query_latest_mod = f"""
        SELECT MAX(modified_date) as latest_mod
        FROM {input_table}
        WHERE job_id = '{job_id}'
    """
    latest_mod_df = client.execute_query(schema, query_latest_mod)
    latest_mod = latest_mod_df['latest_mod'].iloc[0]
    assert latest_mod is not None, f"No data for job_id {job_id}!"

    # Step 2: Fetch all data for that job_id and modified_date
    query_main = f"""
        SELECT *
        FROM {input_table}
        WHERE job_id = '{job_id}' AND modified_date = TIMESTAMP '{latest_mod}'
    """
    df = client.execute_query(schema, query_main)
    print(f"[INFO] Loaded {df.shape[0]} rows for job_id {job_id}, modified_date {latest_mod}")


    df.columns = (
        df.columns.str.replace(r'[^a-zA-Z0-9_]', '', regex=True)
                .str.replace(r'\s+', '_', regex=True)
                .str.strip().str.lower()
    )
    print("[INFO] Data loaded:", df.shape)

    # Load Config
    with open(config_file_path) as f:
        qc_config = json.load(f).get('qc_checks', {})


    MANDATORY_FIELDS = ['drug_name', 'recommended_dosage', 'method_of_administration']
    OPTIONAL_FIELDS = ['drug_link_id', 'drug_url', 'manufacturer', 'disease_full', 'disease_short', 'indication', 'subindications','usage', 'lineoftherapy',
        'line_of_therapy_simplified','typeoftherapy','combinationdrug', 'combo_therapy_flag', 'combo_1', 'combo_2', 'combo_3',
        'cycle_duration', 'duration_of_treatment', 'set_duration_less_than_a_year', 'number_of_cycles','treatment_is_based_on',
        'patient_group','is_pediatric','weightkg','dose_per_kg','flat_dose','mg_per_dose','dosing_segment_used','medications','notes',
        'yearly_dosage','yearly_cycles', 'subsequent_year_dosage_in_mg','number_of_doses_in_subsequent_years','first_year_dosage_in_mg',
        'number_of_doses_in_first_year','adverse_reaction_study_id','adverse_reaction_median_duration_of_treatment','adverse_reaction_max_duration_of_treatment',
        'adverse_reaction_median_duration_of_treatment_weeks','adverse_reaction_max_duration_of_treatment_weeks','derived_duration_of_treatment',
        'derived_set_duration_under_1_year','created_date','modified_date','processed_by', 'job_id', 'active_flag']

    # QC FUNCTIONS

    def check_null_thresholds(df):
        errors = []
        # Check mandatory fields - must have zero nulls
        for col in MANDATORY_FIELDS:
            if col not in df.columns:
                errors.append(f"Mandatory field '{col}' missing from dataset")
                continue
            null_count = df[col].isnull().sum() + (df[col].astype(str).str.strip() == '').sum()
            if null_count > 0:
                errors.append(f"Mandatory field '{col}' has {null_count} missing/null values")

        # Check optional fields - null % ≤ 5%
        total_rows = len(df)
        for col in OPTIONAL_FIELDS:
            if col not in df.columns:
                continue # optional field missing is not fatal
            null_count = df[col].isnull().sum() + (df[col].astype(str).str.strip() == '').sum()
            null_pct = null_count / total_rows
            if null_pct > 0.05:
                errors.append(f"Optional field '{col}' null percentage {null_pct:.2%} exceeds 5% threshold")

        return errors

    def null_threshold_check(df):
        errors = check_null_thresholds(df)
        if errors:
            # Mark entire df as failed with error messages
            df_copy = df.copy()
            df_copy['pass_fail'] = 'Fail'
            df_copy['error_message_query'] = "; ".join(errors)
            return df_copy
        else:
            return pd.DataFrame()

    def leading_trailing(series):
        return series.str.len() != series.str.strip().str.len()

    def non_numeric(series):
        return ~series.astype(str).str.strip().str.match(r'^-?\d+(\.\d+)?$', na=False)

    def invalid_lengths(series, lengths):
        cleaned = series.astype(str).str.replace('-', '', regex=False).str.strip()
        return ~cleaned.str.len().isin(lengths) & ~cleaned.eq('')

    def not_in_set(series, allowed, ignore_case=True):
        series_clean = series.astype(str).str.strip()
        if ignore_case:
            return ~series_clean.str.lower().isin([x.lower() for x in allowed])
        return ~series_clean.isin(allowed)

    def regex_not_match(series, pattern, ignore_case=True):
        flags = re.IGNORECASE if ignore_case else 0
        return series.astype(str).str.strip().apply(lambda x: not bool(re.fullmatch(pattern, x, flags=flags)))

    def less_than_equal(series, threshold):
        series = pd.to_numeric(series, errors='coerce')
        return series <= threshold

    def less_than_equal_extract(series, regex, threshold):
        numeric_part = series.astype(str).str.extract(regex)[0]
        numeric_part = pd.to_numeric(numeric_part, errors='coerce')
        return numeric_part <= threshold

    def value_out_of_range(series, regex, min_value):
        numeric_part = series.astype(str).str.extract(regex)[0]
        numeric_part = pd.to_numeric(numeric_part, errors='coerce')
        return (numeric_part.notna()) & (numeric_part < min_value)

    def inconsistent_values(df, group, target):
        grouped = df.groupby(group)[target].nunique()
        problem_keys = grouped[grouped > 1].index
        return df[df[group].isin(problem_keys)]

    def combo_flag_yes_requires_combo(df, combinationdrug_col, flag_col, c1, c2, c3):
        """
        Flag rows where combo_therapy_flag is 'yes' but - combinationdrug is empty/NA OR - none of combo_1, combo_2, combo_3 has value
        """
        flag_yes = df[flag_col].astype(str).str.strip().str.lower() == 'yes'
        combo_drug_val = df[combinationdrug_col].astype(str).str.strip()
        combo_1_val = df[c1].astype(str).str.strip()
        combo_2_val = df[c2].astype(str).str.strip()
        combo_3_val = df[c3].astype(str).str.strip()

        cond_combinationdrug_empty = combo_drug_val == ''
        cond_combo_columns_empty = (combo_1_val == '') & (combo_2_val == '') & (combo_3_val == '')

        # Fail if flag_yes but combinationdrug empty OR combo columns empty
        fail_mask = flag_yes & (cond_combinationdrug_empty | cond_combo_columns_empty)
        return df[fail_mask]

    def is_empty_value(x):
        if pd.isna(x):
            return True
        s = str(x).strip().lower()
        return s == "" or s in ["nan", "none", "null"]

    def combo_flag_requires_no_combo_values(df, combinationdrug_col, flag_col, c1, c2, c3):
        # Rows with flag == "no" (case-insensitive)
        flag_no = df[flag_col].astype(str).str.strip().str.lower() == "no"
        combo_drug_empty = df[combinationdrug_col].apply(is_empty_value)
        combo1_empty = df[c1].apply(is_empty_value)
        combo2_empty = df[c2].apply(is_empty_value)
        combo3_empty = df[c3].apply(is_empty_value)

        # Fail if flag is "no" but either combinationdrug is NOT empty or any combo column NOT empty
        fail_mask = flag_no & (~combo_drug_empty | ~(combo1_empty & combo2_empty & combo3_empty))
        return df[fail_mask]

    def conditional_consistency(df, cols, conditions):
        # Simple example: check if derived duration contradicts under1year flag
        col_duration, col_flag = cols[0], cols[1]
        duration = pd.to_numeric(df[col_duration], errors='coerce')
        yes_flag = df[col_flag].astype(str).str.strip().str.lower() == 'yes'
        no_flag = df[col_flag].astype(str).str.strip().str.lower() == 'no'
        bad_yes = (duration >= conditions['duration_threshold']) & yes_flag
        bad_no = (duration < conditions['duration_threshold']) & no_flag
        return df[bad_yes | bad_no]

    def inconsistent_grouping_unique(df, group_by_cols, target_col):
        """
        Returns rows where the group key (across group_by_cols) maps to more than one target_col value
        """
        violence = (
            df
            .groupby(group_by_cols)[target_col]
            .nunique()
            .reset_index()
        )
        problem_keys = violence.loc[violence[target_col] > 1, group_by_cols]
        if problem_keys.empty:
            return pd.DataFrame()
        mask = df[group_by_cols].apply(tuple, axis=1).isin(problem_keys.apply(tuple, axis=1))
        return df[mask]

    def datetime_not_valid(series):
        """
        Returns boolean series True where string cannot be parsed as datetime or is in the future.
        """
        invalid_mask = []
        now = pd.Timestamp.now()
        for v in series:
            try:
                parsed = pd.to_datetime(v)
                invalid_mask.append(parsed > now)
            except Exception:
                invalid_mask.append(True)
        return pd.Series(invalid_mask, index=series.index)

    def date_order_check(df, columns):
        """
        Checks if created_date <= modified_date, returns df where condition is violated.
        """
        dt1 = pd.to_datetime(df[columns[0]], errors='coerce')
        dt2 = pd.to_datetime(df[columns[1]], errors='coerce')
        mask = dt1 > dt2
        return df[mask]

    def check_illegal_formatting_all_columns(df):
        pattern = r'(<[^>]+>|[\u2022•∙▪︎\u2002\u200B-\u200D\uFEFF]|[\x00-\x1f])'
        failing_rows = df.apply(lambda row: any(re.search(pattern, str(v)) if pd.notna(v) else False for v in row), axis=1)
        return df[failing_rows]

    def check_duplicates(df, key_cols):
        return df[df.duplicated(subset=key_cols, keep=False)]

    def check_schema(df, expected_cols_types):
        errors = []

        # Check for missing columns
        missing_cols = [col for col in expected_cols_types if col not in df.columns]
        if missing_cols:
            errors.append(f"Missing columns: {missing_cols}")

        # Check for extra columns
        extra_cols = [col for col in df.columns if col not in expected_cols_types]
        if extra_cols:
            errors.append(f"Extra unexpected columns: {extra_cols}")

        # Validate data types approximately
        # Since df.dtypes may not fully reflect string lengths/type, do basic type checks
        type_mismatches = []
        for col, expected_type in expected_cols_types.items():
            if col not in df.columns:
                continue
            actual_dtype = df[col].dtype

            if expected_type == "string":
                # For string, accept object dtype or string dtype
                if not (pd.api.types.is_string_dtype(actual_dtype) or actual_dtype == 'object'):
                    type_mismatches.append(
                        f"Column '{col}' expected string but got {actual_dtype}"
                    )
            elif expected_type == "timestamp":
                if not (pd.api.types.is_datetime64_any_dtype(actual_dtype)):
                    type_mismatches.append(
                        f"Column '{col}' expected timestamp but got {actual_dtype}"
                    )
            # Add other types if needed here

        if type_mismatches:
            errors.extend(type_mismatches)

        # Return df with all rows marked fail if schema fails, or empty df if all good
        if errors:
            # Create a summary string listing errors for reporting
            err_summary = "; ".join(errors)

            # Mark all rows as failed, with message
            df_copy = df.copy()
            df_copy['pass_fail'] = 'Fail'
            df_copy['error_message_query'] = "Schema Validation Failed"
            return df_copy

        return pd.DataFrame()

    # DISPATCH MAP

    def run_check(df, rule):
        t = rule['type']
        cols = rule.get('columns', [])
        if t == "schema_check":
            expected_cols_types = rule['expected_columns']
            return check_schema(df, expected_cols_types)
        if t == "null_threshold":
            return null_threshold_check(df)
        if t == 'leading_trailing_whitespace':
            return df[leading_trailing(df[cols[0]].astype(str))]
        if t == 'non_numeric':
            return df[non_numeric(df[cols[0]])]
        if t == 'not_in_lengths':
            return df[invalid_lengths(df[cols[0]], rule['lengths'])]
        if t == 'not_in_set':
            return df[not_in_set(df[cols[0]], rule['allowed'], rule.get('ignore_case', True))]
        if t == 'regex_not_match':
            return df[regex_not_match(df[cols[0]], rule['pattern'], rule.get('ignore_case', True))]
        if t == 'less_than_or_equal_to_value':
            return df[less_than_equal(df[cols[0]], rule['threshold'])]
        if t == 'less_than_or_equal_to_value_extracted':
            return df[less_than_equal_extract(df[cols[0]], rule['number_extract_regex'], rule['threshold'])]
        if t == 'value_out_of_range':
            return df[value_out_of_range(df[cols[0]], rule['numeric_pattern'], rule['min_value'])]
        if t == 'inconsistent_values':
            return inconsistent_values(df, rule['group_by'], rule['target'])
        if t == "combo_flag_yes_requires_combo":
            return combo_flag_yes_requires_combo(df, *rule['columns'])
        if t == "combo_flag_requires_no_combo_values":
            return combo_flag_requires_no_combo_values(df, *rule['columns'])
        if t == 'conditional_consistency':
            return conditional_consistency(df, cols, rule['conditions'])
        if t == "illegal_formatting_all_columns":
            return check_illegal_formatting_all_columns(df)
        if t == "duplicate_key_check":
            return check_duplicates(df, rule['columns'])
        if t == "datetime_not_valid":
            return df[datetime_not_valid(df[cols[0]])]
        if t == "date_order_check": # For this type, must return the rows where created_date > modified_date
            return date_order_check(df, cols)
        if t == "inconsistent_grouping_unique":
            return inconsistent_grouping_unique(df, rule["group_by"], rule["target"])

        return pd.DataFrame([])


    qc_summary, qc_failures = [], {}
    df['pass_fail'] = 'Pass'
    df['error_message_query'] = ''

    # Run schema validation first
    if "schema_validation" in qc_config:
        rule = qc_config["schema_validation"]
        fail_df = run_check(df, rule)
        if not fail_df.empty:
            qc_failures["schema_validation"] = fail_df
            qc_summary.append(f"FAIL: schema_validation – {len(fail_df)} rows")
            # Mark all rows in original df as fail for schema too
            for i in df.index:
                df.at[i, 'pass_fail'] = 'Fail'
                df.at[i, 'error_message_query'] = fail_df.at[i, 'error_message_query']
        else:
            qc_summary.append("PASS: schema_validation")

    for name, rule in qc_config.items():
        if name == "schema_validation":
            continue
        try:
            failed = run_check(df, rule)
            if not failed.empty:
                qc_failures[name] = failed
                qc_summary.append(f"FAIL: {name} – {len(failed)} rows")
                idxs = failed.index.tolist()
                # for i in idxs:
                #     if 'Missing' in name or 'missing' in name or 'null' in name:
                #         df.at[i, 'pass_fail'] = 'Warning' if df.at[i, 'pass_fail'] == 'Pass' else df.at[i, 'pass_fail']
                #     else:
                #         df.at[i, 'pass_fail'] = 'Fail'
                #     df.at[i, 'error_message_query'] += f"; {name}" if df.at[i, 'error_message_query'] else name

                for i in idxs:
                    if 'Missing' in name or 'missing' in name or 'null' in name:
                        # Do nothing: let silent warning column handle empty
                        continue
                    else:
                        df.at[i, 'pass_fail'] = 'Fail'
                        df.at[i, 'error_message_query'] += f"; {name}" if df.at[i, 'error_message_query'] else name

            else:
                qc_summary.append(f"PASS: {name}")
        except Exception as e:
            qc_summary.append(f"ERROR: {name} – {str(e)}")

    def get_empty_cols(row):
        cols = df.columns.difference(['pass_fail', 'error_message_query', 'empty_columns_silent_warning'])
        return '; '.join([c for c in cols if row[c] == '' or pd.isna(row[c])])

    df['empty_columns_silent_warning'] = df.apply(get_empty_cols, axis=1)


    # EXPORT

    with pd.ExcelWriter(output_excel, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Consolidated_QC_Results', index=False)
        pd.DataFrame({'QC Summary': qc_summary}).to_excel(writer, sheet_name='QC_Summary', index=False)

    print(f"[INFO] QC complete. Results written to: {output_excel} excel file")


    output_table = 'dailymed_dosing_qc'

    #adding for col rename

    # Map normalized pandas columns to target Trino column names:
    COL_RENAME_MAP = {
        'weightkg': 'weight(kg)'
        # Add more aliases if needed for other columns
    }
    df.rename(columns=COL_RENAME_MAP, inplace=True)

    TRINO_COLS = [
        "drug_link_id", "drug_url", "drug_name", "manufacturer", "disease_full", "disease_short", "indication", "subindications",
        "usage", "lineoftherapy", "line_of_therapy_simplified", "typeoftherapy", "combinationdrug", "combo_therapy_flag",
        "combo_1", "combo_2", "combo_3", "recommended_dosage", "cycle_duration", "duration_of_treatment", "set_duration_less_than_a_year",
        "number_of_cycles", "treatment_is_based_on", "method_of_administration", "patient_group", "is_pediatric", "weight(kg)",
        "dose_per_kg", "flat_dose", "mg_per_dose", "dosing_segment_used", "medications", "notes", "yearly_dosage", "yearly_cycles",
        "subsequent_year_dosage_in_mg", "number_of_doses_in_subsequent_years", "first_year_dosage_in_mg", "number_of_doses_in_first_year",
        "adverse_reaction_study_id", "adverse_reaction_median_duration_of_treatment", "adverse_reaction_max_duration_of_treatment",
        "adverse_reaction_median_duration_of_treatment_(weeks)", "adverse_reaction_max_duration_of_treatment_(weeks)",
        "derived_duration_of_treatment", "derived_set_duration_under_1_year", "created_date", "modified_date", "processed_by",
        "job_id", "active_flag", "pass_fail", "error_message_query", "empty_columns_silent_warning"
    ]

    # Standardize/rename columns before insert
    for norm, actual in COL_RENAME_MAP.items():
        if norm in df.columns:
            df.rename(columns={norm: actual}, inplace=True)

    # Filter/select columns in exact Trino table order
    df_to_insert = df[[col for col in TRINO_COLS if col in df.columns]]
    client.insert_dataframe(df_to_insert, schema, output_table, batch_size=100)

    #client.insert_dataframe(df, schema, output_table, batch_size=100)
    print(f"[INFO] QC complete. Results written to: {output_table} trino database")

    return {"qc_output": output_excel, "qc_summary": qc_summary}

# db_queries.py

import pandas as pd
import numpy as np
from datetime import datetime
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from sqlalchemy import text
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from db_connection import TrinoConnection
logger = logging.getLogger("db_queries")

TRINO_TYPE_MAP = {
    "drug_link_id": "VARCHAR(100)",
    "drug_url": "VARCHAR(500)",
    "drug_name": "VARCHAR(300)",
    "manufacturer": "VARCHAR(300)",
    "disease_full": "VARCHAR(300)",
    "disease_short": "VARCHAR(300)",
    "indication": "VARCHAR(300)",
    "subindications": "VARCHAR(300)",
    "usage": "VARCHAR(300)",
    "lineoftherapy": "VARCHAR(300)",
    "line_of_therapy_simplified": "VARCHAR(300)",
    "typeoftherapy": "VARCHAR(300)",
    "combinationdrug": "VARCHAR(300)",
    "combo_therapy_flag": "VARCHAR(5)",
    "combo_1": "VARCHAR(300)",
    "combo_2": "VARCHAR(50)",
    "combo_3": "VARCHAR(50)",
    "recommended_dosage": "VARCHAR(400)",
    "cycle_duration": "VARCHAR(300)",
    "duration_of_treatment": "VARCHAR(300)",
    "set_duration_less_than_a_year": "VARCHAR(300)",
    "number_of_cycles": "VARCHAR(300)",
    "treatment_is_based_on": "VARCHAR(300)",
    "method_of_administration": "VARCHAR(300)",
    "patient_group": "VARCHAR(300)",
    "is_pediatric": "VARCHAR(300)",
    "weight(kg)": "VARCHAR(300)",
    "dose_per_kg": "VARCHAR(300)",
    "flat_dose": "VARCHAR(300)",
    "mg_per_dose": "VARCHAR(300)",
    "dosing_segment_used": "VARCHAR(300)",
    "medications": "VARCHAR(300)",
    "notes": "VARCHAR(600)",
    "yearly_dosage": "VARCHAR(300)",
    "yearly_cycles": "VARCHAR(300)",
    "subsequent_year_dosage_in_mg": "VARCHAR(300)",
    "number_of_doses_in_subsequent_years": "VARCHAR(300)",
    "first_year_dosage_in_mg": "VARCHAR(300)",
    "number_of_doses_in_first_year": "VARCHAR(300)",
    "adverse_reaction_study_id": "VARCHAR(200)",
    "adverse_reaction_median_duration_of_treatment": "VARCHAR(200)",
    "adverse_reaction_max_duration_of_treatment": "VARCHAR(200)",
    "adverse_reaction_median_duration_of_treatment_(weeks)": "DECIMAL(38,0)",
    "adverse_reaction_max_duration_of_treatment_(weeks)": "DECIMAL(38,0)",
    "derived_duration_of_treatment": "INTEGER",
    "derived_set_duration_under_1_year": "VARCHAR(10)",
    "created_date": "TIMESTAMP(3)",
    "modified_date": "TIMESTAMP(3)",
    "processed_by": "VARCHAR(300)",
    "active_flag": "VARCHAR(4)",
    "job_id": "VARCHAR(45)"
}


class dbQueryClient:
    def __init__(self, connection):
        self.connection = connection

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
    def execute_query(self, schema, query):
        try:
            logger.info(f"Running query in schema: {schema}")
            engine = self.connection.get_engine(schema)
            df = pd.read_sql(query, engine)
            logger.info(f"Retrieved {len(df)} rows.")
            return df
        except Exception as e:
            logger.exception("[Failed] Failed to execute SELECT query.")
            raise

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
    def deactivate_old_rows(self, schema, table, ndc_list, modified_time):
        ndc_values = ', '.join(f"'{ndc}'" for ndc in ndc_list)
        update_query = f"""
            UPDATE {table}
            SET active_flag = 'N',
                modified_date = TIMESTAMP '{modified_time}'
            WHERE ndc IN ({ndc_values})
            AND active_flag = 'Y'
        """
        logger.info("Deactivating existing records with matching NDCs...")
        self.execute_query(schema, update_query)

    # def format_value(self, v):
    #     if v is None or (isinstance(v, float) and np.isnan(v)):
    #         return "NULL"
    #     elif isinstance(v, (pd.Timestamp, datetime, np.datetime64)):
    #         return f"TIMESTAMP '{pd.to_datetime(v).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}'"
    #     elif isinstance(v, str):
    #         escaped = v.replace("'", "''")
    #         return f"'{escaped}'"
    #     else:
    #         return str(v)

    def format_value(self, v, col):
        dtype = TRINO_TYPE_MAP.get(col, "VARCHAR")
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "NULL"
        elif isinstance(v, (pd.Timestamp, datetime, np.datetime64)):
            val = f"{pd.to_datetime(v).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
            return f"CAST(TIMESTAMP '{val}' AS {dtype})"
        elif isinstance(v, str):
            escaped = v.replace("'", "''")
            return f"CAST('{escaped}' AS {dtype})"
        else:
            return f"CAST({v} AS {dtype})"


    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
    def insert_dataframe(self, df: pd.DataFrame, schema: str, table_name: str, batch_size=100):
        try:
            logger.info(f"Inserting {len(df)} rows into {schema}.{table_name}...")
            print(f"Inserting {len(df)} rows into {schema}.{table_name}...")
            engine = self.connection.get_engine(schema)
            logger.info(f"Engine is created")
            print(f"Engine is created")
            logger.info(f"[DEBUG] self.conn type: {type(self.connection)}, callable: {callable(self.connection)}")
            print(f"[DEBUG] self.conn type: {type(self.connection)}, callable: {callable(self.connection)}")
            with engine.begin() as conn:
                columns = list(df.columns)
                col_string = ",".join([f'"{col}"' for col in columns])
                rows = df.replace({pd.NA: None, float("nan"): None}).to_records(index=False)

                # for i in range(0, len(rows), batch_size):
                #     batch = rows[i:i+batch_size]
                #     values_clause = ", ".join(
                #         f"({', '.join(self.format_value(v) for v in row)})" for row in batch
                #     )
                #     insert_sql = f"""
                #     INSERT INTO {table_name} ({col_string})
                #     VALUES {values_clause}
                #     """
                #     conn.execute(text(insert_sql))

                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i+batch_size]
                    values_clause = ", ".join(
                        "(" + ", ".join(self.format_value(v, col) for v, col in zip(row, columns)) + ")"
                        for row in batch
                    )
                    insert_sql = f'''
                        INSERT INTO {table_name} ({col_string})
                        VALUES {values_clause}
                    '''
                    conn.execute(text(insert_sql))

                    logger.info(f"Inserted rows {i + 1} to {i + len(batch)}")
            logger.info(f"All {len(df)} rows inserted into {schema}.{table_name}.")
            print(f"All {len(df)} rows inserted into {schema}.{table_name}.")
        except Exception:
            logger.exception(f"Failed to insert data into {schema}.{table_name}.")
            print(f"Failed to insert data into {schema}.{table_name}.")
            raise

    def get_existing_drug_link_ids_created_date(self, schema, table_name, drug_urls):
        url_values = ', '.join(f"'{url}'" for url in drug_urls)
        query = f"""
        SELECT drug_url, drug_link_id, created_date, modified_date
        FROM (
            SELECT
                drug_url, drug_link_id, created_date, modified_date,
                ROW_NUMBER() OVER (
                    PARTITION BY drug_url, drug_link_id
                    ORDER BY modified_date ASC
                ) AS row_num
            FROM {table_name}
            WHERE drug_url IN ({url_values})
        )
        WHERE row_num = 1
    """
        return self.execute_query(schema, query)
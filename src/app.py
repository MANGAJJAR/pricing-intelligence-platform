#app.py code file

import sys
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_current_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

from fastapi import FastAPI, HTTPException, File
from fastapi.responses import FileResponse
from datetime import datetime, timezone
import asyncio
import uvicorn
import os
import logging
import pandas as pd
import io
from queue_system import approved_Denied_Payload, DrugQueue, FullProcessRequest, OperationType, JobStatus, SearchRequest, process_single_entry, UploadFile, FilePathRequest, LinkRequest, ProcessExcelRequestWithMetadata
from src.drug_processor import processor
# from queue_system import SectionProcessingStatus
from main import load_checkpoint, load_column_names
from db_queries import dbQueryClient
from db_connection import TrinoConnection
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app and queue
app = FastAPI(title="NDC Processing API", version="2.0.0")
queue = DrugQueue()
CONFIG_JSON_PATH = "config/config.json"

@app.on_event("startup")
async def startup_event():
    """Start the job processing loop on startup"""
    logger.info("[STARTUP] Starting NDC Processing API")
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("outputs", exist_ok=True)
    os.makedirs("log", exist_ok=True)
    os.makedirs("checkpoints", exist_ok=True)

    # Start the processing loop
    asyncio.create_task(queue.process_jobs())
    logger.info("[STARTUP] Job processing loop initiated")

# FILE UPLOAD ENDPOINT
# @app.post("/upload")
# async def upload_file(file: UploadFile = File(...)):
#     """Upload an Excel file for processing"""
#     if not file.filename.endswith(('.xlsx', '.xls')):
#         raise HTTPException(status_code=400, detail="Only Excel files are allowed")

#     # Save uploaded file
#     timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
#     filename = f"uploads/{timestamp}_{file.filename}"

#     with open(filename, "wb") as buffer:
#         shutil.copyfileobj(file.file, buffer)

#     logger.info(f"[UPLOAD] File uploaded: {filename}")

#     return {
#         "message": "File uploaded successfully",
#         "filename": filename,
#         "original_name": file.filename,
#         "size": os.path.getsize(filename)
#     }

@app.post("/search")
async def perform_generic_search(request: SearchRequest):
    """
    Performs a search (normal or advanced) for a given generic name or NDC code on DailyMed.
    """
    try:
        result = await process_single_entry(request.generic_name_or_NDC_code)
        return result
    except Exception as e:
        logger.error(f"Error processing single search for '{request.generic_name_or_NDC_code}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An error occurred during search: {e}")


# @app.post("/upload-excel")
# async def upload_excel_for_search(file: UploadFile = File(...)):
#     """
#     Uploads an Excel file containing a list of generic names or NDC codes
#     in the first column (no header expected).
#     """
#     logger.info(f"Received Excel upload: {file.filename}")
#     if not file.filename.endswith(('.xlsx', '.xls')):
#         logger.warning(f"Invalid file type uploaded: {file.filename}")
#         raise HTTPException(status_code=400, detail="Invalid file type. Please upload an Excel file (.xlsx or .xls).")

#     try:
#         # Read the Excel file into a pandas DataFrame, expecting no header
#         df = pd.read_excel(io.BytesIO(await file.read()), header=None)

#         # Check if the DataFrame is empty or has no columns after reading
#         if df.empty or df.shape[1] == 0:
#             logger.warning("Uploaded Excel file is empty.")
#             raise HTTPException(status_code=400, detail="Excel file is empty or contains no data.")

#         results = []
#         # Iterate over each value in the first column (index 0)
#         # Use .iloc[:, 0] to select all rows from the first column
#         for entry_value_raw in df.iloc[:, 0]:
#             entry_value = str(entry_value_raw) # Ensure it's a string and remove whitespace
#             if entry_value: # Only process non-empty entries
#                 result = await process_single_entry(entry_value)
#                 results.append(result)

#         logger.info(f"Finished processing Excel file.")
#         return {
#             "message": "Excel file processed successfully.",
#             "total_entries_processed": len(results),
#             "results": results
#         }

#     except KeyError:
#         # This catch might not be strictly necessary if we always use .iloc[:, 0]
#         # but good for general robustness if column access logic changes.
#         logger.error("Error accessing expected column in Excel file.", exc_info=True)
#         raise HTTPException(status_code=400, detail="Error accessing expected column. Ensure the first column contains the data.")
#     except Exception as e:
#         logger.critical(f"Critical error processing Excel file: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=f"Error processing file: {e}")

@app.post("/process-excel-by-path-with-metadata", summary="Process an Excel file using specified column names from its server path")
async def process_excel_by_path_with_metadata(request: ProcessExcelRequestWithMetadata):
    """
    Processes an Excel file from a given server path.
    It expects a 'metadata' dictionary to specify which columns to extract
    and what their actual names are in the Excel file.
    The Excel file is expected to have a header row.
    """
    document_path = request.file_path
    column_mapping = request.metadata
    logger.info(f"Received request to process Excel from path: {document_path} with metadata: {column_mapping}")

    # Validate file existence
    if not os.path.exists(document_path):
        logger.warning(f"File not found at path: {document_path}")
        raise HTTPException(status_code=400, detail=f"File not found at path: {document_path}")

    # Validate file type
    if not document_path.lower().endswith(('.xlsx', '.xls')):
        logger.warning(f"Invalid file type for path: {document_path}")
        raise HTTPException(status_code=400, detail="Invalid file type. Please provide an Excel file path (.xlsx or .xls).")

    try:
        # Read the Excel file into a pandas DataFrame.
        # By default, pd.read_excel assumes the first row is the header (header=0).
        df = pd.read_excel(document_path)

        # Check if the DataFrame is empty
        if df.empty:
            logger.warning(f"Excel file at '{document_path}' is empty.")
            raise HTTPException(status_code=400, detail="Excel file is empty or contains no data.")

        all_results = {}
        total_entries_processed = 0

        # Iterate through the metadata to extract and process each specified column
        for logical_name, excel_col_name in column_mapping.items():
            if excel_col_name not in df.columns:
                logger.warning(f"Column '{excel_col_name}' (mapped to '{logical_name}') not found in Excel file '{document_path}'. Available columns: {list(df.columns)}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Column '{excel_col_name}' (mapped to '{logical_name}') not found in the Excel file. Please check your metadata and the Excel file's header names."
                )

            column_results = []
            # Iterate over each value in the specified column
            # Use .dropna() to skip NaN values (empty cells)
            for entry_value_raw in df[excel_col_name].dropna():
                entry_value = str(entry_value_raw).strip() # Ensure string and remove whitespace
                if entry_value: # Only process non-empty, non-whitespace entries
                    result = await process_single_entry(entry_value) # Call your existing processing function
                    column_results.append(result)
                    total_entries_processed += 1

            all_results[logical_name] = column_results

        logger.info(f"Finished processing Excel file from path: {document_path}")
        return {
            "message": "Excel file processed successfully from path using metadata.",
            "processed_file_path": document_path,
            "total_entries_processed": total_entries_processed,
            "results_by_column": all_results # Group results by the logical column name
        }

    except KeyError as e:
        logger.error(f"Metadata key error processing Excel file at path: {document_path}. Error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Invalid column name in metadata or Excel file: {e}. Please ensure actual column names match the Excel headers.")
    except Exception as e:
        logger.critical(f"Critical error processing Excel file from path '{document_path}' with metadata: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing file: {e}. Check server logs for more details.")



# @app.post("/process-excel-by-path", summary="Process an Excel file given its server path")
# async def process_excel_by_path(request: FilePathRequest):
#     """
#     Processes an Excel file from a given server path, extracting records
#     from the first column (no header expected).
#     """
#     document_path = request.file_path
#     logger.info(f"Received request to process Excel from path: {document_path}")

#     # Validate file existence
#     if not os.path.exists(document_path):
#         logger.warning(f"File not found at path: {document_path}")
#         raise HTTPException(status_code=400, detail=f"File not found at path: {document_path}")

#     # Validate file type (optional, but good practice)
#     if not document_path.lower().endswith(('.xlsx', '.xls')):
#         logger.warning(f"Invalid file type for path: {document_path}")
#         raise HTTPException(status_code=400, detail="Invalid file type. Please provide an Excel file path (.xlsx or .xls).")

#     try:
#         # Read the Excel file into a pandas DataFrame from the provided path
#         df = pd.read_excel(document_path, header=None)

#         # Check if the DataFrame is empty or has no columns after reading
#         if df.empty or df.shape[1] == 0:
#             logger.warning(f"Excel file at '{document_path}' is empty.")
#             raise HTTPException(status_code=400, detail="Excel file is empty or contains no data.")

#         results = []
#         # Iterate over each value in the first column (index 0)
#         for entry_value_raw in df.iloc[:, 0]:
#             entry_value = str(entry_value_raw) # Ensure it's a string and remove whitespace
#             if entry_value: # Only process non-empty entries
#                 result = await process_single_entry(entry_value)
#                 results.append(result)

#         logger.info(f"Finished processing Excel file from path: {document_path}")
#         return {
#             "message": "Excel file processed successfully from path.",
#             "processed_file_path": document_path,
#             "total_entries_processed": len(results),
#             "results": results
#         }

#     except FileNotFoundError:
#         # This case is already covered by os.path.exists, but good for robustness
#         logger.error(f"File not found during pandas read for path: {document_path}", exc_info=True)
#         raise HTTPException(status_code=404, detail=f"File not found at path: {document_path}")
#     except KeyError:
#         logger.error(f"Error accessing expected column in Excel file at path: {document_path}", exc_info=True)
#         raise HTTPException(status_code=400, detail="Error accessing expected column. Ensure the first column contains the data.")
#     except Exception as e:
#         logger.critical(f"Critical error processing Excel file from path '{document_path}': {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=f"Error processing file: {e}")

# # @app.post("/process/full", summary="Submit a document for full processing")
# async def submit_full_processing(request: FullProcessRequest):
#     """
#     Submit a document path for a full processing operation.
#     Operation type will be fixed to 'full' and start index to '0'.
#     """
#     if not os.path.exists(request.document_path):
#         raise HTTPException(status_code=400, detail=f"File not found at path: {request.document_path}")

#     # Fixed values for operation_type and start_index
#     job_id = await queue.add_job(
#         operation_type=OperationType.FULL, # Always FULL
#         start_index=0, # Always 0
#         document_path=request.document_path
#     )

#     return {
#         "job_id": job_id,
#         "status": "queued",
#         "operation_type": "full",
#         "message": "Full processing job submitted successfully"
#     }

@app.post("/process_links/full", summary="Submit list of links for full processing")
async def submit_full_link_processing(request: LinkRequest):
    """Submit list of links for full processing"""
    # if not os.path.exists(request.document_path):
    #     raise HTTPException(status_code=400, detail="File not found")

    job_id = await queue.add_job(drug_list=request.drug_link_list, user_id=request.Processed_by, operation_type=OperationType.FULL, genericName=request.genricName, process_type=request.process_type)

    return {
        "job_id": job_id,
        "status": "queued",
        "operation_type": "full",
        "message": "Full processing job submitted successfully"
    }


# @app.post("/process/incremental")
# async def submit_incremental_processing(request: IncrementalRequest):
#     """Submit a document for incremental processing from a specific index"""
#     if not os.path.exists(request.document_path):
#         raise HTTPException(status_code=400, detail="File not found")

#     if request.start_index < 0:
#         raise HTTPException(status_code=400, detail="Start index must be non-negative")

#     job_id = await queue.add_job(
#         request.document_path,
#         OperationType.INCREMENTAL,
#         request.start_index
#     )

#     return {
#         "job_id": job_id,
#         "status": "queued",
#         "operation_type": "incremental",
#         "start_index": request.start_index,
#         "message": f"Incremental processing job submitted from index {request.start_index}"
#     }


# JOB CONTROL ENDPOINTS
@app.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    """Pause a running job"""
    if job_id not in queue.jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await processor.pause_job(job_id)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Update queue job status
    queue.jobs[job_id].status = JobStatus.PAUSED

    return result

@app.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    """Resume a paused job"""
    if job_id not in queue.jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    result = processor.resume_job(job_id)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Update queue job status
    queue.jobs[job_id].status = JobStatus.PROCESSING

    return result

@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a running or paused job"""
    if job_id not in queue.jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    result = processor.cancel_job(job_id)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Update queue job status
    queue.jobs[job_id].status = JobStatus.CANCELLING

    return result

# JOB STATUS ENDPOINTS
@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get detailed status of a specific job"""
    if job_id not in queue.jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = queue.jobs[job_id]
    detailed_status = processor.get_job_status(job_id)

    checkpoint_data = await load_checkpoint(job_id)
    if not checkpoint_data:
        logger.warning(f"[PAUSE] No checkpoint found for job: {job_id}, starting fresh")
    if checkpoint_data:
        # processed = len(checkpoint_data.get('processed_ndcs', []))
        processed_links=set(checkpoint_data.get('processed_ndcs', []))
        # processed_dicts=checkpoint_data.get("processed_dicts", [])
        unprocessed_links=set(checkpoint_data.get('unprocessed_links', []))
    else:
        # processed = 0
        processed_links=0
        processed_dicts=0
        unprocessed_links="not started yet"

    return {
        "user_id": job.user_id,
        "job_id": job_id,
        "genericName": job.genericName,
        "process_type": job.process_type,
        "status": job.status.value,
		"section_processing_status": job.section_status.value,
        "currently_processing_link": job.currently_processing_link,
        "operation_type": job.operation_type.value,
        "document_path": job.document_path,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "start_index": job.start_index,
        "error": job.error,
        "result": job.result,
        "processed_links": processed_links,
        "unprocessed_links": unprocessed_links,
        "detailed_status": detailed_status
    }

@app.get("/jobs")
async def get_all_jobs():
    """Get status of all jobs"""
    jobs_info = []
    for job_id, job in queue.jobs.items():
        checkpoint_data = await load_checkpoint(job_id)
        if not checkpoint_data:
            logger.warning(f"[PAUSE] No checkpoint found for job: {job_id}, starting fresh")
        if checkpoint_data:
            # processed = len(checkpoint_data.get('processed_ndcs', []))
            processed_links=set(checkpoint_data.get('processed_ndcs', []))
            processed_dicts=checkpoint_data.get("processed_dicts", [])
        else:
            # processed = 0
            processed_links=0
            processed_dicts=0
        jobs_info.append({
            "job_id": job_id,
            "status": job.status.value,
			"section_processing_status": job.section_status.value,
            "currently_processing_link": job.currently_processing_link,
            "operation_type": job.operation_type.value,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "start_index": job.start_index,
            "processed_type": job.process_type,
            "genericName": job.genericName,
            "processed_links": processed_links,
            "processed_dicts": processed_dicts
        })

    return {
        "total_jobs": len(jobs_info),
        "jobs": jobs_info,
        "queue_status": queue.get_queue_status()
    }

# FILE DOWNLOAD ENDPOINTS
@app.get("/jobs/{job_id}/download")
async def download_result(job_id: str):
    """Download the result file for a completed job"""
    if job_id not in queue.jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = queue.jobs[job_id]

    if job.status != JobStatus.APPROVAL_QUEUE:
        raise HTTPException(status_code=400, detail="Job is not completed")

    if not job.result or "output_file" not in job.result:
        raise HTTPException(status_code=400, detail="No output file available")

    output_file = job.result["output_file"]
    output_file_ndc_mapping = job.result["output_file_ndc_mapping"]

    if not os.path.exists(output_file):
        raise HTTPException(status_code=404, detail="Output file not found")

    # return FileResponse(
    #     output_file,
    #     media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    #     filename=os.path.basename(output_file)
    # )
    return {"raw_output_file_path" : output_file, "ndc_mapping_output_file_path": output_file_ndc_mapping}

# @app.get("/files/list")
# async def list_uploaded_files():
#     """List all uploaded files"""
#     uploads_dir = "uploads"
#     if not os.path.exists(uploads_dir):
#         return {"files": []}

#     files = []
#     for filename in os.listdir(uploads_dir):
#         if filename.endswith(('.xlsx', '.xls')):
#             filepath = os.path.join(uploads_dir, filename)
#             files.append({
#                 "filename": filename,
#                 "path": filepath,
#                 "size": os.path.getsize(filepath),
#                 "modified": datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
#             })

#     return {"files": files}

# CLEANUP ENDPOINTS
@app.post("/cleanup/jobs")
async def cleanup_old_jobs(max_age_hours: int = 504):
    """Clean up old completed/failed jobs"""
    cleaned_count = processor.cleanup_completed_jobs(max_age_hours)

    # Also clean up from queue
    queue_cleaned = 0
    jobs_to_remove = []
    current_time = datetime.utcnow()

    for job_id, job in queue.jobs.items():
        if job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
            if job.completed_at:
                age_hours = (current_time - job.completed_at).total_seconds() / 3600
                if age_hours > max_age_hours:
                    jobs_to_remove.append(job_id)

    for job_id in jobs_to_remove:
        del queue.jobs[job_id]
        queue_cleaned += 1

    return {
        "message": f"Cleaned up {cleaned_count + queue_cleaned} old jobs",
        "processor_cleaned": cleaned_count,
        "queue_cleaned": queue_cleaned
    }

@app.post("/approve-mmit/")
async def approve_mmit(payload: approved_Denied_Payload):
    import numpy as np
    try:
        #Load CSV
        # df_approved = pd.read_csv(payload.mmit_dict)
        if payload.mmit_dict.endswith(".csv"):
            df_approved = pd.read_csv(payload.mmit_dict)
        elif payload.mmit_dict.endswith(".xlsx"):
            df_approved = pd.read_excel(payload.mmit_dict)
        else:
            raise ValueError("Unsupported file type")

        if df_approved.empty:
            raise HTTPException(status_code=400, detail="CSV file is empty or invalid")
        if payload.job_id not in queue.jobs:
            raise HTTPException(status_code=404, detail="Job not found")

        queue.jobs[payload.job_id].status=JobStatus.APPROVED
        job=queue.jobs[payload.job_id]
        print(job.status)
        # detailed_status = processor.get_job_status(job_id)
        #Adding required metadata columns
        approved_date_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        # df['job_id'] = payload.job_id
        df_approved['approved_denied_by'] = payload.approved_or_denied_by
        df_approved['comments'] = payload.comments
        # df['process_type'] = payload.process_type
        # df['generic_name'] = payload.genericName
        df_approved['status'] = "Approved"
        df_approved['approved_denied_date'] = pd.to_datetime(approved_date_time)
        # df['modified_date'] = pd.to_datetime(now_utc)
        print(df_approved.isna().sum())  # Counts of NaN per column
        print(df_approved.dtypes)        # Data types of columns
        df_approved.replace('NULL', None, inplace=True)
        df_approved.replace({np.nan: None}, inplace=True)
        df_approved = df_approved.where(pd.notnull(df_approved), None)
        print(df_approved.isna().sum())  # Counts of NaN per column
        print(df_approved.dtypes)        # Data types of columns
        columns_to_convert = [
            'drug_link_id', 'job_id', 'drug_url', 'drug_name', 'manufacturer', 'disease_full',
            'disease_short', 'indication', 'subindications', 'usage', 'lineoftherapy',
            'line_of_therapy_simplified', 'typeoftherapy', 'combinationdrug', 'combo_therapy_flag',
            'combo_1', 'combo_2', 'combo_3', 'recommended_dosage', 'cycle_duration',
            'duration_of_treatment', 'set_duration_less_than_a_year', 'number_of_cycles',
            'treatment_is_based_on', 'method_of_administration', 'patient_group', 'is_pediatric',
            'weight(kg)', 'dose_per_kg', 'flat_dose', 'mg_per_dose', 'dosing_segment_used',
            'medications', 'notes', 'yearly_dosage', 'yearly_cycles', 'subsequent_year_dosage_in_mg',
            'number_of_doses_in_subsequent_years', 'first_year_dosage_in_mg',
            'number_of_doses_in_first_year', 'adverse_reaction_study_id',
            'adverse_reaction_median_duration_of_treatment', 'adverse_reaction_max_duration_of_treatment',
            'derived_set_duration_under_1_year', 'processed_by', 'active_flag', 'approved_denied_by',
            'comments', 'status'
        ]
        for col in columns_to_convert:
            if col in df_approved.columns:
                if not pd.api.types.is_string_dtype(df_approved[col]):
                    df_approved[col] = df_approved[col].apply(lambda x: str(x).strip() if pd.notnull(x) else None)
        print(df_approved.dtypes)

        try:
            columns=await load_column_names(CONFIG_JSON_PATH,"trino_dailymed_dosing_master_tbl_column_names")
            df_approved=df_approved[columns]
            # Insert into Trino
            conn = TrinoConnection()
            db_client = dbQueryClient(conn)

            trino_schema="z_abv_cws_bts_ps_data_analytics"
            master_table="dailymed_dosing_master"
            db_client.insert_dataframe(df=df_approved,schema=trino_schema,table_name=master_table)

        except Exception as e:
            logger.error(f"dailymed_dosing_master table insert_dataframe failed: {str(e)}")

        return {"status": "success - Approved", "message": "Data approved and inserted"}

    except Exception as e:
        logging.exception("Error processing approval")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/denied-mmit/")
async def denied_mmit(payload: approved_Denied_Payload):
    import numpy as np
    try:
        #Load CSV
        # df_approved = pd.read_csv(payload.mmit_dict)
        if payload.mmit_dict.endswith(".csv"):
            df_approved = pd.read_csv(payload.mmit_dict)
        elif payload.mmit_dict.endswith(".xlsx"):
            df_approved = pd.read_excel(payload.mmit_dict)
        else:
            raise ValueError("Unsupported file type")

        if df_approved.empty:
            raise HTTPException(status_code=400, detail="CSV file is empty or invalid")
        if payload.job_id not in queue.jobs:
            raise HTTPException(status_code=404, detail="Job not found")

        queue.jobs[payload.job_id].status=JobStatus.DENIED
        job=queue.jobs[payload.job_id]

        #Adding required metadata columns
        denied_date_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        # df['job_id'] = payload.job_id
        df_approved['approved_denied_by'] = payload.approved_or_denied_by
        df_approved['comments'] = payload.comments
        # df['process_type'] = payload.process_type
        # df['generic_name'] = payload.genericName
        df_approved['status'] = "Denied"
        df_approved['approved_denied_date'] = pd.to_datetime(denied_date_time)
        # df['modified_date'] = pd.to_datetime(now_utc)
        print(df_approved.isna().sum())  # Counts of NaN per column
        print(df_approved.dtypes)        # Data types of columns
        df_approved.replace('NULL', None, inplace=True)
        df_approved.replace({np.nan: None}, inplace=True)
        df_approved = df_approved.where(pd.notnull(df_approved), None)
        print(df_approved.isna().sum())  # Counts of NaN per column
        print(df_approved.dtypes)        # Data types of columns
        columns_to_convert = [
            'drug_link_id', 'job_id', 'drug_url', 'drug_name', 'manufacturer', 'disease_full',
            'disease_short', 'indication', 'subindications', 'usage', 'lineoftherapy',
            'line_of_therapy_simplified', 'typeoftherapy', 'combinationdrug', 'combo_therapy_flag',
            'combo_1', 'combo_2', 'combo_3', 'recommended_dosage', 'cycle_duration',
            'duration_of_treatment', 'set_duration_less_than_a_year', 'number_of_cycles',
            'treatment_is_based_on', 'method_of_administration', 'patient_group', 'is_pediatric',
            'weight(kg)', 'dose_per_kg', 'flat_dose', 'mg_per_dose', 'dosing_segment_used',
            'medications', 'notes', 'yearly_dosage', 'yearly_cycles', 'subsequent_year_dosage_in_mg',
            'number_of_doses_in_subsequent_years', 'first_year_dosage_in_mg',
            'number_of_doses_in_first_year', 'adverse_reaction_study_id',
            'adverse_reaction_median_duration_of_treatment', 'adverse_reaction_max_duration_of_treatment',
            'derived_set_duration_under_1_year', 'processed_by', 'active_flag', 'approved_denied_by',
            'comments', 'status'
        ]
        for col in columns_to_convert:
            if col in df_approved.columns:
                if not pd.api.types.is_string_dtype(df_approved[col]):
                    df_approved[col] = df_approved[col].apply(lambda x: str(x).strip() if pd.notnull(x) else None)
        print(df_approved.dtypes)
        try:
            columns=await load_column_names(CONFIG_JSON_PATH,"trino_dailymed_dosing_master_tbl_column_names")
            df_approved=df_approved[columns]
            # Insert into Trino
            conn = TrinoConnection()
            db_client = dbQueryClient(conn)

            trino_schema="z_abv_cws_bts_ps_data_analytics"
            master_table="dailymed_dosing_master"
            db_client.insert_dataframe(df=df_approved,schema=trino_schema,table_name=master_table)
        except Exception as e:
            logger.error(f"dailymed_dosing_master table insert_dataframe failed: {str(e)}")

        return {"status": "Denied", "message": "Data Denied and inserted"}

    except Exception as e:
        logging.exception("Error processing approval")
        raise HTTPException(status_code=500, detail=str(e))

from fastapi import Query

@app.get("/jobs/{job_id}/download_qc")
async def download_qc_result(job_id: str, file_type: str = Query("processed", enum=["processed","qc","rectified","error_log"])):
    if job_id not in queue.jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = queue.jobs[job_id]
    result = job.result or {}

    file_map = {
        "processed": result.get("output_file"),
        "qc": result.get("qc_results_file"),
        "rectified": result.get("rectified_file"),
        "error_log": result.get("error_log_file")
    }
    file_path = file_map.get(file_type)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Requested file for '{file_type}' not found")
    return FileResponse(file_path, filename=os.path.basename(file_path), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# NDC status tracker
@app.get("/process-section-status")
async def get_processing_status():
    """Get current processing status of all NDCs"""
    return queue.status_tracker.get_status()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
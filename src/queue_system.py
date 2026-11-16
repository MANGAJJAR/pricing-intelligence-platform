#queue_system.py code file

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from enum import Enum
from typing import Dict, Optional, List, Callable, Awaitable, Any
from datetime import datetime
import uuid
import asyncio
import uvicorn
import os
import shutil
import logging
try:
    from src.drug_processor import processor
except ModuleNotFoundError:
    from drug_processor import processor
from pydantic import BaseModel
from typing import Dict, Any, List
import pandas as pd
import io
import logging
from datetime import datetime, timezone

from section_status import SectionProcessingStatus
from generic_name_NDC_link_main import convert_generic_name, extract_drug_info, is_NDC, if_NDC

now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
now_str = now.replace('-', '').replace(':', '').replace(' ', '_')

# Configure logging
logging.basicConfig(level=logging.INFO)
#logger = logging.getLogger(__name__)
logger = logging.getLogger("QueueSystem")

class JobStatus(Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLING = "cancelling"
    APPROVAL_QUEUE= "approval queue"
    APPROVED = "Approved"
    DENIED = "Denied"

class OperationType(Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    RESUME = "resume"

class Job(BaseModel):
    id: str
    user_id: str
    status: JobStatus
    section_status: Optional[SectionProcessingStatus] = SectionProcessingStatus.NOT_STARTED
    currently_processing_link: str
    document_path: Optional[str] = None
    drug_link_list: Optional[List[Dict]]= None
    operation_type: OperationType
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[dict] = None
    start_index: Optional[int] = 0
    process_type: Optional[str] =None
    genericName: Optional[str] = None

# Pydantic model for the request body for single search
class SearchRequest(BaseModel):
    generic_name_or_NDC_code: str

class approved_Denied_Payload(BaseModel):
    job_id: str
    processed_by: str
    comments: str
    mmit_dict: str  # Path to CSV file
    process_type: str
    genericName: str
    approved_or_denied_by: str

class ProcessExcelRequestWithMetadata(BaseModel):
    file_path: str = Field(..., example="C:\\Users\\xyz\\Documents\\AbbVie\\Daily_med\\main\\inputs\\input_file.xlsx")
    metadata: dict[str, str] = Field(
        ...,
        example={"NDC": "NDC_col", "Generic_Name": "Generic_Name_col"},
        description="A dictionary mapping logical column names to actual Excel column names."
    )

class FilePathRequest(BaseModel):
    file_path: str

class FullProcessRequest(BaseModel):
    document_path: str

# class LinkRequest(BaseModel):
#     drug_link_list: list
    # operation_type: Optional[OperationType] = OperationType.FULL
    # start_index: Optional[int] = 0
class DrugLinkItem(BaseModel):
    Label: str
    Drug_URL: str  # Will correctly map from "Drug URL"
    NDCs: List[str]
    Packager: str

class LinkRequest(BaseModel):
    drug_link_list: List[DrugLinkItem]
    Processed_by: str
    genricName: str
    process_type: str

# class LinkRequest(BaseModel):
#     drug_link_list: list
    # operation_type: Optional[OperationType] = OperationType.FULL
    # start_index: Optional[int] = 0

class IncrementalRequest(BaseModel):
    document_path: str
    start_index: int

class ResumeRequest(BaseModel):
    job_id: str

class ProcessingStatus:
    def __init__(self):
        self.current_status: Dict[str, str] = {}  # Format: {"NDC": "section_being_processed"}

    def update_status(self, ndc: str, section: str):
        self.current_status[ndc] = section

    def clear_status(self, ndc: str):
        if ndc in self.current_status:
            del self.current_status[ndc]

    def get_status(self) -> Dict[str, str]:
        return self.current_status

class DrugQueue:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}
        self.queue = asyncio.Queue()
        self.active_tasks = set()
        self.processing = False
        #self.status_tracker = ProcessingStatus()

    async def add_job(self, operation_type: OperationType = OperationType.FULL,
                     start_index: int = 0, document_path: str=None, drug_list:List[DrugLinkItem]=None, user_id:str=None, genericName:str=None, process_type:str=None) -> str:
        """Add a new job to the queue"""
        job_id = str(uuid.uuid4())
        print(f"document_path:{document_path}")
        print(type(document_path))
        print(drug_list)
        print(type(drug_list))
        job = Job(
            id=job_id,
            user_id=user_id,
            status=JobStatus.QUEUED,
			section_status=SectionProcessingStatus.NOT_STARTED,
            currently_processing_link="",
            document_path=document_path,
			drug_link_list=[item.dict() for item in drug_list],
            operation_type=operation_type,
            created_at=datetime.utcnow(),
            start_index=start_index,
            process_type=process_type,
            genericName=genericName
        )
        self.jobs[job_id] = job
        await self.queue.put(job_id)
        logger.info(f"[QUEUE] Added {operation_type.value} job {job_id} to queue")
        return job_id

    async def add_resume_job(self, original_job_id: str, document_path: str) -> str:
        """Add a resume job for an existing job"""
        job_id = f"resume_{original_job_id}_{str(uuid.uuid4())[:8]}"
        job = Job(
            id=job_id,
            status=JobStatus.QUEUED,
			section_status=SectionProcessingStatus.NOT_STARTED,
            currently_processing_link="NOT STARTED",
            document_path=document_path,
            operation_type=OperationType.RESUME,
            created_at=datetime.utcnow()
        )
        self.jobs[job_id] = job
        await self.queue.put(job_id)
        logger.info(f"[QUEUE] Added resume job {job_id} for original job {original_job_id}")
        return job_id

	# Added new function for section processing status updates
    async def _update_job_section_status(self, job_id: str, new_status: str, currently_processing_link:str):
        if job_id in self.jobs:
            try:
                self.jobs[job_id].section_status = SectionProcessingStatus(new_status)
                self.jobs[job_id].currently_processing_link = currently_processing_link
                logger.info(f"[JOB {job_id}] Section status updated to: {new_status}")
            except ValueError:
                logger.warning(f"[JOB {job_id}] Invalid section status: {new_status}. Skipping update.")
        else:
            logger.warning(f"[JOB {job_id}] Job not found when trying to update section status.")

    async def process_jobs(self):
        """Main job processing loop"""
        logger.info("[QUEUE] Job processing loop started")
        while True:
            job_id = None  # Initialize job_id to None
            try:
                job_id = await self.queue.get()
                job = self.jobs[job_id]

                logger.info(f"[QUEUE] Processing job {job_id} ({job.operation_type.value})")

                job.status = JobStatus.PROCESSING
                job.started_at = datetime.utcnow()
                job.section_status = SectionProcessingStatus.NOT_STARTED # Ensure it's reset or explicitly set
                job.currently_processing_link = "NOT STARTED"

                # Create a partial function for the section status update callback
                update_callback = lambda status, currently_processing_link: self._update_job_section_status(job_id, status, currently_processing_link) # New: Create callback

                result = None # Initialize result


                # Create document processing task
                if job.document_path and job.document_path is not None:
                    task = asyncio.create_task(
                        processor.process_ndc_document(
                            job.document_path,
                            job_id,
                            job.operation_type.value,
                            job.start_index or 0,
							update_section_status=update_callback
                        )
                    )

                    self.active_tasks.add(task)
                    task.add_done_callback(lambda t: self.active_tasks.discard(t))

                    # Wait for completion
                    result = await task

                # Create link processing task
                if job.drug_link_list:
                    task = asyncio.create_task(
                        processor.process_drug_links(
                            drug_link_list=job.drug_link_list,
                            job_id=job_id,
                            operation_type=job.operation_type.value,
                            start_index=job.start_index or 0,
							update_section_status=update_callback,
                            user_id=job.user_id

                        )
                    )
                    self.active_tasks.add(task)
                    task.add_done_callback(lambda t: self.active_tasks.discard(t))

                    # Wait for completion
                    result = await task

				# Ensure result is set after one of the processing paths is taken
                if result is None:
                    # This case should ideally not happen if either document_path or drug_link_list is always set.
                    # Add error handling or a default completed state.
                    job.status = JobStatus.FAILED
                    job.error = "No processing path taken (neither document_path nor drug_link_list provided)."
                    job.section_status = SectionProcessingStatus.FAILED_SECTION
                    logger.error(f"[QUEUE] Job {job_id} failed: {job.error}")
                    continue # Continue to the next job

                # Update job status based on result
                if result["status"] == "completed":
                    print(result)
                    # now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    # now_str = now.replace('-', '').replace(':', '').replace(' ', '_')
                    job.status = JobStatus.APPROVAL_QUEUE
                    #job.result = result
                    job.result = {
                        "output_file": f"outputs/Async_Final_Dosage_details_{job_id}_{now_str}.xlsx",
                        "qc_results_file": f"output/query_qc_results_data_{job_id}_{now_str}.xlsx",
                        "rectified_file": f"output/rectified_data_final_{job_id}_{now_str}.xlsx",
                        "error_log_file": f"log/error_log_{job_id}_{now_str}.xlsx"
                    }

                    job.section_status = SectionProcessingStatus.COMPLETED_SECTION # Final section status
                    job.currently_processing_link ="Processed all links"
                    logger.info(f"[QUEUE] Job {job_id} completed successfully")
                elif result["status"] == "cancelled":
                    job.status = JobStatus.CANCELLED
                    #job.result = result
                    job.result = {
                        "output_file": f"outputs/Async_Final_Dosage_details_{job_id}_{now_str}.xlsx",
                        "qc_results_file": f"output/query_qc_results_data_{job_id}_{now_str}.xlsx",
                        "rectified_file": f"output/rectified_data_final_{job_id}_{now_str}.xlsx",
                        "error_log_file": f"log/error_log_{job_id}_{now_str}.xlsx"
                    }
                    job.section_status = SectionProcessingStatus.FAILED_SECTION # Or a specific cancelled section status if needed

                    logger.info(f"[QUEUE] Job {job_id} was cancelled")
                else:
                    job.status = JobStatus.FAILED
                    job.error = result.get("error")
                    job.section_status = SectionProcessingStatus.FAILED_SECTION # Final section status on failure
                    logger.error(f"[QUEUE] Job {job_id} failed: {job.error}")

            except Exception as e:
                logger.error(f"[QUEUE] Error processing job {job_id}: {e}")
                if job_id in self.jobs:
                    self.jobs[job_id].status = JobStatus.FAILED
                    self.jobs[job_id].error = str(e)
                    self.jobs[job_id].section_status = SectionProcessingStatus.FAILED_SECTION

            finally:
                if job_id in self.jobs:
                    self.jobs[job_id].completed_at = datetime.utcnow()
                self.queue.task_done()

    def get_queue_status(self) -> Dict:
        """Get overall queue status"""
        total_jobs = len(self.jobs)
        status_counts = {}
        section_status_counts = {} # New: for section status breakdown


        for job in self.jobs.values():
            status = job.status.value
            status_counts[status] = status_counts.get(status, 0) + 1
			# New: Count section statuses
            section_status = job.section_status.value
            section_status_counts[section_status] = section_status_counts.get(section_status, 0) + 1


        return {
            "total_jobs": total_jobs,
            "queue_size": self.queue.qsize(),
            "active_tasks": len(self.active_tasks),
            "status_breakdown": status_counts,
			"section_status_breakdown": section_status_counts # New: Added section status breakdown
        }

# Helper function to process a single entry (reusing your existing logic)
async def process_single_entry(entry_value: str) -> Dict[str, Any]:
    """
    Processes a single generic name or NDC code and returns its search results.
    """
    logger.info(f"Processing entry: '{entry_value}'")
    generic_name_or_NDC_code = entry_value
    # genericName_or_NDC_type = "" # This variable is not used after assignment, can be removed if not needed

    if is_NDC(generic_name_or_NDC_code):
        converted = if_NDC(generic_name_or_NDC_code)
        # genericName_or_NDC_type = "NDC"
    else:
        converted = convert_generic_name(generic_name_or_NDC_code)
        # genericName_or_NDC_type = "generic"

    labels, drug_urls, NDCs_list, packagers, search_type = extract_drug_info(converted)

    drug_info_list = []
    if drug_urls: # Ensure drug_urls is not None and not empty
        for i in range(len(labels)):
            label = labels[i]
            drug_url = drug_urls[i]
            ndcs_of_one = NDCs_list[i]
            packager = packagers[i]
            drug_info_list.append({"Label": label, "Drug URL": drug_url, "NDCs": ndcs_of_one, "Packager": packager})

    response_data_single = {
        "Original Generic Name or NDC": generic_name_or_NDC_code,
        "Converted Generic Name or NDC": converted,
        "No. of Results": len(drug_info_list) if drug_info_list else 0,
        "Drug Information": drug_info_list if drug_info_list else f"No drug information found for '{generic_name_or_NDC_code}'.",
        "Search Type": search_type # Renamed from "Search" to "Search Type" for clarity consistent with genericName_or_NDC_type if you were to use it.
    }

    logger.info(f"Finished processing entry: '{entry_value}'. Results found: {len(drug_info_list)}")

    return response_data_single

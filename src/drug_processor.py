#drug_processor.py code file

import os
from datetime import datetime
from typing import Dict, Callable, Awaitable, List
import logging
from typing import List
from main import main, load_checkpoint, get_operation_status, pause_operation, resume_operation, cancel_operation

from orchestration import full_pipeline  # Import new orchestrator

#logger = logging.getLogger(__name__)
logger = logging.getLogger("DrugProcessor")
class DrugProcessor:
    """Enhanced drug processor with operation controls"""

    def __init__(self):
        self.active_jobs = {}  # Track active jobs

    async def process_ndc_document(self, document_path: str, job_id: str,
                                 operation_type: str = "full",
                                 start_index: int = 0,
                                 status_tracker=None, drug_link_list: List[dict]=None,
								 update_section_status: Callable[[str], Awaitable[None]] = None, user_id:str=None) -> Dict:
        """
        Enhanced wrapper around main() with job tracking and operation controls

        Args:
            document_path: Path to input Excel file
            job_id: Unique job identifier
            operation_type: Type of operation ('full', 'incremental', 'resume')
            start_index: Starting index for incremental processing
			update_section_status: Callback to update the processing section status

        """
        try:
            # Prepare audit log
            audit_log = {
                "job_id": job_id,
                "document": os.path.basename(document_path),
                "start_time": datetime.utcnow().isoformat(),
                "status": "processing",
                "operation_type": operation_type,
                "start_index": start_index
            }

            # Track active job
            self.active_jobs[job_id] = {
                "status": "processing",
                "start_time": datetime.utcnow(),
                "document_path": document_path,
                "operation_type": operation_type
            }

            logger.info(f"[START] Starting {operation_type} processing for job: {job_id}")

            # Handle different operation types
            if operation_type == "resume":
                result = await self._handle_resume_operation(job_id=job_id, document_path=document_path, update_section_status=update_section_status,user_id=user_id)
            elif operation_type == "incremental":
                result = await self._handle_incremental_operation(job_id=job_id, document_path=document_path, start_index=start_index, update_section_status=update_section_status,user_id=user_id)
            else:  # full processing
                result = await self._handle_full_operation(job_id=job_id, document_path=document_path, update_section_status=update_section_status,user_id=user_id)

            # Update job status
            if result["status"] == "completed":
                self.active_jobs[job_id]["status"] = "completed"
                self.active_jobs[job_id]["output_file"] = result.get("output_file")
                self.active_jobs[job_id]["end_time"] = datetime.utcnow()

                logger.info(f"[COMPLETED] Job {job_id} completed successfully")

                return {
                    "status": "completed",
                    "output_file": result.get("output_file"),
                    "document": document_path,
                    "job_id": job_id,
                    "total_processed": result.get("total_processed", 0),
                    "execution_time": result.get("execution_time", "N/A"),
                    "errors": result.get("errors", 0)
                }
            elif result["status"] == "cancelled":
                self.active_jobs[job_id]["status"] = "cancelled"
                self.active_jobs[job_id]["end_time"] = datetime.utcnow()

                logger.info(f"[CANCELLED] Job {job_id} was cancelled")

                return {
                    "status": "cancelled",
                    "job_id": job_id,
                    "processed_count": result.get("processed_count", 0),
                    "checkpoint_saved": result.get("checkpoint_saved", False),
                    "message": "Job was cancelled. You can resume it later."
                }
            else:
                # Handle failed status
                self.active_jobs[job_id]["status"] = "failed"
                self.active_jobs[job_id]["error"] = result.get("error")
                self.active_jobs[job_id]["end_time"] = datetime.utcnow()

                logger.error(f"[FAILED] Job {job_id} failed: {result.get('error')}")

                return {
                    "status": "failed",
                    "error": result.get("error"),
                    "job_id": job_id,
                    "document": document_path,
                    "processed_count": result.get("processed_count", 0)
                }

        except Exception as e:
            logger.error(f"[ERROR] Exception in process_ndc_document: {e}")

            # Update job status
            if job_id in self.active_jobs:
                self.active_jobs[job_id]["status"] = "failed"
                self.active_jobs[job_id]["error"] = str(e)
                self.active_jobs[job_id]["end_time"] = datetime.utcnow()

            return {
                "status": "failed",
                "error": str(e),
                "job_id": job_id,
                "document": document_path
            }

    async def process_drug_links(self, job_id: str,
                                    operation_type: str = "full",
                                    start_index: int = 0, drug_link_list: List[dict]=None,
									update_section_status: Callable[[str], Awaitable[None]] = None, user_id:str =None) -> Dict:
            try:
                # Prepare audit log
                # audit_log = {
                #     "job_id": job_id,
                #     "document": os.path.basename(document_path),
                #     "start_time": datetime.utcnow().isoformat(),
                #     "status": "processing",
                #     "operation_type": operation_type,
                #     "start_index": start_index
                # }

                # Track active job
                self.active_jobs[job_id] = {
                    "status": "processing",
                    "start_time": datetime.utcnow(),
                    # "document_path": document_path,
                    "operation_type": operation_type
                }

                logger.info(f"[START] Starting {operation_type} processing for job: {job_id}")

                # Handle different operation types
                if operation_type == "resume":
                    result = await self._handle_resume_operation(job_id=job_id, drug_link_list=drug_link_list, update_section_status=update_section_status,user_id=user_id)
                elif operation_type == "incremental":
                    result = await self._handle_incremental_operation(job_id=job_id, drug_link_list=drug_link_list, start_index=start_index, update_section_status=update_section_status,user_id=user_id)
                else:  # full processing
                    result = await self._handle_full_operation(job_id=job_id, drug_link_list=drug_link_list, update_section_status=update_section_status,user_id=user_id)

                # Update job status
                if result["status"] == "completed":
                    self.active_jobs[job_id]["status"] = "completed"
                    self.active_jobs[job_id]["output_file"] = result.get("output_file")
                    self.active_jobs[job_id]["end_time"] = datetime.utcnow()

                    logger.info(f"[COMPLETED] Job {job_id} completed successfully")

                    return {
                        "status": "completed",
                        "output_file": result.get("output_file"),
                        # "document": document_path,
                        "output_file_ndc_mapping": result.get("output_file_ndc_mapping"),
                        "job_id": job_id,
                        "total_processed": result.get("total_processed", 0),
                        "execution_time": result.get("execution_time", "N/A"),
                        "errors": result.get("errors", 0)
                    }
                elif result["status"] == "cancelled":
                    self.active_jobs[job_id]["status"] = "cancelled"
                    self.active_jobs[job_id]["end_time"] = datetime.utcnow()

                    logger.info(f"[CANCELLED] Job {job_id} was cancelled")

                    return {
                        "status": "cancelled",
                        "job_id": job_id,
                        "processed_count": result.get("processed_count", 0),
                        "checkpoint_saved": result.get("checkpoint_saved", False),
                        "message": "Job was cancelled. You can resume it later."
                    }
                else:
                    # Handle failed status
                    self.active_jobs[job_id]["status"] = "failed"
                    self.active_jobs[job_id]["error"] = result.get("error")
                    self.active_jobs[job_id]["end_time"] = datetime.utcnow()

                    logger.error(f"[FAILED] Job {job_id} failed: {result.get('error')}")

                    return {
                        "status": "failed",
                        "error": result.get("error"),
                        "job_id": job_id,
                        # "document": document_path,
                        "processed_count": result.get("processed_count", 0)
                    }

            except Exception as e:
                logger.error(f"[ERROR] Exception in process_ndc_document: {e}")

                # Update job status
                if job_id in self.active_jobs:
                    self.active_jobs[job_id]["status"] = "failed"
                    self.active_jobs[job_id]["error"] = str(e)
                    self.active_jobs[job_id]["end_time"] = datetime.utcnow()

                return {
                    "status": "failed",
                    "error": str(e),
                    "job_id": job_id,
                    # "document": document_path
                }


    async def _handle_full_operation(self, job_id: str, document_path: str=None, drug_link_list:List[dict]=None,
									update_section_status: Callable[[str], Awaitable[None]] = None,user_id:str=None) -> Dict:
        """Handle full processing operation"""
        logger.info(f"[FULL] Starting full processing for job: {job_id}")
        logger.info(f"[DEBUG] input_file type: {type(document_path)}, value: {document_path}")
        logger.info(f"[DEBUG] list_links type: {type(drug_link_list)}, length: {len(drug_link_list) if drug_link_list else 0}")


        res = await full_pipeline(job_id=job_id, input_file=document_path, list_links=drug_link_list)
        return res
        #return await main(job_id=job_id, input_file=document_path, update_section_status=update_section_status, list_links=drug_link_list,user_id=user_id)

    async def _handle_incremental_operation(self, job_id: str, start_index: int, document_path: str=None, drug_link_list:List[dict]=None,
                                            update_section_status: Callable[[str], Awaitable[None]] = None,user_id:str=None) -> Dict:

        """Handle incremental processing operation"""
        logger.info(f"[INCREMENTAL] Starting incremental processing for job: {job_id} from index: {start_index}")
        return await main(job_id=job_id, input_file=document_path, start_index=start_index, list_links=drug_link_list,update_section_status=update_section_status,user_id=user_id)

    async def _handle_resume_operation(self, job_id: str, document_path: str=None, drug_link_list:List[dict]=None,
										update_section_status: Callable[[str], Awaitable[None]] = None,user_id:str=None) -> Dict:
        """Handle resume processing operation"""
        logger.info(f"[RESUME] Attempting to resume job: {job_id}")

        # Load checkpoint data
        checkpoint_data = await load_checkpoint(job_id)

        if not checkpoint_data:
            logger.warning(f"[RESUME] No checkpoint found for job: {job_id}, starting fresh")
            return await main(job_id=job_id, input_file=document_path, update_section_status=update_section_status,user_id=user_id)

        logger.info(f"[RESUME] Resuming job: {job_id} from checkpoint with {len(checkpoint_data.get('processed_ndcs', []))} processed NDCs")

        return await main(
            job_id=job_id,
            input_file=document_path,
            start_index=checkpoint_data.get('current_index', 0),
            processed_ndcs=set(checkpoint_data.get('processed_ndcs', [])),
            resume_data=checkpoint_data,
			list_links=drug_link_list,
            update_section_status=update_section_status,
            user_id=user_id
        )

    def get_job_status(self, job_id: str) -> Dict:
        """Get detailed job status"""
        if job_id not in self.active_jobs:
            return {"error": "Job not found"}

        job_info = self.active_jobs[job_id].copy()

        # Get real-time operation status if job is active
        if job_info["status"] == "processing":
            operation_status = get_operation_status()
            if "job_id" in operation_status and operation_status["job_id"] == job_id:
                job_info.update(operation_status)

        return job_info

    async def pause_job(self, job_id: str) -> Dict:
        """Pause a running job"""
        if job_id not in self.active_jobs:
            return {"error": "Job not found"}

        if self.active_jobs[job_id]["status"] != "processing":
            return {"error": "Job is not currently processing"}

        pause_operation()
        self.active_jobs[job_id]["status"] = "paused"

        logger.info(f"[PAUSE] Job {job_id} paused")
        # return {"message": f"Job {job_id} paused successfully"}

        # if controller is None:
        #     return {"error": "Controller is not initialized"}
        # processed_ndcs_list = list(controller.processed_ndcs)
        # all_ndcs = getattr(controller, "all_ndcs", [])
        # pending_ndcs_list = list(set(all_ndcs) - controller.processed_ndcs)

        # logger.info(f"[PAUSE] Job {job_id} paused. Processed: {len(processed_ndcs_list)} | Pending: {len(pending_ndcs_list)}")

        # return {
        #     "message": f"Job {job_id} paused successfully",
        #     "processed_count": len(processed_ndcs_list),
        #     "pending_count": len(pending_ndcs_list),
        #     "processed_ndcs": processed_ndcs_list,
        #     "pending_ndcs": pending_ndcs_list
        # }
        checkpoint_data = await load_checkpoint(job_id)
        if not checkpoint_data:
            logger.warning(f"[PAUSE] No checkpoint found for job: {job_id}, starting fresh")
        if checkpoint_data:
            processed = len(checkpoint_data.get('processed_ndcs', []))
            processed_limks=set(checkpoint_data.get('processed_ndcs', []))
            processed_dicts=checkpoint_data.get("processed_dicts", [])
        else:
            processed = 0
            processed_limks=0
            processed_dicts=0

        logger.info(f"[PAUSE] Job {job_id} paused. Processed: {processed} | Pending: ")
        # logger.info(f"[PAUSE] Job {job_id} paused. Processed: {len(checkpoint_data.get('processed_ndcs', []))} | Pending: ")
        # logger.info(f"[RESUME] Resuming job: {job_id} from checkpoint with {len(checkpoint_data.get('processed_ndcs', []))} processed NDCs")

        # return await main(
        #     job_id=job_id,
        #     input_file=document_path,
        #     start_index=checkpoint_data.get('current_index', 0),
        #     processed_ndcs=set(checkpoint_data.get('processed_ndcs', [])),
        #     resume_data=checkpoint_data
        # )
        return {
                "message": f"Job {job_id} paused successfully",
                # "processed_count": len(processed_ndcs_list),
                # "pending_count": len(pending_ndcs_list),
                "processed_links": processed_limks,
                # "pending_ndcs": pending_ndcs_list,
                "processed_dicts": processed_dicts
            }
        # return {"message": f"Job {job_id} paused successfully"}


    def resume_job(self, job_id: str) -> Dict:
        """Resume a paused job"""
        if job_id not in self.active_jobs:
            return {"error": "Job not found"}

        if self.active_jobs[job_id]["status"] == "completed":
            return {"status": "Job is completed"}

        if self.active_jobs[job_id]["status"] != "paused":
            return {"error": "Job is not currently paused"}

        resume_operation()
        self.active_jobs[job_id]["status"] = "processing"

        logger.info(f"[RESUME] Job {job_id} resumed")
        return {"message": f"Job {job_id} resumed successfully"}

    def cancel_job(self, job_id: str) -> Dict:
        """Cancel a running or paused job"""
        if job_id not in self.active_jobs:
            return {"error": "Job not found"}

        if self.active_jobs[job_id]["status"] not in ["processing", "paused"]:
            return {"error": "Job cannot be cancelled in its current state"}

        cancel_operation()
        self.active_jobs[job_id]["status"] = "cancelling"

        logger.info(f"[CANCEL] Job {job_id} cancellation requested")
        return {"message": f"Job {job_id} cancellation requested"}

    def get_all_jobs(self) -> Dict:
        """Get status of all jobs"""
        return {
            "active_jobs": len([j for j in self.active_jobs.values() if j["status"] == "processing"]),
            "total_jobs": len(self.active_jobs),
            "jobs": self.active_jobs
        }

    def cleanup_completed_jobs(self, max_age_hours: int = 24) -> int:
        """Clean up old completed/failed jobs"""
        current_time = datetime.utcnow()
        cleaned_count = 0

        jobs_to_remove = []
        for job_id, job_info in self.active_jobs.items():
            if job_info["status"] in ["completed", "failed", "cancelled"]:
                if "end_time" in job_info:
                    age_hours = (current_time - job_info["end_time"]).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        jobs_to_remove.append(job_id)

        for job_id in jobs_to_remove:
            del self.active_jobs[job_id]
            cleaned_count += 1

        logger.info(f"[CLEANUP] Cleaned up {cleaned_count} old jobs")
        return cleaned_count

# Global processor instance
processor = DrugProcessor()

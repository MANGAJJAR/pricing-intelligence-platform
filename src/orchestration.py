#orchestration.py code file

import asyncio
from main import main as run_main
from qc_error import run_qc
from qc_rectification import run_rectification

async def full_pipeline(job_id, input_file, **kwargs):
    # Step 1: Run main.py logic
    main_result = await run_main(job_id=job_id, input_file=input_file, **kwargs)
    if main_result.get("status") != "completed":
        return {**main_result, "step": "main_processing"}
    # Step 2: Run QC (qc_error.py logic)
    qc_result = run_qc(job_id=job_id)
    # Step 3: Run rectification (qc_rectification.py logic)
    rectification_result = await run_rectification(job_id=job_id)
    return {
        "status": "completed",
        "step": "full_pipeline",
        "main": main_result,
        "qc": qc_result,
        "rectification": rectification_result,
    }

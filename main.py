import os
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from dotenv import load_dotenv
from processor import process_pdf_from_url
from typing import Dict, Any

# I-load ang environment variables mula sa .env file (para sa local dev)
load_dotenv()

app = FastAPI()

# I-define ang structure ng config na inaasahan
class ProcessRequest(BaseModel):
    pdf_file_id: str
    config: Dict[str, Any] # Tumanggap ng isang generic na dictionary

@app.on_event("startup")
async def startup_event():
    # Tiyakin na ang Vercel Blob environment variables ay naka-set
    if not os.getenv('BLOB_STORE_ID') or not os.getenv('BLOB_READ_WRITE_TOKEN'):
        raise RuntimeError("BLOB_STORE_ID and BLOB_READ_WRITE_TOKEN must be set in environment variables.")

@app.get("/")
async def root():
    return {"greeting": "Hello, World!", "message": "Welcome to FastAPI!"}

@app.post("/process-pdf")
async def create_processing_job(request: ProcessRequest, background_tasks: BackgroundTasks):
    """
    Tumatanggap ng request at sinisimulan ang PDF processing sa background.
    """
    try:
        # Gamitin ang BackgroundTasks para agad na mag-return ng response
        # habang tumatakbo ang mabigat na trabaho sa background.
        config = request.config
        background_tasks.add_task(process_pdf_from_url,
                                  request.pdf_file_id,
                                  config.get('issue_number'),
                                  config.get('publication_date'),
                                  config.get('table_of_contents', []) # Magbigay ng empty list bilang default)
        )
        
        print(f"Accepted job for issue: {config.get('issue_number')}. Processing in background.")
        
        # Agad na mag-return ng 202 Accepted response
        return {"message": "Processing job accepted", "issue_name": config.get('issue_number')}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def read_root():
    return {"status": "Magazine Worker is running"}
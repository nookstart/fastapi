import os
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from dotenv import load_dotenv
from processor import process_pdf_from_url

# I-load ang environment variables mula sa .env file (para sa local dev)
load_dotenv()

app = FastAPI()

# I-define ang itsura ng request body
class ProcessRequest(BaseModel):
    pdf_file_id: str
    issue_name: str

@app.on_event("startup")
async def startup_event():
    # Tiyakin na ang Vercel Blob environment variables ay naka-set
    if not os.getenv('BLOB_STORE_ID') or not os.getenv('BLOB_TOKEN'):
        raise RuntimeError("BLOB_STORE_ID and BLOB_TOKEN must be set in environment variables.")
    
    # Itakda ang mga ito para magamit ng `vercel-blob` library
    os.environ['VERCEL_BLOB_STORE_ID'] = os.environ['BLOB_STORE_ID']
    os.environ['VERCEL_BLOB_TOKEN'] = os.environ['BLOB_TOKEN']

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
        background_tasks.add_task(process_pdf_from_url, request.pdf_file_id, request.issue_name)
        
        print(f"Accepted job for issue: {request.issue_name}. Processing in background.")
        
        # Agad na mag-return ng 202 Accepted response
        return {"message": "Processing job accepted", "issue_name": request.issue_name}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def read_root():
    return {"status": "Magazine Worker is running"}
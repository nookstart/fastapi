import os
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from dotenv import load_dotenv
from fastapi.concurrency import asynccontextmanager
from processor import process_pdf_from_url

from processor import process_pdf_from_url
from reflow_processor import process_pdf_for_reflow
from models import ProcessRequest, ReflowRequest
from supabase import create_client, Client

# I-load ang environment variables mula sa .env file (para sa local dev)
load_dotenv()

# --- App State ---
# Gagawa tayo ng isang dictionary para paglagyan ng ating shared resources
app_state = {}

# --- Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ito ay tatakbo bago mag-start ang server
    print("--- 🚀 Initializing Supabase Client on App Startup 🚀 ---")
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("Supabase URL and Key must be set in environment variables.")
    
    if not supabase_url.endswith('/'):
        print("WARNING: SUPABASE_URL does not have a trailing slash. Appending it.")
        supabase_url += '/'
            
    # I-initialize ang client at i-save sa app_state
    app_state["supabase_client"] = create_client(supabase_url, supabase_key)
    print("--- ✅ Supabase Client Initialized ---")
    
    yield # Ito ang magpapatakbo sa application
    
    # Ito ay tatakbo pagkatapos mag-shutdown ng server (optional)
    print("---  shutting down ---")
    app_state.clear()

app = FastAPI(lifespan=lifespan)

# --- ✨ DEPENDENCY INJECTION ✨ ---
def get_supabase() -> Client:
    """Dependency to get the Supabase client from app state."""
    return app_state["supabase_client"]

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
                                  config.issue_number,
                                  config.publication_date,
                                  config.table_of_contents
        )
        
        print(f"Accepted job for issue: {config.issue_number}. Processing in background.")
        
        # Agad na mag-return ng 202 Accepted response
        return {"message": "Processing job accepted", "issue_name": config.issue_number}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/reflow-pdf")
async def trigger_reflow_pdf(
    request: ReflowRequest,
    background_tasks: BackgroundTasks,
    # I-inject ang Supabase client sa endpoint
    supabase: Client = Depends(get_supabase) 
    ): # <-- Gamitin ang bagong model
    """
    Endpoint para sa bago at improved na 'reflow' processing.
    """
    try:
        background_tasks.add_task(
            process_pdf_for_reflow,
            request.pdf_file_id,
            request.config, # <-- Ipasa ang buong config object
            supabase
        )
        return {"message": f"Accepted REFLOW job for issue: {request.config.issue_number}. Processing in background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def read_root():
    return {"status": "Magazine Worker is running"}
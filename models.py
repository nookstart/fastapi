from pydantic import BaseModel
from typing import List, Dict, Any

# --- Model para sa Reflow Config ---
class ReflowConfig(BaseModel):
    issue_number: str
    publication_date: str
    table_of_contents: List[Dict[str, Any]]

# --- Model para sa Reflow Request Body ---
class ReflowRequest(BaseModel):
    pdf_file_id: str
    config: ReflowConfig

# --- Model para sa Original Image-based Process ---
class ProcessRequest(BaseModel):
    pdf_file_id: str
    config: ReflowConfig
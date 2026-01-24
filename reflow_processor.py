import fitz  # PyMuPDF
import io
import json
from typing import Dict, Any, List
from collections import Counter
from supabase import create_client, Client 
import os
from slugify import slugify

# I-import ang mga helper functions at models
from processor import get_drive_service, save_to_database # Gagamitin natin ang save_to_database mamaya
from models import ReflowConfig

# --- ✨ BAGONG ROBUST SUPABASE CLIENT GETTER ✨ ---
_supabase_client = None

def get_supabase_client() -> Client:
    """
    Initializes and returns a singleton Supabase client.
    Raises an exception if credentials are not set.
    """
    global _supabase_client
    if _supabase_client is None:
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")

        if not supabase_url or not supabase_key:
            raise ValueError("Supabase URL and Key must be set in environment variables.")
        
        # Siguraduhin na may trailing slash
        if not supabase_url.endswith('/'):
            supabase_url += '/'
            
        print("Initializing Supabase client...")
        _supabase_client = create_client(supabase_url, supabase_key)
    
    return _supabase_client

def upload_to_supabase_storage(bucket_name: str, file_path: str, file_body: bytes, content_type: str):
    """Helper function para mag-upload ng file sa Supabase Storage."""
    try:
        supabase = get_supabase_client() # <-- Gamitin ang bagong function
        supabase.storage.from_(bucket_name).upload(
            file=file_body,
            path=file_path,
            file_options={"content-type": content_type, "cache-control": "3600", "upsert": "true"}
        )
        public_url = supabase.storage.from_(bucket_name).get_public_url(file_path)
        print(f"  - ✅ Uploaded to Supabase: {public_url}")
        return public_url
    except Exception as e:
        # Mag-print ng mas detalyadong error
        print(f"  - ❌ Supabase upload failed for {file_path}. Error Type: {type(e).__name__}, Details: {e}")
        return None

def reconstruct_page_layout(page: fitz.Page, pdf_document: fitz.Document, issue_name: str, page_number: int) -> List[Dict[str, Any]]:
    """
    Analyzes a single page for granular content elements (spans),
    flags potential shadow text, and smart-crops images.
    """
    print("    - Starting granular extraction...")
    
    # --- Step 1: Extract text blocks (dict) at image info ---
    text_dict = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    image_info_list = page.get_image_info(xrefs=True)

    elements = []
    
    # --- Step 2: Process Text Spans (Granular) ---
    # I-iterate ang bawat block, line, at span
    for block_idx, block in enumerate(text_dict.get("blocks", [])):
        if block['type'] != 0:  # 0 = text block
            continue
        
        spans_in_block = []
        # Mag-declare ng isang counter para sa buong block
        span_counter_in_block = 0
        for line_idx, line in enumerate(block.get("lines", [])):
            for span_idx, span in enumerate(line.get("spans", [])):
                # Linisin ang text, minsan may kasamang weird whitespace
                span_text = span['text'].strip()
                if not span_text:
                    continue

                # I-store ang lahat ng spans sa isang temporary list
                spans_in_block.append({
                    "id": f"p{page_number}_b{block_idx}_s{span_counter_in_block}",
                    "block_id": f"p{page_number}_b{block_idx}",
                    "type": "text",
                    "bbox": span["bbox"],
                    "content": span_text,
                    "font_info": {
                        "size": round(span["size"], 2),
                        "font": span["font"],
                        "color": span["color"],
                    },
                    "reflow_hints": {} # Placeholder para sa ating "intelligent" hula
                })
                span_counter_in_block += 1

        # --- Step 3: Shadow Text Detection Logic (within the same block) ---
        # Pagkatapos kolektahin lahat ng spans sa isang block, i-compare sila
        final_spans_for_block = []
        for i, current_span in enumerate(spans_in_block):
            is_shadow = False
            # I-compare ang current_span sa lahat ng iba pang spans sa block
            for j, other_span in enumerate(spans_in_block):
                if i == j:  # Huwag i-compare sa sarili niya
                    continue

                # Condition 1: Pareho ba ang text?
                if current_span['content'] == other_span['content']:
                    # Condition 2: Halos magkadikit ba ang position?
                    # Check kung ang top-left corner nila ay napakaliit lang ang agwat
                    dist_x = abs(current_span['bbox'][0] - other_span['bbox'][0])
                    dist_y = abs(current_span['bbox'][1] - other_span['bbox'][1])
                    
                    # Kung ang agwat ay mas mababa sa 2 pixels, posibleng shadow ito
                    if dist_x < 2 and dist_y < 2:
                        # Condition 3: Alin ang nasa likod?
                        # Ang span na unang na-render (mas mababang index sa original PDF structure)
                        # ay malamang ang shadow.
                        if j < i:
                            is_shadow = True
                            break # Found a shadow, no need to check further

            current_span["reflow_hints"]["is_shadow_text"] = is_shadow
            final_spans_for_block.append(current_span)
        
        # Idagdag ang na-filter na spans sa main elements list
        elements.extend(final_spans_for_block)


    # --- Step 4: Process Images (Smart Cropping - walang pagbabago) ---
    for img_info in image_info_list:
        bbox = img_info['bbox']
        xref = img_info['xref']
        if xref == 0: continue

        zoom_matrix = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=zoom_matrix, clip=bbox)
        image_bytes = pix.tobytes("png")
        image_ext = "png"

        image_filename = f"page_{page_number}_xref_{xref}_cropped.{image_ext}"
        supabase_path = f"{issue_name}/images/{image_filename}"
        
        public_url = upload_to_supabase_storage(
            bucket_name="magazine-pages",
            file_path=supabase_path,
            file_body=image_bytes,
            content_type=f"image/{image_ext}"
        )

        if public_url:
            elements.append({
                "id": f"p{page_number}_img_{xref}",
                "block_id": None, # Ang mga imahe ay walang block sa text_dict
                "type": "image",
                "bbox": bbox,
                "src": public_url,
                "reflow_hints": {}
            })

    # --- Step 5: Sort all elements by their vertical position (walang pagbabago) ---
    elements.sort(key=lambda el: el["bbox"][1])
    
    print(f"    - Extracted {len(elements)} granular elements.")
    
    # Sa ngayon, i-return natin ang buong listahan. Ang pag-alis ng `bbox` ay gagawin na sa front-end.
    return elements

def process_pdf_for_reflow(file_id: str, config: ReflowConfig) -> Dict[str, Any]:
    """
    Main function to process the PDF for reflow.
    """

    issue_name = config.issue_number
    print(f"--- 🚀 REFLOW PROCESSOR INITIATED for: {issue_name} 🚀 ---")

    try:
        # 1. Download PDF
        drive_service = get_drive_service()
        request = drive_service.files().get_media(fileId=file_id)
        file_bytes = io.BytesIO(request.execute())
        
        # 2. Open PDF
        pdf_document = fitz.open(stream=file_bytes, filetype="pdf")
        
        structured_magazine = {
            "issue_number": issue_name,
            "publication_date": config.publication_date,
            "table_of_contents": config.table_of_contents,
            "pages": []
        }

        # 3. Process each page
        for page_num in range(len(pdf_document)):
            page = pdf_document.load_page(page_num)
            page_number = page_num + 1
            print(f"\n--- Reconstructing Page {page_number} ---")

            # Ipasa ang buong `pdf_document` para ma-extract ang images
            page_content = reconstruct_page_layout(page, pdf_document, issue_name, page_number)
            
            structured_magazine["pages"].append({
                "page_number": page_number,
                "content": page_content
            })

        # 4. I-UPLOAD ANG FINAL JSON SA SUPABASE STORAGE
        print("\n--- Uploading final semantic JSON to Supabase ---")
        final_json_output = json.dumps(structured_magazine, indent=2).encode('utf-8')
        json_path = f"{issue_name}/content.json"
        
        json_public_url = upload_to_supabase_storage(
            bucket_name="magazine-pages", # O kung saan mo gustong i-save ang JSON
            file_path=json_path,
            file_body=final_json_output,
            content_type="application/json"
        )

        if not json_public_url:
            raise Exception("Failed to upload the final JSON file. Aborting.")

        # 5. I-UPDATE ANG DATABASE
        print("\n--- Updating 'magazine_issues' table in Supabase DB ---")
        try:
            supabase = get_supabase_client() # <-- Gamitin din dito
            issue_slug = slugify(issue_name) # Siguraduhing may slugify function ka
            supabase.table("magazine_issues").upsert(
                {
                    "issue_number": issue_name,
                    "issue_slug": issue_slug,
                    "publication_date": config.publication_date,
                    "reflow_content_url": json_public_url,
                    "status": "processed_reflow"
                },
                on_conflict="issue_slug" 
            ).execute()
            print("  - ✅ Database updated successfully.")
        except Exception as e:
            print(f"  - ❌ Database update failed. Error: {e}")
            raise

        print("\n--- ✅ REFLOW PROCESSOR FINISHED ---")
        return {"status": "success", "processor": "reflow", "message": "Reconstruction, upload, and DB update complete."}

    except Exception as e:
        print(f"❌ An error occurred in reflow_processor: {e}")
        raise
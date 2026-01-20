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


# --- SUPABASE SETUP ---
# Kunin ang Supabase credentials mula sa environment variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

# Gumawa ng isang global Supabase client
# Mas efficient ito kaysa gumawa ng client sa bawat request
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

def upload_to_supabase_storage(bucket_name: str, file_path: str, file_body: bytes, content_type: str):
    """Helper function para mag-upload ng file sa Supabase Storage."""
    if not supabase:
        print("  - WARNING: Supabase client not initialized. Skipping upload.")
        return None
    try:
        # Ang `upload` ay mag-o-overwrite by default kung may existing file
        supabase.storage.from_(bucket_name).upload(
            file=file_body,
            path=file_path,
            file_options={"content-type": content_type, "cache-control": "3600", "upsert": "true"}
        )
        # Kunin ang public URL
        public_url = supabase.storage.from_(bucket_name).get_public_url(file_path)
        print(f"  - ✅ Uploaded to Supabase: {public_url}")
        return public_url
    except Exception as e:
        print(f"  - ❌ Supabase upload failed for {file_path}. Error: {e}")
        return None

def reconstruct_page_layout(page: fitz.Page, pdf_document: fitz.Document, issue_name: str, page_number: int) -> List[Dict[str, Any]]:
    """
    Analyzes a single page, extracts content, and SMART CROPS images before uploading.
    """
    # --- Step 1 & 2: Text extraction at font size calculation (walang pagbabago) ---
    text_dict = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    font_sizes = [round(s['size']) for b in text_dict.get("blocks", []) if b['type'] == 0 for l in b['lines'] for s in l['spans']]
    most_common_size = Counter(font_sizes).most_common(1)[0][0] if font_sizes else 12

    # --- Step 3: Combine text and images ---
    elements = []
    
    # Process text blocks (walang pagbabago)
    for block in text_dict.get("blocks", []):
        if block['type'] == 0:
            full_text = "".join(s['text'] for l in block['lines'] for s in l['spans'])
            block_font_sizes = [round(s['size']) for l in block['lines'] for s in l['spans']]
            avg_size = sum(block_font_sizes) / len(block_font_sizes) if block_font_sizes else 0
            element_type = "heading" if avg_size > most_common_size * 1.5 else "paragraph"
            
            elements.append({
                "type": element_type,
                "text": full_text.strip(),
                "bbox": block["bbox"],
            })

    # ✨ --- BAGONG LOGIC PARA SA "SMART CROPPING" NG IMAGES --- ✨
    # Gamitin ang get_image_info para makuha ang 'bbox' ng bawat image sa page
    image_info_list = page.get_image_info(xrefs=True)

    for img_info in image_info_list:
        # Ang bbox ay ang actual na sukat at posisyon ng image sa page
        bbox = img_info['bbox']
        xref = img_info['xref']
        
        if xref == 0: continue

        # Gumawa ng Pixmap (isang rendered na imahe) ng page,
        # pero i-CROP ito gamit ang eksaktong bbox ng imahe.
        # Ang `clip=bbox` ang nagsasagawa ng cropping.
        # Magdagdag tayo ng zoom (dpi=200) para sa mas mataas na kalidad.
        zoom_matrix = fitz.Matrix(2, 2) # 2x zoom = 144 dpi
        pix = page.get_pixmap(matrix=zoom_matrix, clip=bbox)
        
        # I-convert ang Pixmap sa bytes (PNG format para suportahan ang transparency)
        image_bytes = pix.tobytes("png")
        image_ext = "png"

        # Gumawa ng unique na file path para sa Supabase
        image_filename = f"page_{page_number}_xref_{xref}_cropped.{image_ext}"
        supabase_path = f"{issue_name}/images/{image_filename}"
        
        # I-upload ang na-CROP na image sa Supabase Storage
        public_url = upload_to_supabase_storage(
            bucket_name="magazine-pages",
            file_path=supabase_path,
            file_body=image_bytes,
            content_type=f"image/{image_ext}"
        )

        if public_url:
            elements.append({
                "type": "image",
                "src": public_url,
                "bbox": bbox, # Gamitin ang totoong bbox para sa sorting
            })

    # --- Step 4 & 5: Sort at Finalize (walang pagbabago) ---
    elements.sort(key=lambda el: el["bbox"][1])

    final_content = []
    for el in elements:
        item = {"type": el["type"]}
        if el["type"] == "image":
            item["src"] = el["src"]
        else:
            item["text"] = el["text"]
        final_content.append(item)
            
    return final_content


def process_pdf_for_reflow(file_id: str, config: ReflowConfig) -> Dict[str, Any]:
    """
    Main function to process the PDF for reflow.
    """
    if not supabase:
        raise Exception("Supabase client is not initialized. Check environment variables.")

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
            # Gamitin ang `upsert` para mag-insert ng bago o mag-update kung existing na
            issue_slug = slugify(issue_name)
            supabase.table("magazine_issues").upsert({
                "issue_number": issue_name,
                "issue_slug": issue_slug,
                "publication_date": config.publication_date,
                "reflow_content_url": json_public_url, # Bagong column para sa reflow
                "status": "processed_reflow" # Bagong status
            }, on_conflict="issue_slug").execute()
            print("  - ✅ Database updated successfully.")
        except Exception as e:
            print(f"  - ❌ Database update failed. Error: {e}")
            raise

        print("\n--- ✅ REFLOW PROCESSOR FINISHED ---")
        return {"status": "success", "processor": "reflow", "message": "Reconstruction, upload, and DB update complete."}

    except Exception as e:
        print(f"❌ An error occurred in reflow_processor: {e}")
        raise
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

def int_to_hex_color(color_int: int) -> str:
    """Converts an integer color representation to a CSS hex string."""
    if not isinstance(color_int, int) or not 0 <= color_int <= 16777215:
        return "#000000"  # Default to black for invalid input
    # Format as a 6-digit hex string with leading zeros if needed
    return f"#{color_int:06x}"

def detect_columns(page: fitz.Page, blocks: List[Dict[str, Any]], tolerance_px: int = 10) -> List[fitz.Rect]:
    """
    Analyzes text block positions to detect column layout.
    Returns a list of bounding boxes, one for each detected column.
    """
    if not blocks:
        return [page.rect] # Kung walang blocks, i-assume na 1 column (buong page)

    # Step 1: Kunin ang horizontal center ng bawat block
    center_points = sorted([((b['bbox'][0] + b['bbox'][2]) / 2) for b in blocks])

    if not center_points:
        return [page.rect]

    # Step 2: I-cluster ang mga center points
    clusters = []
    current_cluster = [center_points[0]]
    
    for i in range(1, len(center_points)):
        # Kung ang agwat sa pagitan ng dalawang center points ay maliit, nasa iisang cluster sila
        if center_points[i] - current_cluster[-1] < tolerance_px:
            current_cluster.append(center_points[i])
        else:
            # Kung malaki ang agwat, simulan ang bagong cluster
            clusters.append(current_cluster)
            current_cluster = [center_points[i]]
    clusters.append(current_cluster)

    # Step 3: I-calculate ang average center ng bawat cluster
    # Ito ang magiging representative x-coordinate ng bawat column
    column_x_centers = [sum(c) / len(c) for c in clusters]
    print(f"      - Detected {len(column_x_centers)} potential column centers at x-coords: {[round(c) for c in column_x_centers]}")

    # Step 4: I-define ang boundaries (bbox) ng bawat column
    column_bboxes = []
    page_width = page.rect.width
    
    # Kung isa lang ang column, sakupin ang buong page
    if len(column_x_centers) <= 1:
        return [page.rect]

    # Kung marami, hatiin ang page
    for i, center_x in enumerate(column_x_centers):
        # Define ang left (x0) at right (x1) boundary ng column
        if i == 0:
            x0 = 0
        else:
            # Ang gitna sa pagitan ng kasalukuyang center at ng nakaraang center
            x0 = (center_x + column_x_centers[i-1]) / 2
        
        if i == len(column_x_centers) - 1:
            x1 = page_width
        else:
            # Ang gitna sa pagitan ng kasalukuyang center at ng susunod na center
            x1 = (center_x + column_x_centers[i+1]) / 2
            
        column_bboxes.append(fitz.Rect(x0, 0, x1, page.rect.height))
        
    return column_bboxes

# --- ✨ BAGONG HELPER: "PER-BLOCK" COLUMN DETECTION ✨ ---
def detect_columns_within_block(spans: List[Dict[str, Any]], block_bbox: fitz.Rect, tolerance_px: int = 10) -> List[fitz.Rect]:
    """
    Analyzes span positions WITHIN a single block to detect internal columns.
    Returns a list of bounding boxes for each detected column, relative to the block.
    """
    if not spans:
        return [block_bbox]

    # Step 1: Kunin ang horizontal center ng bawat span
    center_points = sorted([((s['bbox'][0] + s['bbox'][2]) / 2) for s in spans])

    if not center_points:
        return [block_bbox]

    # Step 2: I-cluster ang mga center points (parehong logic tulad ng dati)
    clusters = []
    current_cluster = [center_points[0]]
    for i in range(1, len(center_points)):
        if center_points[i] - current_cluster[-1] < tolerance_px:
            current_cluster.append(center_points[i])
        else:
            clusters.append(current_cluster)
            current_cluster = [center_points[i]]
    clusters.append(current_cluster)

    column_x_centers = [sum(c) / len(c) for c in clusters]
    
    # Kung isa lang ang column, ang buong block ang column
    if len(column_x_centers) <= 1:
        return [block_bbox]

    # Step 3: I-define ang boundaries ng bawat column sa loob ng block
    column_bboxes = []
    for i, center_x in enumerate(column_x_centers):
        x0 = (center_x + column_x_centers[i-1]) / 2 if i > 0 else block_bbox.x0
        x1 = (center_x + column_x_centers[i+1]) / 2 if i < len(column_x_centers) - 1 else block_bbox.x1
        column_bboxes.append(fitz.Rect(x0, block_bbox.y0, x1, block_bbox.y1))

    return column_bboxes

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
    Analyzes a single page, adding alignment hints, converting colors,
    and handling layout heuristics.
    """
    print("    - Starting advanced layout analysis...")

    # Kunin ang sukat ng page para sa alignment check
    page_width = page.rect.width
    page_center_x = page_width / 2
    
    elements = []
    
   # --- Step 1: Process Text Spans with new heuristics ---
    text_dict = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    for block_idx, block in enumerate(text_dict.get("blocks", [])):
        if block['type'] != 0: continue

        block_bbox = fitz.Rect(block['bbox'])
        
        # Kunin lahat ng spans sa loob ng block na ito
        spans_in_block_raw = [s for line in block.get("lines", []) for s in line.get("spans", []) if s['text'].strip()]
        
        # --- Step 2: I-detect ang columns SA LOOB ng block na ito ---
        # Ang tolerance ay pwedeng mas maliit ngayon dahil block-level na
        column_layout = detect_columns_within_block(spans_in_block_raw, block_bbox, tolerance_px=20)
        column_count = len(column_layout)
        if column_count > 1:
            print(f"      - Block p{page_number}_b{block_idx} has {column_count} internal columns.")

        # --- Step 3: I-proseso ang bawat span at i-assign sa column ng block ---
        spans_in_block_processed = []
        for span_counter, span in enumerate(spans_in_block_raw):
            # Alignment detection (para sa buong block)
            block_center_x = (block_bbox.x0 + block_bbox.x1) / 2
            alignment = "center" if abs(block_center_x - page_center_x) < (page_width * 0.05) else "left"

            # Assign span to its column within the block
            span_center_x = (span['bbox'][0] + span['bbox'][2]) / 2
            col_idx = 0
            for i, col_bbox in enumerate(column_layout):
                if col_bbox.x0 <= span_center_x < col_bbox.x1:
                    col_idx = i
                    break
            
            spans_in_block_processed.append({
                "id": f"p{page_number}_b{block_idx}_s{span_counter}",
                "block_id": f"p{page_number}_b{block_idx}",
                "type": "text", "bbox": span["bbox"], "content": span['text'].strip(),
                "font_info": {"size": round(span["size"], 2), "font": span["font"], "color": int_to_hex_color(span["color"])},
                "reflow_hints": {
                    "alignment": alignment,
                    "layout_info": {"column_count": column_count, "column_index": col_idx}
                }
            })

        # --- Shadow Text Detection (walang pagbabago) ---
        final_spans_for_block = []
        for i, current_span in enumerate(spans_in_block_processed):
            is_shadow = False
            for j, other_span in enumerate(spans_in_block_processed):
                if i == j: continue
                if current_span['content'] == other_span['content']:
                    dist_x = abs(current_span['bbox'][0] - other_span['bbox'][0])
                    dist_y = abs(current_span['bbox'][1] - other_span['bbox'][1])
                    if dist_x < 2 and dist_y < 2 and j < i:
                        is_shadow = True
                        break
            current_span["reflow_hints"]["is_shadow_text"] = is_shadow
            final_spans_for_block.append(current_span)
        
        elements.extend(final_spans_for_block)


    image_info_list = page.get_image_info(xrefs=True)
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
        img_center_x = (bbox[0] + bbox[2]) / 2
        col_idx = 0
        for i, col_bbox in enumerate(column_layout):
            if col_bbox.x0 <= img_center_x < col_bbox.x1:
                col_idx = i
                break
        
        if public_url:
            elements.append({
                "id": f"p{page_number}_img_{xref}",
                "block_id": f"p{page_number}_img_block_{xref}",
                "type": "image",
                "bbox": bbox,
                "src": public_url,
                "reflow_hints": {
                    "layout_info": {"column_count": 1, "column_index": 0}
                }
            })

    # --- Step 5: Sort all elements by their vertical position (walang pagbabago) ---
    # elements.sort(key=lambda el: el["bbox"][1])

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
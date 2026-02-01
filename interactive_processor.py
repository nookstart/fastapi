import fitz
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from io import BytesIO
from supabase import create_client, Client
from PIL import Image, ImageChops
from slugify import slugify
from datetime import datetime

# --- Google Drive Authentication ---
def get_drive_service():
    client_email = os.getenv("GOOGLE_CLIENT_EMAIL")
    private_key_raw = os.getenv("GOOGLE_PRIVATE_KEY")
    if not client_email or not private_key_raw:
        raise ValueError("GOOGLE_CLIENT_EMAIL and GOOGLE_PRIVATE_KEY environment variables must be set.")
    private_key = private_key_raw.replace('\\n', '\n')
    creds_info = {
        "type": "service_account", "private_key": private_key, "client_email": client_email,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=['https://www.googleapis.com/auth/drive.readonly'])
    return build('drive', 'v3', credentials=creds)

def download_pdf_from_drive(file_id: str):
    try:
        print(f"Authenticating with Google Drive to download file_id: {file_id}")
        service = get_drive_service()
        request = service.files().get_media(fileId=file_id)
        file_content = BytesIO()
        downloader = request.execute(num_retries=3)
        if downloader:
            file_content.write(downloader)
            file_content.seek(0)
            print(f"Successfully downloaded PDF with file_id: {file_id}")
            return file_content.getvalue()
        else:
            raise Exception("Google Drive downloader returned empty content.")
    except Exception as e:
        print(f"Error downloading from Google Drive: {e}")
        raise

# --- Supabase Upload Helper ---
def upload_to_supabase_storage(supabase: Client, bucket_name: str, file_path: str, file_body: bytes, content_type: str):
    """Helper function to upload a file to Supabase Storage and get its public URL."""
    try:
        supabase.storage.from_(bucket_name).upload(
            file=file_body,
            path=file_path,
            file_options={"content-type": content_type, "cache-control": "3600", "upsert": "true"}
        )
        public_url = supabase.storage.from_(bucket_name).get_public_url(file_path)
        print(f"  - ✅ Uploaded to Supabase: {public_url}")
        return public_url
    except Exception as e:
        print(f"  - ❌ Supabase upload failed for {file_path}. Error Type: {type(e).__name__}, Details: {e}")
        return None

# --- Main Interactive Processor Function ---
def process_pdf_interactive(pdf_file_id: str, config: dict, supabase: Client):
    issue_name = config.issue_number
    issue_slug = slugify(issue_name)
    print(f"--- 🚀 INTERACTIVE PROCESSOR INITIATED for: {issue_name} 🚀 ---")
    
    try:
        pdf_data = download_pdf_from_drive(pdf_file_id)
        pdf_document = fitz.open(stream=pdf_data, filetype="pdf")
        
        manifest = {
            "issue_number": issue_name,
            "publication_date": config.publication_date,
            "table_of_contents": config.table_of_contents, # <-- Idagdag ang TOC
            "pages": []
        }

        for page_num in range(len(pdf_document)):
            page = pdf_document.load_page(page_num)
            print(f"\n--- Processing Page {page_num + 1} ---")

            # --- ✨ STEP 1: AUTOCROP LOGIC (Mula sa lumang processor) ✨ ---
            dpi = 150  # Itakda ang DPI para sa initial render
            pix_full = page.get_pixmap(dpi=dpi)
            img_bytes_full = pix_full.tobytes("png")

            print("  - Analyzing image for autocropping...")
            image = Image.open(BytesIO(img_bytes_full))
            grayscale_image = image.convert('L')
            inverted_image = ImageChops.invert(grayscale_image)
            autocrop_pixel_bbox = inverted_image.getbbox()

            if autocrop_pixel_bbox:
                print(f"  - Content found at pixel bbox: {autocrop_pixel_bbox}. Cropping...")
                cropped_image = image.crop(autocrop_pixel_bbox)
                
                buffer = BytesIO()
                cropped_image.save(buffer, format='PNG')
                page_image_bytes = buffer.getvalue() # Ito na ang final bytes na ia-upload

                # Kunin ang dimensions ng na-crop na imahe
                final_width = cropped_image.width
                final_height = cropped_image.height
                
                # I-convert ang pixel bbox pabalik sa PDF points para sa frontend
                scale = dpi / 72.0
                x0, y0, x1, y1 = autocrop_pixel_bbox
                final_content_box = [x0 / scale, y0 / scale, x1 / scale, y1 / scale]
            else:
                # Fallback kung mag-fail ang autocrop
                print("  - ⚠️ Autocrop failed. Using full page image.")
                page_image_bytes = img_bytes_full
                final_width = pix_full.width
                final_height = pix_full.height
                final_content_box = [page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1]
            
            # --- ✨ STEP 2: I-UPLOAD ANG NA-CROP NA IMAHE ✨ ---
            page_image_path = f"{issue_name}/page_{page_num + 1}.png"
            page_image_url = upload_to_supabase_storage(
                supabase, "magazine-pages", page_image_path, 
                page_image_bytes, # <-- Gamit na nito ang na-crop na bytes
                "image/png"
            )

            # --- STEP 3: I-EXTRACT ANG HOTSPOTS (walang pagbabago) ---
            hotspots = [
                {"type": "url", "uri": link['uri'], "bbox": [link['from'].x0, link['from'].y0, link['from'].x1, link['from'].y1]}
                for link in page.get_links() if link['kind'] == fitz.LINK_URI
            ]
            
            element_hotspots = []
            print("  - Extracting text blocks...")
            text_blocks = page.get_text("blocks")
            for block in text_blocks:
                if block[6] == 0 and block[4].strip():
                    element_hotspots.append({
                        "type": "text", "bbox": [block[0], block[1], block[2], block[3]],
                        "content": block[4].replace('\n', ' ').strip()
                    })

            print("  - Extracting, cropping, and uploading images...")
            for img_info in page.get_image_info(xrefs=True):
                if img_info['xref'] == 0: continue
                try:
                    img_pix = page.get_pixmap(clip=img_info['bbox'])
                    img_bytes = img_pix.tobytes("png")
                    img_path = f"{issue_name}/elements/element_page_{page_num + 1}_xref_{img_info['xref']}.png"
                    img_url = upload_to_supabase_storage(
                        supabase, "magazine-pages", img_path, img_bytes, "image/png"
                    )
                    if img_url:
                        element_hotspots.append({
                            "type": "image", "bbox": list(img_info['bbox']), "src": img_url
                        })
                except Exception as e:
                    print(f"    - ⚠️ Could not process image element with xref {img_info['xref']}. Reason: {e}")

            # --- ✨ STEP 4: I-UPDATE ANG MANIFEST DATA ✨ ---
            manifest["pages"].append({
                "page_num": page_num + 1,
                "image_url": page_image_url,
                "width": final_width,             # <-- Gamitin ang bagong width
                "height": final_height,           # <-- Gamitin ang bagong height
                "crop_box": final_content_box,    # <-- Idagdag ang crop_box
                "hotspots": hotspots,
                "element_hotspots": element_hotspots
            })
            print(f"  - ✅ Page processed. Final dimensions: {final_width}x{final_height}")

        # ... (ang natitirang bahagi ng code para sa pag-upload ng manifest ay mananatiling pareho) ...
        manifest_path = f"{issue_name}/manifest.json"
        print(f"\n--- Uploading final manifest to: {manifest_path} ---")
        manifest_url = upload_to_supabase_storage(
            supabase, "magazine-pages", manifest_path,
            json.dumps(manifest, indent=2).encode('utf-8'), "application/json"
        )
        print(f"--- Updating 'magazine_issues' table for slug: {issue_slug} ---")
        db_payload = {
            "issue_number": issue_name,
            "issue_slug": issue_slug,
            "publication_date": config.publication_date,
            "status": "published_interactive", # Isang bagong status para malinaw
            "manifest_url": manifest_url,
            # Ang cover_image_url ay maaaring i-set dito kung kukunin natin ang first page
            "cover_image_url": manifest['pages'][0]['image_url'] if manifest['pages'] else None,
        }
        
        # Ang `upsert` na may `on_conflict` ay nagsisigurong idempotent ito.
        # I-u-update nito ang existing entry kung may kaparehong 'issue_slug', kung hindi, gagawa ito ng bago.
        supabase.table("magazine_issues").upsert(
            db_payload, 
            on_conflict="issue_slug"
        ).execute()
        print("  - ✅ Database updated successfully.")
        print(f"--- ✅ INTERACTIVE PROCESSING COMPLETE for: {issue_name} ---")

    except Exception as e:
        print(f"--- ❌ An error occurred during interactive processing: {e} ---")
        raise
import fitz
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from io import BytesIO
from supabase import create_client, Client

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
    print(f"--- 🚀 INTERACTIVE PROCESSOR INITIATED for: {issue_name} 🚀 ---")
    
    try:
        pdf_data = download_pdf_from_drive(pdf_file_id)
        pdf_document = fitz.open(stream=pdf_data, filetype="pdf")
        
        manifest = {"pages": []}
        
        for page_num in range(len(pdf_document)):
            page = pdf_document.load_page(page_num)
            print(f"\n--- Processing Page {page_num + 1} ---")

            zoom_matrix = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=zoom_matrix)
            page_image_bytes = pix.tobytes("png")
            page_image_path = f"{issue_name}/page_{page_num + 1}.png"
            
            page_image_url = upload_to_supabase_storage(
                supabase, "magazine-pages", page_image_path, page_image_bytes, "image/png"
            )

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
                    print(f"    - ⚠️ Could not process image with xref {img_info['xref']}. Reason: {e}")

            manifest["pages"].append({
                "page_num": page_num + 1, "image_url": page_image_url, "width": pix.width,
                "height": pix.height, "hotspots": hotspots, "element_hotspots": element_hotspots
            })
            print(f"  - ✅ Found {len(hotspots)} basic hotspots and {len(element_hotspots)} element hotspots.")

        manifest_path = f"{issue_name}/manifest.json"
        print(f"\n--- Uploading final manifest to: {manifest_path} ---")
        upload_to_supabase_storage(
            supabase, "magazine-pages", manifest_path,
            json.dumps(manifest, indent=2).encode('utf-8'), "application/json"
        )
        
        print(f"--- ✅ INTERACTIVE PROCESSING COMPLETE for: {issue_name} ---")

    except Exception as e:
        print(f"--- ❌ An error occurred during interactive processing: {e} ---")
        raise
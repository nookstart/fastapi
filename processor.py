import fitz  # PyMuPDF
import json
from vercel_blob import put
from typing import Dict, Any
import os
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

def get_drive_service():
    client_email = os.getenv("GOOGLE_CLIENT_EMAIL")
    private_key = os.getenv("GOOGLE_PRIVATE_KEY")

    if not client_email or not private_key:
        raise ValueError("GOOGLE_CLIENT_EMAIL and GOOGLE_PRIVATE_KEY environment variables must be set.")

    """Creates an authenticated Google Drive service object."""
    creds = service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "private_key": os.getenv("GOOGLE_PRIVATE_KEY").replace('\\n', '\n'),
            "client_email": os.getenv("GOOGLE_CLIENT_EMAIL"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
        },
        scopes=['https://www.googleapis.com/auth/drive.readonly'] # Read-only lang ang kailangan
    )
    service = build('drive', 'v3', credentials=creds)
    return service

# Itakda ang VERCEL_BLOB_STORE_ID mula sa environment variables
# Kakailanganin mo itong i-set sa Railway.
# os.environ['BLOB_STORE_ID'] = 'iyong_vercel_blob_store_id' 
# os.environ['BLOB_TOKEN'] = 'iyong_vercel_blob_read_write_token'

def process_pdf_from_url(file_id: str, issue_name: str) -> Dict[str, Any]:
    """
    Downloads a PDF, renders pages to PNG, extracts hotspots, and uploads to Vercel Blob.
    """
    print(f"Processing PDF for issue: {issue_name}")

    drive_service = get_drive_service()
    request = drive_service.files().get_media(fileId=file_id)

    # Gumamit ng in-memory buffer para i-download ang file
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
        print(f"  > Download {int(status.progress() * 100)}%.")
    
    pdf_bytes = fh.getvalue()
    print("  > PDF downloaded successfully.")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    hotspots = {
        "links": [],
        "emails": [],
        "phones": [],
        "urls": []
    }
    
    image_urls = []

    # 3. I-proseso ang bawat page
    for i, page in enumerate(doc):
        page_num = i + 1
        print(f"Processing Page {page_num}/{len(doc)}...")

        # --- A. I-render ang page sa PNG ---
        pix = page.get_pixmap(dpi=150)  # Mag-adjust ng DPI para sa quality vs. file size
        img_bytes = pix.tobytes("png")
        
        # I-upload ang image sa Vercel Blob
        # Gumamit ng one-based, zero-padded na pangalan
        image_filename = f"page-{page_num:02d}.png"
        blob_image = put(
            f"magazine-pages/{issue_name}/{image_filename}",
            img_bytes,
            add_random_suffix=False,
            allow_overwrite=True
        )
        image_urls.append({"page_number": page_num, "url": blob_image['url']})
        print(f"  > Uploaded image to: {blob_image['url']}")

        # --- B. I-extract ang mga links (hotspots) ---
        links = page.get_links()
        for link in links:
            if link.get('kind') == fitz.LINK_URI:
                hotspots["links"].append({
                    "page": page_num,
                    "url": link.get('uri'),
                    "bbox": list(link.get('from')) # Ang 'from' ay ang Rect object
                })

    # 4. I-assemble ang manifest/hotspots JSON
    manifest = {
        "metadata": {
            "issue_name": issue_name,
            "total_pages": len(doc),
            "pdf_file_id": file_id
        },
        "hotspots": hotspots,
        "pages": image_urls # Isama ang listahan ng mga na-upload na images
    }
    
    # 5. I-upload ang manifest.json sa Vercel Blob
    manifest_str = json.dumps(manifest, indent=2)
    blob_manifest = put(
        f"magazine-pages/{issue_name}/manifest.json",
        manifest_str.encode('utf-8'),
        add_random_suffix=False,
        allow_overwrite=True
    )
    print(f"Uploaded manifest to: {blob_manifest['url']}")

    return {"status": "success", "manifest_url": blob_manifest['url'], "page_count": len(doc)}
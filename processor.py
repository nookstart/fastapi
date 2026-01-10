import fitz  # PyMuPDF
import requests
import json
from vercel_blob import put
from typing import Dict, Any

# Itakda ang VERCEL_BLOB_STORE_ID mula sa environment variables
# Kakailanganin mo itong i-set sa Railway.
# os.environ['BLOB_STORE_ID'] = 'iyong_vercel_blob_store_id' 
# os.environ['BLOB_TOKEN'] = 'iyong_vercel_blob_read_write_token'

def process_pdf_from_url(pdf_url: str, issue_name: str) -> Dict[str, Any]:
    """
    Downloads a PDF, renders pages to PNG, extracts hotspots, and uploads to Vercel Blob.
    """
    print(f"Processing PDF for issue: {issue_name}")

    # 1. I-download ang PDF
    print(f"Downloading PDF from: {pdf_url}")
    response = requests.get(pdf_url)
    response.raise_for_status()  # Mag-throw ng error kung hindi 200 OK
    pdf_bytes = response.content

    # 2. Buksan ang PDF gamit ang PyMuPDF
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
            {'access': 'public', 'add_random_suffix': False, 'allow_overwrite': True}
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
            "pdf_source_url": pdf_url
        },
        "hotspots": hotspots,
        "pages": image_urls # Isama ang listahan ng mga na-upload na images
    }
    
    # 5. I-upload ang manifest.json sa Vercel Blob
    manifest_str = json.dumps(manifest, indent=2)
    blob_manifest = put(
        f"magazine-pages/{issue_name}/manifest.json",
        manifest_str,
        {'access': 'public', 'add_random_suffix': False, 'allow_overwrite': True}
    )
    print(f"Uploaded manifest to: {blob_manifest['url']}")

    return {"status": "success", "manifest_url": blob_manifest['url'], "page_count": len(doc)}
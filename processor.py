import fitz  # PyMuPDF
import json
from vercel_blob import put
from typing import Dict, Any, List
import os
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from supabase import create_client, Client
from slugify import slugify
import re
from PIL import Image, ImageChops
import io

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

def save_to_database(
        issue_name: str, 
        publication_date: str, 
        manifest_url: str, 
        cover_image_url: str,
        page_dimensions: Dict[str, int], # <-- Bagong parameter
        pages_data: List[Dict[str, Any]],
        toc_data: List[Dict[str, Any]]
    ):
    """Saves the processed magazine issue and pages to the Supabase database."""
    print("  > Saving data to Supabase...")
    
    try:
        # I-initialize ang Supabase client
        url: str = os.environ.get("SUPABASE_URL")
        key: str = os.environ.get("SUPABASE_SERVICE_KEY")
        supabase: Client = create_client(url, key)
        # 1. Gumamit ng 'upsert' para sa magazine_issues
        issue_slug = slugify(issue_name)
        issue_response = supabase.table("magazine_issues").upsert({
            "issue_slug": issue_slug,
            "issue_number": issue_name,
            "publication_date": publication_date,
            "status": "published",
            "manifest_url": manifest_url,
            "cover_image_url": cover_image_url,
            "page_dimensions": page_dimensions # <-- I-save ang dimensions
        }, on_conflict="issue_slug").execute()
        
        # Kunin ang ID ng na-upsert na issue
        issue_id = issue_response.data[0]['id']
        print(f"  > Upserted issue with ID: {issue_id}")

        # 2. Ihanda ang records para sa magazine_pages
        pages_to_upsert = []
        for page in pages_data:
            page_num = page['page_number']
            # Hanapin ang katumbas na TOC entry para sa page number na ito
            toc_entry = next((item for item in toc_data if item['page'] == page_num), None)
            
            pages_to_upsert.append({
                "issue_id": issue_id,
                "page_number": page_num,
                "background_image_url": page['url'],
                "section": toc_entry['section'] if toc_entry else None,
                "title": toc_entry['title'] if toc_entry else None,
                "crop_box": page['crop_box'],
                "width": page['width'],
                "height": page['height'],
            })

        # 3. Gumamit ng 'upsert' para sa magazine_pages
        # Tiyakin na may composite unique key ka sa (issue_id, page_number) sa iyong Supabase table
        pages_response = supabase.table("magazine_pages").upsert(
            pages_to_upsert, on_conflict="issue_id, page_number"
        ).execute()

        print(f"  > Upserted {len(pages_response.data)} pages.")
        return {"db_status": "success"}

    except Exception as e:
        print(f"  > DATABASE ERROR: {e}")
        # Mag-throw ng error para malaman ng Vercel Cron na nag-fail ito
        raise
# Itakda ang VERCEL_BLOB_STORE_ID mula sa environment variables
# Kakailanganin mo itong i-set sa Railway.
# os.environ['BLOB_STORE_ID'] = 'iyong_vercel_blob_store_id' 
# os.environ['BLOB_TOKEN'] = 'iyong_vercel_blob_read_write_token'

def autocrop_image(image_bytes: bytes) -> bytes:
    """
    Tatanggap ng image bytes, aalisin ang mga puting borders,
    at ibabalik ang na-crop na image bytes.
    """
    # I-load ang image mula sa bytes
    image = Image.open(io.BytesIO(image_bytes))
    
    # I-convert sa grayscale para mas madali ang analysis
    # (Hindi kailangan kung sigurado kang laging puti ang background)
    # bg = Image.new(image.mode, image.size, image.getpixel((0,0)))
    # diff = ImageChops.difference(image, bg)
    # diff = ImageChops.add(diff, diff, 2.0, -100)
    # bbox = diff.getbbox()

    # Isang mas simpleng approach: i-convert sa 'L' (grayscale) at kunin ang bbox
    grayscale_image = image.convert('L')
    
    # I-invert ang image para ang content ay maging puti at ang background ay itim
    inverted_image = ImageChops.invert(grayscale_image)
    
    # Kunin ang bounding box ng non-black (original content) pixels
    bbox = inverted_image.getbbox()
    
    if bbox:
        # I-crop ang original (colored) na image gamit ang nahanap na bbox
        cropped_image = image.crop(bbox)
        
        # I-save ang na-crop na image pabalik sa isang in-memory bytes buffer
        buffer = io.BytesIO()
        cropped_image.save(buffer, format='PNG')
        return buffer.getvalue()
    else:
        # Kung walang nahanap na content (e.g., isang blangkong page),
        # ibalik na lang ang original na image bytes
        return image_bytes

def process_pdf_from_url(file_id: str, issue_name: str, publication_date: str, toc_data: List[Dict[str, Any]]) -> Dict[str, Any]:
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
    
    # ✨ I-DEFINE ANG MGA REGEX PATTERNS DITO ✨
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    phone_pattern = r'\b(?:\+?(\d{1,3}))?[-. (]*(\d{3})[-. )]*(\d{3})[-. ]*(\d{4})\b'
    # Mas simpleng URL pattern para sa text
    url_pattern = r'\b(?:https?://|www\.)(?:[-\w.]|(?:%[\da-fA-F]{2}))+\b'

    image_urls = []

    # 3. I-proseso ang bawat page
    for i, page in enumerate(doc):
        page_num = i + 1
        print(f"Processing Page {page_num}/{len(doc)}...")
        # --- ✨ HAKBANG 1: I-RENDER ANG BUONG PAGE ✨ ---
        dpi = 150
        pix = page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("png")

        # --- ✨ HAKBANG 2: I-CALCULATE ANG CROP BOX GAMIT ANG PILLOW ✨ ---
        image = Image.open(io.BytesIO(img_bytes))
        grayscale_image = image.convert('L')
        inverted_image = ImageChops.invert(grayscale_image)
        # Ang bbox na ito ay nasa PIXEL coordinates
        autocrop_pixel_bbox = inverted_image.getbbox()

        if autocrop_pixel_bbox:
            # I-crop ang imahe
            cropped_image = image.crop(autocrop_pixel_bbox)
            buffer = io.BytesIO()
            cropped_image.save(buffer, format='PNG')
            cropped_img_bytes = buffer.getvalue()
            
            # ✨ I-CONVERT ANG PIXEL BBOX PABALIK SA PDF POINTS ✨
            # Ang scale factor ay dpi / 72 (standard PDF points per inch)
            scale = dpi / 72.0
            x0, y0, x1, y1 = autocrop_pixel_bbox
            final_content_box = fitz.Rect(x0 / scale, y0 / scale, x1 / scale, y1 / scale)
            
            print(f"  > Autocropped image. Original: {len(img_bytes)} bytes, Cropped: {len(cropped_img_bytes)} bytes")
        else:
            # Kung walang nahanap na content, gamitin ang original
            cropped_img_bytes = img_bytes
            final_content_box = page.rect
            print(f"  > Warning: Autocrop failed for page {page_num}. Using full page.")

        # 3. I-upload ang na-crop na image bytes
        image_filename = f"page-{page_num:02d}.png"
        blob_image = put(
            f"magazine-pages/{issue_name}/{image_filename}",
            cropped_img_bytes, # <-- Gamitin ang na-crop na bytes
            options={'token': os.environ['BLOB_READ_WRITE_TOKEN'], "allowOverwrite": True, "access": 'public'}
        )
        # Para makuha ang bagong dimensions, kailangan nating i-load ulit ang na-crop na image
        final_image = Image.open(io.BytesIO(cropped_img_bytes))

        image_urls.append({
            "page_number": page_num, 
            "url": blob_image['url'],
            # ✨ IDAGDAG ANG BAGONG DIMENSIONS ✨
            "width": final_image.width,
            "height": final_image.height,
            "crop_box": {
                "x0": final_content_box.x0, 
                "y0": final_content_box.y0, 
                "x1": final_content_box.x1, 
                "y1": final_content_box.y1
            }
        })
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
        # --- C. ✨ I-EXTRACT ANG TEXT AT I-SCAN GAMIT ANG REGEX ✨ ---
        
        # --- ✨ HAKBANG 4: I-adjust ang hotspot bbox gamit ang Master Content Box ✨ ---

        # Kunin ang top-left offset ng page's cropbox
        print(f"    [DEBUG] Page {page_num}: Starting HYBRID hotspot extraction...")

        # I-iterate ang lahat ng text blocks
        text_blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES)["blocks"]
        for block in text_blocks:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span["text"]
                        span_bbox = fitz.Rect(span["bbox"]) # Bbox ng buong span

                        # 1. Hanapin ang emails sa loob ng text ng span
                        for match in re.finditer(email_pattern, text):
                            # Ngayon, hanapin ang eksaktong BBOX ng match na iyon
                            # sa loob lang ng span's bounding box.
                            match_rects = page.search_for(match.group(0), clip=span_bbox, quads=False)
                            if match_rects:
                                # Kunin ang unang match (kadalasang isa lang naman ito)
                                rect = match_rects[0]
                                print(f"      [DEBUG] Found Email: '{match.group(0)}' at {list(rect)}")
                                hotspots["emails"].append({"page": page_num, "value": match.group(0), "bbox": list(rect)})

                        # 2. Gawin din para sa phones
                        for match in re.finditer(phone_pattern, text):
                            match_rects = page.search_for(match.group(0), clip=span_bbox, quads=False)
                            if match_rects:
                                rect = match_rects[0]
                                print(f"      [DEBUG] Found Phone: '{match.group(0)}' at {list(rect)}")
                                hotspots["phones"].append({"page": page_num, "value": match.group(0), "bbox": list(rect)})

                        # 3. Gawin din para sa URLs
                        for match in re.finditer(url_pattern, text):
                            match_rects = page.search_for(match.group(0), clip=span_bbox, quads=False)
                            if match_rects:
                                rect = match_rects[0]
                                print(f"      [DEBUG] Found URL: '{match.group(0)}' at {list(rect)}")
                                hotspots["urls"].append({"page": page_num, "value": match.group(0), "bbox": list(rect)})
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
        options={"allowOverwrite": True, "access": 'public'}
    )
    print(f"Uploaded manifest to: {blob_manifest['url']}")
    first_page_for_dims = doc[0]
    original_pdf_dimensions = {
        "width": first_page_for_dims.rect.width,
        "height": first_page_for_dims.rect.height
    }
    save_to_database(
        issue_name=issue_name,
        publication_date=publication_date,
        manifest_url=blob_manifest['url'],
        cover_image_url=image_urls[0]['url'] if image_urls else None,
        # Ipasa ang bagong dimensions
        page_dimensions=original_pdf_dimensions, 
        pages_data=image_urls,
        toc_data=toc_data
    )
    print(f"  First page dim: ")
    return {"status": "success", "manifest_url": blob_manifest['url'], "page_count": len(doc)}
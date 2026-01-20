import fitz  # PyMuPDF
import io
from typing import Dict, Any, List

# I-import ang mga helper functions na kailangan natin
from processor import get_drive_service
from models import ReflowConfig

def process_pdf_for_reflow(file_id: str, config: ReflowConfig) -> Dict[str, Any]:
    """
    Downloads a PDF from Google Drive, extracts structured content (text and images)
    from each page, and prepares it for reflow.
    """
    issue_name = config.issue_number
    print(f"--- 🚀 REFLOW PROCESSOR INITIATED for: {issue_name} 🚀 ---")

    try:
        # --- 1. I-DOWNLOAD ANG PDF MULA SA GOOGLE DRIVE ---
        print(f"Downloading PDF with File ID: {file_id}...")
        drive_service = get_drive_service()
        request = drive_service.files().get_media(fileId=file_id)
        file_bytes = io.BytesIO()
        downloader = io.BytesIO(request.execute())
        file_bytes.write(downloader.read())
        file_bytes.seek(0)
        print("✅ PDF downloaded successfully.")

        # --- 2. BUKSAN ANG PDF GAMIT ANG PYMUPDF ---
        pdf_document = fitz.open(stream=file_bytes, filetype="pdf")
        print(f"PDF opened. It has {len(pdf_document)} pages.")

        # Dito natin iimbakin ang structured content para sa buong magazine
        structured_magazine = {
            "issue_number": issue_name,
            "publication_date": config.publication_date,
            "pages": []
        }

        # --- 3. I-PROCESS ANG BAWAT PAGE ---
        for page_num in range(len(pdf_document)):
            page = pdf_document.load_page(page_num)
            page_number = page_num + 1
            print(f"\n--- Processing Page {page_number} ---")

            # A. I-EXTRACT ANG TEXT BLOCKS GAMIT ANG GET_TEXT("DICT")
            # Ito ang pinakamalakas na feature. Nagbibigay ito ng nested structure.
            text_dict = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
            
            # B. I-EXTRACT ANG IMAGES
            # Ang `full=True` ay para makuha ang lahat ng info tungkol sa image.
            image_list = page.get_images(full=True)

            # --- 4. I-DISPLAY ANG NA-EXTRACT NA DATA (FOR DEBUGGING) ---
            # Sa ngayon, i-print lang natin ang summary para makita natin.
            
            print(f"  📄 Found {len(text_dict.get('blocks', []))} text blocks.")
            for i, block in enumerate(text_dict.get('blocks', [])):
                if block['type'] == 0: # 0 means a text block
                    # Ang bawat block ay may 'lines', at ang bawat line ay may 'spans'
                    # Ang 'spans' ang naglalaman ng actual text at font info.
                    first_span = block['lines'][0]['spans'][0]
                    sample_text = first_span['text'][:30] # Kunin ang unang 30 characters
                    font_size = round(first_span['size'])
                    print(f"    - Block {i}: (Font Size: ~{font_size}pt) '{sample_text}...'")

            print(f"  🖼️ Found {len(image_list)} images.")
            for i, img in enumerate(image_list):
                # Ang `img[7]` ay ang pangalan/description ng image, kung meron.
                img_name = img[7] 
                print(f"    - Image {i}: '{img_name}'")

            # (Sa susunod na step, dito natin ilalagay ang logic para pagsamahin
            # at i-sort ang text at images para i-reconstruct ang layout)

        print("\n--- ✅ REFLOW PROCESSOR FINISHED ---")
        return {"status": "success", "processor": "reflow", "message": "Advanced extraction finished."}

    except Exception as e:
        print(f"❌ An error occurred in reflow_processor: {e}")
        # Mag-raise ng error para malaman ng FastAPI na nag-fail ang task
        raise
import fitz  # PyMuPDF
import io
import json
from typing import Dict, Any, List
from collections import Counter

# I-import ang mga helper functions at models
from processor import get_drive_service, save_to_database # Gagamitin natin ang save_to_database mamaya
from models import ReflowConfig

def reconstruct_page_layout(page: fitz.Page) -> List[Dict[str, Any]]:
    """
    Analyzes a single page to identify and structure its content elements.
    This is the "Heuristics Engine".
    """
    # Step 1: Extract all text blocks and images
    text_dict = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    image_list = page.get_images(full=True)

    # Step 2: Calculate the most common font size to identify paragraphs
    font_sizes = []
    for block in text_dict.get("blocks", []):
        if block['type'] == 0: # 0 = text block
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font_sizes.append(round(span["size"]))
    
    # Gamitin ang Counter para mahanap ang pinaka-madalas na font size
    # Kung walang text, default sa 12
    most_common_size = Counter(font_sizes).most_common(1)[0][0] if font_sizes else 12
    print(f"    - Most common font size (paragraph): {most_common_size}pt")

    # Step 3: Combine text and images into a single list of "elements"
    elements = []
    
    # Process text blocks
    for block in text_dict.get("blocks", []):
        if block['type'] == 0:
            full_text = ""
            # Kunin ang average font size ng block na ito
            block_font_sizes = [round(s['size']) for l in block['lines'] for s in l['spans']]
            avg_size = sum(block_font_sizes) / len(block_font_sizes) if block_font_sizes else 0
            
            # Pagsama-samahin ang text mula sa lahat ng spans sa block
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    full_text += span['text'] + " "
                full_text += "\n" # Magdagdag ng newline kada line
            
            # Heuristic: Identify element type based on font size
            element_type = "paragraph"
            if avg_size > most_common_size * 1.5: # Kung 50% mas malaki, ito ay heading
                element_type = "heading"
            
            elements.append({
                "type": element_type,
                "text": full_text.strip(),
                "bbox": block["bbox"], # Itago ang bounding box para sa sorting
            })

    # Process images
    for img in image_list:
        elements.append({
            "type": "image",
            "bbox": img[0:4], # Ang unang 4 na items ay ang bbox (x0, y0, x1, y1)
            "xref": img[0] # xref ay unique identifier ng image
        })

    # Step 4: Sort all elements by their vertical position (top-to-bottom)
    elements.sort(key=lambda el: el["bbox"][1]) # Ang el["bbox"][1] ay ang 'y0'

    # Step 5: Clean up and finalize the list for JSON output
    final_content = []
    for el in elements:
        if el["type"] == "image":
            # I-extract ang image bytes at i-save (sa susunod na step)
            # Sa ngayon, placeholder muna
            final_content.append({
                "type": "image",
                "src": f"placeholder_for_image_xref_{el['xref']}.png"
            })
        else: # For text elements
            final_content.append({
                "type": el["type"],
                "text": el["text"]
            })
            
    return final_content


def process_pdf_for_reflow(file_id: str, config: ReflowConfig) -> Dict[str, Any]:
    """
    Main function to process the PDF for reflow.
    """
    issue_name = config.issue_number
    print(f"--- 🚀 REFLOW PROCESSOR INITIATED for: {issue_name} 🚀 ---")

    try:
        # 1. Download PDF
        print(f"Downloading PDF with File ID: {file_id}...")
        drive_service = get_drive_service()
        request = drive_service.files().get_media(fileId=file_id)
        file_bytes = io.BytesIO(request.execute())
        print("✅ PDF downloaded successfully.")

        # 2. Open PDF
        pdf_document = fitz.open(stream=file_bytes, filetype="pdf")
        print(f"PDF opened. It has {len(pdf_document)} pages.")

        # Dito iimbakin ang final JSON structure
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

            # Tawagin ang ating bagong "Heuristics Engine"
            page_content = reconstruct_page_layout(page)
            
            structured_magazine["pages"].append({
                "page_number": page_number,
                "content": page_content
            })
            
            # I-print ang summary ng reconstructed content
            print(f"    - Reconstructed {len(page_content)} elements.")
            for item in page_content:
                if item['type'] == 'image':
                    print(f"      - Found: Image ({item['src']})")
                else:
                    print(f"      - Found: {item['type'].capitalize()} ('{item['text'][:40].replace(chr(10), ' ')}...')")


        # 4. I-save ang final JSON (sa ngayon, i-print lang natin)
        final_json_output = json.dumps(structured_magazine, indent=2)
        print("\n--- FINAL SEMANTIC JSON OUTPUT ---")
        print(final_json_output)
        print("------------------------------------")
        
        # SA SUSUNOD NA STEP: I-u-upload natin itong `final_json_output` sa Supabase/Vercel Blob
        # at i-u-update ang `magazine_issues` table.

        print("\n--- ✅ REFLOW PROCESSOR FINISHED ---")
        return {"status": "success", "processor": "reflow", "message": "Reconstruction finished."}

    except Exception as e:
        print(f"❌ An error occurred in reflow_processor: {e}")
        raise
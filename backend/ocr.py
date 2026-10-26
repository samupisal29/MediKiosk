import pytesseract
from PIL import Image, ImageFilter
import io
import re
import os

# Allow overriding path via env variable (for Windows where tesseract is not in PATH)
_tesseract_cmd = os.getenv("TESSERACT_CMD", "")
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd

# Common Windows install path fallback
_win_default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if not _tesseract_cmd and os.path.exists(_win_default):
    pytesseract.pytesseract.tesseract_cmd = _win_default

_tesseract_available = True
try:
    pytesseract.get_tesseract_version()
except Exception:
    _tesseract_available = False


def extract_text_from_image(image_bytes: bytes) -> str:
    """
    Run Tesseract OCR on an image and return the extracted text.
    Applies preprocessing to improve accuracy on photographed prescriptions.
    """
    if not _tesseract_available:
        return "[OCR UNAVAILABLE] Tesseract is not installed. Please install Tesseract OCR and add it to PATH."

    try:
        img = Image.open(io.BytesIO(image_bytes))

        # Handle alpha channel
        if img.mode == "RGBA":
            img = img.convert("RGB")

        # Upscale small images — Tesseract performs best at ~300 DPI
        w, h = img.size
        if w < 1200 or h < 1200:
            scale = max(2, int(1200 / min(w, h)))
            img = img.resize((w * scale, h * scale), Image.LANCZOS)

        # Grayscale + double sharpen for cleaner text edges
        img = img.convert("L")
        img = img.filter(ImageFilter.SHARPEN)
        img = img.filter(ImageFilter.SHARPEN)

        # PSM 4 = single column (best for prescriptions)
        # PSM 6 = uniform block (better for lab reports)
        text = pytesseract.image_to_string(img, config=r'--oem 3 --psm 4')

        # Retry with PSM 6 if result is too short
        if len(text.strip()) < 30:
            text = pytesseract.image_to_string(img, config=r'--oem 3 --psm 6')

        cleaned = text.strip()
        return cleaned if cleaned else "[OCR returned empty result for this image]"

    except Exception as e:
        return f"[OCR ERROR] {str(e)}"


def regex_extract_information(raw_text: str) -> dict:
    """
    Regex-based information extraction from OCR text.

    Handles two document types:
      1. Clinical/lab reports  — vitals (BP, HR, Temp, SpO2, Glucose)
      2. Prescriptions         — patient name, diagnosis, medications with frequency

    Returns a dict of {label: value} pairs.
    """
    if not raw_text or raw_text.startswith("[OCR"):
        return {}

    extracted = {}

    # Patient Name (match on single line only)
    name_match = re.search(r'(?i)\bName\s*[:\-]?\s*([A-Za-z][A-Za-z0-9 .\t]{2,40})', raw_text)
    if name_match:
        extracted["Patient Name (from doc)"] = name_match.group(1).strip()

    # Age / Sex
    age_match = re.search(r'(?i)\bAge[\s/]*Sex\s*[:\-]?\s*(\d{1,3})\s*/?s*(M(?:ale)?|F(?:emale)?)', raw_text)
    if age_match:
        extracted["Age / Gender (from doc)"] = f"{age_match.group(1)} / {age_match.group(2)}"
    else:
        # fallback: Age/Sex : 25 / Male
        age_match2 = re.search(r'(?i)\bAge/Sex\s*[:\-]?\s*(\d{1,3})\s*/\s*(Male|Female|M|F)', raw_text)
        if age_match2:
            extracted["Age / Gender (from doc)"] = f"{age_match2.group(1)} / {age_match2.group(2)}"

    # Diagnosis / Topic
    diag_match = re.search(
        r'(?i)\b(?:topic|diagnosis|dx|complaint)\s*[:\-]?\s*(.+)',
        raw_text
    )
    if diag_match:
        extracted["Diagnosis (from doc)"] = diag_match.group(1).strip()[:120]

    # Medications with Tab/Cap/Syp prefix (clean image / typed text)
    med_pattern = re.findall(
        r'(?i)'
        r'(?:\d+[\.\)]\s*)?'
        r'(Tab|Cap|Syp|Inj|Syr|Oint|Drops?|Gel|Cream)[\.\s]+'
        r'([A-Za-z]{3}[A-Za-z0-9 \-]{0,27})'  # Ensure at least 3 letters for drug name
        r'(?:[\s\n]+(\d+(?:\.\d+)?\s*(?:mg|mcg|ml|g|IU)?))?'
        r'(?:[\s\S]*?(OD|BD|TDS|QID|SOS|HS|PRN))?',
        raw_text
    )
    for i, (form, name, dose, freq) in enumerate(med_pattern[:6], 1):
        # Reject junk matches like "Gel. TDS" where drug name is empty or just "TDS"
        name = name.strip()
        if len(name) < 3 or name.upper() in ["OD", "BD", "TDS", "QID", "SOS", "HS", "PRN"]:
            continue
            
        parts = [f"{form}. {name}"]
        if dose:
            parts.append(dose.strip())
        if freq:
            parts.append(freq.strip())
        extracted[f"Medication {i}"] = " — ".join(parts)

    # Fallback A: Drug name on own line followed by OD/BD/TDS on next line
    # Handles noisy OCR where Tab/Cap prefix was misread
    if not any(k.startswith("Medication") for k in extracted):
        SKIP = {"caring", "pharmacy", "advice", "follow", "avoid", "drink", "small",
                "note", "review", "symptoms", "treatment", "imteyaz", "fahad",
                "opd", "prescription", "name", "age", "topic", "weight", "drx"}
        # Allow some noise between the name and the newline/frequency
        freq_anchor = re.findall(
            r'(?m)^([A-Za-z][A-Za-z0-9 ]{3,25})(?:.*?)?\n(?:.*?)?\b(OD|BD|TDS|QID|SOS|HS|PRN|CD)\b',
            raw_text
        )
        for i, (name, freq) in enumerate(freq_anchor[:6], 1):
            name = name.strip()
            if name and not any(s in name.lower() for s in SKIP):
                # Map 'CD' OCR typo to 'OD'
                if freq == 'CD': freq = 'OD'
                extracted[f"Medication {i}"] = f"{name} — {freq}"

    # Fallback B: Generic dosage pattern (e.g., Metformin 500mg)
    if not any(k.startswith("Medication") for k in extracted):
        generic_meds = re.findall(r'(?i)\b([A-Za-z]{4,})\s+(\d+\s*(?:mg|mcg|ml|g)\b)', raw_text)
        for name, dose in generic_meds[:3]:
            extracted[f"Medication: {name.capitalize()}"] = dose

    # Vitals: Blood Pressure
    bp = re.search(r'(?i)\b(?:bp|blood\s*pressure)\b[\s:\-]*(\d{2,3}\s*/\s*\d{2,3})', raw_text)
    if bp:
        extracted["Blood Pressure"] = bp.group(1).replace(" ", "")

    # Vitals: Heart Rate
    hr = re.search(r'(?i)\b(?:hr|heart\s*rate|pulse)\b[\s:\-]*(\d{2,3})\s*(?:bpm)?', raw_text)
    if hr:
        extracted["Heart Rate"] = f"{hr.group(1)} bpm"

    # Vitals: Temperature
    temp = re.search(r'(?i)\b(?:temp(?:erature)?)\b[\s:\-]*(\d{2,3}(?:\.\d{1,2})?)\s*([CcFf])\b', raw_text)
    if temp:
        extracted["Temperature"] = f"{temp.group(1)} {temp.group(2).upper()}"

    # Vitals: SpO2
    spo2 = re.search(r'(?i)\b(?:spo2|o2\s*sat(?:uration)?)\b[\s:\-]*(\d{2,3})\s*%?', raw_text)
    if spo2:
        extracted["SpO2"] = f"{spo2.group(1)}%"

    # Vitals: Blood Glucose
    glucose = re.search(r'(?i)\b(?:glucose|blood\s*sugar)\b[\s:\-]*(\d{2,4})\s*(?:mg/dl|mmol/l)?', raw_text)
    if glucose:
        extracted["Blood Glucose"] = f"{glucose.group(1)} mg/dL"

    return extracted

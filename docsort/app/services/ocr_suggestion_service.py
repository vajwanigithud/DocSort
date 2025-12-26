import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Dict, List

from docsort.app.services import pdf_utils, naming_service

try:
    import PIL  # type: ignore
    from PIL import Image, ImageFilter, ImageOps, ImageStat  # type: ignore
except Exception:  # pragma: no cover - optional dependency guard
    PIL = None  # type: ignore
    Image = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageOps = None  # type: ignore
    ImageStat = None  # type: ignore

logger = logging.getLogger(__name__)

_text_cache: Dict[str, str] = {}
_logged_ocr_unavailable = False
OCR_MAX_PAGES = 2
_WEAK_TEXT_CHARS = 120
_WEAK_TEXT_WORDS = 18


def _try_import_cv2():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        return cv2, np
    except Exception:
        return None, None


def _deskew_binary(image_gray, cv2, np):
    coords = cv2.findNonZero(255 - image_gray)
    if coords is None or coords.size == 0:
        return image_gray
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.2:
        return image_gray
    h, w = image_gray.shape[:2]
    center = (w // 2, h // 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(image_gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _preprocess_with_cv2(pil_image):
    cv2, np = _try_import_cv2()
    if not cv2 or not np:
        raise RuntimeError("cv2 not available")
    np_img = np.array(pil_image)
    if np_img.ndim == 3 and np_img.shape[2] >= 3:
        gray = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)
    else:
        gray = np_img if np_img.ndim == 2 else np_img[:, :, 0]
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    try:
        denoised = cv2.fastNlMeansDenoising(enhanced, None, h=8, templateWindowSize=7, searchWindowSize=21)
    except Exception:
        denoised = cv2.GaussianBlur(enhanced, (3, 3), 0)
    try:
        _, thresh = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    except Exception:
        thresh = cv2.adaptiveThreshold(
            denoised,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            35,
            8,
        )
    try:
        deskewed = _deskew_binary(thresh, cv2, np)
    except Exception:
        deskewed = thresh
    blurred = cv2.GaussianBlur(deskewed, (0, 0), sigmaX=1.0)
    sharpened = cv2.addWeighted(deskewed, 1.5, blurred, -0.5, 0)
    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(sharpened, cv2.MORPH_OPEN, kernel, iterations=1)
    cleaned = np.clip(cleaned, 0, 255).astype("uint8")
    return Image.fromarray(cleaned)


def _preprocess_with_pil_only(pil_image):
    if not Image:
        return pil_image
    gray = pil_image.convert("L")
    auto = ImageOps.autocontrast(gray)
    try:
        blurred = auto.filter(ImageFilter.MedianFilter(size=3))
    except Exception:
        blurred = auto
    sharpened = blurred.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))
    stat = ImageStat.Stat(sharpened)
    mean = stat.mean[0] if stat.mean else 128
    threshold = 0.9 * mean
    binarized = sharpened.point(lambda p: 255 if p > threshold else 0)
    return binarized


def preprocess_for_ocr(pil_image):
    try:
        return _preprocess_with_cv2(pil_image)
    except Exception:
        try:
            return _preprocess_with_pil_only(pil_image)
        except Exception:
            return pil_image


def _cache_key(path: Path, max_pages: int) -> str:
    try:
        stat = path.stat()
        mtime = int(stat.st_mtime)
    except Exception:
        mtime = 0
    return f"{str(path.resolve())}::{mtime}::{max_pages}"


def _try_pypdf_text(path: Path, max_pages: int) -> str:
    try:
        text, err = pdf_utils.extract_pdf_text(str(path), max_pages=max_pages)
        if err:
            logger.debug("pypdf text extraction error for %s: %s", path, err)
        return text or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("pypdf text extraction failed for %s: %s", path, exc)
        return ""


def _try_ocr(path: Path, max_pages: int) -> str:
    global _logged_ocr_unavailable
    start = time.time()
    try:
        import fitz  # PyMuPDF
    except Exception:
        if not _logged_ocr_unavailable:
            logger.info("OCR unavailable: PyMuPDF not installed")
            _logged_ocr_unavailable = True
        return ""
    try:
        import pytesseract  # type: ignore
    except Exception:
        if not _logged_ocr_unavailable:
            logger.info("OCR unavailable: pytesseract not installed")
            _logged_ocr_unavailable = True
        return ""
    try:
        candidate_cmd = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        current_cmd = getattr(getattr(pytesseract, "pytesseract", pytesseract), "tesseract_cmd", None)
        if candidate_cmd.exists():
            if not current_cmd or not Path(str(current_cmd)).exists():
                pytesseract.pytesseract.tesseract_cmd = str(candidate_cmd)
    except Exception:
        pass

    def _ocr_page(doc_obj, page_idx: int, scale: float, psm: int) -> str:
        try:
            if not Image:
                raise RuntimeError("PIL not available")
            page = doc_obj.load_page(page_idx)
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            try:
                processed_image = preprocess_for_ocr(image)
            except Exception as exc:  # noqa: BLE001
                logger.debug("OCR preprocess failed path=%s p=%s scale=%s err=%s", path, page_idx, scale, exc)
                processed_image = image
            processed_image = processed_image.convert("L")
            config = f"--oem 1 --psm {psm}"
            return pytesseract.image_to_string(processed_image, lang="eng", config=config)
        except Exception as exc:  # noqa: BLE001
            logger.debug("OCR page render failed p=%s scale=%s for %s: %s", page_idx, scale, path, exc)
            return ""

    try:
        doc = fitz.open(path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("OCR open failed for %s: %s", path, exc)
        return ""
    texts: List[str] = []

    def _is_weak_text(val: str) -> bool:
        return len(val) < _WEAK_TEXT_CHARS or len(val.split()) < _WEAK_TEXT_WORDS

    def _ocr_page_with_retries(doc_obj, page_idx: int) -> str:
        attempt_settings = [(2.0, 6), (1.6, 4)]
        best = ""
        for attempt_idx, (scale, psm) in enumerate(attempt_settings):
            text = _ocr_page(doc_obj, page_idx, scale, psm)
            chars = len(text)
            words = len(text.split())
            retry = _is_weak_text(text) and attempt_idx + 1 < len(attempt_settings)
            logger.info(
                "OCR attempt path=%s page=%s scale=%.2f psm=%s retry=%s chars=%s words=%s",
                path,
                page_idx,
                scale,
                psm,
                retry,
                chars,
                words,
            )
            if chars > len(best):
                best = text
            if not retry:
                break
        return best

    max_pages_to_use = min(max_pages, doc.page_count, OCR_MAX_PAGES)
    try:
        for page_idx in range(max_pages_to_use):
            text = _ocr_page_with_retries(doc, page_idx)
            if text:
                texts.append(text)
    finally:
        doc.close()
    elapsed = time.time() - start
    combined = "\n".join(texts)
    if combined:
        logger.info("OCR finished path=%s duration=%.2fs chars=%s", path, elapsed, len(combined))
    else:
        logger.info("OCR finished with no text path=%s duration=%.2fs", path, elapsed)
    return combined


def get_text_for_pdf(path: str, max_pages: int = 1) -> str:
    pdf_path = Path(path)
    if not pdf_path.exists():
        return ""
    effective_max_pages = max_pages
    key = _cache_key(pdf_path, effective_max_pages)
    if key in _text_cache:
        return _text_cache[key]
    logger.info("OCR text request start path=%s max_pages=%s", pdf_path, max_pages)
    text = _try_pypdf_text(pdf_path, max_pages=effective_max_pages)
    if len(text) < 200 or len(text.split()) < 15:
        ocr_text = _try_ocr(pdf_path, max_pages=effective_max_pages)
        if ocr_text:
            text = ocr_text
    _text_cache[key] = text or ""
    logger.info("OCR text request finished path=%s chars=%s", pdf_path, len(text or ""))
    return text or ""


def build_ocr_suggestions(text: str, fallback_stem: str) -> List[str]:
    suggestions: List[str] = []
    if not text:
        return suggestions
    text_lower = text.lower()

    def find_doc_type() -> str:
        for token in ["invoice", "tax invoice", "estimate", "receipt"]:
            if token in text_lower:
                return token
        return ""

    def find_number() -> str:
        patterns = [
            r"(?:invoice|inv)[^\d]{0,15}(\d{2,8})",
            r"(?:estimate)[^\d]{0,15}(\d{2,8})",
            r"(?:receipt)[^\d]{0,15}(\d{2,8})",
            r"(?:no\.?|number|#)[^\d]{0,10}(\d{2,8})",
        ]
        for pat in patterns:
            m = re.search(pat, text_lower, re.IGNORECASE)
            if m:
                candidate = m.group(1)
                if candidate and not re.fullmatch(r"0+", candidate):
                    return candidate
        return ""

    def find_date() -> str:
        patts = [
            r"\b(\d{4}-\d{2}-\d{2})\b",
            r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b",
            r"\b(\d{2}\.\d{2}\.\d{4})\b",
        ]
        for pat in patts:
            m = re.search(pat, text)
            if m:
                raw = m.group(1)
                norm = pdf_utils.normalize_date(raw) if hasattr(pdf_utils, "normalize_date") else None
                return norm or raw
        return ""

    def find_vendor() -> str:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return ""
        top = lines[0]
        if len(lines) > 1 and len(lines[1]) > len(top):
            top = lines[1]
        return re.sub(r"[^\w\s\-\.]", "", top)[:30]

    doc_type = find_doc_type()
    number = find_number()
    date = find_date()
    vendor = find_vendor()

    base_tokens = []
    if vendor:
        base_tokens.append(vendor)
    if doc_type:
        base_tokens.append(doc_type.title())
    if number:
        base_tokens.append(number)
    if date:
        base_tokens.append(date)
    if base_tokens:
        suggestions.append("_".join(base_tokens) + ".pdf")
    if vendor and doc_type and number:
        suggestions.append(f"{vendor}_{doc_type.title()}_{number}.pdf")
    if vendor:
        suggestions.append(f"{vendor}_{fallback_stem}.pdf")
    suggestions.append(f"{fallback_stem}.pdf")

    deduped: List[str] = []
    seen = set()
    for s in suggestions:
        clean = naming_service.enforce_no_spaces(s)
        clean = re.sub(r'[<>:"/\\\\|?*]', "", clean)
        if not clean.lower().endswith(".pdf"):
            clean = f"{clean}.pdf"
        if clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


def fingerprint_text(text: str) -> str:
    snippet = (text or "")[:300].encode("utf-8", errors="ignore")
    return hashlib.sha1(snippet).hexdigest()

# Self-check: python -c "import pytesseract; pytesseract.pytesseract.tesseract_cmd=r'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'; from docsort.app.services.ocr_suggestion_service import get_text_for_pdf; print(get_text_for_pdf(r'<PDF>', max_pages=2)[:800])"

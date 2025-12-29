import hashlib
import logging
import os
import re
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, List, Tuple

from docsort.app.services import naming_service, pdf_utils, ocr_input_cache
from docsort.app.storage import ocr_cache_store

try:
    from PIL import Image, ImageFilter, ImageOps, ImageStat
except Exception:  # pragma: no cover - optional dependency guard
    Image = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_TEXT_CACHE_MAX_ENTRIES = 128
_text_cache: OrderedDict[str, str] = OrderedDict()
_logged_ocr_unavailable = False
OCR_MAX_PAGES = 2
_WEAK_TEXT_CHARS = 120
_WEAK_TEXT_WORDS = 18
_OCR_DPI = 300
_OCR_PSMS = [6, 11, 4]


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


def _preprocess_with_cv2(pil_image: Any) -> Any:
    cv2, np = _try_import_cv2()
    if not cv2 or not np:
        raise RuntimeError("cv2 not available")
    if not Image:
        raise RuntimeError("PIL not available")
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


def _preprocess_with_pil_only(pil_image: Any) -> Any:
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


def preprocess_for_ocr(pil_image: Any) -> Any:
    try:
        return _preprocess_with_cv2(pil_image)
    except Exception:
        try:
            return _preprocess_with_pil_only(pil_image)
        except Exception:
            return pil_image


def _text_quality_score(text: str) -> float:
    if not text:
        return 0.0
    lower = text.lower()
    word_count = len(text.split())
    alpha_chars = sum(1 for c in text if c.isalpha())
    total_chars = len(text)
    alpha_ratio = alpha_chars / total_chars if total_chars else 0.0
    keyword_tokens = ["invoice", "receipt", "estimate", "appointment", "order", "po", "reference", "total", "date"]
    token_hits = sum(1 for tok in keyword_tokens if tok in lower)
    return (word_count * 1.0) + (alpha_ratio * 40.0) + (token_hits * 6.0)


def _cache_key(path: Path, max_pages: int) -> str:
    try:
        stat = path.stat()
        mtime = int(stat.st_mtime)
    except Exception:
        mtime = 0
    return f"{str(path.resolve())}::{mtime}::{max_pages}"


def _configure_tesseract_command(pytesseract) -> None:
    try:
        candidate_paths: List[Path] = []
        env_cmd = os.environ.get("TESSERACT_CMD")
        if env_cmd:
            candidate_paths.append(Path(env_cmd))
        which_cmd = shutil.which("tesseract")
        if which_cmd:
            candidate_paths.append(Path(which_cmd))
        candidate_paths.append(Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
        for candidate in candidate_paths:
            if candidate and candidate.exists():
                pytesseract.pytesseract.tesseract_cmd = str(candidate)
                return
    except Exception:
        return


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
    if not Image:
        if not _logged_ocr_unavailable:
            logger.info("OCR unavailable: PIL not installed")
            _logged_ocr_unavailable = True
        return ""
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
    _configure_tesseract_command(pytesseract)

    def _ocr_page(doc_obj, page_idx: int, scale: float, psm: int) -> Tuple[str, str, float, int, int]:
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
            lang_candidates = ["eng+osd", "eng"]
            last_lang = lang_candidates[-1]
            best_local = ""
            for lang_candidate in lang_candidates:
                try:
                    config = f"--oem 3 --psm {psm}"
                    text = pytesseract.image_to_string(processed_image, lang=lang_candidate, config=config)
                    best_local = text
                    break
                except Exception as tess_exc:  # noqa: BLE001
                    last_lang = lang_candidate
                    logger.debug(
                        "OCR tesseract attempt failed path=%s p=%s scale=%s psm=%s lang=%s err=%s",
                        path,
                        page_idx,
                        scale,
                        psm,
                        lang_candidate,
                        tess_exc,
                    )
            text = best_local
            score = _text_quality_score(text)
            words = len(text.split())
            chars = len(text)
            return text, last_lang, score, words, chars
        except Exception as exc:  # noqa: BLE001
            logger.debug("OCR page render failed p=%s scale=%s for %s: %s", page_idx, scale, path, exc)
            return "", "", 0.0, 0, 0

    texts: List[str] = []

    def _is_weak_text(val: str) -> bool:
        return len(val) < _WEAK_TEXT_CHARS or len(val.split()) < _WEAK_TEXT_WORDS

    def _ocr_page_with_retries(doc_obj, page_idx: int) -> str:
        scale_psm_plan = [
            (_OCR_DPI / 72, list(_OCR_PSMS)),
        ]
        best_text = ""
        best_score = -1.0
        for scale_idx, (scale, psms) in enumerate(scale_psm_plan):
            for psm in psms:
                text, lang_used, score, words, chars = _ocr_page(doc_obj, page_idx, scale, psm)
                is_retry = _is_weak_text(text)
                is_best = score > best_score
                if is_best:
                    best_text = text
                    best_score = score
                logger.info(
                    "OCR attempt path=%s page=%s dpi=%s psm=%s lang=%s retry=%s chars=%s words=%s score=%.2f best=%s",
                    path,
                    page_idx,
                    int(scale * 72),
                    psm,
                    lang_used,
                    is_retry,
                    chars,
                    words,
                    score,
                    is_best,
                )
            if best_text and not _is_weak_text(best_text):
                break
        return best_text

    try:
        with fitz.open(path) as doc:
            max_pages_to_use = min(max_pages, doc.page_count, OCR_MAX_PAGES)
            for page_idx in range(max_pages_to_use):
                text = _ocr_page_with_retries(doc, page_idx)
                if text:
                    texts.append(text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("OCR open failed for %s: %s", path, exc)
        return ""
    elapsed = time.time() - start
    combined = "\n".join(texts)
    if combined:
        logger.info("OCR finished path=%s duration=%.2fs chars=%s", path, elapsed, len(combined))
    else:
        logger.info("OCR finished with no text path=%s duration=%.2fs", path, elapsed)
    return combined


def get_text_for_pdf(path: str, max_pages: int = 1) -> str:
    pdf_path = Path(path).resolve()
    if not pdf_path.exists():
        return ""
    effective_max_pages = max_pages
    key = _cache_key(pdf_path, effective_max_pages)
    if key in _text_cache:
        cached_val = _text_cache[key]
        _text_cache.move_to_end(key)
        return cached_val

    fingerprint = ocr_cache_store.compute_fingerprint(pdf_path)
    cache_hit = False
    text_source = ""
    text = ""

    if fingerprint:
        try:
            persistent_text = ocr_cache_store.get_cached_text(
                str(pdf_path), max_pages=effective_max_pages, fingerprint=fingerprint
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("OCR sqlite cache read failed path=%s err=%s", pdf_path, exc)
            persistent_text = ""
        if persistent_text:
            cache_hit = True
            text_source = "sqlite"
            text = persistent_text

    if not text:
        text = _try_pypdf_text(pdf_path, max_pages=effective_max_pages)
        text_source = "pypdf" if text else text_source

    if len(text) < 200 or len(text.split()) < 15:
        cached_path = ocr_input_cache.cache_pdf_for_ocr(pdf_path)
        if cached_path:
            ocr_text = _try_ocr(cached_path, max_pages=effective_max_pages)
            text_source = "ocr-cached"
        else:
            logger.warning("OCR cache copy unavailable for %s; falling back to direct OCR", pdf_path)
            ocr_text = _try_ocr(pdf_path, max_pages=effective_max_pages)
            text_source = "ocr-original"
        if ocr_text:
            text = ocr_text

    _text_cache[key] = text or ""
    _text_cache.move_to_end(key)
    if len(_text_cache) > _TEXT_CACHE_MAX_ENTRIES:
        _text_cache.popitem(last=False)
    if fingerprint:
        try:
            ocr_cache_store.upsert_cached_text(
                str(pdf_path), max_pages=effective_max_pages, text=text or "", fingerprint=fingerprint
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("OCR sqlite cache write failed path=%s err=%s", pdf_path, exc)
    logger.info(
        "OCR text request path=%s fingerprint=%s cache_hit=%s source=%s chars=%s",
        pdf_path,
        bool(fingerprint),
        cache_hit,
        text_source or "none",
        len(text or ""),
    )
    return text or ""


def _dedupe_preserve(names: Iterable[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for name in names:
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


def _format_filename(parts: List[str], max_len: int = 80) -> str:
    tokens: List[str] = []
    for part in parts:
        if not part:
            continue
        token = re.sub(r'[<>:"/\\|?*]', "", str(part))
        token = re.sub(r"\s+", "_", token)
        token = re.sub(r"_+", "_", token).strip("_")
        if token:
            tokens.append(token)
    name = "_".join(tokens) if tokens else "Document"
    name = naming_service.enforce_no_spaces(name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    if len(name) > max_len:
        root, ext = os.path.splitext(name)
        name = f"{root[: max_len - len(ext)]}{ext}"
    return name


def _format_amount(currency: str, amount: str) -> str:
    cur = (currency or "").strip().upper()
    if cur in {"$", "USD"}:
        cur = "USD"
    elif cur in {"€", "EUR"}:
        cur = "EUR"
    elif cur in {"£", "GBP"}:
        cur = "GBP"
    elif cur in {"AED"}:
        cur = "AED"
    if not amount:
        return cur
    return f"{cur}-{amount}" if cur else amount


def _extract_generic_fields(text: str) -> dict:
    lines = [ln.strip() for ln in (text or "").replace(" ", " ").splitlines() if ln.strip()]
    lower = (text or "").lower()

    def find_date() -> str:
        date_patterns = [
            r"\b\d{4}-\d{2}-\d{2}\b",
            r"\b\d{2}[/-]\d{2}[/-]\d{4}\b",
            r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b",
        ]
        for line in lines:
            if "date" in line.lower():
                for pat in date_patterns:
                    m = re.search(pat, line)
                    if m:
                        return m.group(0)
        for pat in date_patterns:
            m = re.search(pat, text or "")
            if m:
                return m.group(0)
        return ""

    def find_primary_id() -> str:
        id_patterns = [
            r"(invoice|inv|reference|ref|appointment|po|order|ticket)[^\w]{0,10}([A-Z0-9][A-Z0-9\-\/]{2,40})",
            r"\b(ID|No\.?|Number|Ref\.?)[:\s\-]{0,4}([A-Z0-9][A-Z0-9\-\/]{2,40})",
        ]
        for pat in id_patterns:
            m = re.search(pat, text or "", re.IGNORECASE)
            if m:
                candidate = m.group(2)
                if candidate and not re.fullmatch(r"0+", candidate):
                    return candidate
        return ""

    def find_party() -> str:
        org_tokens = {"llc", "ltd", "trading", "fze", "inc", "pty", "plc", "corp", "company"}
        best = ""
        best_score = -1.0
        for line in lines[:12]:
            cleaned = re.sub(r"[^\w\s\-\&\.\,]", "", line).strip()
            if not cleaned:
                continue
            letters = sum(1 for c in cleaned if c.isalpha())
            upper_ratio = (sum(1 for c in cleaned if c.isupper()) / letters) if letters else 0
            token_count = len(cleaned.split())
            score = len(cleaned) + upper_ratio * 10 + token_count * 1.5
            lower_line = cleaned.lower()
            if any(tok in lower_line for tok in org_tokens):
                score += 8
            if score > best_score:
                best_score = score
                best = cleaned
        return best

    def find_amount() -> tuple[str, str]:
        amt_pat = r"(grand\s*total|total\s*amount|amount\s*due|balance\s*due|total)\s*[:\-]?\s*([A-Z]{0,4})?\s*([\$€£]?\s*[0-9][0-9\.,]*)"
        matches = list(re.finditer(amt_pat, text or "", flags=re.IGNORECASE))
        if not matches:
            return "", ""
        m = matches[-1]
        cur = (m.group(2) or "").strip()
        amt = (m.group(3) or "").replace(",", "").replace(" ", "")
        amt = re.sub(r"[^\d\.]", "", amt)
        return cur, amt

    def find_doc_tag() -> str:
        if "invoice" in lower:
            return "Invoice"
        if "receipt" in lower:
            return "Receipt"
        if "appointment" in lower:
            return "Appointment"
        if "purchase order" in lower or " po" in lower or "po " in lower or "order" in lower:
            return "Order"
        return "Document"

    cur, amt = find_amount()
    return {
        "date": find_date(),
        "primary_id": find_primary_id(),
        "party": find_party(),
        "currency": cur,
        "amount": amt,
        "doc_tag": find_doc_tag(),
    }


def _build_generic_suggestions(text: str, fallback_stem: str) -> List[str]:
    fields = _extract_generic_fields(text)
    amount_token = _format_amount(fields["currency"], fields["amount"])
    party = fields["party"]
    doc_tag = fields["doc_tag"]
    primary_id = fields["primary_id"]
    date = fields["date"]

    templates = [
        [date, party, doc_tag, primary_id, amount_token],
        [party, doc_tag, primary_id, date],
        [doc_tag, primary_id, party, date],
        [party, primary_id],
        [date, doc_tag, party],
    ]
    suggestions = [_format_filename(parts) for parts in templates]
    suggestions = _dedupe_preserve(suggestions)

    base_fallback = _format_filename([fallback_stem or doc_tag or "Document"])
    filler_idx = 1
    while len(suggestions) < 5:
        filler = base_fallback if filler_idx == 1 else _format_filename([fallback_stem or doc_tag or "Document", str(filler_idx)])
        filler_idx += 1
        if filler.lower() in (s.lower() for s in suggestions):
            continue
        suggestions.append(filler)
    return suggestions[:5]

def _self_test_cache_read_first() -> bool:
    """Lightweight sanity check: sqlite cache win even if cache_pdf_for_ocr returns None."""
    import tempfile

    tmp = Path(tempfile.gettempdir()) / f"docsort_ocr_test_{time.time_ns()}.pdf"
    tmp.write_bytes(b"%PDF-1.4 test")
    saved_get_cached = ocr_cache_store.get_cached_text
    saved_upsert = ocr_cache_store.upsert_cached_text
    saved_cache_pdf = ocr_input_cache.cache_pdf_for_ocr
    saved_pypdf = _try_pypdf_text
    saved_ocr = _try_ocr
    try:
        ocr_cache_store.get_cached_text = lambda *a, **k: "hello invoice"  # type: ignore[assignment]
        ocr_cache_store.upsert_cached_text = lambda *a, **k: None  # type: ignore[assignment]
        ocr_input_cache.cache_pdf_for_ocr = lambda *a, **k: None  # type: ignore[assignment]
        globals()["_try_pypdf_text"] = lambda *a, **k: ""  # type: ignore[assignment]
        globals()["_try_ocr"] = lambda *a, **k: ""  # type: ignore[assignment]
        result = get_text_for_pdf(str(tmp), max_pages=1)
        return result == "hello invoice"
    finally:
        ocr_cache_store.get_cached_text = saved_get_cached  # type: ignore[assignment]
        ocr_cache_store.upsert_cached_text = saved_upsert  # type: ignore[assignment]
        ocr_input_cache.cache_pdf_for_ocr = saved_cache_pdf  # type: ignore[assignment]
        globals()["_try_pypdf_text"] = saved_pypdf
        globals()["_try_ocr"] = saved_ocr
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def build_ocr_suggestions(text: str, fallback_stem: str) -> List[str]:
    if not text:
        return []
    return _build_generic_suggestions(text, fallback_stem)


def fingerprint_text(text: str) -> str:
    snippet = (text or "")[:300].encode("utf-8", errors="ignore")
    return hashlib.sha1(snippet).hexdigest()

# Self-check (manual): python - <<'PY'
# from docsort.app.services.ocr_suggestion_service import get_text_for_pdf
# path = r"<PDF>"
# print(len(get_text_for_pdf(path, max_pages=1)))
# print(len(get_text_for_pdf(path, max_pages=1)))  # second run should hit sqlite cache
# PY

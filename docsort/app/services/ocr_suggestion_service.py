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
from docsort.app.services import invoice_field_extractor
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
MAX_FILENAME_LEN = 80


def _preprocess_lightweight(pil_image: Any) -> Any:
    if not Image:
        return pil_image
    gray = pil_image.convert("L")
    auto = ImageOps.autocontrast(gray)
    try:
        sharpened = auto.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    except Exception:
        sharpened = auto
    try:
        threshold = sharpened.point(lambda p: 255 if p > 180 else 0)
    except Exception:
        threshold = sharpened
    return threshold


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
        return _preprocess_lightweight(pil_image)
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
    invoice_tokens = ["invoice", "tax invoice", "trn", "total", "date", "invoice no", "inv no"]
    token_hits = sum(1 for tok in invoice_tokens if tok in lower)
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

    def _ocr_page(doc_obj, page_idx: int, scale: float, psm: int) -> Tuple[str, str]:
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
            for lang_candidate in lang_candidates:
                try:
                    config = f"--oem 3 --psm {psm}"
                    text = pytesseract.image_to_string(processed_image, lang=lang_candidate, config=config)
                    return text, lang_candidate
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
            return "", last_lang
        except Exception as exc:  # noqa: BLE001
            logger.debug("OCR page render failed p=%s scale=%s for %s: %s", page_idx, scale, path, exc)
            return "", ""

    texts: List[str] = []

    def _is_weak_text(val: str) -> bool:
        return len(val) < _WEAK_TEXT_CHARS or len(val.split()) < _WEAK_TEXT_WORDS

    def _ocr_page_with_retries(doc_obj, page_idx: int) -> str:
        scale_psm_plan = [
            (300 / 72, [11, 6, 4]),
            (2.5, [11, 6, 4]),
        ]
        best_text = ""
        best_score = -1.0
        for scale_idx, (scale, psms) in enumerate(scale_psm_plan):
            for psm in psms:
                text, lang_used = _ocr_page(doc_obj, page_idx, scale, psm)
                score = _text_quality_score(text)
                chars = len(text)
                words = len(text.split())
                is_retry = _is_weak_text(text)
                is_best = score > best_score
                if is_best:
                    best_text = text
                    best_score = score
                logger.info(
                    "OCR attempt path=%s page=%s scale=%.2f psm=%s lang=%s retry=%s chars=%s words=%s score=%.2f best=%s",
                    path,
                    page_idx,
                    scale,
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
    pdf_path = Path(path)
    if not pdf_path.exists():
        return ""
    cached_path = ocr_input_cache.cache_pdf_for_ocr(pdf_path)
    if not cached_path:
        logger.warning("OCR cache copy unavailable for %s", pdf_path)
        return ""
    logger.info("OCR using cached copy: src=%s cached=%s", pdf_path, cached_path)
    effective_max_pages = max_pages
    key = _cache_key(pdf_path, effective_max_pages)
    if key in _text_cache:
        cached_val = _text_cache[key]
        _text_cache.move_to_end(key)
        return cached_val
    fingerprint = ocr_cache_store.compute_fingerprint(pdf_path)
    if fingerprint:
        try:
            persistent_text = ocr_cache_store.get_cached_text(
                str(pdf_path), max_pages=effective_max_pages, fingerprint=fingerprint
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("OCR sqlite cache read failed path=%s err=%s", pdf_path, exc)
            persistent_text = ""
        if persistent_text:
            _text_cache[key] = persistent_text
            _text_cache.move_to_end(key)
            if len(_text_cache) > _TEXT_CACHE_MAX_ENTRIES:
                _text_cache.popitem(last=False)
            logger.info(
                "OCR sqlite cache hit path=%s max_pages=%s chars=%s",
                pdf_path,
                effective_max_pages,
                len(persistent_text),
            )
            return persistent_text
    logger.info("OCR text request start path=%s max_pages=%s", pdf_path, max_pages)
    text = _try_pypdf_text(cached_path, max_pages=effective_max_pages)
    if len(text) < 200 or len(text.split()) < 15:
        ocr_text = _try_ocr(cached_path, max_pages=effective_max_pages)
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
    logger.info("OCR text request finished path=%s chars=%s", pdf_path, len(text or ""))
    return text or ""


def _normalize_component(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"[^\w\-\.\s]", "", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned


def _format_filename(parts: List[str]) -> str:
    tokens = [_normalize_component(p) for p in parts if p]
    name = "_".join([t for t in tokens if t])
    if not name:
        name = "Document"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    if len(name) > MAX_FILENAME_LEN:
        root, ext = os.path.splitext(name)
        name = f"{root[: MAX_FILENAME_LEN - len(ext)]}{ext}"
    name = naming_service.enforce_no_spaces(name)
    name = re.sub(r'[<>:"/\\\\|?*]', "", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


def _dedupe_preserve(names: Iterable[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


def _format_amount(currency: str, amount: str) -> str:
    cur = (currency or "").strip().upper()
    if cur in {"$", "USD"}:
        cur = "USD"
    elif cur in {"€", "EUR"}:
        cur = "EUR"
    elif cur in {"£", "GBP"}:
        cur = "GBP"
    elif cur in {"د.إ", "AED"}:
        cur = "AED"
    if not amount:
        return cur
    return f"{cur}-{amount}" if cur else amount


def _build_invoice_suggestions(fields: invoice_field_extractor.InvoiceFields, fallback_stem: str) -> List[str]:
    number = fields.invoice_number
    date = fields.invoice_date
    vendor = fields.vendor or "Invoice"
    customer = fields.customer
    amount_token = _format_amount(fields.currency, fields.total_amount)
    suggestions: List[str] = []
    if number and date:
        suggestions.append(_format_filename([date, vendor, "Invoice", number, customer, amount_token]))
        suggestions.append(_format_filename([vendor, "Invoice", number, date, customer]))
        if amount_token:
            suggestions.append(_format_filename([vendor, "Invoice", number, date, amount_token]))
        suggestions.append(_format_filename([vendor, date, f"Invoice-{number}"]))
        suggestions.append(_format_filename([f"Invoice-{number}", date, vendor]))
    else:
        if number:
            suggestions.append(_format_filename([vendor, "Invoice", number]))
        if date:
            suggestions.append(_format_filename([date, vendor, "Invoice", number]))
        suggestions.append(_format_filename([vendor, fallback_stem]))
    return _dedupe_preserve(suggestions)


def build_ocr_suggestions(text: str, fallback_stem: str) -> List[str]:
    if not text:
        return []
    fields = invoice_field_extractor.extract_invoice_fields(text)
    logger.debug(
        "OCR invoice fields vendor=%s number=%s date=%s customer=%s amount=%s score=%.2f",
        fields.vendor,
        fields.invoice_number,
        fields.invoice_date,
        fields.customer,
        _format_amount(fields.currency, fields.total_amount),
        fields.score,
    )
    suggestions = _build_invoice_suggestions(fields, fallback_stem)
    if len(suggestions) < 5:
        base_fallbacks = [
            _format_filename([fallback_stem]),
            _format_filename([fields.vendor or "", fallback_stem]),
            _format_filename([fields.vendor or "Invoice", fields.invoice_date or "", fields.invoice_number or ""]),
        ]
        for name in base_fallbacks:
            if len(suggestions) >= 5:
                break
            if name.lower() not in [s.lower() for s in suggestions]:
                suggestions.append(name)
    suggestions = _dedupe_preserve(suggestions)
    idx = 1
    while len(suggestions) < 5:
        filler = _format_filename([fallback_stem, str(idx)])
        if filler.lower() not in [s.lower() for s in suggestions]:
            suggestions.append(filler)
        idx += 1
    return suggestions[:5]


def fingerprint_text(text: str) -> str:
    snippet = (text or "")[:300].encode("utf-8", errors="ignore")
    return hashlib.sha1(snippet).hexdigest()

# Self-check (manual): python - <<'PY'
# from docsort.app.services.ocr_suggestion_service import get_text_for_pdf
# path = r"<PDF>"
# print(len(get_text_for_pdf(path, max_pages=1)))
# print(len(get_text_for_pdf(path, max_pages=1)))  # second run should hit sqlite cache
# PY

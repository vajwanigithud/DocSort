import logging
import re
from pathlib import Path
from typing import Optional, Tuple

from pypdf import PdfReader

logger = logging.getLogger(__name__)
TRN_PATTERNS = [r"\bTRN\b", r"TAX\s*REGISTRATION", r"\bVAT\b"]


def get_pdf_page_count(path: str) -> Tuple[int, Optional[str]]:
    """Return page count; on failure return 1 and error message."""
    pdf_path = Path(path)
    try:
        with pdf_path.open("rb") as fh:
            reader = PdfReader(fh)
            count = len(reader.pages)
        logger.info("Loaded PDF page_count=%s path=%s", count, pdf_path)
        return count, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read PDF page count for %s: %s", pdf_path, exc)
        return 1, str(exc)


def _is_trn_context(text: str) -> bool:
    return any(re.search(pat, text, flags=re.IGNORECASE) for pat in TRN_PATTERNS)


def _accept_candidate_number(text: str, span: Tuple[int, int], number: str) -> bool:
    context = text[max(0, span[0] - 25) : min(len(text), span[1] + 25)]
    if _is_trn_context(context):
        logger.debug("Rejecting candidate number due to TRN/VAT context: %s", context)
        return False
    if len(number) > 8:
        logger.debug("Rejecting candidate number too long (possible TRN): %s", number)
        return False
    return True


def _has_trn_in_match(match_text: str) -> bool:
    return _is_trn_context(match_text)


def extract_pdf_text(path: str, max_pages: int = 1) -> Tuple[str, Optional[str]]:
    """Return extracted text and optional error."""
    pdf_path = Path(path)
    try:
        with pdf_path.open("rb") as fh:
            reader = PdfReader(fh)
            pages = []
            for idx, page in enumerate(reader.pages):
                if idx >= max_pages:
                    break
                try:
                    pages.append(page.extract_text() or "")
                except Exception as page_exc:  # noqa: BLE001
                    logger.warning("Text extract failed on page %s for %s: %s", idx + 1, pdf_path, page_exc)
            text = "\n".join(pages).strip()
        logger.info("PDF text extracted path=%s pages=%s", pdf_path, min(max_pages, len(reader.pages)))
        return text, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to extract text for %s: %s", pdf_path, exc)
        return "", str(exc)


def detect_doc_type_and_number(text: str) -> Tuple[Optional[str], Optional[str]]:
    doc_type, number, _ = detect_doc_fields_from_text(text)
    return doc_type, number


def detect_doc_fields_from_text(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    doc_type = None
    number = None
    date_str: Optional[str] = None

    def _normalize_date(raw: str) -> Optional[str]:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            return raw
        m = re.match(r"^(\d{2})[/-](\d{2})[/-](\d{4})$", raw)
        if m:
            d, mth, y = m.groups()
            return f"{y}-{mth}-{d}"
        m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})$", raw)
        if m:
            import calendar

            d, mon, y = m.groups()
            try:
                mon_num = list(calendar.month_abbr).index(mon[:3].title())
                return f"{y}-{mon_num:02d}-{int(d):02d}"
            except Exception:
                return None
        return None

    def _date_sane(val: str) -> bool:
        if not val or "\n" in val:
            return False
        if len(val.strip()) < 8 or len(val.strip()) > 10:
            return False
        m = re.match(r"^(\d{4})-\d{2}-\d{2}$", val)
        if not m:
            return False
        year = int(m.group(1))
        if year < 2000 or year > 2100:
            return False
        return True

    date_match = re.search(
        r"(?:DATE\s*[:\-]?\s*)?(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{2}-\d{2}-\d{4}|\d{1,2}\s+[A-Za-z]{3,}\s+\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if date_match:
        normalized = _normalize_date(date_match.group(1))
        if normalized and _date_sane(normalized):
            date_str = normalized

    candidates = []
    keyword_patterns = [
        (r"\bTAX\s+INVOICE\b[^\n\d]{0,30}\b(?:NO\.?|NUMBER|#|:)\b[^\n\d]{0,10}([A-Za-z0-9\-]{2,12})", "Invoice", "keyword"),
        (r"\bINVOICE\b[^\n\d]{0,30}\b(?:NO\.?|NUMBER|#|:)\b[^\n\d]{0,10}([A-Za-z0-9\-]{2,12})", "Invoice", "keyword"),
        (r"\bINV\s*NO\b[^\n\d]{0,10}([A-Za-z0-9\-]{2,12})", "Invoice", "keyword"),
        (r"\bESTIMATE\b[^\n\d]{0,30}\b(?:NO\.?|NUMBER|#|:)?\b[^\n\d]{0,10}([A-Za-z0-9\-]{2,12})", "Estimate", "keyword"),
        (r"\bRECEIPT\b[^\n\d]{0,30}\b(?:NO\.?|NUMBER|#|:)?\b[^\n\d]{0,10}([A-Za-z0-9\-]{2,12})", "Receipt", "keyword"),
    ]

    def _valid_candidate(num: str) -> bool:
        if _is_trn_context(num):
            return False
        if re.match(r"\+?\d{10,15}$", num):
            return False
        if re.match(r"\d{4}-\d{2}-\d{2}$", num):
            return False
        digits_only = re.sub(r"\D", "", num)
        if len(digits_only) < 2 or len(digits_only) > 8:
            return False
        return True

    for pattern, label, rule in keyword_patterns:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            num = m.group(1)
            if not _valid_candidate(num):
                logger.debug("Rejecting %s candidate (rule=%s) invalid number: %s", label, rule, num)
                continue
            span = m.span(1)
            if not _accept_candidate_number(text, span, num):
                continue
            candidates.append((label, num, rule, span[0]))

    if not candidates:
        # fallback search in top 40% of text
        cutoff = int(len(text) * 0.4)
        head = text[:cutoff] if cutoff > 0 else text
        for m in re.finditer(r"\b([A-Za-z]?\d{4,8}[A-Za-z]?)\b", head):
            num = m.group(1)
            if not _valid_candidate(num):
                continue
            candidates.append((None, num, "fallback", m.start()))

    if candidates:
        candidates.sort(key=lambda x: (0 if x[2] == "keyword" else 1, x[3]))
        chosen = candidates[0]
        doc_type = chosen[0] or doc_type
        number = chosen[1]
        logger.info("Doc detect selection rule=%s type=%s number=%s date=%s", chosen[2], doc_type, number, date_str)

    if not doc_type:
        for label in ["Invoice", "Estimate", "Receipt"]:
            if re.search(label, text, flags=re.IGNORECASE):
                doc_type = label
                break

    return doc_type, number, date_str


def detect_doc_fields_from_pdf(path: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    text, err = extract_pdf_text(path, max_pages=1)
    if not text and err:
        return None, None, None, err
    doc_type, number, date_str = detect_doc_fields_from_text(text or "")
    return doc_type, number, date_str, err


def build_suggested_filename(path: str, fallback_stem: str) -> str:
    pdf_path = Path(path)
    doc_type, doc_number, date_str, err = detect_doc_fields_from_pdf(str(pdf_path))
    rule = "basename"
    base_name = pdf_path.stem or "document"

    def _sanitize(name: str) -> str:
        name = name.replace(" ", "_")
        name = re.sub(r'[\\/:*?"<>|]', "", name)
        name = re.sub(r"_+", "_", name)
        return name.strip("_")

    if doc_type and doc_number:
        rule = "type+number+date" if date_str else "type+number"
        parts = [doc_type, doc_number]
        if date_str:
            parts.append(date_str)
        base_name = "_".join(parts)
    elif doc_type and date_str:
        rule = "type+date"
        base_name = f"{doc_type}_{date_str}"
    elif doc_number:
        rule = "number"
        base_name = str(doc_number)

    base_name = _sanitize(base_name)
    filename = f"{base_name}.pdf"
    logger.info("PDF suggestion rule=%s type=%s number=%s date=%s result=%s", rule, doc_type, doc_number, date_str, filename)
    return filename


def _self_test() -> None:
    samples = [
        "TAX INVOICE # 12345\nTotal due",
        "Estimate No. 777 for project X",
        "Receipt #9988 Thank you",
        "Plain text without numbers",
        "TAX INVOICE\nINVOICE NO: 5329\nTRN 100011199500003",
    ]
    for text in samples:
        doc_type, num, date = detect_doc_fields_from_text(text)
        print(f"{text!r} -> {doc_type}, {num}, {date}")


if __name__ == "__main__":
    _self_test()

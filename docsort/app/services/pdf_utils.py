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
    patterns = [
        (
            r"\bTAX\s+INVOICE\b[^\d\n]{0,30}\b(?:NO\.?|NUMBER|#|:)\b[^\d\n]{0,10}(\d{2,8})\b",
            "TAX INVOICE",
        ),
        (
            r"\bINVOICE\b[^\d\n]{0,30}\b(?:NO\.?|NUMBER|#|:)\b[^\d\n]{0,10}(\d{2,8})\b",
            "INVOICE",
        ),
        (
            r"\bESTIMATE\b[^\d\n]{0,30}\b(?:NO\.?|NUMBER|#|:)?\b[^\d\n]{0,10}(\d{2,8})\b",
            "ESTIMATE",
        ),
        (
            r"\bRECEIPT\b[^\d\n]{0,30}\b(?:NO\.?|NUMBER|#|:)?\b[^\d\n]{0,10}(\d{2,8})\b",
            "RECEIPT",
        ),
    ]
    for pattern, label in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            span = m.span()
            number = m.group(1)
            match_text = m.group(0)
            if _has_trn_in_match(match_text):
                logger.debug("Rejecting %s candidate due to TRN/VAT in match: %s", label, match_text)
                continue
            if not _accept_candidate_number(text, span, number):
                continue
            return label, number
    return None, None


def detect_doc_fields_from_text(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    doc_type = None
    number = None
    date_str: Optional[str] = None
    date_match = re.search(r"DATE\s*[:\-]?\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})", text, flags=re.IGNORECASE)
    if date_match:
        raw_date = date_match.group(1)
        if "-" in raw_date:
            date_str = raw_date
        else:
            # dd/mm/yyyy -> yyyy-mm-dd
            try:
                parts = raw_date.split("/")
                date_str = f"{parts[2]}-{parts[1]}-{parts[0]}"
            except Exception:
                date_str = raw_date
    patterns = [
        (
            r"\bTAX\s+INVOICE\b[^\d\n]{0,30}\b(?:NO\.?|NUMBER|#|:)\b[^\d\n]{0,10}(\d{2,8})\b",
            "Invoice",
        ),
        (
            r"\bINVOICE\b[^\d\n]{0,30}\b(?:NO\.?|NUMBER|#|:)\b[^\d\n]{0,10}(\d{2,8})\b",
            "Invoice",
        ),
        (
            r"\bESTIMATE\b[^\d\n]{0,30}\b(?:NO\.?|NUMBER|#|:)?\b[^\d\n]{0,10}(\d{2,8})\b",
            "Estimate",
        ),
        (
            r"\bRECEIPT\b[^\d\n]{0,30}\b(?:NO\.?|NUMBER|#|:)?\b[^\d\n]{0,10}(\d{2,8})\b",
            "Receipt",
        ),
    ]
    for pattern, label in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            span = m.span()
            context = text[max(0, span[0] - 16) : min(len(text), span[1] + 16)]
            if _is_trn_context(context):
                logger.debug("Rejecting %s candidate due to TRN context: %s", label, context)
                continue
            candidate = m.group(1)
            if len(candidate) > 8:
                logger.debug("Rejecting %s candidate too long: %s", label, candidate)
                continue
            if _has_trn_in_match(m.group(0)):
                logger.debug("Rejecting %s candidate due to TRN in match: %s", label, m.group(0))
                continue
            doc_type = label
            number = candidate
            break
    if doc_type and not number:
        close_num = re.search(rf"{doc_type}\s*(?:NO\.?|#|NUMBER)?\s*(\d{{2,}})", text, flags=re.IGNORECASE)
        if close_num:
            span = close_num.span()
            context = text[max(0, span[0] - 16) : min(len(text), span[1] + 16)]
            candidate = close_num.group(1)
            if not _is_trn_context(context) and len(candidate) <= 8:
                number = candidate
            else:
                logger.debug("Rejecting nearby number due to context/length: %s", context)
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
    fallback = fallback_stem or pdf_path.stem or "document"
    doc_type, doc_number, date_str, err = detect_doc_fields_from_pdf(str(pdf_path))
    if doc_type and doc_number:
        safe_type = re.sub(r"\s+", "_", doc_type.title()).strip("_")
        filename = f"{safe_type}_{doc_number}.pdf"
        logger.info("PDF suggestion matched %s: %s %s", pdf_path, doc_type, doc_number)
        return filename
    if doc_type and doc_type.lower() in {"invoice", "estimate"} and date_str:
        safe_type = doc_type.title()
        filename = f"{safe_type}_{date_str}.pdf"
        logger.info("PDF suggestion using date fallback %s: %s %s", pdf_path, doc_type, date_str)
        return filename
    if err:
        logger.warning("PDF suggestion fallback for %s due to: %s", pdf_path, err)
    if not fallback.lower().endswith(".pdf"):
        fallback = f"{fallback}.pdf"
    return fallback


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

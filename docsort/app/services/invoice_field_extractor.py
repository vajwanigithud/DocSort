import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class InvoiceFields:
    vendor: str
    invoice_number: str
    invoice_date: str
    currency: str
    total_amount: str
    customer: str
    score: float


_VENDOR_SKIP = {"tax invoice", "invoice", "receipt", "invoice summary"}
_CURRENCY_PATTERN = r"(USD|EUR|GBP|AED|SAR|QAR|KWD|OMR|USD|CAD|AUD|INR|PKR|NPR|BHD|CHF|JPY|CNY|USD|\$|€|£)"


def _sanitize_token(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', " ", text or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned[:80]


def _extract_lines(text: str) -> List[str]:
    raw_lines = (text or "").replace("\u00a0", " ").splitlines()
    return [ln.strip() for ln in raw_lines if ln.strip()]


def _parse_date(raw: str) -> str:
    raw = raw.strip()
    # Try YYYY-MM-DD directly
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d"]:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    # Normalize dd/mm/yyyy variations
    if re.match(r"\d{2}[/-]\d{2}[/-]\d{4}", raw):
        parts = re.split(r"[/-]", raw)
        try:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except Exception:
            return raw
    return raw


def _extract_vendor(lines: List[str]) -> str:
    candidates = lines[:10]
    best = ""
    best_score = -1.0
    for line in candidates:
        lower = line.lower()
        if any(skip in lower for skip in _VENDOR_SKIP):
            continue
        cleaned = re.sub(r"[^\w\s\-\&\.\,]", "", line).strip()
        if not cleaned:
            continue
        letters = sum(1 for c in cleaned if c.isalpha())
        uppers = sum(1 for c in cleaned if c.isupper())
        upper_ratio = uppers / letters if letters else 0
        token_count = len(cleaned.split())
        score = len(cleaned) + (upper_ratio * 10) + (token_count * 2)
        if score > best_score:
            best_score = score
            best = cleaned
    return _sanitize_token(best)


def _extract_invoice_number(text: str) -> Tuple[str, float]:
    patterns = [
        r"(?:invoice\s*(?:no\.?|number|#)?|inv\.?|inv\s*no\.?|invoice\s*#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/]{2,30})",
        r"(?:bill\s*no\.?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/]{2,30})",
        r"\b([A-Z]{2,5}[-/ ]?\d{3,})\b",
    ]
    best = ""
    best_score = -1.0
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            candidate = m.group(1).strip()
            if not candidate or re.fullmatch(r"0+", candidate):
                continue
            score = len(candidate)
            if "invoice" in m.group(0).lower() or "inv" in m.group(0).lower():
                score += 10
            if score > best_score:
                best_score = score
                best = candidate
    return _sanitize_token(best), max(best_score, 0)


def _extract_date(text: str) -> Tuple[str, float]:
    date_patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{2}[/-]\d{2}[/-]\d{4}\b",
        r"\b\d{2}\.\d{2}\.\d{4}\b",
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b",
    ]
    for pat in date_patterns:
        m = re.search(pat, text)
        if m:
            raw = m.group(0)
            return _parse_date(raw), 1.0
    return "", 0.0


def _extract_amount(text: str) -> Tuple[str, str, float]:
    # Look for "Total", "Amount Due", etc., consider the last occurrence as likely final total
    amount_patterns = [
        r"(grand\s*total|total\s*amount|amount\s*due|balance\s*due|total)\s*[:\-]?\s*(" + _CURRENCY_PATTERN + r")?\s*([\$€£]?\s*[0-9][0-9\.,]*)",
    ]
    best_currency = ""
    best_amount = ""
    for pat in amount_patterns:
        matches = list(re.finditer(pat, text, flags=re.IGNORECASE))
        if not matches:
            continue
        # choose last match
        m = matches[-1]
        cur = m.group(2) or ""
        amt = m.group(3) or ""
        amt = amt.replace(",", "").replace(" ", "")
        amt = re.sub(r"[^\d\.]", "", amt)
        best_currency = cur.replace(" ", "").upper().replace("$", "USD").replace("€", "EUR").replace("£", "GBP")
        best_amount = amt
        break
    if best_amount:
        return best_amount, best_currency, 1.0
    return "", "", 0.0


def _extract_customer(text: str, lines: List[str]) -> str:
    markers = ["bill to", "customer", "client", "sold to", "ship to"]
    lowered = text.lower()
    for marker in markers:
        idx = lowered.find(marker)
        if idx != -1:
            snippet = text[idx : idx + 120]
            parts = snippet.splitlines()
            if parts:
                candidate = parts[0]
                if len(parts) > 1:
                    candidate = parts[1]
                return _sanitize_token(candidate)
    for line in lines:
        lower = line.lower()
        if any(marker in lower for marker in markers):
            return _sanitize_token(line)
    return ""


def extract_invoice_fields(text: str) -> InvoiceFields:
    lines = _extract_lines(text)
    flat = " ".join(lines)
    vendor = _extract_vendor(lines)
    number, num_score = _extract_invoice_number(text)
    date_val, date_score = _extract_date(text)
    amount, currency, amt_score = _extract_amount(text)
    customer = _extract_customer(text, lines)

    score = 0.0
    if number:
        score += 30
    if date_val:
        score += 30
    if vendor:
        score += 20
    if amount:
        score += 10
    if customer:
        score += 5
    score += num_score + date_score + amt_score

    fields = InvoiceFields(
        vendor=vendor,
        invoice_number=_sanitize_token(number),
        invoice_date=date_val,
        currency=_sanitize_token(currency),
        total_amount=_sanitize_token(amount),
        customer=customer,
        score=score,
    )
    logger.debug("Extracted invoice fields: %s", fields)
    return fields


def self_test() -> None:
    samples = [
        """
        RANGEELO GENERAL TRADING LLC
        TAX INVOICE
        Invoice No: INV-5406
        Date: 24/11/2025
        Bill To: Hollywood Departmental Store
        Grand Total AED 371.70
        """,
        """
        Super Supplies Pty Ltd
        Invoice # 2024/00123
        Invoice Date 01 Jan 2025
        Amount Due USD 1,240.50
        Client: Delta Trading
        """,
        """
        ACME Corp
        Receipt
        Receipt No 88991-AX
        Date: 2025-02-05
        Total: $88.00
        Customer John Smith
        """,
    ]
    for idx, sample in enumerate(samples, start=1):
        fields = extract_invoice_fields(sample)
        print(f"Sample {idx}: {fields}")


if __name__ == "__main__":
    self_test()

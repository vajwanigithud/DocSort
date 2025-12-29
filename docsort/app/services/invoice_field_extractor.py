from __future__ import annotations

import dataclasses
import re
from datetime import datetime
from typing import Iterable, List, Optional, Tuple


@dataclasses.dataclass
class InvoiceFields:
    vendor: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    currency: str = ""
    total_amount: str = ""
    customer: str = ""
    score: float = 0.0


DATE_PATTERNS = [
    r"\b(\d{4})[/-](\d{2})[/-](\d{2})\b",
    r"\b(\d{2})[/-](\d{2})[/-](\d{4})\b",
    r"\b(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})\b",
]

MONTH_MAP = {m.lower(): idx for idx, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}

AMOUNT_LABELS = ["total", "grand total", "amount due", "balance due", "total due", "invoice total"]
NUMBER_LABELS = ["invoice", "inv", "inv.", "inv no", "invoice no", "invoice number", "number", "no"]


def _normalize_space(val: str) -> str:
    return re.sub(r"\s+", " ", val or "").strip()


def _clean_token(val: str) -> str:
    cleaned = re.sub(r"[^\w\-\.\s/]", "", val or "")
    cleaned = re.sub(r"_+", "_", cleaned)
    return _normalize_space(cleaned)


def _parse_date(raw: str) -> Optional[str]:
    raw = raw.strip()
    for pat in DATE_PATTERNS:
        m = re.search(pat, raw, flags=re.IGNORECASE)
        if not m:
            continue
        try:
            if len(m.groups()) == 3:
                g1, g2, g3 = m.groups()
                if pat.startswith(r"\b(\d{4})"):
                    dt = datetime(int(g1), int(g2), int(g3))
                elif pat.startswith(r"\b(\d{2})[/-](\d{2})[/-](\d{4})"):
                    dt = datetime(int(g3), int(g2), int(g1))
                else:
                    mon = MONTH_MAP.get(g2[:3].title().lower())
                    if not mon:
                        continue
                    dt = datetime(int(g3), mon, int(g1))
                return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    return None


def _score_vendor_line(line: str, idx: int) -> float:
    cleaned = _clean_token(line)
    if not cleaned or len(cleaned) < 2:
        return -1
    letters = sum(1 for c in cleaned if c.isalpha())
    upper = sum(1 for c in cleaned if c.isupper())
    upper_ratio = upper / letters if letters else 0
    has_suffix = any(sfx in cleaned.lower() for sfx in ["llc", "ltd", "fze", "inc", "pty", "plc", "trading"])
    words = cleaned.split()
    score = len(words) * 2 + upper_ratio * 6
    if has_suffix:
        score += 4
    if len(words) == 1:
        score -= 1
    if idx == 0:
        score += 2
    return score


def _extract_vendor(lines: List[str]) -> str:
    header_lines = lines[:10]
    best = ""
    best_score = -1.0
    skip_tokens = {"invoice", "tax invoice", "receipt", "statement", "quotation", "estimate"}
    for idx, raw in enumerate(header_lines):
        cleaned = _clean_token(raw)
        if not cleaned:
            continue
        lower = cleaned.lower()
        if any(tok in lower for tok in skip_tokens):
            continue
        score = _score_vendor_line(cleaned, idx)
        if score > best_score:
            best_score = score
            best = cleaned
    return best[:50]


def _extract_invoice_number(text: str) -> Tuple[str, float]:
    candidates: List[Tuple[str, float]] = []
    for m in re.finditer(r"(?:invoice|inv)[\s:\-#]{0,4}([A-Z0-9][A-Z0-9\/\-\._]{2,20})", text, flags=re.IGNORECASE):
        val = m.group(1)
        score = 3.0
        if re.search(r"[A-Za-z]", val):
            score += 1.0
        candidates.append((val, score))
    for m in re.finditer(r"\b([A-Z]?\d{4,10}[A-Z]?)\b", text):
        val = m.group(1)
        if re.fullmatch(r"\d{6,}", val) and len(val) > 8:
            continue
        if re.fullmatch(r"\d{4}", val):
            continue
        candidates.append((val, 1.0))
    if not candidates:
        return "", 0.0
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


def _extract_date(text: str) -> Tuple[str, float]:
    for m in re.finditer(r"(?:invoice\s+date|date|dated)[:\-\s]*([^\n]{4,20})", text, flags=re.IGNORECASE):
        parsed = _parse_date(m.group(1))
        if parsed:
            return parsed, 2.5
    for pat in DATE_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            if len(m.groups()) == 3:
                try:
                    parsed = _parse_date(" ".join(m.groups()))
                except Exception:
                    parsed = None
                if parsed:
                    return parsed, 2.0
    return "", 0.0


def _extract_amount(text: str) -> Tuple[str, str, float]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    best_amt = ""
    best_cur = ""
    best_score = 0.0
    currency_pat = r"(USD|EUR|GBP|AED|SAR|QAR|OMR|KWD|\$|€|£|د\.إ)"
    amount_pat = r"([0-9]{1,3}(?:[, ]\d{3})*(?:\.\d{2})?)"
    for ln in lines:
        lower = ln.lower()
        if not any(lbl in lower for lbl in AMOUNT_LABELS):
            continue
        m = re.search(currency_pat + r"\s*" + amount_pat, ln, flags=re.IGNORECASE)
        cur = ""
        amt = ""
        if m:
            cur = m.group(1)
            amt = m.group(2)
        else:
            m = re.search(amount_pat, ln)
            amt = m.group(1) if m else ""
        if not amt:
            continue
        amt_clean = re.sub(r"[^\d\.]", "", amt)
        try:
            float(amt_clean)
        except Exception:
            continue
        score = 2.0
        if cur:
            score += 1.0
        if score > best_score:
            best_score = score
            best_amt = amt_clean
            best_cur = cur
    return best_amt, best_cur, best_score


def _extract_customer(text: str) -> str:
    for label in ["bill to", "billed to", "customer", "client", "sold to", "ship to"]:
        pattern = rf"{label}[:\-\s]*([^\n]{{3,60}})"
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            candidate = _clean_token(m.group(1))
            if candidate:
                return candidate[:50]
    return ""


def extract_invoice_fields(text: str) -> InvoiceFields:
    raw = (text or "").replace("\u00a0", " ")
    text_lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    text_flat = _normalize_space(raw)
    vendor = _extract_vendor(text_lines)
    invoice_number, num_score = _extract_invoice_number(text_flat)
    invoice_date, date_score = _extract_date(raw)
    amount, currency, amt_score = _extract_amount(raw)
    customer = _extract_customer(raw)
    base_score = (3.0 if invoice_number else 0.0) + (3.0 if invoice_date else 0.0) + (2.0 if vendor else 0.0)
    base_score += 0.5 if customer else 0.0
    base_score += 0.5 if amount else 0.0
    total_score = base_score + num_score + date_score + amt_score

    fields = InvoiceFields(
        vendor=vendor,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        currency=currency,
        total_amount=amount,
        customer=customer,
        score=total_score,
    )
    return fields


def _sample_texts() -> List[Tuple[str, str]]:
    return [
        (
            "ACME TRADING LLC\nTax Invoice\nInvoice No: INV-10234\nDate: 12/01/2025\nBill To: Mega Corp\nTotal AED 12,345.67",
            "basic",
        ),
        (
            "INVOICE\nFOUR SEASONS SUPPLY FZE\nInvoice Number 2024/00123\nInvoice Date 2024-11-05\nCustomer: Blue Water\nGrand Total USD 3,250.00",
            "slash-number",
        ),
        (
            "Receipt\nVendor: Bright Co Pty Ltd\nInv no. 8891\n01 Jan 2025\nAmount Due $250.00\nBill To Client Name",
            "receipt-style",
        ),
    ]


def self_test() -> List[Tuple[str, InvoiceFields]]:
    results: List[Tuple[str, InvoiceFields]] = []
    for text, name in _sample_texts():
        fields = extract_invoice_fields(text)
        results.append((name, fields))
    return results


if __name__ == "__main__":
    for label, fields in self_test():
        print(f"[{label}] -> {dataclasses.asdict(fields)}")

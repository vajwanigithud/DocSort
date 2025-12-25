import re
from datetime import date, datetime
from typing import Union


def sanitize_token(s: str) -> str:
    cleaned = (s or "").strip()
    cleaned = cleaned.replace(" ", "_")
    # Remove illegal filename characters.
    cleaned = re.sub(r'[\\\\/:*?"<>|]', "", cleaned)
    return cleaned


def enforce_no_spaces(filename: str) -> str:
    return (filename or "").replace(" ", "_")


def format_date_ddmmyyyy(dt: Union[date, datetime, None]) -> str:
    if not dt:
        return "00-00-0000"
    if isinstance(dt, datetime):
        dt = dt.date()
    return dt.strftime("%d-%m-%Y")


def build_option_a(vendor: str, doctype: str, number: str, date_str: str) -> str:
    vendor_token = sanitize_token(vendor)
    doctype_token = sanitize_token(doctype)
    number_token = sanitize_token(number)
    date_token = sanitize_token(date_str or "00-00-0000")
    base = f"{vendor_token}_{doctype_token}_{number_token}_{date_token}"
    base = enforce_no_spaces(base)
    return f"{base}.pdf"

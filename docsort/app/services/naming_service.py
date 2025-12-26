import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

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
        return ""
    if isinstance(dt, datetime):
        dt = dt.date()
    return dt.strftime("%d-%m-%Y")


def build_option_a(
    vendor: str,
    doctype: str,
    number: str,
    date_str: str,
    source_basename: str = "",
) -> str:
    """
    Build a filename without placeholders.
    Precedence:
      1) vendor+doctype+number(+date)
      2) vendor+doctype+date
      3) vendor+doctype+number
      4) vendor+number
      5) vendor+basename (fallback)
    """

    def _clean_token(val: str) -> str:
        token = sanitize_token(val)
        token = re.sub(r"_+", "_", token).strip("_")
        return token

    vendor_token = _clean_token(vendor)
    doctype_token = _clean_token(doctype)
    number_token = _clean_token(number)
    date_token = _clean_token(date_str)
    basename = _clean_token(Path(source_basename).stem if source_basename else "")

    if doctype_token.lower() == "type":
        doctype_token = ""
    if re.fullmatch(r"0+", number_token) or number_token == "000":
        number_token = ""

    def _prefix(parts):
        if vendor_token:
            return [vendor_token, *[p for p in parts if p]]
        return [p for p in parts if p]

    rule = "fallback"
    tokens = []
    if doctype_token and number_token:
        tokens = _prefix([doctype_token, number_token])
        if date_token:
            tokens.append(date_token)
        rule = "type+number+date" if date_token else "type+number"
    elif doctype_token and date_token:
        tokens = _prefix([doctype_token, date_token])
        rule = "type+date"
    elif doctype_token and number_token:
        tokens = _prefix([doctype_token, number_token])
        rule = "type+number"
    elif number_token:
        tokens = _prefix([number_token])
        rule = "number"
    else:
        base = basename or vendor_token or "document"
        if vendor_token and base != vendor_token:
            tokens = _prefix([base])
        else:
            tokens = [base]
        rule = "basename"

    name_base = "_".join(tokens)
    name_base = re.sub(r"_+", "_", name_base).strip("_")
    filename = f"{name_base}.pdf"
    logger.info("Naming rule=%s vendor=%s type=%s number=%s date=%s base=%s", rule, vendor_token, doctype_token, number_token, date_token, filename)
    return filename

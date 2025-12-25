import io
import logging
from pathlib import Path
from typing import Generator, Tuple

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

logger = logging.getLogger(__name__)


def _make_placeholder(page_num: int, size: Tuple[int, int]) -> bytes:
    img = Image.new("RGB", size, color="#f2f2f2")
    draw = ImageDraw.Draw(img)
    text = f"Page {page_num}"
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None
    text_size = draw.textbbox((0, 0), text, font=font)
    w = text_size[2] - text_size[0]
    h = text_size[3] - text_size[1]
    draw.text(((size[0] - w) / 2, (size[1] - h) / 2), text, fill="#333", font=font)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def iter_pdf_thumbnails(path: str, thumb_size=(180, 220), full_size=(500, 650)) -> Generator[Tuple[int, bytes, bytes], None, None]:
    pdf_path = Path(path)
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        raise FileNotFoundError(f"PDF not found or invalid: {path}")
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    for idx in range(total_pages):
        page_num = idx + 1
        # Placeholder rendering due to lack of rasterizer; still creates distinct page previews.
        thumb_bytes = _make_placeholder(page_num, thumb_size)
        full_bytes = _make_placeholder(page_num, full_size)
        yield page_num, thumb_bytes, full_bytes

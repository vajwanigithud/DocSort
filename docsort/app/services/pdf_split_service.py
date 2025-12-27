from pathlib import Path
from typing import List, Tuple

from pypdf import PdfReader, PdfWriter


def split_pdf_to_ranges(source_pdf_path: str, out_dir: str, ranges: List[Tuple[int, int]]) -> List[str]:
    src_path = Path(source_pdf_path)
    if not src_path.exists():
        raise FileNotFoundError(f"Source PDF not found: {source_pdf_path}")
    if src_path.suffix.lower() != ".pdf":
        raise ValueError("Source must be a PDF file.")

    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    outputs: List[str] = []
    stem = src_path.stem
    with src_path.open("rb") as src_fh:
        reader = PdfReader(src_fh)
        total_pages = len(reader.pages)

        for start, end in ranges:
            if start < 1 or end < start or end > total_pages:
                raise ValueError(f"Invalid range {start}-{end} for total pages {total_pages}")
            writer = PdfWriter()
            for idx in range(start - 1, end):
                writer.add_page(reader.pages[idx])
            out_path = out_dir_path / f"{stem}_p{start}-{end}.pdf"
            with out_path.open("wb") as fh:
                writer.write(fh)
            outputs.append(str(out_path.resolve()))
    try:
        reader.close()  # type: ignore[attr-defined]
    except Exception:
        pass
    return outputs

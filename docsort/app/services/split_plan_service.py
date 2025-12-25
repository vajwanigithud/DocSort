from typing import List, Tuple


def build_fixed_batches(total_pages: int, batch_size: int) -> List[Tuple[int, int]]:
    if total_pages < 1 or batch_size < 1:
        raise ValueError("Total pages and batch size must be positive.")
    groups = []
    start = 1
    while start <= total_pages:
        end = min(start + batch_size - 1, total_pages)
        groups.append((start, end))
        start = end + 1
    return groups


def build_from_pattern(total_pages: int, pattern: List[int]) -> List[Tuple[int, int]]:
    if total_pages < 1:
        raise ValueError("Total pages must be positive.")
    if not pattern:
        raise ValueError("Pattern cannot be empty.")
    if any(p <= 0 for p in pattern):
        raise ValueError("Pattern values must be positive.")
    groups = []
    start = 1
    for count in pattern:
        end = start + count - 1
        if end > total_pages:
            raise ValueError("Pattern exceeds total pages.")
        groups.append((start, end))
        start = end + 1
    if start <= total_pages:
        raise ValueError("Pattern does not cover all pages.")
    return groups


def build_from_ranges(ranges_text: str, total_pages: int) -> List[Tuple[int, int]]:
    if total_pages < 1:
        raise ValueError("Total pages must be positive.")
    if not ranges_text.strip():
        raise ValueError("Ranges cannot be empty.")
    parts = [p.strip() for p in ranges_text.split(",") if p.strip()]
    groups: List[Tuple[int, int]] = []
    for part in parts:
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(part)
        if start < 1 or end < start or end > total_pages:
            raise ValueError(f"Invalid range {start}-{end}")
        groups.append((start, end))
    ok, error = validate_groups(total_pages, groups)
    if not ok:
        raise ValueError(error)
    return groups


def make_singletons(total_pages: int) -> List[Tuple[int, int]]:
    if total_pages < 1:
        return []
    return [(i, i) for i in range(1, total_pages + 1)]


def validate_groups(total_pages: int, groups: List[Tuple[int, int]]) -> Tuple[bool, str]:
    if any(start < 1 or end < start or end > total_pages for start, end in groups):
        return False, "Groups out of bounds or invalid."
    sorted_groups = sorted(groups, key=lambda g: g[0])
    for idx in range(1, len(sorted_groups)):
        prev = sorted_groups[idx - 1]
        cur = sorted_groups[idx]
        if cur[0] <= prev[1]:
            return False, "Groups overlap or are not strictly ascending."
    return True, ""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def trace_from_page(page, query_terms: List[str], page_number: int, parser: str) -> Dict[str, Any]:
    text = page.extract_text() or ""
    snippet = ""
    for ln in text.splitlines():
        if any(t in ln for t in query_terms if t):
            snippet = ln.strip()
            break
    bbox = None
    try:
        words = page.extract_words(keep_blank_chars=False, use_text_flow=True) or []
        for w in words:
            if any(t in (w.get("text") or "") for t in query_terms if t):
                bbox = (float(w.get("x0")), float(w.get("top")), float(w.get("x1")), float(w.get("bottom")))
                break
    except Exception:
        bbox = None

    return {"page": page_number, "bbox": bbox, "text": snippet, "parser": parser}


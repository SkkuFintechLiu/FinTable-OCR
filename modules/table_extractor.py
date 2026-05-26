from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from modules.utils import normalize_compact


@dataclass
class ExtractedTable:
    table: List[List[str]]
    bbox: Optional[Tuple[float, float, float, float]]
    parser: str
    score: float
    notes: str = ""


def _safe_table(table: Any) -> Optional[List[List[str]]]:
    if not table:
        return None
    if not isinstance(table, list):
        return None
    out: List[List[str]] = []
    for r in table:
        if r is None:
            continue
        if not isinstance(r, list):
            continue
        out.append([(c if c is not None else "") for c in r])
    return out if out else None


def _table_text(table: List[List[str]], max_rows: int = 6) -> str:
    return normalize_compact("\n".join([" ".join([c or "" for c in r]) for r in table[:max_rows]]))


def _score_table(table: List[List[str]]) -> float:
    txt = _table_text(table)
    score = 0.0
    if "年末" in txt or "年度" in txt or "截至" in txt:
        score += 2.0
    if "金额" in txt or "合计" in txt:
        score += 1.2
    if "银行" in txt or "债券" in txt or "有息" in txt:
        score += 0.8
    row0 = _table_text([table[0]]) if table else ""
    if "项目" in row0 or "科目" in row0 or "类别" in row0:
        score += 0.6
    if len(table) >= 4:
        score += 0.2
    if table and len(table[0]) >= 4:
        score += 0.2
    return score


def extract_candidate_tables(page) -> List[ExtractedTable]:
    candidates: List[ExtractedTable] = []

    lattice_settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "intersection_tolerance": 5,
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "edge_min_length": 3,
        "min_words_vertical": 2,
        "min_words_horizontal": 2,
        "keep_blank_chars": False,
        "text_tolerance": 3,
    }

    stream_settings = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "edge_min_length": 3,
        "min_words_vertical": 1,
        "min_words_horizontal": 1,
        "keep_blank_chars": False,
        "text_tolerance": 3,
    }

    for parser, settings in [("lattice", lattice_settings), ("stream", stream_settings)]:
        try:
            tables = page.extract_tables(settings) or []
        except Exception:
            tables = []
        for t in tables:
            st = _safe_table(t)
            if not st:
                continue
            candidates.append(ExtractedTable(table=st, bbox=None, parser=parser, score=_score_table(st)))

    try:
        found = page.find_tables(lattice_settings)
        for ft in found or []:
            try:
                tb = ft.extract()
            except Exception:
                tb = None
            st = _safe_table(tb)
            if not st:
                continue
            candidates.append(ExtractedTable(table=st, bbox=getattr(ft, "bbox", None), parser="lattice_bbox", score=_score_table(st) + 0.4))
    except Exception:
        pass

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates


def pick_best_table(candidates: List[ExtractedTable], must_include: Optional[List[str]] = None) -> Optional[ExtractedTable]:
    if not candidates:
        return None
    if not must_include:
        return candidates[0]

    must = [normalize_compact(x) for x in must_include if x]
    best: Optional[ExtractedTable] = None
    best_score = -1.0
    for c in candidates:
        txt = _table_text(c.table, max_rows=10)
        ok = True
        for m in must:
            if m and m not in txt:
                ok = False
                break
        if not ok:
            continue
        if c.score > best_score:
            best = c
            best_score = c.score
    return best or candidates[0]


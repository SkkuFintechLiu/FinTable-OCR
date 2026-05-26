from __future__ import annotations

from typing import Any, Dict, Tuple


def base_confidence(parser: str) -> int:
    if parser in {"lattice_bbox", "lattice"}:
        return 96
    if parser in {"stream"}:
        return 90
    if parser in {"pdfplumber_table"}:
        return 92
    if parser in {"text_table"}:
        return 88
    if parser in {"ocr"}:
        return 70
    if parser in {"llm"}:
        return 60
    return 80


def score_cell(flag: str, parser: str) -> int:
    score = base_confidence(parser)
    if flag == "ok":
        return score
    if flag == "missing":
        return max(30, score - 50)
    if flag == "invalid":
        return max(40, score - 45)
    if flag == "unit_unknown":
        return max(50, score - 35)
    return max(40, score - 40)


def score_task(cells: Dict[str, Dict[str, Any]], parser: str) -> Tuple[int, str]:
    flags = []
    for k, v in (cells or {}).items():
        if k == "__trace__":
            continue
        if isinstance(v, dict) and "flag" in v:
            flags.append(v.get("flag") or "missing")
    if not flags:
        return 0, "无可评估字段"

    scores = [score_cell(f, parser) for f in flags]
    score = int(round(sum(scores) / max(1, len(scores))))
    reason = "字段均可提取"
    if any(f in {"invalid", "unit_unknown"} for f in flags):
        reason = "存在异常字段（单位未知或文本不可解析）"
    if any(f == "missing" for f in flags):
        reason = "存在缺失字段"
    return score, reason


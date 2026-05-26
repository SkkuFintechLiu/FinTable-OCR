import re
from typing import Any, Optional, Tuple


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def normalize_compact(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def is_annual_header(header: str) -> Optional[int]:
    h = normalize_compact(header or "")
    m = re.search(r"(20\d{2})年末", h)
    if m:
        return int(m.group(1))
    m = re.search(r"(20\d{2})年度", h)
    if m:
        return int(m.group(1))
    m = re.search(r"截至(20\d{2})年12月31日", h)
    if m:
        return int(m.group(1))
    m = re.search(r"(20\d{2})年12月末", h)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(20\d{2})年", h)
    if m:
        return int(m.group(1))
    if re.search(r"20\d{2}年\d{1,2}月末", h):
        return None
    if re.search(r"20\d{2}年\d{1,2}-\d{1,2}月", h):
        return None
    if re.search(r"20\d{2}Q[1-4]", h, re.IGNORECASE):
        return None
    if "最近一期" in h or "季度" in h or "半年度" in h:
        return None
    return None


def detect_unit_multiplier_to_yi(text: str) -> Tuple[float, str]:
    t = normalize_compact(text)
    m = re.search(r"单位[:：](亿元|万元|元)", t)
    if not m:
        return 1.0, ""
    u = m.group(1)
    if u == "亿元":
        return 1.0, "亿元"
    if u == "万元":
        return 1.0 / 10000.0, "万元"
    if u == "元":
        return 1.0 / 100000000.0, "元"
    return 1.0, ""


def parse_number(value: Any) -> Tuple[Optional[float], Optional[str]]:
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    s = str(value).strip()
    if not s or s in {"-", "—", "–"}:
        return None, None
    s2 = s.replace(",", "")
    s2 = re.sub(r"[^\d\.\-\+]", "", s2)
    if not s2 or s2 in {"-", "+", "."}:
        return None, s
    try:
        return float(s2), None
    except Exception:
        return None, s


def safe_year_from_text(text: str) -> Optional[int]:
    m = re.search(r"(20\d{2})", text or "")
    if not m:
        return None
    return int(m.group(1))

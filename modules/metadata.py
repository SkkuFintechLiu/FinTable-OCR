import os
import re
from typing import Optional


_COMPANY_PATTERNS = [
    re.compile(r"[\u4e00-\u9fa5（）()]{4,60}(?:有限责任公司|股份有限公司|集团有限公司|有限公司)"),
    re.compile(r"[\u4e00-\u9fa5（）()]{4,60}公司"),
]

_DOC_PROSPECTUS_HINTS = ["募集说明书", "中期票据募集说明书", "公司债券募集说明书", "非公开发行公司债券", "面向专业投资者"]
_DOC_ANNUAL_HINTS = ["公司债券年度报告", "年度报告"]

_GLOBAL_EXCLUDE = [
    "证券股份有限公司",
    "银行股份有限公司",
    "律师事务所",
    "会计师事务所",
    "信用增进",
    "担保有限公司",
    "子公司",
    "管理委员会",
    "人民政府",
    "交易所",
    "登记结算",
]

_PROSPECTUS_PRIMARY_FIELDS = ["发行人全称：", "发行人全称"]
_PROSPECTUS_STOP_KEYWORDS = ["募集说明书", "公司债券", "中期票据", "非公开发行"]
_PROSPECTUS_EXCLUDE_KEYWORDS = ["证券", "律师", "会计师", "银行", "信用增进", "评级", "担保", "承销商", "事务所"]

_GLOSSARY_HINTS = ["公司、发行人", "公司、本公司、发行人", "发行人、公司"]


def _cleanup(s: str) -> str:
    s = re.sub(r"\s+", "", s or "")
    s = re.sub(r"[·•]", "", s)
    return s


def normalize_issuer_name(name: str) -> str:
    v = _cleanup(name or "")
    if not v:
        return ""
    if v.startswith("未知发行人_"):
        return v

    v = v.replace("(", "（").replace(")", "）")
    v = re.sub(r"[，,;；:：_]+$", "", v)
    v = re.sub(r"^(公司债券年度报告|债券年度报告|年度报告)", "", v)

    while True:
        v2 = re.sub(r"(公司债券年度报告|债券年度报告|公司债券中期报告|债券中期报告|中期报告|半年度报告|年度报告|募集说明书|募集说明)$", "", v)
        if v2 == v:
            break
        v = v2

    while True:
        m = re.search(r"（([^）]{1,30})）$", v)
        if not m:
            break
        inner = m.group(1)
        if re.search(r"20\d{2}", inner) or "年" in inner or "第" in inner or "期" in inner:
            v = v[: m.start()].strip()
            continue
        break

    v = v.replace("集团有限责任公司", "集团有限公司")
    v = v.replace("有限责任公司", "有限公司")
    v = re.sub(r"(有限公司)公司$", r"\1", v)
    v = v.strip("—-·•_ ")
    return v


def _is_excluded_name(name: str) -> bool:
    v = _cleanup(name or "")
    if not v:
        return True
    for k in _GLOBAL_EXCLUDE:
        if k and k in v:
            return True
    return False


def _extract_pages_text_pdfplumber(pdf_path: str, max_pages: int) -> list[str]:
    import pdfplumber

    out: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i in range(min(max_pages, len(pdf.pages))):
            out.append(pdf.pages[i].extract_text() or "")
    return out


def _extract_pages_text(pdf_path: str, max_pages: int) -> list[str]:
    pages = []
    try:
        pages = _extract_pages_text_pdfplumber(pdf_path, max_pages)
    except Exception:
        pages = []
    if pages and any(_cleanup(t) for t in pages):
        return pages
    try:
        from modules.ocr import is_paddle_ocr_available, ocr_page_text

        if not is_paddle_ocr_available():
            return pages
        out: list[str] = []
        for i in range(max_pages):
            t = ocr_page_text(pdf_path, i)
            out.append(t or "")
        return out
    except Exception:
        return pages


def _detect_doc_type(head_text: str) -> str:
    t = _cleanup(head_text)
    if any(k in t for k in _DOC_PROSPECTUS_HINTS):
        return "prospectus"
    if any(k in t for k in _DOC_ANNUAL_HINTS):
        return "annual_report"
    return ""


def _extract_from_line_right_side(text: str, keys: list[str], max_length: int) -> Optional[str]:
    lines = [(ln or "").strip() for ln in (text or "").splitlines() if (ln or "").strip()]
    for i, ln in enumerate(lines):
        for k in keys:
            if k and k in ln:
                m = re.search(re.escape(k) + r"\s*[:：]?\s*(.{2,80})", ln)
                if m:
                    v = normalize_issuer_name(m.group(1))
                    if 2 <= len(v) <= max_length:
                        return v
                if i + 1 < len(lines):
                    v = normalize_issuer_name(lines[i + 1])
                    if 2 <= len(v) <= max_length:
                        return v
    return None


def _extract_table_value_right_side(text: str, field: str, max_length: int) -> Optional[str]:
    lines = [(ln or "").strip() for ln in (text or "").splitlines() if (ln or "").strip()]
    for i, ln in enumerate(lines):
        if field not in ln:
            continue
        m = re.search(re.escape(field) + r"\s*[:：]?\s*(.{2,80})", ln)
        if m:
            v = normalize_issuer_name(m.group(1))
            if 2 <= len(v) <= max_length:
                return v
        if i + 1 < len(lines):
            v = normalize_issuer_name(lines[i + 1])
            if 2 <= len(v) <= max_length:
                return v
    return None


def _extract_issuer_strict(pdf_path: str) -> Optional[str]:
    try:
        head_pages = _extract_pages_text(pdf_path, 3)
    except Exception:
        return None

    head_text = "\n".join(head_pages)
    doc_type = _detect_doc_type(head_text)

    if doc_type == "annual_report":
        try:
            pages = _extract_pages_text(pdf_path, 30)
        except Exception:
            pages = head_pages

        section_hits = ["第一节发行人情况", "第一节公司基本情况"]
        sub_hits = ["一、公司基本信息", "一、公司基本信息"]
        field = "中文名称"

        start_idx = 0
        for i, t in enumerate(pages):
            c = _cleanup(t)
            if any(k in c for k in section_hits):
                start_idx = i
                break

        for i in range(start_idx, min(start_idx + 10, len(pages))):
            c = _cleanup(pages[i])
            if not (any(k in c for k in sub_hits) or field in c):
                continue
            v = _extract_table_value_right_side(pages[i], field, 80)
            if v and ("有限公司" in v or "集团" in v) and not _is_excluded_name(v):
                return v

        return None

    if doc_type == "prospectus":
        try:
            pages = _extract_pages_text(pdf_path, 20)
        except Exception:
            pages = head_pages

        for t in pages:
            v = _extract_from_line_right_side(t, _PROSPECTUS_PRIMARY_FIELDS, 80)
            if v and ("有限公司" in v) and not _is_excluded_name(v):
                return v

        try:
            pages2 = _extract_pages_text(pdf_path, 40)
        except Exception:
            pages2 = pages
        for t in pages2:
            for hint in _GLOSSARY_HINTS:
                if hint and hint in _cleanup(t):
                    m = re.search(r"指\s*([\u4e00-\u9fa5（）()]{2,80})", t)
                    if m:
                        v = normalize_issuer_name(m.group(1))
                        if ("有限公司" in v) and not _is_excluded_name(v):
                            return v

        try:
            cover_pages = _extract_pages_text(pdf_path, 2)
        except Exception:
            cover_pages = pages[:2]
        cover_text = "\n".join(cover_pages)
        cut = len(cover_text)
        for k in _PROSPECTUS_STOP_KEYWORDS:
            idx = cover_text.find(k)
            if idx >= 0:
                cut = min(cut, idx)
        cover_text = cover_text[:cut]
        cand = re.findall(r"[\u4e00-\u9fa5]{2,50}(?:集团)?有限公司", cover_text)
        cand = [normalize_issuer_name(x) for x in cand if x]
        cand = [x for x in cand if ("有限公司" in x) and not _is_excluded_name(x) and not any(bad in x for bad in _PROSPECTUS_EXCLUDE_KEYWORDS)]
        if cand:
            cand = sorted(set(cand), key=lambda x: (-len(x), x))
            return cand[0]

        return None

    return None


def _pick_company(text: str) -> Optional[str]:
    t = _cleanup(text)
    candidates = []
    for pat in _COMPANY_PATTERNS:
        for m in pat.finditer(t):
            v = m.group(0)
            if 6 <= len(v) <= 60:
                candidates.append(v)
    if not candidates:
        return None
    candidates = sorted(set(candidates), key=lambda x: (-len(x), x))
    return candidates[0]


def _extract_header_text_pdfplumber(pdf_path: str, max_pages: int = 2) -> str:
    import pdfplumber

    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for i in range(min(max_pages, len(pdf.pages))):
            p = pdf.pages[i]
            h = p.height
            w = p.width
            top_box = (0, 0, w, min(120, h))
            try:
                header = p.within_bbox(top_box).extract_text() or ""
            except Exception:
                header = ""
            text += "\n" + header
            if not header:
                body = p.extract_text() or ""
                text += "\n" + "\n".join((body.splitlines() or [])[:15])
    return text


def _extract_header_text_pymupdf(pdf_path: str, max_pages: int = 2) -> str:
    import fitz

    doc = fitz.open(pdf_path)
    text = ""
    try:
        for i in range(min(max_pages, doc.page_count)):
            page = doc.load_page(i)
            rect = page.rect
            clip = fitz.Rect(0, 0, rect.width, min(120, rect.height))
            text += "\n" + page.get_text("text", clip=clip)
            if not text.strip():
                text += "\n" + page.get_text("text")
    finally:
        doc.close()
    return text


def extract_issuer(pdf_path: str) -> Optional[str]:
    strict = _extract_issuer_strict(pdf_path)
    if strict and not _is_excluded_name(strict):
        return strict

    text = ""
    try:
        text = _extract_header_text_pdfplumber(pdf_path)
    except Exception:
        text = ""

    issuer = _pick_company(text)
    if issuer:
        v = normalize_issuer_name(issuer)
        if v and not _is_excluded_name(v):
            return v
        if not _is_excluded_name(issuer):
            return issuer

    try:
        text = _extract_header_text_pymupdf(pdf_path)
    except Exception:
        text = ""

    issuer = _pick_company(text)
    if issuer:
        v = normalize_issuer_name(issuer)
        if v and not _is_excluded_name(v):
            return v
        if not _is_excluded_name(issuer):
            return issuer

    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            p = pdf.pages[0]
            body = p.extract_text() or ""
            m = re.search(r"发行人[:：]\s*([^\n]{2,40})", body)
            if m:
                v = _cleanup(m.group(1))
                v2 = normalize_issuer_name(v)
                if v2 and not _is_excluded_name(v2):
                    return v2
                if not _is_excluded_name(v):
                    return v
    except Exception:
        pass

    base = os.path.splitext(os.path.basename(pdf_path))[0]
    base = re.sub(r"^\w{8,32}_", "", base)
    base = re.sub(r"\d{4}", "", base)
    base = base.strip("_- ")
    base2 = normalize_issuer_name(base)
    if base2 and not _is_excluded_name(base2):
        return base2
    if not _is_excluded_name(base):
        return base or None
    return None


def extract_year(pdf_path: str, filename: str = "") -> Optional[int]:
    m = re.search(r"(20\d{2})", filename or "")
    if m:
        return int(m.group(1))

    text = ""
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            for i in range(min(3, len(pdf.pages))):
                text += "\n" + (pdf.pages[i].extract_text() or "")
    except Exception:
        text = ""

    m = re.search(r"(20\d{2})年度报告", text)
    if m:
        return int(m.group(1))

    m = re.search(r"(20\d{2})年(?!\d)", text)
    if m:
        return int(m.group(1))

    return None

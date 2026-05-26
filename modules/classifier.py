import re


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def classify_text(text: str) -> str:
    t = _normalize_text(text)
    if "发行公告" in t and ("募集说明书" not in t and "募集说明" not in t):
        return "unknown"
    if "公司债券中期报告" in t or "债券中期报告" in t or "中期报告" in t or "半年度报告" in t:
        return "annual_report"
    if "公司债券年度报告" in t:
        return "annual_report"
    if "债券年度报告" in t or "年度报告" in t:
        return "annual_report"
    if "募集说明书" in t or "募集说明" in t:
        return "prospectus"
    return "unknown"


def classify_file_type(pdf_path: str) -> str:
    text = ""
    first_page = ""
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            if pdf.pages:
                first_page = pdf.pages[0].extract_text() or ""
            for i in range(min(3, len(pdf.pages))):
                p = pdf.pages[i]
                text += "\n" + (p.extract_text() or "")
    except Exception:
        text = ""

    if not _normalize_text(text):
        try:
            import fitz

            doc = fitz.open(pdf_path)
            if doc.page_count > 0:
                first_page = doc.load_page(0).get_text("text")
            for i in range(min(3, doc.page_count)):
                text += "\n" + doc.load_page(i).get_text("text")
            doc.close()
        except Exception:
            text = ""

    if not _normalize_text(text):
        try:
            from modules.ocr import is_paddle_ocr_available, ocr_first_pages_text, ocr_page_text

            if is_paddle_ocr_available():
                text = ocr_first_pages_text(pdf_path, 3)
                if not first_page:
                    first_page = ocr_page_text(pdf_path, 0)
        except Exception:
            text = ""

    if first_page:
        fp = _normalize_text(first_page)
        if "发行公告" in fp and ("募集说明书" not in fp and "募集说明" not in fp):
            return "unknown"

    return classify_text(text)

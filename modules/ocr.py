import io
import os
import sys
import tempfile
import threading
from functools import lru_cache
from typing import Optional


def _default_cache_home() -> str:
    if not getattr(sys, "frozen", False):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        return os.path.join(root, ".cache", "paddlex_cache")
    base = ""
    if sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Caches")
    elif sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "DebtExtractor", "paddlex_cache")


def _ensure_cache_env() -> None:
    cache_home = os.environ.get("PADDLE_PDX_CACHE_HOME") or _default_cache_home()
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", cache_home)
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
    try:
        os.makedirs(cache_home, exist_ok=True)
    except Exception:
        os.environ["PADDLE_PDX_CACHE_HOME"] = os.path.join(tempfile.gettempdir(), "paddlex_cache")
        os.makedirs(os.environ["PADDLE_PDX_CACHE_HOME"], exist_ok=True)

_OCR_LOCK = threading.Lock()


def is_paddle_ocr_available() -> bool:
    try:
        _ensure_cache_env()
        import paddleocr

        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _get_ocr() -> Optional[object]:
    try:
        _ensure_cache_env()
        try:
            import paddle

            if hasattr(paddle, "set_num_threads"):
                paddle.set_num_threads(1)
        except Exception:
            pass
        from paddleocr import PaddleOCR
        import inspect

        kwargs = {}
        try:
            sig = inspect.signature(PaddleOCR.__init__)
            if "use_gpu" in sig.parameters:
                kwargs["use_gpu"] = False
            if "use_mp" in sig.parameters:
                kwargs["use_mp"] = False
            if "cpu_threads" in sig.parameters:
                kwargs["cpu_threads"] = 1
            if "use_doc_orientation_classify" in sig.parameters:
                kwargs["use_doc_orientation_classify"] = False
            if "use_doc_unwarping" in sig.parameters:
                kwargs["use_doc_unwarping"] = False
            if "use_textline_orientation" in sig.parameters:
                kwargs["use_textline_orientation"] = False
        except Exception:
            kwargs = {}

        return PaddleOCR(lang="ch", **kwargs)
    except Exception:
        return None


def _scale_from_env(name: str, default_v: float) -> float:
    v = (os.environ.get(name) or "").strip()
    if not v:
        return default_v
    try:
        f = float(v)
        if f <= 0:
            return default_v
        return f
    except Exception:
        return default_v


def _render_page_array(pdf_path: str, page_index: int, scale: float) -> Optional[object]:
    try:
        import fitz
        import numpy as np
        from PIL import Image
    except Exception:
        return None

    ocr = _get_ocr()
    if ocr is None:
        return None

    try:
        doc = fitz.open(pdf_path)
        if page_index < 0 or page_index >= doc.page_count:
            doc.close()
            return None
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        doc.close()
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        arr = np.array(img)
        return arr
    except Exception:
        return None


@lru_cache(maxsize=1024)
def _ocr_page_text_scaled(pdf_path: str, page_index: int, scale_key: int) -> str:
    ocr = _get_ocr()
    if ocr is None:
        return ""
    scale = float(scale_key) / 100.0
    with _OCR_LOCK:
        arr = _render_page_array(pdf_path, page_index, scale)
        if arr is None:
            return ""
        try:
            res = ocr.ocr(arr) or []
            lines = []
            if res and isinstance(res[0], dict):
                for blk in res:
                    for txt in (blk.get("rec_texts") or []):
                        if txt:
                            lines.append(str(txt))
            else:
                for r in res:
                    if not r or len(r) < 2:
                        continue
                    txt = r[1][0] if r[1] and len(r[1]) > 0 else ""
                    if txt:
                        lines.append(txt)
            return "\n".join(lines)
        except Exception:
            return ""


def ocr_page_text_locate(pdf_path: str, page_index: int) -> str:
    scale = _scale_from_env("OCR_LOCATE_SCALE", 1.1)
    return _ocr_page_text_scaled(pdf_path, page_index, int(scale * 100))


def ocr_page_text_extract(pdf_path: str, page_index: int) -> str:
    scale = _scale_from_env("OCR_EXTRACT_SCALE", 1.5)
    return _ocr_page_text_scaled(pdf_path, page_index, int(scale * 100))


def ocr_page_text(pdf_path: str, page_index: int) -> str:
    return ocr_page_text_extract(pdf_path, page_index)


def ocr_first_pages_text(pdf_path: str, max_pages: int) -> str:
    out = []
    for i in range(max_pages):
        t = ocr_page_text_locate(pdf_path, i)
        if t:
            out.append(t)
    return "\n".join(out)

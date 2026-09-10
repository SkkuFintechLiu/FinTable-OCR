import io
import os
import re
import sys
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple


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


@dataclass
class OCRResult:
    text: str
    engine: str
    score: float
    raw: Optional[object] = None


class BaseOCREngine(ABC):
    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def ocr_image_array(self, arr) -> Optional[str]: ...

    def ocr_pdf_page(self, pdf_path: str, page_index: int, scale: float) -> Optional[str]:
        arr = _render_page_array(pdf_path, page_index, scale)
        if arr is None:
            return None
        return self.ocr_image_array(arr)


def _render_page_array(pdf_path: str, page_index: int, scale: float) -> Optional[object]:
    try:
        import fitz
        import numpy as np
        from PIL import Image
    except Exception:
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


class PaddleOCREngine(BaseOCREngine):
    name = "paddle"

    def __init__(self):
        self._ocr = None

    def is_available(self) -> bool:
        try:
            _ensure_cache_env()
            import paddleocr  # noqa: F401

            return True
        except Exception:
            return False

    def _get_ocr(self):
        if self._ocr is not None:
            return self._ocr
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
            self._ocr = PaddleOCR(lang="ch", **kwargs)
            return self._ocr
        except Exception:
            return None

    def ocr_image_array(self, arr) -> Optional[str]:
        ocr = self._get_ocr()
        if ocr is None:
            return None
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
            return None


def _unlimited_ocr_sglang_available() -> bool:
    endpoint = (os.environ.get("UNLIMITED_OCR_ENDPOINT") or "").strip()
    if not endpoint:
        return False
    try:
        import requests

        r = requests.get(endpoint.rstrip("/") + "/v1/models", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _call_unlimited_ocr_sglang(arr, prompt: str = "document parsing.") -> Optional[str]:
    endpoint = (os.environ.get("UNLIMITED_OCR_ENDPOINT") or "").strip().rstrip("/")
    if not endpoint:
        return None
    try:
        import base64

        import requests
        from PIL import Image

        img = Image.fromarray(arr)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        image_mode = os.environ.get("UNLIMITED_OCR_IMAGE_MODE", "gundam")
        ngram_window = int(os.environ.get("UNLIMITED_OCR_NGRAM_WINDOW", "128"))
        payload = {
            "model": os.environ.get("UNLIMITED_OCR_MODEL", "Unlimited-OCR"),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"<image>{prompt}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 32768,
            "skip_special_tokens": False,
            "images_config": {"image_mode": image_mode},
            "sampling_params": {"no_repeat_ngram_size": 35},
        }
        try:
            from sglang.srt.sampling.custom_logit_processor import DeepseekOCRNoRepeatNGramLogitProcessor

            payload["custom_logit_processor"] = {
                "type": "DeepseekOCRNoRepeatNGramLogitProcessor",
                "ngram_window": ngram_window,
                "no_repeat_ngram_size": 35,
            }
        except Exception:
            pass
        headers = {}
        api_key = os.environ.get("UNLIMITED_OCR_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        r = requests.post(
            endpoint + "/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=int(os.environ.get("UNLIMITED_OCR_TIMEOUT", "300")),
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip() if content else None
    except Exception:
        return None


def _unlimited_ocr_transformers_available() -> bool:
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        from transformers import AutoModel, AutoTokenizer  # noqa: F401

        return True
    except Exception:
        return False


class UnlimitedOCREngine(BaseOCREngine):
    name = "unlimited"

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._mode = None

    def is_available(self) -> bool:
        if _unlimited_ocr_sglang_available():
            self._mode = "sglang"
            return True
        if _unlimited_ocr_transformers_available():
            self._mode = "transformers"
            return True
        return False

    def _load_model(self):
        if self._model is not None and self._tokenizer is not None:
            return
        if self._mode == "transformers":
            try:
                import torch
                from transformers import AutoModel, AutoTokenizer

                model_name = os.environ.get("UNLIMITED_OCR_MODEL_NAME", "baidu/Unlimited-OCR")
                self._tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
                dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                self._model = (
                    AutoModel.from_pretrained(
                        model_name,
                        trust_remote_code=True,
                        use_safetensors=True,
                        torch_dtype=dtype,
                    )
                    .eval()
                    .cuda()
                )
            except Exception:
                self._model = None
                self._tokenizer = None

    def ocr_image_array(self, arr) -> Optional[str]:
        if self._mode == "sglang":
            return _call_unlimited_ocr_sglang(arr)
        if self._mode == "transformers":
            self._load_model()
            if self._model is None or self._tokenizer is None:
                return None
            try:
                import tempfile

                from PIL import Image

                img = Image.fromarray(arr)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    img.save(tmp, format="PNG")
                    tmp_path = tmp.name
                try:
                    out_dir = tempfile.mkdtemp(prefix="uocr_")
                    image_mode = os.environ.get("UNLIMITED_OCR_IMAGE_MODE", "gundam")
                    if image_mode == "base":
                        base_size = 1024
                        image_size = 1024
                        crop_mode = False
                        ngram_window = 1024
                    else:
                        base_size = 1024
                        image_size = 640
                        crop_mode = True
                        ngram_window = 128
                    self._model.infer(
                        self._tokenizer,
                        prompt="<image>document parsing.",
                        image_file=tmp_path,
                        output_path=out_dir,
                        base_size=base_size,
                        image_size=image_size,
                        crop_mode=crop_mode,
                        max_length=32768,
                        no_repeat_ngram_size=35,
                        ngram_window=ngram_window,
                        save_results=False,
                    )
                    out_file = os.path.join(out_dir, os.path.splitext(os.path.basename(tmp_path))[0] + ".md")
                    if os.path.exists(out_file):
                        with open(out_file, "r", encoding="utf-8") as f:
                            return f.read().strip()
                    return None
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
            except Exception:
                return None
        return None


def _score_text_quality(text: str) -> float:
    if not text:
        return 0.0
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    total_chars = sum(len(ln) for ln in lines)
    avg_line_len = total_chars / len(lines) if lines else 0
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    digits = len(re.findall(r"\d", text))
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    meaningful = chinese_chars + digits + ascii_letters
    meaningful_ratio = meaningful / max(1, total_chars)
    variety_score = 0.0
    if chinese_chars > 0:
        variety_score += 0.4
    if digits > 0:
        variety_score += 0.3
    if ascii_letters > 0:
        variety_score += 0.2
    common_table_markers = ["合计", "项目", "单位", "金额", "科目", "类别", "小计", "总计"]
    table_hits = sum(1 for m in common_table_markers if m in text)
    table_score = min(1.0, table_hits / 4.0) * 0.3
    length_score = min(1.0, total_chars / 500.0) * 0.3
    line_score = min(1.0, len(lines) / 20.0) * 0.2
    avg_line_score = min(1.0, avg_line_len / 30.0) * 0.2
    quality = (
        meaningful_ratio * 0.4
        + variety_score * 0.2
        + table_score * 0.15
        + length_score * 0.15
        + line_score * 0.05
        + avg_line_score * 0.05
    )
    return max(0.0, min(1.0, quality))


def _merge_texts(paddle_text: str, unlimited_text: str) -> str:
    if not paddle_text and not unlimited_text:
        return ""
    if not paddle_text:
        return unlimited_text
    if not unlimited_text:
        return paddle_text
    s_paddle = _score_text_quality(paddle_text)
    s_unlimited = _score_text_quality(unlimited_text)
    if s_unlimited < s_paddle * 0.7:
        return paddle_text
    if s_paddle < s_unlimited * 0.7:
        return unlimited_text
    paddle_lines = set(ln.strip() for ln in paddle_text.splitlines() if ln.strip())
    merged_lines = []
    for ln in unlimited_text.splitlines():
        s = ln.strip()
        merged_lines.append(ln)
        if s and s in paddle_lines:
            paddle_lines.discard(s)
    for ln in paddle_text.splitlines():
        s = ln.strip()
        if s and s in paddle_lines:
            merged_lines.append(ln)
    return "\n".join(merged_lines)


_ENGINE_REGISTRY: List[BaseOCREngine] = []


def _get_engines() -> List[BaseOCREngine]:
    if _ENGINE_REGISTRY:
        return _ENGINE_REGISTRY
    _ENGINE_REGISTRY.append(PaddleOCREngine())
    _ENGINE_REGISTRY.append(UnlimitedOCREngine())
    return _ENGINE_REGISTRY


def is_paddle_ocr_available() -> bool:
    for e in _get_engines():
        if e.name == "paddle":
            return e.is_available()
    return False


def is_unlimited_ocr_available() -> bool:
    for e in _get_engines():
        if e.name == "unlimited":
            return e.is_available()
    return False


def is_any_ocr_available() -> bool:
    return any(e.is_available() for e in _get_engines())


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


def _get_ocr_strategy() -> str:
    s = (os.environ.get("OCR_STRATEGY") or "").strip().lower()
    if s in ("paddle_only", "unlimited_only", "dual", "dual_merge", "dual_best"):
        return s
    return "dual_merge"


@lru_cache(maxsize=1024)
def _ocr_page_text_scaled(pdf_path: str, page_index: int, scale_key: int) -> str:
    scale = float(scale_key) / 100.0
    strategy = _get_ocr_strategy()
    engines = _get_engines()
    paddle_engine = None
    unlimited_engine = None
    for e in engines:
        if e.name == "paddle":
            paddle_engine = e
        elif e.name == "unlimited":
            unlimited_engine = e
    with _OCR_LOCK:
        paddle_text = ""
        unlimited_text = ""
        if strategy in ("paddle_only", "dual", "dual_merge", "dual_best"):
            if paddle_engine and paddle_engine.is_available():
                paddle_text = paddle_engine.ocr_pdf_page(pdf_path, page_index, scale) or ""
        if strategy in ("unlimited_only", "dual", "dual_merge", "dual_best"):
            if unlimited_engine and unlimited_engine.is_available():
                unlimited_text = unlimited_engine.ocr_pdf_page(pdf_path, page_index, scale) or ""
        if strategy == "paddle_only":
            return paddle_text
        if strategy == "unlimited_only":
            return unlimited_text
        if strategy == "dual_best":
            s_p = _score_text_quality(paddle_text)
            s_u = _score_text_quality(unlimited_text)
            if s_u >= s_p:
                return unlimited_text if unlimited_text else paddle_text
            return paddle_text if paddle_text else unlimited_text
        return _merge_texts(paddle_text, unlimited_text)


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

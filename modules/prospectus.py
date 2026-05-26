import re
from typing import Any, Dict, List, Optional, Tuple

from modules.parse_types import ProspectusParsed
from modules.table_extractor import extract_candidate_tables, pick_best_table
from modules.trace import trace_from_page
from modules.utils import detect_unit_multiplier_to_yi, is_annual_header, normalize_compact, parse_number, safe_year_from_text


def _cell(value: Any = None, raw: Optional[str] = None, flag: str = "missing") -> Dict[str, Any]:
    return {"value": value, "raw": raw, "flag": flag}


_TABLE_KEYWORDS = [
    "发行人有息债务分类表",
    "有息负债结构情况",
    "有息负债结构规划",
    "发行人有息负债明细情况",
    "有息负债明细情况",
    "有息债务结构情况",
    "有息负债余额",
    "有息负债余额情况",
    "长短期债务情况",
    "公司有息负债情况",
    "有息债务构成情况",
    "有息债务结构",
    "有息负债基本情况",
    "有息债务余额和类型情况",
    "发行人有息负债余额和类型情况",
    "最近两年有息负债余额和类型",
    "发行人有息债务结构情况",
    "有息债务余额情况",
    "债务结构情况",
    "债务余额和类型情况",
]


_ROW_MAP = [
    ("bank_loans", ["银行借款", "银行贷款", "银行借款及票据", "银行借款及贷款"]),
    ("credit_bonds", ["债券融资", "公司信用类债券", "信用类债券", "应付债券", "公司债券", "企业债券", "债务融资工具"]),
    ("non_bank_loans", ["融资租赁", "非标融资", "非银行金融机构贷款", "非银行金融机构借款", "信托借款", "委托贷款"]),
    ("other", ["其他融资", "供应链融资", "信用证", "其他有息债务", "债权融资计划", "资管融资", "专项债券转贷"]),
    ("total", ["合计", "总计"]),
]


def _map_row_label(label: str) -> Optional[str]:
    v = normalize_compact(label or "")
    if not v:
        return None
    if len(v) > 30:
        return None
    if ("应付" in v) and ("应付债券" not in v):
        return None
    if any(k in v for k in ["应付款", "应付账款", "合同负债", "预收", "短期借款", "长期借款", "一年内到期", "非流动负债"]):
        return None
    if any(k in v for k in ["截至", "报告期", "余额", "同比", "变动", "发行人", "本公司"]):
        return None
    for key, pats in _ROW_MAP:
        for p in pats:
            if p and p in v:
                return key
    if v.endswith("借款"):
        if any(k in v for k in ["信托", "委托", "融资租赁"]):
            return "non_bank_loans"
        return "bank_loans"
    if any(k in v for k in ["票据贴现", "贴现"]):
        return "bank_loans"
    return None


def _extract_tables(page) -> List[List[List[str]]]:
    settings = {
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
    try:
        return page.extract_tables(settings) or []
    except Exception:
        try:
            t = page.extract_table(settings)
            return [t] if t else []
        except Exception:
            return []


def _is_year_col(header: str) -> Optional[int]:
    y = is_annual_header(header)
    if y is not None:
        return y
    h = normalize_compact(header or "")
    m = re.search(r"截至(20\d{2})年末", h)
    if m:
        return int(m.group(1))
    return None


def _table_year_columns(table: List[List[str]]) -> Dict[int, int]:
    if not table or not table[0]:
        return {}
    year_cols: Dict[int, int] = {}

    max_scan = min(4, len(table))
    max_cols = max(len(r) for r in table[:max_scan] if r)
    for col in range(max_cols):
        parts: List[str] = []
        for r in range(max_scan):
            row = table[r] if r < len(table) else []
            if col < len(row):
                parts.append(row[col] or "")
        y = _is_year_col("".join(parts))
        if y is not None:
            year_cols[y] = col

    if year_cols:
        return year_cols

    header_row = table[0]
    for i, c in enumerate(header_row):
        y = _is_year_col(c)
        if y is not None:
            year_cols[y] = i
    if year_cols:
        return year_cols

    if len(table) >= 2:
        header_row2 = table[1]
        for i, c in enumerate(header_row2):
            y = _is_year_col(c)
            if y is not None:
                year_cols[y] = i
    return year_cols


def _mapped_row_hits(table: List[List[str]]) -> int:
    hits = 0
    for r in table[1:]:
        if not r or not r[0]:
            continue
        k = _map_row_label(r[0])
        if k and k != "total":
            hits += 1
    return hits


def _mapped_row_key_set(table: List[List[str]]) -> set:
    out = set()
    for r in table[1:]:
        if not r or not r[0]:
            continue
        k = _map_row_label(r[0])
        if k and k != "total":
            out.add(k)
    return out


def _best_table_from_candidates(candidates: List[Any]) -> Optional[Any]:
    best = None
    best_score = -1.0
    for c in candidates:
        year_cols = _table_year_columns(c.table)
        if not year_cols:
            continue
        ks = _mapped_row_key_set(c.table)
        if len(ks) < 2 or ("bank_loans" not in ks and "credit_bonds" not in ks):
            continue
        score = float(c.score) + len(ks) * 0.6 + (0.2 if c.bbox else 0.0)
        if score > best_score:
            best = c
            best_score = score
    return best


def _looks_like_header_row(row: List[str]) -> bool:
    if not row:
        return False
    txt = normalize_compact("".join([c or "" for c in row]))
    return "年末" in txt and ("项目" in txt or "类别" in txt or "科目" in txt)


def _merge_continuation(t1: List[List[str]], t2: List[List[str]]) -> Optional[List[List[str]]]:
    if not t1 or not t2:
        return None
    h1 = t1[0]
    h2 = t2[0]
    if _looks_like_header_row(h1) and _looks_like_header_row(h2) and normalize_compact("".join(h1)) == normalize_compact("".join(h2)):
        return [h1] + t1[1:] + t2[1:]
    if _looks_like_header_row(h1) and not _looks_like_header_row(h2):
        return t1 + t2
    return None


def _detect_unit_near_debt_table(text: str) -> Tuple[float, Optional[str]]:
    mult, unit = detect_unit_multiplier_to_yi(text or "")
    lines = [(ln or "").strip() for ln in (text or "").splitlines() if (ln or "").strip()]
    anchors = ["期限结构", "余额、类型", "一年以内", "1年以内"]
    anchor_idx = -1
    for i, ln in enumerate(lines):
        c = normalize_compact(ln)
        if any(a in c for a in anchors):
            anchor_idx = i
    if anchor_idx < 0:
        return mult, unit
    start = max(0, anchor_idx - 6)
    end = min(len(lines), anchor_idx + 18)
    slice_text = "\n".join(lines[start:end])
    mult2, unit2 = detect_unit_multiplier_to_yi(slice_text)
    if unit2:
        return mult2, unit2
    return mult, unit


def _slice_debt_table_text(text: str) -> str:
    lines = [(ln or "").rstrip() for ln in (text or "").splitlines() if (ln or "").strip()]
    strong_anchors = [
        "余额、类型和期限结构",
        "余额、类型及期限结构",
        "余额类型和期限结构",
        "期限结构如下",
        "按债务类型的分类情况",
        "按债务类型分类情况",
        "债务类型的分类情况",
        "按融资方式分类",
        "按融资方式的分类",
        "融资方式分类情况",
    ]
    weak_anchors = [
        "一年以内（含",
        "一年以内(含",
        "一年以内",
        "1年以内",
    ]
    anchor_idxs: List[int] = []
    for i, ln in enumerate(lines):
        c = normalize_compact(ln)
        if any(a in c for a in strong_anchors) or any(a in c for a in weak_anchors):
            anchor_idxs.append(i)
    if not anchor_idxs:
        return text or ""

    row_hints = [
        "银行借款",
        "银行贷款",
        "公司债券",
        "企业债券",
        "债务融资工具",
        "应付债券",
        "债券融资",
        "融资租赁",
        "非标融资",
        "信托借款",
        "委托贷款",
        "其他融资",
        "其他有息负债",
        "合计",
    ]

    def score(idx: int) -> int:
        end = min(len(lines), idx + 90)
        window = "\n".join(lines[idx:end])
        c = normalize_compact(window)
        s = 0
        if ("项目" in c) or ("科目" in c) or ("类别" in c):
            s += 2
        if "单位" in c:
            s += 1
        if re.search(r"20\\d{2}", c):
            s += 2
        if "2024" in c:
            s += 2
        for h in row_hints:
            if h in c:
                s += 1
        return s

    best_idx = max(anchor_idxs, key=score)
    start = best_idx
    for k in range(1, 5):
        if best_idx - k < 0:
            break
        ck = normalize_compact(lines[best_idx - k])
        if ("单位" in ck) or (ck.startswith("表")):
            start = best_idx - k
            continue
        break

    end = min(len(lines), best_idx + 90)
    for j in range(best_idx + 1, end):
        cj = normalize_compact(lines[j])
        if j - best_idx <= 3:
            continue
        if ("截至" in cj) and ("项目" not in cj) and ("单位" not in cj) and ("表" not in cj):
            end = j
            break
    return "\n".join(lines[start:end])


def _parse_period_groups_from_text(text: str) -> List[str]:
    lines = [(ln or "").strip() for ln in (text or "").splitlines() if (ln or "").strip()]
    best: List[str] = []
    best_score = -1
    for i, ln in enumerate(lines):
        compact = normalize_compact(ln)
        if "募集说明书" in compact:
            continue
        parts = [p for p in re.split(r"\s+", ln.strip()) if p]
        if not parts:
            continue

        has_year = bool(re.search(r"20\d{2}\s*年", compact))
        has_within = ("一年以内" in compact) or ("1年以内" in compact)
        has_item = ("项目" in compact) or ("科目" in compact) or ("类别" in compact)

        if has_item and (("年末" in compact) or ("年度" in compact) or ("截至" in compact) or has_within):
            def keep_period_token(x: str) -> bool:
                cx = normalize_compact(x)
                if not cx:
                    return False
                if ("一年以内" in cx) or ("1年以内" in cx):
                    return True
                if re.search(r"20\d{2}年", cx):
                    return True
                if ("年末" in cx) or ("年度" in cx) or ("截至" in cx) or ("月末" in cx):
                    return True
                return False

            if parts[0] in {"项目", "科目", "类别"} and len(parts) >= 3:
                return [p for p in parts[1:] if keep_period_token(p)]
            if "项目" in parts and len(parts) >= 4:
                try:
                    idx = parts.index("项目")
                    if idx + 1 < len(parts):
                        return [p for p in parts[idx + 1 :] if keep_period_token(p)]
                except Exception:
                    pass
            if has_within and has_year:
                year_tokens = re.findall(r"20\d{2}年(?:\d{1,2}月末|\d{1,2}月|\d{1,2}-\d{1,2}月|末|度)?", compact)
                if year_tokens:
                    within_tag = "一年以内" if "一年以内" in compact else "1年以内"
                    return [within_tag] + year_tokens

        if not has_year:
            continue

        year_tokens = re.findall(r"20\d{2}年(?:\d{1,2}月末|\d{1,2}月|\d{1,2}-\d{1,2}月|末|度)?", compact)
        if len(year_tokens) < 2 and not has_within:
            continue

        within_part = ""
        if i > 0:
            prev = normalize_compact(lines[i - 1])
            if ("一年以内" in prev) or ("1年以内" in prev):
                within_part = lines[i - 1].strip()
        if not within_part and has_within:
            within_part = ln.strip()

        if within_part:
            cand = [within_part] + year_tokens
        else:
            cand = year_tokens

        score = 0
        if any(("一年以内" in normalize_compact(x)) or ("1年以内" in normalize_compact(x)) for x in cand):
            score += 2
        if has_item:
            score += 1
        if len(year_tokens) >= 3:
            score += 1

        if score > best_score:
            best = cand
            best_score = score

    return best


def _parse_period_groups_from_split_lines(text: str) -> List[str]:
    lines = [(ln or "").strip() for ln in (text or "").splitlines() if (ln or "").strip()]
    for i in range(len(lines) - 1):
        a = lines[i]
        b = lines[i + 1]
        ca = normalize_compact(a)
        cb = normalize_compact(b)
        b_is_item = ("项目" in cb) or ("科目" in cb) or ("类别" in cb)
        a_is_item = ("项目" in ca) or ("科目" in ca) or ("类别" in ca)

        a_has_year = ("年末" in ca) or ("年度" in ca) or ("截至" in ca) or ("一年以内" in ca) or re.search(r"20\d{2}年", ca)
        b_has_year = ("年末" in cb) or ("年度" in cb) or ("截至" in cb) or ("一年以内" in cb) or re.search(r"20\d{2}年", cb)

        if b_is_item and a_has_year:
            year_tokens = re.findall(r"20\d{2}年(?:\d{1,2}月末|\d{1,2}月|\d{1,2}-\d{1,2}月|末|度)?", ca)
            if len(year_tokens) >= 2:
                return year_tokens
        if a_is_item and b_has_year:
            year_tokens = re.findall(r"20\d{2}年(?:\d{1,2}月末|\d{1,2}月|\d{1,2}-\d{1,2}月|末|度)?", cb)
            if len(year_tokens) >= 2:
                return year_tokens
            if len(year_tokens) == 1:
                toks = [year_tokens[0]]
                j = i + 2
                while j < len(lines) and len(toks) < 6:
                    cc = normalize_compact(lines[j])
                    more = re.findall(r"20\d{2}年(?:\d{1,2}月末|\d{1,2}月|\d{1,2}-\d{1,2}月|末|度)?", cc)
                    if len(more) != 1:
                        break
                    toks.append(more[0])
                    j += 1
                if len(toks) >= 2:
                    return toks
    return []


def _annual_group_indices(periods: List[str]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for idx, p in enumerate(periods):
        y = is_annual_header(p)
        if y is not None:
            out[y] = idx
    return out


def _extract_row_label_and_numbers(line: str) -> Tuple[str, List[Optional[str]]]:
    ln = (line or "").strip()
    if not ln:
        return "", []
    m = re.search(r"[-–—]|\d", ln)
    if not m:
        return ln, []
    label = ln[: m.start()].strip()
    tail = ln[m.start() :].strip()
    if not tail:
        return label, []
    raw_tokens = [t for t in re.split(r"\s+", tail) if t]
    nums: List[Optional[str]] = []
    for t in raw_tokens:
        tt = t.replace("—", "-").replace("–", "-")
        if tt in {"-", "--"}:
            nums.append("0")
            continue
        if "%" in tt or "％" in tt:
            tt = tt.replace("%", "").replace("％", "")
        n, err = parse_number(tt)
        if err is None and n is not None:
            nums.append(tt)
            continue
        mm = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", tt)
        if mm:
            nums.append(tt)
    return label, nums


def _parse_text_table_with_periods(
    header_text: str,
    body_text: str,
    periods: List[str],
    default_year: Optional[int],
    mult: float,
    unit: Optional[str],
) -> Tuple[Dict[int, Dict[str, Dict[str, Any]]], Optional[Tuple[int, str]], str]:
    if not periods or len(periods) < 2:
        return {}, None, ""
    annual_groups = _annual_group_indices(periods)
    if not annual_groups:
        return {}, None, ""
    if 2024 in annual_groups:
        annual_groups = {2024: annual_groups[2024]}
    data_by_year: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for y in annual_groups.keys():
        data_by_year[y] = {k: _cell(None, None, "missing") for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]}
    broad_seen: Dict[int, set] = {y: set() for y in annual_groups.keys()}

    used: Optional[Tuple[int, str]] = None
    hit = False
    main_keys = set()
    lines = [(ln or "").strip() for ln in (body_text or "").splitlines() if (ln or "").strip()]
    group_count = len(periods)
    header_compact = normalize_compact(header_text or "")
    stride = 2 if ("占比" in header_compact) else 1
    need_tokens = group_count * stride

    def is_num_line(x: str) -> bool:
        cx = normalize_compact(x)
        if not cx:
            return False
        if re.search(r"[\u4e00-\u9fa5]", cx):
            return False
        return bool(re.fullmatch(r"[-–—/=%\d,\.]+", cx))

    merged_lines: List[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        c = normalize_compact(ln)
        if c and (not re.search(r"\d", c)) and re.search(r"[\u4e00-\u9fa5]", c):
            nums: List[str] = []
            j = i + 1
            while j < len(lines) and len(nums) < need_tokens:
                if not is_num_line(lines[j]):
                    break
                nums.append(lines[j])
                j += 1
            if nums:
                merged_lines.append(ln + " " + " ".join(nums))
                i = j
                continue
        merged_lines.append(ln)
        i += 1
    lines = merged_lines

    for ln in lines:
        compact = normalize_compact(ln)
        if compact.startswith("金额") and "占比" in compact:
            continue
        label, nums = _extract_row_label_and_numbers(ln)
        label_compact = normalize_compact(label)
        mapped = _map_row_label(label)
        if not mapped:
            continue
        if len(nums) < group_count:
            continue
        if len(nums) >= group_count * 2:
            stride = 2
            need_tokens = group_count * stride
        elif len(nums) >= group_count:
            stride = 1
            need_tokens = group_count * stride
        if len(nums) < need_tokens:
            continue

        hit = True
        for y, gidx in annual_groups.items():
            amount_idx = gidx * stride
            if amount_idx >= len(nums):
                continue
            amount_tok = nums[amount_idx]
            if amount_tok is None:
                continue
            n, err = parse_number(amount_tok)
            if err is not None or n is None:
                if (data_by_year[y].get(mapped) or {}).get("flag") != "ok":
                    data_by_year[y][mapped] = _cell(None, str(amount_tok), "invalid")
                continue
            if unit:
                nv = round(n * mult, 2)
                cur = data_by_year[y].get(mapped) or {}
                cur_flag = cur.get("flag")
                cur_val = cur.get("value")

                is_broad = False
                if mapped == "credit_bonds" and label_compact in {"债券融资", "公司信用类债券", "信用类债券"}:
                    is_broad = True
                if mapped == "non_bank_loans" and label_compact in {"非标融资", "非银行金融机构贷款", "非银行金融机构借款"}:
                    is_broad = True
                if mapped == "other":
                    is_broad = False

                if cur_flag != "ok":
                    data_by_year[y][mapped] = _cell(nv, None, "ok")
                    if is_broad and mapped in {"credit_bonds", "non_bank_loans"}:
                        broad_seen[y].add(mapped)
                elif is_broad:
                    data_by_year[y][mapped] = _cell(nv, None, "ok")
                    if mapped in {"credit_bonds", "non_bank_loans"}:
                        broad_seen[y].add(mapped)
                elif mapped in broad_seen[y] and mapped in {"credit_bonds", "non_bank_loans"}:
                    pass
                else:
                    if isinstance(cur_val, (int, float)):
                        data_by_year[y][mapped] = _cell(round(float(cur_val) + nv, 2), None, "ok")
                    else:
                        data_by_year[y][mapped] = _cell(nv, None, "ok")

                if mapped != "total" and (data_by_year[y].get(mapped) or {}).get("flag") == "ok":
                    main_keys.add(mapped)
            else:
                if (data_by_year[y].get(mapped) or {}).get("flag") != "ok":
                    data_by_year[y][mapped] = _cell(None, str(amount_tok), "unit_unknown")
        used = used or (0, "text_table")

    if not hit:
        return {}, None, ""
    if len(main_keys) < 2 or ("bank_loans" not in main_keys and "credit_bonds" not in main_keys):
        for y, yd in data_by_year.items():
            if ((yd.get("bank_loans") or {}).get("flag") == "ok") and ((yd.get("total") or {}).get("flag") == "ok"):
                return data_by_year, used, ""
        return {}, None, ""
    return data_by_year, used, ""


def parse_prospectus(pdf_path: str, fallback_year: Optional[int] = None) -> ProspectusParsed:
    import pdfplumber

    parsed = ProspectusParsed()
    try:
        from modules.metadata import extract_issuer

        parsed.issuer = extract_issuer(pdf_path) or ""
    except Exception:
        parsed.issuer = ""

    with pdfplumber.open(pdf_path) as pdf:
        ocr_enabled = False
        ocr_page_text = None
        ocr_page_text_locate = None
        try:
            from modules.ocr import is_paddle_ocr_available, ocr_page_text_extract as _ocr_page_text, ocr_page_text_locate as _ocr_page_text_locate

            if is_paddle_ocr_available():
                ocr_enabled = True
                ocr_page_text = _ocr_page_text
                ocr_page_text_locate = _ocr_page_text_locate
        except Exception:
            ocr_enabled = False
            ocr_page_text = None
            ocr_page_text_locate = None

        def ocr_candidate_pages(max_pages: int = 260, step: int = 10) -> List[int]:
            if not (ocr_enabled and ocr_page_text):
                return []
            n = len(pdf.pages)
            scan_n = min(n, max_pages)
            hits: set[int] = set()
            key_rows = [
                "银行借款",
                "银行贷款",
                "公司债券",
                "企业债券",
                "债务融资工具",
                "融资租赁",
                "非标融资",
                "信托借款",
                "委托贷款",
                "其他融资",
                "期限结构",
            ]
            for i in range(0, scan_n, max(1, step)):
                t = ocr_page_text(pdf_path, i) or ""
                c = normalize_compact(t)
                if not c:
                    continue
                looks_like_table = (("项目" in c) or ("科目" in c) or ("类别" in c)) and ("合计" in c) and ("单位" in c or "金额" in c)
                if looks_like_table and ("2024" in c or "2023" in c) and any(k in c for k in key_rows):
                    for j in range(max(0, i - 4), min(n, i + 5)):
                        hits.add(j)
                    if len(hits) >= 25:
                        break
            if hits:
                return sorted(hits)[:40]
            head = list(range(min(40, n)))
            tail = list(range(max(0, n - 10), n))
            return sorted(set(head + tail))

        head_text = ""
        struct_head_text = ""
        for i in range(min(3, len(pdf.pages))):
            t0 = pdf.pages[i].extract_text() or ""
            struct_head_text += "\n" + t0
            t = t0
            if (not t) and ocr_enabled and ocr_page_text_locate:
                t = ocr_page_text_locate(pdf_path, i) or ""
            head_text += "\n" + t

        default_year = fallback_year or safe_year_from_text(head_text)

        data_by_year: Dict[int, Dict[str, Dict[str, Any]]] = {}
        notes = ""
        unit_seen = False
        used = None

        candidate_pages: List[int] = []
        scanned_pdf = not normalize_compact(struct_head_text)
        if scanned_pdf:
            candidate_pages = []
        else:
            ocr_page_pool: Optional[List[int]] = None
            ocr_pool_set: Optional[set] = None
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if (not text) and ocr_enabled and ocr_page_text_locate:
                    if ocr_page_pool is None:
                        ocr_page_pool = ocr_candidate_pages()
                        ocr_pool_set = set(ocr_page_pool)
                    if i in (ocr_pool_set or set()):
                        text = ocr_page_text_locate(pdf_path, i) or ""
                compact = normalize_compact(text)
                semantic_ok = False
                if re.search(r"2024\s*年末|2024\s*年|截至\s*2024\s*年", compact) and ("金额" in compact) and (("项目" in compact) or ("科目" in compact) or ("类别" in compact)):
                    if any(k in compact for k in ["银行贷款", "银行借款", "债券融资", "信用类债券", "非标融资", "融资租赁", "信托借款"]):
                        semantic_ok = True
                semantic_ok2 = False
                if re.search(r"2024\s*年末|2024\s*年|截至\s*2024\s*年", compact) and (("项目" in compact) or ("科目" in compact) or ("类别" in compact)) and ("单位" in compact):
                    if any(k in compact for k in ["银行借款", "银行贷款", "公司债券", "企业债券", "债务融资工具", "融资租赁", "信托借款", "委托贷款", "非标融资", "债权融资计划", "其他有息负债"]):
                        semantic_ok2 = True
                if any(k in compact for k in _TABLE_KEYWORDS):
                    candidate_pages.append(i)
                    continue
                if semantic_ok and (("一年以内" in compact) or ("1年以内" in compact)):
                    candidate_pages.append(i)
                    continue
                if semantic_ok2:
                    candidate_pages.append(i)

        if (not candidate_pages) and (not scanned_pdf):
            candidate_pages = list(range(len(pdf.pages)))
        if len(candidate_pages) > 140:
            candidate_pages = candidate_pages[:80] + candidate_pages[80::2]

        def ok_count(yd: Dict[str, Any]) -> int:
            keys = ["bank_loans", "non_bank_loans", "other", "total"]
            c = 0
            for k in keys:
                v = yd.get(k)
                if isinstance(v, dict) and v.get("flag") == "ok":
                    c += 1
            return c

        def score_candidate(d: Dict[int, Dict[str, Dict[str, Any]]]) -> float:
            if not d:
                return -1.0
            yd = d.get(2024)
            if not yd:
                yd = next((v for k, v in d.items() if k != 0), None)
            if not yd:
                return -1.0
            keys = ["bank_loans", "credit_bonds", "non_bank_loans", "other", "total"]
            ok = sum(1 for k in keys if isinstance(yd.get(k), dict) and yd[k].get("flag") == "ok")
            pos = sum(
                1
                for k in keys
                if isinstance(yd.get(k), dict)
                and yd[k].get("flag") == "ok"
                and isinstance(yd[k].get("value"), (int, float))
                and float(yd[k]["value"]) > 0.05
            )
            zeros = sum(
                1
                for k in keys
                if isinstance(yd.get(k), dict)
                and yd[k].get("flag") == "ok"
                and (yd[k].get("value") in {0, 0.0, None})
            )
            bank_pos = 1 if (isinstance(yd.get("bank_loans"), dict) and yd["bank_loans"].get("flag") == "ok" and isinstance(yd["bank_loans"].get("value"), (int, float)) and float(yd["bank_loans"]["value"]) > 0.05) else 0
            return ok * 10 + pos * 6 + bank_pos * 6 - zeros * 2

        best_data_by_year: Dict[int, Dict[str, Dict[str, Any]]] = {}
        best_used: Optional[Tuple[int, str]] = None
        best_score = -1.0

        ocr_trace_text = ""
        ocr_unit_text = ""

        if scanned_pdf and ocr_enabled and ocr_page_text and ocr_page_text_locate:
            n = len(pdf.pages)
            head = list(range(min(20, n)))
            tail = list(range(max(0, n - 10), n))
            sample_pages = sorted(set(head + tail + list(range(0, min(n, 260), 16))))
            if len(sample_pages) > 50:
                sample_pages = sample_pages[:50]
            key_rows = [
                "有息负债",
                "有息债务",
                "债务结构",
                "债务类型",
                "融资方式",
                "余额",
                "银行借款",
                "银行贷款",
                "公司债券",
                "企业债券",
                "债务融资工具",
                "融资租赁",
                "非标融资",
                "信托借款",
                "委托贷款",
                "其他融资",
                "期限结构",
            ]

            def score_hit(c: str) -> int:
                hit = 0
                has_kw = any(k in c for k in _TABLE_KEYWORDS)
                has_item = ("项目" in c) or ("科目" in c) or ("类别" in c)
                has_unit = ("单位" in c) or ("金额" in c)
                year_tokens = re.findall(r"20\d{2}", c)
                num_tokens = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?", c)

                if has_item:
                    hit += 4
                if has_unit:
                    hit += 2
                if "合计" in c:
                    hit += 1
                if has_kw:
                    hit += 2
                if len(set(year_tokens)) >= 2:
                    hit += 2
                if len(num_tokens) >= 8:
                    hit += 2
                hit += sum(1 for k in key_rows if k in c)

                if (not has_item) and (not has_kw):
                    return 0
                return hit

            def pick_best(pages: List[int]) -> Tuple[Optional[int], int]:
                bi = None
                bs = -1
                for i in pages:
                    t = ocr_page_text_locate(pdf_path, i) or ""
                    c = normalize_compact(t)
                    if not c:
                        continue
                    h = score_hit(c)
                    if h > bs:
                        bs = h
                        bi = i
                return bi, bs

            best_i, best_hit_score = pick_best(sample_pages)

            if best_i is not None and best_hit_score >= 3:
                extract_pages = [best_i]
                if best_i + 1 < n:
                    extract_pages.append(best_i + 1)
                elif best_i - 1 >= 0:
                    extract_pages.append(best_i - 1)
                combined_parts = []
                for pi in extract_pages[:2]:
                    combined_parts.append(ocr_page_text(pdf_path, pi) or "")
                combined_text = "\n".join([p for p in combined_parts if p])
                body_text = combined_text
                mult, unit = _detect_unit_near_debt_table(body_text)
                if unit:
                    unit_seen = True
                periods = _parse_period_groups_from_text(body_text) or _parse_period_groups_from_split_lines(body_text)
                if not periods:
                    body_text = _slice_debt_table_text(combined_text)
                    mult, unit = _detect_unit_near_debt_table(body_text)
                    if unit:
                        unit_seen = True
                    periods = _parse_period_groups_from_text(body_text) or _parse_period_groups_from_split_lines(body_text)
                if periods:
                    dby, _u, _n = _parse_text_table_with_periods(body_text, body_text, periods, default_year, mult, unit or None)
                    if dby:
                        best_data_by_year = dby
                        best_used = (best_i + 1, "ocr_text_table")
                        best_score = score_candidate(dby)
                        for ln in combined_text.splitlines():
                            if any(k in ln for k in _TABLE_KEYWORDS):
                                ocr_trace_text = ln.strip()
                                break
                        if not ocr_trace_text:
                            ocr_trace_text = (combined_text.splitlines() or [""])[0].strip()
                        for ln in combined_text.splitlines():
                            if "单位" in ln:
                                ocr_unit_text = ln.strip()
                                break
            elif not notes:
                notes = "扫描件：采样OCR未命中目标表格"

        for i in candidate_pages:
            page = pdf.pages[i]
            text = page.extract_text() or ""
            if (not text) and ocr_enabled and ocr_page_text:
                text = ocr_page_text(pdf_path, i) or ""
            slice_text = _slice_debt_table_text(text)
            mult, unit = _detect_unit_near_debt_table(slice_text)
            if unit:
                unit_seen = True

            candidates = extract_candidate_tables(page)
            best = _best_table_from_candidates(candidates)
            if not best:
                periods = _parse_period_groups_from_text(slice_text) or _parse_period_groups_from_split_lines(slice_text)
                if (not periods) and i + 1 < len(pdf.pages):
                    next_text = pdf.pages[i + 1].extract_text() or ""
                    if (not next_text) and ocr_enabled and ocr_page_text:
                        next_text = ocr_page_text(pdf_path, i + 1) or ""
                    combined0 = _slice_debt_table_text(text + "\n" + next_text)
                    periods = _parse_period_groups_from_text(combined0) or _parse_period_groups_from_split_lines(combined0)
                dby, _u, _n = ({}, None, "")
                if periods:
                    dby, _u, _n = _parse_text_table_with_periods(slice_text, slice_text, periods, default_year, mult, unit or None)
                if periods and i + 1 < len(pdf.pages):
                    next_text = pdf.pages[i + 1].extract_text() or ""
                    if (not next_text) and ocr_enabled and ocr_page_text:
                        next_text = ocr_page_text(pdf_path, i + 1) or ""
                    combined_text = _slice_debt_table_text(text + "\n" + next_text)
                    mult2, unit2 = _detect_unit_near_debt_table(combined_text)
                    mult_use, unit_use = (mult2, unit2) if unit2 else (mult, unit)
                    if unit_use:
                        unit_seen = True
                    dby2, _u2, _n2 = _parse_text_table_with_periods(combined_text, combined_text, periods, default_year, mult_use, unit_use or None)
                    if dby2:
                        if score_candidate(dby2) >= score_candidate(dby):
                            dby = dby2
                if not dby:
                    continue
                sc = score_candidate(dby)
                if sc > best_score:
                    best_score = sc
                    best_data_by_year = dby
                    best_used = (i + 1, "text_table")
                continue

            table = best.table
            if i + 1 < len(pdf.pages):
                next_candidates = extract_candidate_tables(pdf.pages[i + 1])
                next_best = _best_table_from_candidates(next_candidates)
                if next_best:
                    merged = _merge_continuation(table, next_best.table)
                    if merged:
                        table = merged

            year_cols = _table_year_columns(table)
            if not year_cols:
                continue

            if used is None:
                used = (i + 1, best.parser)

            for y in year_cols.keys():
                if y not in data_by_year:
                    data_by_year[y] = {k: _cell(None, None, "missing") for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]}

            for r in table[1:]:
                if not r or len(r) < 2:
                    continue
                label = r[0] if len(r) else ""
                mapped = _map_row_label(label)
                if not mapped:
                    continue

                for y, col_idx in year_cols.items():
                    if col_idx >= len(r):
                        continue
                    val_cell = r[col_idx]
                    num, raw_err = parse_number(val_cell)
                    if raw_err is not None:
                        data_by_year[y][mapped] = _cell(None, str(val_cell), "invalid")
                        continue
                    if num is None:
                        data_by_year[y][mapped] = _cell(None, None, "missing")
                        continue
                    if unit:
                        if (data_by_year[y].get(mapped) or {}).get("flag") != "ok":
                            data_by_year[y][mapped] = _cell(round(num * mult, 2), None, "ok")
                    else:
                        if (data_by_year[y].get(mapped) or {}).get("flag") != "ok":
                            data_by_year[y][mapped] = _cell(None, str(val_cell), "unit_unknown")
            sc = score_candidate(data_by_year)
            if sc > best_score:
                best_score = sc
                best_data_by_year = {y: {k: v for k, v in yd.items()} for y, yd in data_by_year.items()}
                best_used = (i + 1, best.parser)
            data_by_year = {}

        if best_data_by_year:
            data_by_year = best_data_by_year
            used = best_used

        if 2024 in data_by_year:
            data_by_year = {2024: data_by_year[2024]}
        elif data_by_year:
            data_by_year = {2024: {k: _cell(None, None, "missing") for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]}}
            if not notes:
                notes = "未找到2024年度列"

        for y, yd in data_by_year.items():
            if y == 0:
                continue
            tv = (yd.get("total") or {}).get("value")
            if not isinstance(tv, (int, float)):
                continue
            tvf = float(tv)
            sum_known = 0.0
            missing = []
            for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other"]:
                cell = yd.get(k) or {}
                if (cell.get("flag") or "missing") == "ok" and isinstance(cell.get("value"), (int, float)):
                    sum_known += float(cell["value"])
                else:
                    missing.append(k)
            if not missing:
                continue
            if abs(sum_known - tvf) <= 0.05:
                for k in missing:
                    yd[k] = _cell(0.0, None, "ok")
                continue
            if len(missing) == 1 and tvf >= sum_known - 0.05:
                diff = round(tvf - sum_known, 2)
                if diff >= 0:
                    yd[missing[0]] = _cell(diff, None, "ok")

        parsed.data_by_year = {y: {k: v for k, v in yd.items()} for y, yd in data_by_year.items()}
        if not notes and not unit_seen:
            notes = "单位未知"
        parsed.notes = notes

        missing_fields: List[str] = []
        for y, yd in data_by_year.items():
            for f, cell in yd.items():
                if (cell.get("flag") or "missing") in {"missing", "invalid", "unit_unknown"}:
                    missing_fields.append(f"{y}.{f}")
        parsed.missing_fields = missing_fields

        if not parsed.data_by_year:
            y = default_year if default_year is not None else 0
            parsed.data_by_year = {y: {k: _cell(None, None, "missing") for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]}}
            parsed.missing_fields = [f"{y}.{k}" for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]]
            if not parsed.notes:
                parsed.notes = "未找到目标表格"
        else:
            try:
                if used:
                    pno, parser = used
                    page = pdf.pages[pno - 1]
                    parsed.data_by_year[0] = parsed.data_by_year.get(0, {})
                    if parser == "ocr_text_table":
                        parsed.data_by_year[0]["__trace__"] = {
                            "task": {"page": pno, "bbox": None, "text": ocr_trace_text, "parser": parser},
                            "unit": {"page": pno, "bbox": None, "text": ocr_unit_text, "parser": parser},
                        }
                    else:
                        parsed.data_by_year[0]["__trace__"] = {
                            "task": trace_from_page(page, _TABLE_KEYWORDS, pno, parser),
                            "unit": trace_from_page(page, ["单位：", "单位:"], pno, parser),
                        }
            except Exception:
                pass

    return parsed

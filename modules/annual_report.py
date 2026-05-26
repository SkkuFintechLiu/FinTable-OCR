import re
from typing import Any, Dict, List, Optional, Tuple

from modules.parse_types import AnnualReportParsed
from modules.table_extractor import extract_candidate_tables, pick_best_table
from modules.trace import trace_from_page
from modules.utils import detect_unit_multiplier_to_yi, normalize_compact, parse_number, safe_year_from_text


def _cell(value: Any = None, raw: Optional[str] = None, flag: str = "missing") -> Dict[str, Any]:
    return {"value": value, "raw": raw, "flag": flag}


def _find_directory_start_page(pdf) -> Optional[int]:
    for i in range(min(12, len(pdf.pages))):
        text = pdf.pages[i].extract_text() or ""
        if "目录" in text:
            compact = normalize_compact(text)
            m = re.search(r"六、负债情况.{0,20}?(\d{1,4})", compact)
            if m:
                p = int(m.group(1)) - 1
                if p >= 0:
                    return p
    return None


def _extract_tables(page) -> List[List[List[str]]]:
    candidates = extract_candidate_tables(page)
    best = pick_best_table(candidates, must_include=["有息", "合计"])
    return [best.table] if best else []


def _pick_table(tables: List[List[List[str]]]) -> Optional[List[List[str]]]:
    for t in tables:
        if not t or not t[0]:
            continue
        header = normalize_compact("".join([c or "" for c in t[0]]))
        if "有息债务" in header and ("金额合计" in header or "占比" in header):
            return t
    for t in tables:
        if not t or not t[0]:
            continue
        flat = normalize_compact("\n".join([" ".join([c or "" for c in r]) for r in t[:3]]))
        if "有息债务结构情况" in flat and "金额合计" in flat:
            return t
    return None


def _header_map(header_row: List[str]) -> Dict[str, int]:
    idx = {}
    for i, c in enumerate(header_row):
        v = normalize_compact(c or "")
        if not v:
            continue
        if "金额合计" in v or v == "合计":
            idx["total"] = i
        if "1年以内" in v or "一年以内" in v:
            idx["short"] = i
    return idx


def _row_key(label: str) -> Optional[str]:
    v = normalize_compact(label or "")
    if not v:
        return None
    v = v.strip("-")
    if v == "公司信用类":
        return "credit_bonds"
    if "公司信用类债券" in v or "信用类债券" in v or ("信用类" in v and "债券" in v):
        return "credit_bonds"
    if "银行贷款" in v or "银行借款" in v:
        return "bank_loans"
    if "非银行金融机构贷款" in v or "非银" in v:
        return "non_bank_loans"
    if v in {"其他有息债", "其他有息债务", "其他融资"}:
        return "other"
    if "其他有息债务" in v or "其他融资" in v or "其他" == v:
        return "other"
    if v in {"合计", "总计"}:
        return "total"
    return None


def _is_numeric_line(line: str) -> bool:
    parts = [p for p in (line or "").replace("—", "-").replace("–", "-").split() if p]
    if len(parts) < 3:
        return False
    nums = 0
    for p in parts:
        n, err = parse_number(p)
        if err is None and n is not None:
            nums += 1
    return nums >= 3


def _parse_text_table_for_section(text: str, section_title: str) -> Optional[Dict[str, Dict[str, Any]]]:
    t = text or ""
    lines = [ln.strip() for ln in t.splitlines() if (ln or "").strip()]
    st = normalize_compact(section_title or "")
    idx = -1
    for i, ln in enumerate(lines):
        if st and st in normalize_compact(ln):
            idx = i
            break
    if idx < 0:
        keys: List[str] = []
        if "合并口径" in section_title and "有息债务结构情况" in section_title:
            keys = ["合并口径", "有息债务结构情况"]
        elif "有息债务结构情况" in section_title:
            keys = ["有息债务结构情况"]
        elif "到期时间" in section_title:
            keys = ["到期时间"]
        if keys:
            for i, ln in enumerate(lines):
                c = normalize_compact(ln)
                if all(k in c for k in keys):
                    idx = i
                    break
    if idx < 0:
        return None

    data: Dict[str, Dict[str, Any]] = {k: _cell(None, None, "missing") for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]}
    is_maturity = section_title == "到期时间"
    if not is_maturity:
        for j in range(idx, min(idx + 12, len(lines))):
            if "到期时间" in normalize_compact(lines[j]):
                is_maturity = True
                break
    maturity_within_parts = 1
    if is_maturity:
        win = normalize_compact("\n".join(lines[idx : min(idx + 18, len(lines))]))
        if ("个月以内" in win or "6个月" in win) and ("至1年" in win or "超过1年" in win):
            maturity_within_parts = 2

    def split_label_nums(line: str) -> Tuple[str, List[str]]:
        parts = [p for p in (line or "").replace("—", "-").replace("–", "-").split() if p]
        nums: List[str] = []
        keep: List[str] = []
        for p in parts:
            if is_maturity and len(p) > 1 and p[-1] in {"-", "—", "–"}:
                p2 = p[:-1]
                n2, e2 = parse_number(p2)
                if e2 is None and n2 is not None:
                    nums.append(p2)
                    continue
            if p in {"-", "--"}:
                if is_maturity:
                    nums.append("0")
                continue
            if p in {"/"}:
                if is_maturity:
                    nums.append("0")
                continue
            if "%" in p or "％" in p:
                continue
            n, err = parse_number(p)
            if err is None and n is not None:
                nums.append(p)
            else:
                keep.append(p)
        if not parts:
            txt = (line or "").strip()
            if txt:
                if "%" not in txt and "％" not in txt:
                    m = re.findall(r"[-+]?\d+(?:\.\d+)?", txt)
                    for mm in m:
                        nums.append(mm)
                    txt2 = re.sub(r"[-+]?\d+(?:\.\d+)?", "", txt)
                    return txt2.strip(), nums
        return "".join(keep).strip(), nums

    def parse_row(label: str, nums: List[str]) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        key = _row_key(label)
        if not key:
            return None, None, None
        if is_maturity:
            fs: List[float] = []
            for t in nums:
                n, e = parse_number(t)
                if e is None and n is not None:
                    fs.append(float(n))

            candidates: List[Tuple[float, float, float]] = []
            if len(fs) >= 5:
                within_m = None
                total_m = None
                if maturity_within_parts >= 2 and len(fs) >= maturity_within_parts + 3:
                    within_m = sum(fs[1 : 1 + maturity_within_parts])
                    total_m = fs[maturity_within_parts + 2]
                    score_m = abs(sum(fs[1 : maturity_within_parts + 2]) - total_m)
                    candidates.append((score_m, total_m, within_m))
                within = fs[1]
                total = fs[3]
                score = abs((fs[0] + fs[1] + fs[2]) - fs[3])
                candidates.append((score, total, within))
            if len(fs) == 4:
                within = fs[0]
                total = fs[2]
                score = abs((fs[0] + fs[1]) - fs[2])
                candidates.append((score, total, within))

                within2 = fs[1]
                total2 = fs[3]
                score2 = abs((fs[0] + fs[1] + fs[2]) - fs[3])
                candidates.append((score2, total2, within2))
            if len(fs) == 2:
                if abs(fs[0] - fs[1]) <= 0.01:
                    candidates.append((0.0, fs[0], 0.0))
            if len(fs) == 3:
                if fs[2] <= 100.0 and abs(fs[0] - fs[1]) <= 0.01:
                    candidates.append((0.0, fs[0], 0.0))
                else:
                    within = fs[0]
                    total = fs[2]
                    score = abs((fs[0] + fs[1]) - fs[2])
                    candidates.append((score, total, within))

            if not candidates:
                return key, None, None

            candidates.sort(key=lambda x: x[0])
            _, total_v, within_v = candidates[0]
            return key, total_v, within_v

        total = None
        within = None
        if len(nums) >= 4:
            total = nums[-1]
            within = nums[1] if len(nums) > 1 else None
        elif len(nums) >= 3:
            total = nums[-1]
            within = nums[0]
        elif len(nums) >= 2:
            total = nums[-1]
            within = nums[0]
        total_num, total_err = parse_number(total)
        st_num, st_err = parse_number(within)
        return key, (None if total_err is not None else total_num), (None if st_err is not None else st_num)

    label_parts: List[str] = []
    nums: List[str] = []
    recent_label_line = ""
    i = idx + 1
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("注：") or ln.startswith("注:"):
            break

        compact = normalize_compact(ln)
        if "到期时间" in compact or "金额合计" in compact or "金额占有息" in compact or compact == "有息债务类" or compact == "有息债务类别":
            label_parts = []
            nums = []
            i += 1
            continue

        if "别已逾期" in compact or "债务的占比" in compact or compact.startswith("有息债务类") or compact.startswith("有息债务类别"):
            label_parts = []
            nums = []
            i += 1
            continue

        lab, nms = split_label_nums(ln)
        if lab and len(lab) <= 40:
            label_parts.append(lab)
            recent_label_line = lab
        if nms:
            nums.extend(nms)
            cands: List[str] = []
            if lab:
                cands.append(lab)
            if label_parts:
                cands.append("".join(label_parts))
            direct_label = next((c for c in cands if _row_key(c)), "")
            dk = _row_key(direct_label)
            if (not dk) and label_parts and i + 1 < len(lines):
                lab2, nms2 = split_label_nums(lines[i + 1])
                if lab2 and not nms2 and len(lab2) <= 10:
                    combined = "".join(label_parts + [lab2])
                    if _row_key(combined):
                        dk = _row_key(combined)
                        direct_label = combined
                        i += 1

            if dk and len(nms) >= 2:
                k, total_num, st_num = parse_row(direct_label, nms)
                if k:
                    data_key = "total" if k == "total" else k
                    if total_num is None:
                        data[data_key] = _cell(None, None, "missing")
                    else:
                        data[data_key] = _cell(total_num, None, "ok")
                    if k == "total":
                        if st_num is None:
                            data["short_term"] = _cell(None, None, "missing")
                        else:
                            data["short_term"] = _cell(st_num, None, "ok")
                        break
                    label_parts = []
                    nums = []
                    i += 1
                    continue

        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        nxt_lab, nxt_nms = split_label_nums(nxt) if nxt else ("", [])
        should_finalize = False
        if len(nums) >= 4:
            should_finalize = True
            if len(nums) == 4 and nxt_nms and len(nxt_nms) == 3 and nxt_lab:
                should_finalize = True
            elif nxt_nms:
                should_finalize = False
            if nxt.startswith("注") or "境外债券情况" in nxt:
                should_finalize = True

        if should_finalize:
            label = "".join(label_parts).strip()
            if not label_parts and recent_label_line:
                label = recent_label_line
            if len(nums) == 4 and nxt_nms and len(nxt_nms) == 3 and nxt_lab:
                label += nxt_lab
                nums.extend(nxt_nms)
                i += 1
            else:
                if nxt_lab and not nxt_nms and len(nxt_lab) <= 10 and nxt and not nxt.startswith("注"):
                    if not any(k in label for k in ["银行", "合计", "贷款", "债务", "债券"]):
                        label += nxt_lab
                        i += 1


            rows: List[Tuple[str, Optional[float], Optional[float]]] = []
            k, total_num, st_num = parse_row(label, nums)
            if k:
                rows.append((k, total_num, st_num))
            if not k and label_parts:
                k2, total2, st2 = parse_row(label_parts[-1], nums[:4])
                if k2:
                    rows.append((k2, total2, st2))
            if len(nums) >= 8 and label_parts:
                k3, total3, st3 = parse_row(label_parts[-1], nums[4:8])
                if k3:
                    rows.append((k3, total3, st3))

            for kx, total_v, st_v in rows:
                data_key = "total" if kx == "total" else kx
                if total_v is None:
                    data[data_key] = _cell(None, None, "missing")
                else:
                    data[data_key] = _cell(total_v, None, "ok")
                if kx == "total":
                    if st_v is None:
                        data["short_term"] = _cell(None, None, "missing")
                    else:
                        data["short_term"] = _cell(st_v, None, "ok")
                    return data

            label_parts = []
            nums = []

        i += 1

    if all((v.get("flag") or "missing") == "missing" for v in data.values()):
        return None
    return data


def parse_annual_report(pdf_path: str, fallback_year: Optional[int] = None) -> AnnualReportParsed:
    import pdfplumber

    parsed = AnnualReportParsed()

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

        struct_head_text = ""
        for i in range(min(3, len(pdf.pages))):
            struct_head_text += "\n" + (pdf.pages[i].extract_text() or "")
        scanned_pdf = not normalize_compact(struct_head_text)

        start = None if scanned_pdf else _find_directory_start_page(pdf)
        if scanned_pdf:
            n = len(pdf.pages)
            head = list(range(min(20, n)))
            tail = list(range(max(0, n - 10), n))
            scan_pages = sorted(set(head + tail))
        else:
            scan_pages = list(range(start, min(start + 40, len(pdf.pages)))) if start is not None else list(range(len(pdf.pages)))

        head_text = ""
        for i in range(min(3, len(pdf.pages))):
            t = pdf.pages[i].extract_text() or ""
            if (not t) and ocr_enabled and ocr_page_text_locate:
                t = ocr_page_text_locate(pdf_path, i) or ""
            head_text += "\n" + t
        parsed.year = fallback_year or safe_year_from_text(head_text)

        found = False
        notes = ""
        used_parser = ""
        used_page = None
        used_title = ""
        fill_parser = ""
        fill_page = None
        ocr_trace_text = ""
        ocr_unit_text = ""

        def try_title(title: str) -> bool:
            nonlocal found, notes, used_parser, used_page, used_title, ocr_trace_text, ocr_unit_text

            def ok_score(cells: Dict[str, Dict[str, Any]]) -> Tuple[int, int, int]:
                ok = 0
                for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]:
                    v = (cells or {}).get(k) or {}
                    if isinstance(v, dict) and (v.get("flag") or "missing") == "ok":
                        ok += 1
                tot_ok = 1 if ((cells or {}).get("total") or {}).get("flag") == "ok" else 0
                st_ok = 1 if ((cells or {}).get("short_term") or {}).get("flag") == "ok" else 0
                return ok, tot_ok, st_ok

            def apply_unit(cells: Dict[str, Dict[str, Any]], mult: float, unit: str) -> None:
                for _, cell in (cells or {}).items():
                    if not isinstance(cell, dict):
                        continue
                    if cell.get("flag") == "ok" and cell.get("value") is not None:
                        if unit:
                            cell["value"] = round(float(cell["value"]) * mult, 2)
                        else:
                            cell["flag"] = "unit_unknown"

            def slice_by_title(src_text: str, title_text: str) -> str:
                ls = (src_text or "").splitlines()
                idx = -1
                for k, ln in enumerate(ls):
                    if title_text in (ln or ""):
                        idx = k
                        break
                if idx < 0:
                    return src_text
                start = max(0, idx - 8)
                stop = len(ls)
                for j in range(idx + 1, len(ls)):
                    lj = (ls[j] or "").strip()
                    if (re.match(r"^\s*\d{1,3}\s*[\.、](?!\d)", lj) and ("债务" in lj or "到期时间" in lj)) and (title_text not in lj):
                        stop = j
                        break
                return "\n".join(ls[start:stop])

            for i in scan_pages:
                page = pdf.pages[i]
                text = page.extract_text() or ""
                scan_text = text
                if (not scan_text) and ocr_enabled and ocr_page_text_locate:
                    scan_text = ocr_page_text_locate(pdf_path, i) or ""
                compact = normalize_compact(scan_text)
                if title not in compact:
                    continue

                used_ocr = False
                if (not text) and ocr_enabled and ocr_page_text:
                    text = ocr_page_text(pdf_path, i) or scan_text
                    used_ocr = True
                    if not ocr_trace_text:
                        for ln in text.splitlines():
                            if title in ln:
                                ocr_trace_text = ln.strip()
                                break
                    if not ocr_unit_text:
                        for ln in text.splitlines():
                            if "单位" in ln:
                                ocr_unit_text = ln.strip()
                                break

                section_text = slice_by_title(text, title)
                mult, unit = detect_unit_multiplier_to_yi(section_text)
                if not unit and not notes:
                    notes = "单位未知"

                def title_top_y(title_text: str) -> Optional[float]:
                    try:
                        hits = page.search(title_text) or []
                    except Exception:
                        hits = []
                    ys: List[float] = []
                    for h in hits:
                        if isinstance(h, dict):
                            if isinstance(h.get("top"), (int, float)):
                                ys.append(float(h["top"]))
                            elif isinstance(h.get("y0"), (int, float)):
                                ys.append(float(h["y0"]))
                    return min(ys) if ys else None

                candidates = extract_candidate_tables(page)
                if title == "发行人合并口径有息债务结构情况" and ("发行人债务结构情况" in compact):
                    ty = title_top_y(title)
                    if ty is not None:
                        filtered = []
                        for c in candidates:
                            bb = c.bbox
                            if bb and len(bb) == 4 and isinstance(bb[1], (int, float)):
                                if float(bb[1]) >= float(ty) + 4.0:
                                    filtered.append(c)
                            else:
                                filtered.append(c)
                        if filtered:
                            candidates = filtered

                best = pick_best_table(candidates, must_include=["有息", "合计"]) if candidates else None
                table = _pick_table([best.table]) if best else None

                if not table:
                    text_data = _parse_text_table_for_section(section_text, title)
                    if (not text_data) and title != "到期时间" and ("到期时间" in normalize_compact(section_text)):
                        text_data = _parse_text_table_for_section(section_text, "到期时间")
                    if (not text_data) and title != "到期时间" and i + 1 < len(pdf.pages):
                        next_text = pdf.pages[i + 1].extract_text() or ""
                        if (not next_text) and ocr_enabled and ocr_page_text:
                            next_text = ocr_page_text(pdf_path, i + 1) or ""
                        if next_text.strip():
                            fake = "到期时间\n" + next_text
                            text_data = _parse_text_table_for_section(fake, "到期时间")
                    if not text_data:
                        continue
                    if i + 1 < len(pdf.pages):
                        next_text = pdf.pages[i + 1].extract_text() or ""
                        if (not next_text) and ocr_enabled and ocr_page_text:
                            next_text = ocr_page_text(pdf_path, i + 1) or ""
                        if (not unit) and next_text.strip():
                            mult2, unit2 = detect_unit_multiplier_to_yi(section_text + "\n" + next_text)
                            if unit2:
                                mult, unit = mult2, unit2
                                if notes == "单位未知":
                                    notes = ""
                        need_more = ((text_data.get("total") or {}).get("flag") != "ok") or ((text_data.get("short_term") or {}).get("flag") != "ok")
                        if need_more and next_text.strip():
                            combined = section_text + "\n" + next_text
                            text_data2 = _parse_text_table_for_section(combined, title)
                            if (not text_data2) and title != "到期时间" and ("到期时间" in normalize_compact(combined)):
                                text_data2 = _parse_text_table_for_section(combined, "到期时间")
                            if text_data2 and ok_score(text_data2) > ok_score(text_data):
                                text_data = text_data2
                    used_parser = "ocr_text_table" if used_ocr else "text_table"
                    used_page = i + 1
                    apply_unit(text_data, mult, unit)
                    parsed.data = text_data
                    missing_fields: List[str] = []
                    for f, cell in parsed.data.items():
                        if (cell.get("flag") or "missing") in {"missing", "invalid", "unit_unknown"}:
                            missing_fields.append(f"{parsed.year or ''}.{f}".strip("."))
                    parsed.missing_fields = missing_fields
                    parsed.notes = notes
                    used_title = title
                    found = True
                    return True

                used_parser = "pdfplumber_table"
                used_page = i + 1
                if title == "到期时间":
                    def maturity_extract(tokens: List[str]) -> Tuple[Optional[float], Optional[float]]:
                        fs: List[float] = []
                        for t in tokens:
                            if t in {"/", "-", "—", "–"}:
                                fs.append(0.0)
                                continue
                            n, e = parse_number(t)
                            if e is None and n is not None:
                                fs.append(float(n))
                        candidates: List[Tuple[float, float, float]] = []
                        if len(fs) >= 5:
                            within = fs[1]
                            total = fs[3]
                            score = abs((fs[0] + fs[1] + fs[2]) - fs[3])
                            candidates.append((score, total, within))
                        if len(fs) == 4:
                            within = fs[0]
                            total = fs[2]
                            score = abs((fs[0] + fs[1]) - fs[2])
                            candidates.append((score, total, within))

                            within2 = fs[1]
                            total2 = fs[3]
                            score2 = abs((fs[0] + fs[1] + fs[2]) - fs[3])
                            candidates.append((score2, total2, within2))
                        if len(fs) == 3:
                            within = fs[0]
                            total = fs[2]
                            score = abs((fs[0] + fs[1]) - fs[2])
                            candidates.append((score, total, within))
                        if not candidates:
                            return None, None
                        candidates.sort(key=lambda x: x[0])
                        _, total_v, within_v = candidates[0]
                        return total_v, within_v

                    data: Dict[str, Dict[str, Any]] = {k: _cell(None, None, "missing") for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]}
                    for r in table[1:]:
                        if not r:
                            continue
                        c0 = r[0] if len(r) > 0 else ""
                        key = _row_key(c0)
                        if not key and len(r) > 1:
                            key = _row_key((c0 or "") + (r[1] or ""))
                        if not key:
                            continue

                        toks: List[str] = []
                        for c in r[1:]:
                            if c is None:
                                continue
                            for part in str(c).replace("—", "-").replace("–", "-").split():
                                if part:
                                    toks.append(part)
                        total_v, within_v = maturity_extract(toks)
                        data_key = "total" if key == "total" else key
                        if total_v is None:
                            data[data_key] = _cell(None, None, "missing")
                        else:
                            if unit:
                                data[data_key] = _cell(round(float(total_v) * mult, 2), None, "ok")
                            else:
                                data[data_key] = _cell(None, None, "unit_unknown")

                        if key == "total":
                            if within_v is None:
                                data["short_term"] = _cell(None, None, "missing")
                            else:
                                if unit:
                                    data["short_term"] = _cell(round(float(within_v) * mult, 2), None, "ok")
                                else:
                                    data["short_term"] = _cell(None, None, "unit_unknown")

                    missing_fields: List[str] = []
                    for f, cell in data.items():
                        if (cell.get("flag") or "missing") in {"missing", "invalid", "unit_unknown"}:
                            missing_fields.append(f"{parsed.year or ''}.{f}".strip("."))

                    parsed.data = data
                    parsed.missing_fields = missing_fields
                    parsed.notes = notes
                    used_title = title
                    found = True
                    return True

                header_row = table[0]
                hm = _header_map(header_row)
                if "total" not in hm:
                    for r in table[:2]:
                        hm2 = _header_map(r)
                        if "total" in hm2:
                            hm = hm2
                            break

                total_col = hm.get("total")
                short_col = hm.get("short")
                if total_col is None:
                    continue

                data: Dict[str, Dict[str, Any]] = {k: _cell(None, None, "missing") for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]}
                for r in table[1:]:
                    if not r:
                        continue
                    c0 = r[0] if len(r) > 0 else ""
                    key = _row_key(c0)
                    if not key and len(r) > 1:
                        key = _row_key((c0 or "") + (r[1] or ""))
                    if not key:
                        continue

                    val_cell = r[total_col] if total_col < len(r) else None
                    if total_col >= len(r):
                        for c in reversed(r):
                            if not c or "%" in str(c) or "％" in str(c):
                                continue
                            n, e = parse_number(c)
                            if e is None and n is not None:
                                val_cell = c
                                break
                    num, raw_err = parse_number(val_cell)
                    if raw_err is not None:
                        data_key = "total" if key == "total" else key
                        data[data_key] = _cell(None, str(val_cell), "invalid")
                    else:
                        if num is None:
                            data_key = "total" if key == "total" else key
                            data[data_key] = _cell(None, None, "missing")
                        else:
                            if unit:
                                data_key = "total" if key == "total" else key
                                data[data_key] = _cell(round(num * mult, 2), None, "ok")
                            else:
                                data_key = "total" if key == "total" else key
                                data[data_key] = _cell(None, str(val_cell), "unit_unknown")

                    if key == "total" and short_col is not None:
                        st_cell = r[short_col] if short_col < len(r) else None
                        if short_col >= len(r):
                            for c in r:
                                if not c or "%" in str(c) or "％" in str(c):
                                    continue
                                n, e = parse_number(c)
                                if e is None and n is not None:
                                    st_cell = c
                                    break
                        st_num, st_raw_err = parse_number(st_cell)
                        if st_raw_err is not None:
                            data["short_term"] = _cell(None, str(st_cell), "invalid")
                        else:
                            if st_num is None:
                                data["short_term"] = _cell(None, None, "missing")
                            else:
                                if unit:
                                    data["short_term"] = _cell(round(st_num * mult, 2), None, "ok")
                                else:
                                    data["short_term"] = _cell(None, str(st_cell), "unit_unknown")

                if i + 1 < len(pdf.pages):
                    need_more = ((data.get("total") or {}).get("flag") != "ok") or ((data.get("short_term") or {}).get("flag") != "ok")
                    if need_more:
                        next_text = pdf.pages[i + 1].extract_text() or ""
                        if (not next_text) and ocr_enabled and ocr_page_text:
                            next_text = ocr_page_text(pdf_path, i + 1) or ""
                        if next_text.strip():
                            combined = text + "\n" + next_text
                            text_data = _parse_text_table_for_section(combined, title)
                            if text_data and ok_score(text_data) > ok_score(data):
                                apply_unit(text_data, mult, unit)
                                data = text_data

                missing_fields: List[str] = []
                for f, cell in data.items():
                    if (cell.get("flag") or "missing") in {"missing", "invalid", "unit_unknown"}:
                        missing_fields.append(f"{parsed.year or ''}.{f}".strip("."))

                parsed.data = data
                parsed.missing_fields = missing_fields
                parsed.notes = notes
                used_title = title
                found = True
                return True
            return False

        if not found:
            try_title("发行人合并口径有息债务结构情况")
        if not found:
            try_title("有息债务结构情况")
        if not found:
            try_title("到期时间")
        if not found and start is not None:
            scan_pages = list(range(len(pdf.pages)))
            try_title("发行人合并口径有息债务结构情况")
            if not found:
                try_title("有息债务结构情况")
            if not found:
                try_title("到期时间")

        if found and used_title != "到期时间":
            missing_keys = []
            for k in ["credit_bonds", "non_bank_loans", "other"]:
                v = (parsed.data or {}).get(k) or {}
                if (v.get("flag") or "missing") in {"missing", "invalid", "unit_unknown"}:
                    missing_keys.append(k)

            if missing_keys:
                for i in scan_pages:
                    page = pdf.pages[i]
                    text = page.extract_text() or ""
                    if (not text) and ocr_enabled and ocr_page_text:
                        text = ocr_page_text(pdf_path, i) or ""
                    compact = normalize_compact(text)
                    if "到期时间" not in compact:
                        continue
                    if "有息" not in compact:
                        continue

                    mult, unit = detect_unit_multiplier_to_yi(text)
                    tables = _extract_tables(page)
                    table = _pick_table(tables) if tables else None

                    cand = None
                    cand_parser = ""
                    if table:
                        text_data = _parse_text_table_for_section(text, "到期时间")
                        if text_data:
                            apply_unit(text_data, mult, unit)
                            cand = text_data
                            cand_parser = "text_table"

                    if not cand:
                        text_data = _parse_text_table_for_section(text, "到期时间")
                        if text_data:
                            def apply_unit(cells: Dict[str, Dict[str, Any]], mult: float, unit: str) -> None:
                                for _, cell in (cells or {}).items():
                                    if not isinstance(cell, dict):
                                        continue
                                    if cell.get("flag") == "ok" and cell.get("value") is not None:
                                        if unit:
                                            cell["value"] = round(float(cell["value"]) * mult, 2)
                                        else:
                                            cell["flag"] = "unit_unknown"

                            apply_unit(text_data, mult, unit)
                            cand = text_data
                            cand_parser = "text_table"

                    if cand:
                        for k in missing_keys:
                            v2 = (cand or {}).get(k) or {}
                            if (v2.get("flag") or "missing") == "ok":
                                parsed.data[k] = v2
                        fill_parser = cand_parser
                        fill_page = i + 1
                        break

                missing_fields: List[str] = []
                for f, cell in (parsed.data or {}).items():
                    if not isinstance(cell, dict):
                        continue
                    if (cell.get("flag") or "missing") in {"missing", "invalid", "unit_unknown"}:
                        missing_fields.append(f"{parsed.year or ''}.{f}".strip("."))
                parsed.missing_fields = missing_fields

        if found:
            tv = ((parsed.data or {}).get("total") or {}).get("value")
            if isinstance(tv, (int, float)):
                missing_types = []
                sum_known = 0.0
                for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other"]:
                    cell = (parsed.data or {}).get(k) or {}
                    if (cell.get("flag") or "missing") == "ok" and isinstance(cell.get("value"), (int, float)):
                        sum_known += float(cell["value"])
                    else:
                        missing_types.append(k)
                if missing_types and abs(sum_known - float(tv)) <= 0.01:
                    for k in missing_types:
                        parsed.data[k] = _cell(0.0, None, "ok")
                    missing_fields = []
                    for f, cell in (parsed.data or {}).items():
                        if not isinstance(cell, dict):
                            continue
                        if (cell.get("flag") or "missing") in {"missing", "invalid", "unit_unknown"}:
                            missing_fields.append(f"{parsed.year or ''}.{f}".strip("."))
                    parsed.missing_fields = missing_fields

        if not found:
            parsed.data = {k: _cell(None, None, "missing") for k in ["credit_bonds", "bank_loans", "non_bank_loans", "other", "total", "short_term"]}
            parsed.missing_fields = [f"{parsed.year or ''}.{k}".strip(".") for k in parsed.data.keys()]
            parsed.notes = "未找到目标表格"
            parsed.data["__trace__"] = {
                "scan_pages": len(scan_pages),
                "start_from_directory": (start + 1) if start is not None else None,
                "titles_tried": ["发行人合并口径有息债务结构情况", "有息债务结构情况", "到期时间"],
            }
        else:
            try:
                if used_page is not None:
                    tpage = pdf.pages[used_page - 1]
                    if used_parser == "ocr_text_table":
                        parsed.data["__trace__"] = {
                            "task": {"page": used_page, "bbox": None, "text": ocr_trace_text, "parser": used_parser},
                            "unit": {"page": used_page, "bbox": None, "text": ocr_unit_text, "parser": used_parser},
                        }
                    else:
                        parsed.data["__trace__"] = {
                            "task": trace_from_page(tpage, [used_title] if used_title else ["有息债务结构情况", "发行人合并口径有息债务结构情况"], used_page, used_parser),
                            "unit": trace_from_page(tpage, ["单位：", "单位:"], used_page, used_parser),
                        }
                    if fill_page is not None and fill_parser:
                        fpage = pdf.pages[fill_page - 1]
                        parsed.data["__trace__"]["task_fill"] = trace_from_page(fpage, ["到期时间", "有息债务"], fill_page, fill_parser)
            except Exception:
                pass

    return parsed

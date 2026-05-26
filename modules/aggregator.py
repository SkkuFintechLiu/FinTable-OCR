from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from modules.metadata import normalize_issuer_name


DebtField = str
CellFlag = str


def _cell(value: Any = None, raw: Optional[str] = None, flag: CellFlag = "missing") -> Dict[str, Any]:
    return {"value": value, "raw": raw, "flag": flag}


STANDARD_FIELDS: List[DebtField] = [
    "credit_bonds",
    "bank_loans",
    "non_bank_loans",
    "other",
    "total",
    "short_term",
]


@dataclass
class TaskRecord:
    task_id: str
    filename: str
    stored_filename: str
    file_path: str
    created_at: int
    status: str = "queued"
    file_type: str = ""
    issuer: str = ""
    year: Optional[int] = None
    page: Optional[int] = None
    confidence: Optional[int] = None
    error_type: str = ""
    status_reason: str = ""
    parser: str = ""
    extracted: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)
    error: str = ""


@dataclass
class IssuerRecord:
    name: str
    status: str = "success"
    data: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    sources: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class Aggregator:
    def __init__(self) -> None:
        self._tasks: Dict[str, TaskRecord] = {}
        self._issuers: Dict[str, IssuerRecord] = {}

    def _canonicalize_all_issuers(self) -> None:
        for k in list(self._issuers.keys()):
            self._ensure_canonical_issuer(k)

    def _resolve_issuer_key(self, issuer: str) -> str:
        if issuer in self._issuers:
            return issuer
        norm = normalize_issuer_name(issuer)
        if norm in self._issuers:
            return norm
        for k in self._issuers.keys():
            if normalize_issuer_name(k) == norm:
                return k
        return norm

    def _merge_issuer_records(self, dst: IssuerRecord, src: IssuerRecord) -> None:
        def task_rank(tid: str) -> Tuple[int, int]:
            t = self._tasks.get(tid)
            if not t:
                return 0, 0
            return int(t.confidence or 0), int(t.created_at or 0)

        for year, year_data in (src.data or {}).items():
            if year not in (dst.data or {}):
                dst.data[year] = year_data
                dst.sources[year] = src.sources.get(year) or {"source_type": "", "source_file": "", "task_id": "", "notes": ""}
                continue

            old_tid = (dst.sources.get(year) or {}).get("task_id") or ""
            new_tid = (src.sources.get(year) or {}).get("task_id") or ""
            new_rank = task_rank(new_tid) if new_tid else (0, 0)
            old_rank = task_rank(old_tid) if old_tid else (0, 0)
            if new_rank >= old_rank:
                dst.data[year] = year_data
                dst.sources[year] = src.sources.get(year) or dst.sources.get(year) or {"source_type": "", "source_file": "", "task_id": "", "notes": ""}
                continue

            updated = False
            existing = dst.data.get(year) or {}
            for f in STANDARD_FIELDS:
                nv = (year_data or {}).get(f) or _cell(None, None, "missing")
                ov = existing.get(f) or _cell(None, None, "missing")
                if (ov.get("flag") or "missing") != "ok" and (nv.get("flag") or "missing") == "ok":
                    existing[f] = nv
                    updated = True
            dst.data[year] = existing
            if updated:
                dst.sources[year] = src.sources.get(year) or dst.sources.get(year) or {"source_type": "", "source_file": "", "task_id": "", "notes": ""}

        self._recalc_issuer(dst)

    def _ensure_canonical_issuer(self, issuer: str) -> str:
        norm = normalize_issuer_name(issuer)
        if not norm:
            return issuer

        alias_keys = [k for k in self._issuers.keys() if normalize_issuer_name(k) == norm]
        if not alias_keys and norm in self._issuers:
            return norm

        if norm not in self._issuers:
            self._issuers[norm] = IssuerRecord(name=norm)

        dst = self._issuers[norm]
        dst.name = norm

        for k in alias_keys:
            if k == norm:
                continue
            src = self._issuers.get(k)
            if not src:
                continue
            self._merge_issuer_records(dst, src)
            self._issuers.pop(k, None)
            for t in self._tasks.values():
                if t.issuer == k:
                    t.issuer = norm

        return norm

    def create_task(self, task_id: str, filename: str, stored_filename: str, file_path: str, created_at: int) -> TaskRecord:
        task = TaskRecord(task_id=task_id, filename=filename, stored_filename=stored_filename, file_path=file_path, created_at=created_at)
        self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        return self._tasks.get(task_id)

    def list_tasks(self) -> List[TaskRecord]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at)

    def remove_task(self, task_id: str) -> None:
        task = self._tasks.pop(task_id, None)
        if not task:
            return

        for issuer in list(self._issuers.values()):
            years_to_remove: List[str] = []
            for y, src in issuer.sources.items():
                if src.get("task_id") == task_id:
                    years_to_remove.append(y)
            for y in years_to_remove:
                issuer.data.pop(y, None)
                issuer.sources.pop(y, None)
            self._recalc_issuer(issuer)
            if not issuer.data:
                self._issuers.pop(issuer.name, None)

    def remove_issuer(self, issuer_name: str) -> bool:
        issuer_key = self._resolve_issuer_key(issuer_name)
        issuer = self._issuers.pop(issuer_key, None)
        if not issuer:
            return False

        task_ids = set(src.get("task_id") for src in issuer.sources.values() if src.get("task_id"))
        for tid in task_ids:
            t = self._tasks.get(tid)
            if t:
                t.status = "queued"
                t.error = ""
                t.error_type = ""
                t.status_reason = ""
                t.issuer = ""
                t.year = None
                t.page = None
                t.confidence = None
                t.parser = ""
                t.extracted = {}
                t.missing_fields = []
        return True

    def apply_task_result(
        self,
        task_id: str,
        issuer: str,
        file_type: str,
        source_file: str,
        extracted_by_year: Dict[str, Dict[str, Any]],
        notes: str,
    ) -> None:
        issuer_key = self._ensure_canonical_issuer(issuer)
        rec = self._issuers.get(issuer_key)
        if not rec:
            rec = IssuerRecord(name=issuer_key)
            self._issuers[issuer_key] = rec

        def task_rank(tid: str) -> Tuple[int, int]:
            t = self._tasks.get(tid)
            if not t:
                return 0, 0
            return int(t.confidence or 0), int(t.created_at or 0)

        for year, raw_data in extracted_by_year.items():
            year_data: Dict[str, Dict[str, Any]] = {}
            for f in STANDARD_FIELDS:
                cell = raw_data.get(f)
                if isinstance(cell, dict) and "flag" in cell:
                    year_data[f] = {"value": cell.get("value"), "raw": cell.get("raw"), "flag": cell.get("flag")}
                else:
                    if cell is None:
                        year_data[f] = _cell(None, None, "missing")
                    else:
                        year_data[f] = _cell(cell, None, "ok")

            if year not in rec.data:
                rec.data[year] = year_data
                rec.sources[year] = {
                    "source_type": "年度报告" if file_type == "annual_report" else "募集说明书",
                    "source_file": source_file,
                    "task_id": task_id,
                    "notes": notes or "",
                }
                continue

            old_tid = (rec.sources.get(year) or {}).get("task_id") or ""
            new_rank = task_rank(task_id)
            old_rank = task_rank(old_tid) if old_tid else (0, 0)

            if new_rank >= old_rank:
                rec.data[year] = year_data
                rec.sources[year] = {
                    "source_type": "年度报告" if file_type == "annual_report" else "募集说明书",
                    "source_file": source_file,
                    "task_id": task_id,
                    "notes": notes or "",
                }
                continue

            updated = False
            existing = rec.data.get(year) or {}
            for f in STANDARD_FIELDS:
                nv = year_data.get(f) or _cell(None, None, "missing")
                ov = existing.get(f) or _cell(None, None, "missing")
                if (ov.get("flag") or "missing") != "ok" and (nv.get("flag") or "missing") == "ok":
                    existing[f] = nv
                    updated = True
            rec.data[year] = existing
            if updated:
                rec.sources[year] = {
                    "source_type": "年度报告" if file_type == "annual_report" else "募集说明书",
                    "source_file": source_file,
                    "task_id": task_id,
                    "notes": notes or "",
                }

        self._recalc_issuer(rec)

    def update_cell(self, issuer: str, year: str, field: str, value: Any, value_raw: Any = None) -> bool:
        issuer_key = self._resolve_issuer_key(issuer)
        rec = self._issuers.get(issuer_key)
        if not rec:
            return False
        yd = rec.data.get(str(year))
        if not yd:
            return False
        if field not in STANDARD_FIELDS:
            return False

        raw = None
        if value_raw is not None:
            raw = str(value_raw)

        flag = "ok"
        v = value
        if v is None or (isinstance(v, str) and not v.strip()):
            v = None
            flag = "missing"
        else:
            if isinstance(v, str):
                try:
                    v = float(v.replace(",", "").strip())
                except Exception:
                    flag = "invalid"
                    raw = raw or str(value)
                    v = None
            elif isinstance(v, (int, float)):
                v = float(v)
            else:
                flag = "invalid"
                raw = raw or str(value)
                v = None

        yd[field] = {"value": v, "raw": raw, "flag": flag}
        self._recalc_issuer(rec)
        return True

    def _recalc_issuer(self, rec: IssuerRecord) -> None:
        missing_fields: List[str] = []
        any_invalid = False
        for y, yd in rec.data.items():
            for f in STANDARD_FIELDS:
                cell = yd.get(f) or _cell(None, None, "missing")
                flag = cell.get("flag") or "missing"
                if flag == "missing":
                    missing_fields.append(f"{y}.{f}")
                elif flag in {"invalid", "unit_unknown"}:
                    any_invalid = True

        rec.status = "success"
        if missing_fields or any_invalid:
            rec.status = "partial"

        for y in list(rec.data.keys()):
            if y not in rec.sources:
                rec.sources[y] = {"source_type": "", "source_file": "", "task_id": "", "notes": ""}

    def to_public_dict(self) -> Dict[str, Any]:
        self._canonicalize_all_issuers()
        issuers = []
        def issuer_order_key(x: IssuerRecord) -> Tuple[int, str]:
            created: List[int] = []
            for y in (x.sources or {}).keys():
                tid = (x.sources.get(y) or {}).get("task_id") or ""
                t = self._tasks.get(tid)
                if t and t.created_at:
                    created.append(int(t.created_at))
            if not created:
                for t in self._tasks.values():
                    if t.issuer == x.name and t.created_at:
                        created.append(int(t.created_at))
            return (min(created) if created else 10**18), x.name

        for issuer in sorted(self._issuers.values(), key=issuer_order_key):
            missing_fields: List[str] = []
            for y, yd in issuer.data.items():
                for f, cell in yd.items():
                    if (cell.get("flag") or "missing") == "missing":
                        missing_fields.append(f"{y}.{f}")

            issuers.append(
                {
                    "name": issuer.name,
                    "status": issuer.status,
                    "data": {
                        y: {f: (cell.get("value") if cell.get("value") is not None else (cell.get("raw") or None)) for f, cell in yd.items()}
                        for y, yd in issuer.data.items()
                    },
                    "cells": issuer.data,
                    "sources": issuer.sources,
                    "missing_fields": missing_fields,
                    "source": self._issuer_source_summary(issuer),
                    "source_file": self._issuer_source_file_summary(issuer),
                }
            )
        return {"issuers": issuers}

    def _issuer_source_summary(self, issuer: IssuerRecord) -> str:
        for y in sorted(issuer.sources.keys(), reverse=True):
            st = issuer.sources[y].get("source_type")
            if st:
                return st
        return ""

    def _issuer_source_file_summary(self, issuer: IssuerRecord) -> str:
        for y in sorted(issuer.sources.keys(), reverse=True):
            sf = issuer.sources[y].get("source_file")
            if sf:
                return sf
        return ""

    def get_issuer_public(self, issuer: str) -> Optional[Dict[str, Any]]:
        self._canonicalize_all_issuers()
        issuer_key = self._resolve_issuer_key(issuer)
        rec = self._issuers.get(issuer_key)
        if not rec:
            return None
        missing_fields: List[str] = []
        for y, yd in rec.data.items():
            for f, cell in yd.items():
                if (cell.get("flag") or "missing") == "missing":
                    missing_fields.append(f"{y}.{f}")

        return {
            "name": rec.name,
            "status": rec.status,
            "data": {
                y: {f: (cell.get("value") if cell.get("value") is not None else (cell.get("raw") or None)) for f, cell in yd.items()}
                for y, yd in rec.data.items()
            },
            "cells": rec.data,
            "missing_fields": missing_fields,
            "source": self._issuer_source_summary(rec),
            "source_file": self._issuer_source_file_summary(rec),
        }

    def export_payload(self) -> Dict[str, Any]:
        self._canonicalize_all_issuers()
        years: List[str] = []
        for issuer in self._issuers.values():
            for y in issuer.data.keys():
                if y not in years:
                    years.append(y)
        years_sorted = sorted(years)

        rows: List[Dict[str, Any]] = []
        def issuer_order_key(x: IssuerRecord) -> Tuple[int, str]:
            created: List[int] = []
            for y in (x.sources or {}).keys():
                tid = (x.sources.get(y) or {}).get("task_id") or ""
                t = self._tasks.get(tid)
                if t and t.created_at:
                    created.append(int(t.created_at))
            if not created:
                for t in self._tasks.values():
                    if t.issuer == x.name and t.created_at:
                        created.append(int(t.created_at))
            return (min(created) if created else 10**18), x.name

        for issuer in sorted(self._issuers.values(), key=issuer_order_key):
            row = {
                "issuer": issuer.name,
                "years": {},
                "sources": issuer.sources,
            }
            for y in years_sorted:
                yd = issuer.data.get(y) or {f: _cell(None, None, "missing") for f in STANDARD_FIELDS}
                row["years"][y] = yd
            rows.append(row)

        return {"years": years_sorted, "rows": rows}

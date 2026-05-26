import os
import sys
import glob
import json
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.annual_report import parse_annual_report
from modules.prospectus import parse_prospectus
from modules.classifier import classify_file_type
from modules.metadata import extract_issuer, normalize_issuer_name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="uploads")
    ap.add_argument("--out", default="")
    ap.add_argument("--jsonl", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "*.pdf")))
    out = []
    fp = None
    if args.out:
        fp = open(args.out, "w", encoding="utf-8")

    for idx, p in enumerate(files, start=1):
        ft = classify_file_type(p)
        issuer = normalize_issuer_name(extract_issuer(p) or "") or ""
        print(f"[{idx}/{len(files)}] {os.path.basename(p)} {ft}", file=sys.stderr, flush=True)
        if ft == "annual_report":
            r = parse_annual_report(p, fallback_year=None)
            y = str(r.year or "")
            data = r.data
            ok = sum(1 for k, v in data.items() if isinstance(v, dict) and v.get("flag") == "ok")
            rec = {"file": p, "type": ft, "issuer": issuer, "year": y, "ok_fields": ok, "missing": r.missing_fields, "notes": r.notes, "trace": data.get("__trace__")}
        elif ft == "prospectus":
            r = parse_prospectus(p, fallback_year=None)
            years = [str(y) for y in r.data_by_year.keys() if y != 0]
            ok = 0
            if years:
                yd = r.data_by_year.get(int(years[0])) or {}
                ok = sum(1 for k, v in yd.items() if isinstance(v, dict) and v.get("flag") == "ok")
            rec = {"file": p, "type": ft, "issuer": issuer, "years": years, "ok_fields": ok, "missing": r.missing_fields, "notes": r.notes, "trace": (r.data_by_year.get(0) or {}).get("__trace__")}
        else:
            rec = {"file": p, "type": ft, "issuer": issuer}

        if fp and args.jsonl:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fp.flush()
        else:
            out.append(rec)

    if fp:
        if args.jsonl:
            fp.close()
            return
        fp.write(json.dumps(out, ensure_ascii=False, indent=2))
        fp.write("\n")
        fp.close()
        return

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

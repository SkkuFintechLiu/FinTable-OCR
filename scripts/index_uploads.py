import os
import sys
import glob
import json
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.classifier import classify_file_type
from modules.metadata import extract_issuer, extract_year, normalize_issuer_name


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
        y = extract_year(p, os.path.basename(p)) if ft == "annual_report" else None
        rec = {"file": p, "type": ft, "issuer": issuer, "year": y}

        print(f"[{idx}/{len(files)}] {os.path.basename(p)} {ft} {issuer} {y or ''}", file=sys.stderr, flush=True)

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


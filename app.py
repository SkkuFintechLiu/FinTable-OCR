import io
import os
import shutil
import signal
import sys
import threading
import time
import uuid
import webbrowser
from contextlib import suppress
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from flask import Flask, jsonify, render_template, request, send_file
from modules.aggregator import Aggregator
from modules.classifier import classify_file_type
from modules.excel_exporter import build_workbook_bytes
from modules.metadata import extract_issuer, extract_year, normalize_issuer_name
from modules.annual_report import parse_annual_report
from modules.prospectus import parse_prospectus
from modules.confidence import score_task


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
if getattr(sys, "frozen", False) and not os.environ.get("UPLOAD_DIR"):
    UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "runtime_uploads"))
else:
    UPLOAD_DIR = os.path.abspath(os.environ.get("UPLOAD_DIR") or DEFAULT_UPLOAD_DIR)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 300

os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_CONCURRENT_PDF = int((os.environ.get("MAX_CONCURRENT_PDF") or "1").strip() or "1")
executor = ThreadPoolExecutor(max_workers=max(1, MAX_CONCURRENT_PDF))
lock = threading.RLock()

aggregator = Aggregator()


def _cleanup_uploads_on_exit() -> None:
    enabled = (os.environ.get("CLEAR_UPLOADS_ON_EXIT") or "").strip().lower() in {"1", "true", "yes"} or getattr(sys, "frozen", False)
    if not enabled:
        return
    if os.path.abspath(UPLOAD_DIR) == os.path.abspath(DEFAULT_UPLOAD_DIR):
        return
    with suppress(Exception):
        for name in os.listdir(UPLOAD_DIR):
            p = os.path.join(UPLOAD_DIR, name)
            if os.path.isfile(p):
                os.remove(p)
            else:
                shutil.rmtree(p, ignore_errors=True)


def _install_exit_hooks() -> None:
    import atexit

    atexit.register(_cleanup_uploads_on_exit)

    def handler(signum, frame):
        _cleanup_uploads_on_exit()
        raise SystemExit(0)

    for s in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if s is None:
            continue
        with suppress(Exception):
            signal.signal(s, handler)


_install_exit_hooks()


def _now_ts() -> int:
    return int(time.time())


def _pick_port() -> int:
    v = (os.environ.get("PORT") or "").strip()
    if v.isdigit():
        return int(v)
    if getattr(sys, "frozen", False):
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
        finally:
            with suppress(Exception):
                s.close()
    return 5000


def _maybe_open_browser(port: int) -> None:
    if (os.environ.get("AUTO_OPEN_BROWSER") or "").strip().lower() in {"0", "false", "no"}:
        return
    if not getattr(sys, "frozen", False):
        return

    def _open():
        time.sleep(1.2)
        with suppress(Exception):
            webbrowser.open(f"http://127.0.0.1:{port}/")

    t = threading.Thread(target=_open, daemon=True)
    t.start()


def _parse_task(task_id: str) -> None:
    with lock:
        task = aggregator.get_task(task_id)
        if not task:
            return
        task.status = "processing"
        task.error = ""

    try:
        try:
            import fitz  # type: ignore

            doc = fitz.open(task.file_path)
            if getattr(doc, "is_encrypted", False):
                doc.close()
                with lock:
                    task.status = "failed"
                    task.error_type = "encrypted"
                    task.error = "该文件已加密或受限，无法读取"
                return
            _ = doc.page_count
            doc.close()
        except ModuleNotFoundError:
            pass
        except Exception as e:
            with lock:
                task.status = "failed"
                task.error_type = "unreadable_pdf"
                task.error = str(e)
            return

        file_type = task.file_type
        if not file_type:
            file_type = classify_file_type(task.file_path)
            with lock:
                task.file_type = file_type

        if file_type == "unknown":
            with lock:
                task.status = "failed"
                task.error_type = "unknown_type"
                task.error = "未识别文件类型"
            return

        issuer = normalize_issuer_name(extract_issuer(task.file_path) or "")
        year = extract_year(task.file_path, task.filename) or None

        extracted_by_year = {}
        if file_type == "annual_report":
            parsed = parse_annual_report(task.file_path, fallback_year=year)
            extracted_by_year = {str(parsed.year): parsed.data} if parsed.year else {}
            issuer = normalize_issuer_name(issuer or parsed.issuer or issuer)
            year = parsed.year or year
            missing_fields = parsed.missing_fields
            notes = parsed.notes
            with lock:
                task.page = None
                task.parser = ""
                if "__trace__" in parsed.data:
                    t = parsed.data.get("__trace__", {}).get("task") or {}
                    task.page = t.get("page")
                    task.parser = t.get("parser") or ""
                if parsed.year and str(parsed.year) in extracted_by_year:
                    score, reason = score_task(extracted_by_year[str(parsed.year)], task.parser or "pdfplumber_table")
                    task.confidence = score
                    task.status_reason = reason if not notes else f"{reason}；{notes}"
        else:
            parsed = parse_prospectus(task.file_path, fallback_year=year)
            extracted_by_year = {str(y): d for y, d in parsed.data_by_year.items() if y != 0}
            issuer = normalize_issuer_name(issuer or parsed.issuer or issuer)
            missing_fields = parsed.missing_fields
            notes = parsed.notes
            with lock:
                task.page = None
                task.parser = ""
                t = (parsed.data_by_year.get(0) or {}).get("__trace__", {}).get("task") if parsed.data_by_year else None
                if isinstance(t, dict):
                    task.page = t.get("page")
                    task.parser = t.get("parser") or ""
                any_year = next(iter(extracted_by_year.keys()), None)
                if any_year:
                    score, reason = score_task(extracted_by_year[any_year], task.parser or "pdfplumber_table")
                    task.confidence = score
                    task.status_reason = reason if not notes else f"{reason}；{notes}"

        if not issuer:
            issuer = f"未知发行人_{task_id[:8]}"

        with lock:
            task.issuer = issuer
            task.year = year
            task.extracted = extracted_by_year
            task.missing_fields = list(missing_fields)
            task.status_reason = task.status_reason or (notes or "")

        with lock:
            aggregator.apply_task_result(
                task_id=task_id,
                issuer=issuer,
                file_type=file_type,
                source_file=task.filename,
                extracted_by_year=extracted_by_year,
                notes=notes,
            )

        status = "success"
        if missing_fields:
            status = "partial"

        with lock:
            task.status = status
    except Exception as e:
        with lock:
            task.status = "failed"
            task.error_type = "exception"
            task.error = str(e)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/tasks")
def api_tasks():
    with lock:
        tasks = [asdict(t) for t in aggregator.list_tasks()]
    return jsonify({"tasks": tasks})


@app.post("/api/upload")
def api_upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "未收到文件"}), 400
    if len(files) > 50:
        return jsonify({"error": "单次最多上传 50 个文件，请分批上传"}), 400

    tasks = []
    base_ts = _now_ts()
    for idx, f in enumerate(files):
        original = (f.filename or "").strip()
        filename = os.path.basename(original)

        task_id = str(uuid.uuid4())
        stored_name = f"{task_id}.pdf"
        file_path = os.path.join(UPLOAD_DIR, stored_name)
        f.save(file_path)

        ok_pdf = False
        try:
            with open(file_path, "rb") as rf:
                head = rf.read(5)
                ok_pdf = head == b"%PDF-"
        except Exception:
            ok_pdf = False

        with lock:
            task = aggregator.create_task(
                task_id=task_id,
                filename=filename or stored_name,
                stored_filename=stored_name,
                file_path=file_path,
                created_at=base_ts + idx,
            )
            if not ok_pdf:
                task.status = "failed"
                task.error_type = "not_pdf"
                task.error = "文件头不符合 PDF 格式"

        if ok_pdf:
            executor.submit(_parse_task, task_id)
        tasks.append({"task_id": task_id, "filename": filename})

    if not tasks:
        return jsonify({"error": "未发现可处理的 PDF 文件"}), 400
    return jsonify({"tasks": tasks})


@app.post("/api/reprocess_uploads")
def api_reprocess_uploads():
    data = request.get_json(silent=True) or {}
    reset = bool(data.get("reset"))

    pdf_files = []
    try:
        for name in os.listdir(UPLOAD_DIR):
            if name.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(UPLOAD_DIR, name))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    pdf_files = sorted(pdf_files)
    if not pdf_files:
        return jsonify({"error": "uploads 目录下没有 PDF 文件"}), 400

    with lock:
        global aggregator
        if reset:
            aggregator = Aggregator()

    tasks = []
    base_ts = _now_ts()
    for idx, file_path in enumerate(pdf_files):
        filename = os.path.basename(file_path)
        task_id = str(uuid.uuid4())

        ok_pdf = False
        try:
            with open(file_path, "rb") as rf:
                head = rf.read(5)
                ok_pdf = head == b"%PDF-"
        except Exception:
            ok_pdf = False

        with lock:
            task = aggregator.create_task(
                task_id=task_id,
                filename=filename,
                stored_filename=filename,
                file_path=file_path,
                created_at=base_ts + idx,
            )
            if not ok_pdf:
                task.status = "failed"
                task.error_type = "not_pdf"
                task.error = "文件头不符合 PDF 格式"

        if ok_pdf:
            executor.submit(_parse_task, task_id)
        tasks.append({"task_id": task_id, "filename": filename})

    return jsonify({"tasks": tasks, "count": len(tasks)})


@app.get("/api/status/<task_id>")
def api_status(task_id: str):
    with lock:
        task = aggregator.get_task(task_id)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        payload = asdict(task)
    return jsonify(payload)


@app.put("/api/tasks/<task_id>")
def api_update_task(task_id: str):
    data = request.get_json(silent=True) or {}
    file_type = data.get("file_type")
    if file_type not in {"annual_report", "prospectus", "unknown"}:
        return jsonify({"error": "file_type 无效"}), 400

    with lock:
        task = aggregator.get_task(task_id)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        task.file_type = file_type
        task.status = "queued"
        task.error = ""
        task.extracted = {}
        task.missing_fields = []

    executor.submit(_parse_task, task_id)
    return jsonify({"ok": True})


@app.delete("/api/tasks/<task_id>")
def api_delete_task(task_id: str):
    with lock:
        task = aggregator.get_task(task_id)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        aggregator.remove_task(task_id)

    try:
        if task.file_path and os.path.exists(task.file_path):
            os.remove(task.file_path)
    except Exception:
        pass
    return jsonify({"ok": True})


@app.get("/api/data")
def api_data():
    with lock:
        payload = aggregator.to_public_dict()
    return jsonify(payload)


@app.put("/api/data/<path:issuer>")
def api_update_data(issuer: str):
    data = request.get_json(silent=True) or {}
    year = data.get("year")
    field = data.get("field")
    value = data.get("value")
    value_raw = data.get("value_raw")

    if year is None or not field:
        return jsonify({"error": "year/field 必填"}), 400

    with lock:
        updated = aggregator.update_cell(issuer=issuer, year=str(year), field=field, value=value, value_raw=value_raw)
        if not updated:
            return jsonify({"error": "发行人或年份不存在"}), 404
        payload = aggregator.get_issuer_public(issuer)
    return jsonify(payload)


@app.delete("/api/data/<path:issuer>")
def api_delete_issuer(issuer: str):
    with lock:
        ok = aggregator.remove_issuer(issuer)
    if not ok:
        return jsonify({"error": "发行人不存在"}), 404
    return jsonify({"ok": True})


@app.post("/api/export")
def api_export():
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "有息债务汇总.xlsx").strip()
    if not filename.lower().endswith(".xlsx"):
        filename += ".xlsx"

    with lock:
        export_payload = aggregator.export_payload()

    xlsx_bytes = build_workbook_bytes(export_payload)
    bio = io.BytesIO(xlsx_bytes)
    bio.seek(0)
    return send_file(bio, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name=filename)


if __name__ == "__main__":
    port = _pick_port()
    _maybe_open_browser(port)
    host = (os.environ.get("HOST") or "127.0.0.1").strip() or "127.0.0.1"
    app.run(host=host, port=port, debug=False, threaded=True)

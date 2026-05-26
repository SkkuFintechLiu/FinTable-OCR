const state = {
  tasks: new Map(),
  selectedTaskId: null,
  polling: null,
  issuers: [],
  years: [],
  focusIssuer: null,
  sheetZoom: 1,
};

const LABELS = [
  ["credit_bonds", "信用类债券"],
  ["bank_loans", "银行贷款"],
  ["non_bank_loans", "非银行金融机构贷款"],
  ["other", "其他有息债务"],
  ["total", "合计"],
  ["short_term", "1年以内"],
];

function $(id) {
  return document.getElementById(id);
}

function clamp(n, min, max) {
  return Math.min(max, Math.max(min, n));
}

function applySheetZoom(z) {
  const v = clamp(Number(z) || 1, 0.7, 1.4);
  state.sheetZoom = v;
  document.documentElement.style.setProperty("--sheet-zoom", String(v));
  const label = $("zoomLabel");
  if (label) label.textContent = `${Math.round(v * 100)}%`;
  const range = $("zoomRange");
  if (range) range.value = String(Math.round(v * 100));
  try {
    window.localStorage.setItem("sheetZoom", String(v));
  } catch {}
}

function bindZoomUI() {
  const range = $("zoomRange");
  const outBtn = $("zoomOutBtn");
  const inBtn = $("zoomInBtn");
  const resetBtn = $("zoomResetBtn");

  let init = 1;
  try {
    const saved = window.localStorage.getItem("sheetZoom");
    if (saved) init = Number(saved) || 1;
  } catch {}
  applySheetZoom(init);

  if (range) {
    range.addEventListener("input", () => {
      applySheetZoom((Number(range.value) || 100) / 100);
    });
  }
  if (outBtn) outBtn.addEventListener("click", () => applySheetZoom(state.sheetZoom - 0.1));
  if (inBtn) inBtn.addEventListener("click", () => applySheetZoom(state.sheetZoom + 0.1));
  if (resetBtn) resetBtn.addEventListener("click", () => applySheetZoom(1));
}

function humanFileType(t) {
  if (t === "annual_report") return "年度报告";
  if (t === "prospectus") return "募集说明书";
  if (t === "unknown") return "未识别";
  return "自动";
}

function statusPill(s) {
  if (s === "success") return ["完成", "good"];
  if (s === "partial") return ["部分缺失", "warn"];
  if (s === "low_confidence") return ["可信度低", "warn"];
  if (s === "missing_table") return ["未找到表", "warn"];
  if (s === "ocr_required") return ["需 OCR", "warn"];
  if (s === "failed") return ["失败", "bad"];
  if (s === "processing") return ["解析中", ""];
  return ["等待中", ""];
}

function setProgress(pct, label) {
  $("progressBar").style.width = `${pct}%`;
  $("progressValue").textContent = `${pct}%`;
  $("progressLabel").textContent = label || "上传中";
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || "请求失败");
  }
  return res.json();
}

function displayNumber(v) {
  if (typeof v === "number" && Number.isFinite(v)) return v.toFixed(2);
  return v === null || v === undefined ? "" : String(v);
}

function collectYears(issuers) {
  const set = new Set();
  for (const it of issuers || []) {
    const cells = it.cells || {};
    for (const y of Object.keys(cells)) set.add(y);
  }
  return Array.from(set).sort();
}

function buildSourceText(sources, years) {
  const parts = [];
  for (const y of years) {
    const src = (sources || {})[y] || {};
    const st = (src.source_type || "").trim();
    const sf = (src.source_file || "").trim();
    const notes = (src.notes || "").trim();
    if (!st && !sf && !notes) continue;
    let seg = [st, sf].filter(Boolean).join(" ");
    if (notes) seg = seg ? `${seg}（${notes}）` : notes;
    if (seg) parts.push(seg);
  }
  return Array.from(new Set(parts)).join("；");
}

function renderSheet() {
  const head = $("sheetHead");
  const body = $("sheetBody");
  const sourceBar = $("sourceBar");

  const years = state.years || [];
  const issuers = state.issuers || [];

  head.innerHTML = "";
  body.innerHTML = "";

  if (issuers.length === 0) {
    sourceBar.textContent = "暂无数据：上传 PDF 后将自动解析并出现在此处。";
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 2;
    td.style.padding = "14px";
    td.textContent = "暂无数据";
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }

  sourceBar.textContent = "提示：点击队列中的文件可自动定位到对应发行人行；直接编辑单元格后失焦会自动保存。";

  const tr1 = document.createElement("tr");
  const thIssuer = document.createElement("th");
  thIssuer.className = "sticky-top sticky-col";
  thIssuer.rowSpan = 2;
  thIssuer.textContent = "发行人";
  tr1.appendChild(thIssuer);

  for (const y of years) {
    const th = document.createElement("th");
    th.className = "sticky-top year-group";
    th.colSpan = LABELS.length;
    th.textContent = `有息债务${y}`;
    tr1.appendChild(th);
  }

  const thSource = document.createElement("th");
  thSource.className = "sticky-top";
  thSource.rowSpan = 2;
  thSource.textContent = "数据来源";
  tr1.appendChild(thSource);
  head.appendChild(tr1);

  const tr2 = document.createElement("tr");
  for (const y of years) {
    for (const [, label] of LABELS) {
      const th = document.createElement("th");
      th.className = "sticky-top";
      th.textContent = label;
      tr2.appendChild(th);
    }
  }
  head.appendChild(tr2);

  for (const it of issuers) {
    const tr = document.createElement("tr");
    tr.dataset.issuer = it.name || "";
    if (state.focusIssuer && state.focusIssuer === it.name) tr.classList.add("sheet-row-focus");

    const tdIssuer = document.createElement("td");
    tdIssuer.className = "sticky-col";
    tdIssuer.textContent = it.name || "";
    tr.appendChild(tdIssuer);

    const cells = it.cells || {};
    for (const y of years) {
      const yd = cells[y] || {};
      for (const [field] of LABELS) {
        const cell = yd[field] || { value: null, raw: null, flag: "missing" };
        const flag = cell.flag || "missing";

        const td = document.createElement("td");
        td.className = "value";
        if (flag === "missing") td.classList.add("sheet-missing");
        if (flag === "invalid" || flag === "unit_unknown") td.classList.add("sheet-invalid");

        const div = document.createElement("div");
        div.className = "sheet-edit";
        div.contentEditable = "true";
        div.dataset.issuer = it.name || "";
        div.dataset.year = y;
        div.dataset.field = field;
        div.textContent = displayNumber(cell.value !== null && cell.value !== undefined ? cell.value : cell.raw);
        div.addEventListener("focus", () => {
          state.focusIssuer = it.name || "";
          focusIssuerRow(state.focusIssuer);
        });
        div.addEventListener("blur", async () => {
          const issuer = div.dataset.issuer || "";
          const year = div.dataset.year || "";
          const field = div.dataset.field || "";
          const value = (div.textContent || "").trim();
          await fetchJson(`/api/data/${encodeURIComponent(issuer)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ year, field, value }),
          }).catch((err) => {
            $("uploadTip").textContent = String(err.message || err);
          });
          await refreshData();
        });

        td.appendChild(div);
        tr.appendChild(td);
      }
    }

    const tdSource = document.createElement("td");
    tdSource.textContent = buildSourceText(it.sources, years) || "";
    tr.appendChild(tdSource);

    body.appendChild(tr);
  }

  focusIssuerRow(state.focusIssuer);
}

function focusIssuerRow(issuerName) {
  if (!issuerName) return;
  const rows = document.querySelectorAll("#sheetBody tr");
  for (const r of rows) {
    if ((r.dataset.issuer || "") === issuerName) {
      r.classList.add("sheet-row-focus");
      const sc = $("sheetScroll");
      if (sc) {
        const top = r.offsetTop - 60;
        sc.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
      }
    } else {
      r.classList.remove("sheet-row-focus");
    }
  }
}

function renderQueue() {
  const body = $("queueBody");
  body.innerHTML = "";
  const tasks = Array.from(state.tasks.values()).sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
  $("queueStats").textContent = `${tasks.length} 个文件`;

  for (const t of tasks) {
    const row = document.createElement("div");
    row.className = "queue-row" + (t.task_id === state.selectedTaskId ? " active" : "");
    row.dataset.taskId = t.task_id;
    row.addEventListener("click", () => selectTask(t.task_id));

    const file = document.createElement("div");
    file.className = "queue-file";
    file.textContent = t.filename || "";

    const type = document.createElement("div");
    type.className = "queue-muted";
    type.textContent = humanFileType(t.file_type || "");

    const issuer = document.createElement("div");
    issuer.className = "queue-muted";
    issuer.textContent = t.issuer || "—";

    const year = document.createElement("div");
    year.className = "queue-muted";
    year.textContent = t.year ? String(t.year) : "—";

    const st = document.createElement("div");
    const [stText, stClass] = statusPill(t.status || "");
    st.innerHTML = `<span class="pill ${stClass}">${stText}</span>`;

    const extra = document.createElement("div");
    extra.className = "queue-muted";
    const conf = t.confidence !== null && t.confidence !== undefined ? `${t.confidence}` : "—";
    const pg = t.page ? `${t.page}` : "—";
    const reason = (t.error || t.status_reason || "").trim();
    extra.title = reason;
    extra.textContent = `置信度 ${conf} · 页码 ${pg}`;

    const actions = document.createElement("div");
    actions.className = "queue-actions";

    const fixType = document.createElement("button");
    fixType.className = "link-btn";
    fixType.type = "button";
    fixType.textContent = "改类型";
    fixType.addEventListener("click", async (e) => {
      e.stopPropagation();
      const input = window.prompt("输入：annual_report（年报） / prospectus（募集书） / unknown（未识别）", t.file_type || "");
      if (!input) return;
      const v = input.trim();
      await fetchJson(`/api/tasks/${encodeURIComponent(t.task_id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_type: v }),
      }).catch((err) => {
        $("uploadTip").textContent = String(err.message || err);
      });
    });

    const remove = document.createElement("button");
    remove.className = "link-btn";
    remove.type = "button";
    remove.textContent = "移除";
    remove.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetchJson(`/api/tasks/${encodeURIComponent(t.task_id)}`, { method: "DELETE" }).catch((err) => {
        $("uploadTip").textContent = String(err.message || err);
      });
      state.tasks.delete(t.task_id);
      if (state.selectedTaskId === t.task_id) state.selectedTaskId = null;
      renderQueue();
      state.focusIssuer = null;
      renderSheet();
    });

    actions.appendChild(fixType);
    actions.appendChild(remove);

    row.appendChild(file);
    row.appendChild(type);
    row.appendChild(issuer);
    row.appendChild(year);
    row.appendChild(st);
    row.appendChild(extra);
    row.appendChild(actions);

    body.appendChild(row);
  }
}

async function selectTask(taskId) {
  state.selectedTaskId = taskId;
  renderQueue();
  const task = state.tasks.get(taskId);
  state.focusIssuer = task && task.issuer ? task.issuer : null;
  focusIssuerRow(state.focusIssuer);
}

async function refreshTasks() {
  const data = await fetchJson("/api/tasks");
  const tasks = data.tasks || [];
  for (const t of tasks) state.tasks.set(t.task_id, t);
  renderQueue();
}

async function refreshData() {
  const data = await fetchJson("/api/data").catch(() => ({ issuers: [] }));
  state.issuers = data.issuers || [];
  state.years = collectYears(state.issuers);
  renderSheet();
  await refreshTasks();
}

function startPolling() {
  if (state.polling) return;
  state.polling = window.setInterval(async () => {
    const anyRunning = Array.from(state.tasks.values()).some((t) => t.status === "queued" || t.status === "processing");
    if (!anyRunning) return;
    await refreshData().catch(() => {});
  }, 1400);
}

function uploadFiles(files) {
  if (!files || files.length === 0) return;
  if (files.length > 50) {
    $("uploadTip").textContent = "单次最多上传 50 个文件，请分批上传";
    return;
  }

  $("uploadTip").textContent = "";
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload", true);

  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((e.loaded / e.total) * 100);
    setProgress(pct, `上传中：${files[0].name}`);
  };

  xhr.onload = async () => {
    try {
      if (xhr.status >= 400) throw new Error(xhr.responseText || "上传失败");
      const res = JSON.parse(xhr.responseText);
      for (const t of res.tasks || []) {
        state.tasks.set(t.task_id, { task_id: t.task_id, filename: t.filename, status: "queued" });
      }
      setProgress(100, "上传完成，开始解析");
      await refreshTasks();
      startPolling();
      window.setTimeout(() => setProgress(0, "等待上传"), 700);
    } catch (err) {
      $("uploadTip").textContent = String(err.message || err);
      setProgress(0, "等待上传");
    }
  };

  xhr.onerror = () => {
    $("uploadTip").textContent = "上传失败";
    setProgress(0, "等待上传");
  };

  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  xhr.send(form);
}

async function reprocessUploads() {
  $("uploadTip").textContent = "";
  setProgress(0, "扫描 uploads 中的文件");
  try {
    const res = await fetchJson("/api/reprocess_uploads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reset: true }),
    });
    for (const t of res.tasks || []) {
      state.tasks.set(t.task_id, { task_id: t.task_id, filename: t.filename, status: "queued" });
    }
    setProgress(100, `已入队 ${res.count || (res.tasks || []).length} 个文件，开始解析`);
    await refreshTasks();
    startPolling();
    window.setTimeout(() => setProgress(0, "等待上传"), 700);
  } catch (err) {
    $("uploadTip").textContent = String(err.message || err);
    setProgress(0, "等待上传");
  }
}

function openModal() {
  $("modal").classList.remove("hidden");
}

function closeModal() {
  $("modal").classList.add("hidden");
}

async function buildStats() {
  const data = await fetchJson("/api/data").catch(() => ({ issuers: [] }));
  const issuers = data.issuers || [];
  let missing = 0;
  for (const it of issuers) missing += (it.missing_fields || []).length;
  const processed = Array.from(state.tasks.values()).filter((t) => ["success", "partial", "failed"].includes(t.status)).length;
  return `已处理文件数：${processed}\n发行人数：${issuers.length}\n缺失字段数：${missing}`;
}

async function exportExcel() {
  const res = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
  if (!res.ok) throw new Error(await res.text());
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "有息债务汇总.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function bindUploadUI() {
  const dropzone = $("dropzone");
  const input = $("fileInput");
  const chooseBtn = $("chooseBtn");
  const reprocessBtn = $("reprocessBtn");

  function openPicker() {
    input.value = "";
    input.click();
  }

  dropzone.addEventListener("click", openPicker);
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") openPicker();
  });
  chooseBtn.addEventListener("click", openPicker);
  input.addEventListener("change", () => uploadFiles(Array.from(input.files || [])));

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dragover");
  });
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    const files = Array.from(e.dataTransfer.files || []).filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    uploadFiles(files);
  });

  if (reprocessBtn) {
    reprocessBtn.addEventListener("click", async () => {
      const ok = window.confirm("将扫描 uploads 目录并重新提取全部 PDF（会清空当前页面内数据）。继续吗？");
      if (!ok) return;
      await reprocessUploads();
    });
  }
}

function bindModalUI() {
  $("exportBtn").addEventListener("click", async () => {
    $("modalStats").textContent = await buildStats();
    openModal();
  });
  $("modalBackdrop").addEventListener("click", closeModal);
  $("modalCancel").addEventListener("click", closeModal);
  $("modalConfirm").addEventListener("click", async () => {
    $("modalConfirm").disabled = true;
    try {
      await exportExcel();
      closeModal();
    } catch (err) {
      $("uploadTip").textContent = String(err.message || err);
    } finally {
      $("modalConfirm").disabled = false;
    }
  });
}

async function main() {
  bindUploadUI();
  bindModalUI();
  bindZoomUI();
  await refreshData();
  startPolling();
}

main();

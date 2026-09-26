const $ = (id) => document.getElementById(id);

const form = $("upload-form");
const statusEl = $("status");
let currentJobId = null;
let origObjectUrl = null;

function fmt(sec) {
  return sec.toFixed(3) + " s";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("file").files[0];
  if (!file) return;

  const fd = new FormData();
  fd.append("file", file);
  fd.append("threshold_db", $("threshold").value);
  fd.append("min_silence_ms", $("min-silence").value);
  fd.append("padding_ms", $("padding").value);

  $("submit-btn").disabled = true;
  statusEl.textContent = "处理中…";
  try {
    const resp = await fetch("/api/process", { method: "POST", body: fd });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "处理失败");
    renderResult(data, file);
    statusEl.textContent = "完成 ✅";
  } catch (err) {
    statusEl.textContent = "出错：" + err.message;
  } finally {
    $("submit-btn").disabled = false;
  }
});

function renderResult(meta, file) {
  currentJobId = meta.job_id;
  $("result").classList.remove("hidden");

  $("orig-dur").textContent = fmt(meta.original_duration);
  $("out-dur").textContent = fmt(meta.output_duration);
  const saved = meta.original_duration - meta.output_duration;
  const pct = meta.original_duration > 0
    ? " (" + ((saved / meta.original_duration) * 100).toFixed(1) + "%)"
    : "";
  $("saved").textContent = fmt(saved) + pct;
  $("format-info").textContent =
    `采样率 ${meta.sample_rate} Hz · ${meta.channels} 声道（成品与原始一致）`;

  // Players
  if (origObjectUrl) URL.revokeObjectURL(origObjectUrl);
  origObjectUrl = URL.createObjectURL(file);
  $("player-orig").src = origObjectUrl;
  $("player-out").src = `/api/jobs/${currentJobId}/audio`;

  // Downloads
  $("dl-audio").href = `/api/jobs/${currentJobId}/audio`;
  $("dl-audio").download = `nosilence_${currentJobId}.wav`;
  $("dl-json").href = `/api/jobs/${currentJobId}/mapping`;
  $("dl-json").download = `mapping_${currentJobId}.json`;

  // Segment table
  $("seg-count").textContent = meta.segments.length;
  const tbody = $("seg-table");
  tbody.innerHTML = "";
  for (const s of meta.segments) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${s.index}</td>` +
      `<td>${fmt(s.orig_start)}</td><td>${fmt(s.orig_end)}</td>` +
      `<td>${fmt(s.out_start)}</td><td>${fmt(s.out_end)}</td>`;
    tbody.appendChild(tr);
  }

  $("query-result").textContent = "";
  $("query-t").value = "";
  $("query-t").max = meta.original_duration;
}

$("query-btn").addEventListener("click", async () => {
  const out = $("query-result");
  if (!currentJobId) return;
  const t = parseFloat($("query-t").value);
  if (Number.isNaN(t)) {
    out.textContent = "请输入有效的秒数";
    return;
  }
  try {
    const resp = await fetch(
      `/api/jobs/${currentJobId}/query?t=${encodeURIComponent(t)}`
    );
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "查询失败");
    out.textContent = data.cut
      ? `原始 ${fmt(t)} → 已裁掉 ✂`
      : `原始 ${fmt(t)} → 成品 ${fmt(data.output_time)}`;
  } catch (err) {
    out.textContent = "出错：" + err.message;
  }
});

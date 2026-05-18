const $ = (id) => document.getElementById(id);
const state = { selected: null, items: [], filter: "" };

async function fetchJson(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

async function refreshList() {
  try {
    const data = await fetchJson("/api/content?limit=500&withState=true");
    state.items = data.items || [];
    renderList();
  } catch (e) {
    $("count").textContent = `error: ${e.message}`;
  }
}

async function refreshStreamHealth() {
  try {
    const h = await fetchJson("/api/health/stream");
    const pill = $("streamPill");
    pill.textContent = `stream: ${h.state}`;
    pill.classList.toggle("ok", h.ready);
    pill.classList.toggle("bad", !h.ready);
  } catch {
    $("streamPill").textContent = "stream: unreachable";
    $("streamPill").classList.add("bad");
  }
}

function renderList() {
  const list = $("list");
  list.innerHTML = "";
  const filter = state.filter.trim().toLowerCase();
  const matching = state.items.filter((i) =>
    !filter || (i.contentId || "").toLowerCase().includes(filter)
  );
  matching.sort((a, b) => (b.lastUpdatedAt || "").localeCompare(a.lastUpdatedAt || ""));
  for (const item of matching) {
    const el = document.createElement("div");
    el.className = "item" + (state.selected === item.contentId ? " active" : "");
    el.innerHTML = `
      <span class="id">${escapeHtml(item.contentId)}</span>
      <span class="badge ${item.lifecycleStatus}">${item.lifecycleStatus}</span>
    `;
    el.addEventListener("click", () => selectContent(item.contentId));
    list.appendChild(el);
  }
  $("count").textContent = `${matching.length} of ${state.items.length} content items`;
}

async function selectContent(id) {
  state.selected = id;
  renderList();
  $("detailTitle").textContent = id;
  const link = $("permalink");
  link.textContent = `/api/content/${id}/decision-trace`;
  link.href = `/api/content/${encodeURIComponent(id)}/decision-trace`;
  try {
    const s = await fetchJson(`/api/content/${encodeURIComponent(id)}/state`);
    $("state").classList.remove("empty");
    $("state").innerHTML = `
      <dl class="kv">
        <dt>contentId</dt><dd>${escapeHtml(s.contentId)}</dd>
        <dt>lifecycle</dt><dd><span class="badge ${s.lifecycleStatus}">${s.lifecycleStatus}</span></dd>
        <dt>last verification</dt><dd>${escapeHtml(s.lastVerificationStatus || "—")}</dd>
        <dt>last report</dt><dd>${escapeHtml(s.lastReportStatus || "—")}</dd>
        <dt>deleted</dt><dd>${s.deleted}</dd>
        <dt>restored</dt><dd>${s.restored}</dd>
        <dt>decision count</dt><dd>${s.decisionCount}</dd>
        <dt>first seen</dt><dd>${fmtTs(s.firstSeenAt)}</dd>
        <dt>last updated</dt><dd>${fmtTs(s.lastUpdatedAt)}</dd>
      </dl>
    `;
  } catch (e) {
    $("state").classList.add("empty");
    $("state").textContent = `Error: ${e.message}`;
  }
  try {
    const trace = await fetchJson(`/api/content/${encodeURIComponent(id)}/decision-trace`);
    renderTrace(trace.decisions || []);
  } catch (e) {
    $("trace").classList.add("empty");
    $("trace").textContent = `Error: ${e.message}`;
  }
}

function renderTrace(decisions) {
  if (!decisions.length) {
    $("trace").classList.add("empty");
    $("trace").textContent = "No decisions yet.";
    return;
  }
  $("trace").classList.remove("empty");
  const rows = decisions
    .map(
      (d) => `
      <tr>
        <td class="mono">${fmtTs(d.eventTime)}</td>
        <td><span class="badge ${badgeForType(d.eventType)}">${d.eventType}</span></td>
        <td>${escapeHtml(d.status || "—")}</td>
        <td class="mono">${escapeHtml(d.actor || "—")}</td>
        <td class="mono">${escapeHtml(d.sourceTopic || "")}</td>
        <td class="mono">${escapeHtml(d.correlationId || "")}</td>
      </tr>`
    )
    .join("");
  $("trace").innerHTML = `
    <table class="trace">
      <thead>
        <tr>
          <th>event time</th><th>type</th><th>status</th>
          <th>actor</th><th>source topic</th><th>correlation</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function badgeForType(t) {
  switch (t) {
    case "VERIFICATION": return "VERIFIED";
    case "REPORT": return "REPORTED_OPEN";
    case "DELETION": return "DELETED";
    case "OBJECTION_APPROVED": return "RESTORED";
    default: return "NEW";
  }
}

function fmtTs(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().replace("T", " ").replace("Z", "");
  } catch {
    return iso;
  }
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

$("refresh").addEventListener("click", refreshList);
$("filter").addEventListener("input", (e) => {
  state.filter = e.target.value;
  renderList();
});

let autoTimer = null;
function startAuto() {
  stopAuto();
  autoTimer = setInterval(() => {
    refreshList();
    if (state.selected) selectContent(state.selected);
  }, 3000);
}
function stopAuto() { if (autoTimer) clearInterval(autoTimer); }
$("autoRefresh").addEventListener("change", (e) => {
  if (e.target.checked) startAuto(); else stopAuto();
});

refreshStreamHealth();
refreshList();
startAuto();
setInterval(refreshStreamHealth, 5000);

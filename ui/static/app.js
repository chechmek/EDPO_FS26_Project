const $ = (id) => document.getElementById(id);
const state = {
  selected: null,
  activeTab: "content",
  items: [],
  filter: "",
  latest1m: null,
  latest5m: null,
  openCases: [],
  violations: [],
};

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

async function refreshSlaHealth() {
  try {
    const h = await fetchJson("/api/sla/health");
    const pill = $("slaPill");
    pill.textContent = `sla: ${h.status}`;
    pill.classList.toggle("ok", h.consumerReady);
    pill.classList.toggle("bad", !h.consumerReady);
  } catch {
    $("slaPill").textContent = "sla: unreachable";
    $("slaPill").classList.add("bad");
  }
}

async function refreshSlaData() {
  try {
    const [metrics, openCases, violations] = await Promise.all([
      fetchJson("/api/sla/metrics/verification"),
      fetchJson("/api/sla/sla/open-cases"),
      fetchJson("/api/sla/sla/violations?include_resolved=true"),
    ]);
    state.latest1m = metrics.latest1m || null;
    state.latest5m = metrics.latest5m || null;
    state.openCases = openCases.cases || [];
    state.violations = violations.violations || [];
    renderSlaOverview();
    renderSlaLists();
    renderSelectedSlaCase();
  } catch (e) {
    $("slaOpenCount").textContent = "err";
    $("slaViolationCount").textContent = "err";
    $("selectedSlaState").textContent = "err";
    $("slaOpenTable").textContent = `Error: ${e.message}`;
    $("slaViolationTable").textContent = `Error: ${e.message}`;
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
  renderSelectedSlaCase();
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

function renderSlaOverview() {
  renderMetricCard(state.latest1m, "m1");
  renderMetricCard(state.latest5m, "m5");
  $("slaOpenCount").textContent = state.openCases.length;
  $("slaViolationCount").textContent = state.violations.filter((v) => v.status === "open-breach").length;
}

function renderMetricCard(metric, prefix) {
  if (!metric) {
    $(`${prefix}Total`).textContent = "—";
    $(`${prefix}Verified`).textContent = "—";
    $(`${prefix}Rejected`).textContent = "—";
    $(`${prefix}TimedOut`).textContent = "—";
    return;
  }
  const counts = metric.counts || {};
  const rejected = (counts["rejected-peer"] || 0) + (counts["rejected-internal"] || 0) + (counts["rejected-unregistered"] || 0);
  $(`${prefix}Total`).textContent = metric.total ?? "—";
  $(`${prefix}Verified`).textContent = counts.verified ?? 0;
  $(`${prefix}Rejected`).textContent = rejected;
  $(`${prefix}TimedOut`).textContent = counts["timed-out"] ?? 0;
}

function renderSlaLists() {
  renderSlaList(
    $("slaOpenTable"),
    state.openCases,
    "No open SLA cases."
  );
  renderSlaList(
    $("slaViolationTable"),
    state.violations,
    "No SLA violations."
  );
}

function renderSlaList(target, items, emptyText) {
  if (!items.length) {
    target.classList.add("empty");
    target.textContent = emptyText;
    return;
  }
  target.classList.remove("empty");
  const rows = items
    .slice()
    .sort((a, b) => (b.openedAt || "").localeCompare(a.openedAt || ""))
    .slice(0, 10)
    .map((item) => {
      const badgeClass = badgeForSlaStatus(item.status);
      return `
        <button class="sla-list-item" type="button" data-content-id="${escapeHtml(item.contentId)}">
          <span class="sla-list-main">
            <span class="id mono">${escapeHtml(item.contentId)}</span>
            <span class="badge ${badgeClass}">${escapeHtml(item.status || "open")}</span>
          </span>
          <span class="sla-list-meta">opened ${fmtTs(item.openedAt)}</span>
        </button>
      `;
    })
    .join("");
  target.innerHTML = `<div class="sla-list">${rows}</div>`;
  for (const button of target.querySelectorAll(".sla-list-item")) {
    button.addEventListener("click", async () => {
      setActiveTab("content");
      await selectContent(button.dataset.contentId);
    });
  }
}

function renderSelectedSlaCase() {
  const target = $("slaCase");
  if (!state.selected) {
    target.classList.add("empty");
    target.textContent = "No selected content.";
    $("selectedSlaState").textContent = "—";
    return;
  }
  const openCase = state.openCases.find((item) => item.contentId === state.selected);
  const violation = state.violations.find((item) => item.contentId === state.selected);
  const item = violation || openCase;
  if (!item) {
    target.classList.add("empty");
    target.textContent = "No current SLA case for this content.";
    $("selectedSlaState").textContent = "none";
    return;
  }
  target.classList.remove("empty");
  $("selectedSlaState").textContent = item.status || "open";
  target.innerHTML = `
    <dl class="kv">
      <dt>contentId</dt><dd>${escapeHtml(item.contentId)}</dd>
      <dt>reportId</dt><dd>${escapeHtml(item.reportId || "—")}</dd>
      <dt>status</dt><dd>${escapeHtml(item.status || "open")}</dd>
      <dt>openedAt</dt><dd>${fmtTs(item.openedAt)}</dd>
      <dt>currentAgeSeconds</dt><dd>${escapeHtml(item.currentAgeSeconds ?? "—")}</dd>
      <dt>resolvedBy</dt><dd>${escapeHtml(item.resolvedBy || "—")}</dd>
    </dl>
  `;
}

function setActiveTab(tab) {
  state.activeTab = tab;
  const contentButton = $("contentTabButton");
  const slaButton = $("slaTabButton");
  const contentPanel = $("contentTab");
  const slaPanel = $("slaTab");
  const contentActive = tab === "content";
  contentButton.classList.toggle("active", contentActive);
  slaButton.classList.toggle("active", !contentActive);
  contentPanel.classList.toggle("active", contentActive);
  slaPanel.classList.toggle("active", !contentActive);
  contentPanel.hidden = !contentActive;
  slaPanel.hidden = contentActive;
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

function badgeForSlaStatus(status) {
  switch (status) {
    case "open-breach":
      return "DELETED";
    default:
      return "REPORTED_OPEN";
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
    refreshSlaData();
    if (state.selected) selectContent(state.selected);
  }, 3000);
}
function stopAuto() { if (autoTimer) clearInterval(autoTimer); }
$("autoRefresh").addEventListener("change", (e) => {
  if (e.target.checked) startAuto(); else stopAuto();
});
$("contentTabButton").addEventListener("click", () => setActiveTab("content"));
$("slaTabButton").addEventListener("click", () => setActiveTab("sla"));

refreshStreamHealth();
refreshSlaHealth();
refreshList();
refreshSlaData();
setActiveTab("content");
startAuto();
setInterval(refreshStreamHealth, 5000);
setInterval(refreshSlaHealth, 5000);

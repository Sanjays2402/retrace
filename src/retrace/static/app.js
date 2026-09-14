"use strict";
const $ = (id) => document.getElementById(id);
const escapeHTML = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const state = {
  runs: [],
  selected: null,
  task: null,
  detail: null,
  filter: "all",
  events: [],
  cursor: 0,
  busy: false,
};
const badge = (status) =>
  `<span class="badge ${escapeHTML(status)}">${escapeHTML(status)}</span>`;
const duration = (seconds) =>
  seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(1)} s`;
const clock = (seconds) =>
  new Date(seconds * 1000).toLocaleTimeString([], { hour12: false });
const effective = (run) =>
  run.status === "running" && run.lease_until < Date.now() / 1000
    ? "interrupted"
    : run.status;
const needsAttention = (run) =>
  ["failed", "paused", "interrupted"].includes(effective(run));
async function api(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
function renderRuns() {
  const focusedRun = document.activeElement?.dataset.run;
  $("run-count").textContent = state.runs.length;
  $("stat-total").textContent = state.runs.length;
  $("stat-success").textContent = state.runs.filter(
    (r) => r.status === "succeeded",
  ).length;
  $("stat-running").textContent = state.runs.filter(
    (r) => effective(r) === "running",
  ).length;
  $("stat-attention").textContent = state.runs.filter(needsAttention).length;
  const query = $("search").value.toLowerCase();
  const runs = state.runs.filter(
    (r) =>
      `${r.name} ${r.id}`.toLowerCase().includes(query) &&
      (state.filter === "all" ||
        (state.filter === "attention"
          ? needsAttention(r)
          : r.status === state.filter)),
  );
  $("run-list").innerHTML = runs.length
    ? runs
        .map(
          (r) =>
            `<button class="run-card ${r.id === state.selected ? "selected" : ""}" data-run="${escapeHTML(r.id)}" aria-pressed="${r.id === state.selected}"><span class="name">${escapeHTML(r.name)}</span><span class="id">${escapeHTML(r.id.slice(0, 20))}</span><span class="row">${badge(effective(r))}<time>${clock(r.created_at)}</time></span></button>`,
        )
        .join("")
    : '<p class="no-results">No matching runs.<br>Try another search or run <code>retrace demo</code>.</p>';
  if (focusedRun) {
    [...document.querySelectorAll("[data-run]")]
      .find((node) => node.dataset.run === focusedRun)
      ?.focus({ preventScroll: true });
  }
}
function renderGraph(detail) {
  const focusedTask = document.activeElement?.dataset.task;
  const definitions = detail.run.manifest.tasks;
  const levels = new Map();
  const byName = new Map(definitions.map((t) => [t.name, t]));
  function level(name) {
    if (!levels.has(name))
      levels.set(name, Math.max(-1, ...byName.get(name).needs.map(level)) + 1);
    return levels.get(name);
  }
  definitions.forEach((t) => level(t.name));
  const columns = [];
  definitions.forEach((t) => {
    const n = levels.get(t.name);
    (columns[n] ||= []).push(t);
  });
  const maxRows = Math.max(...columns.map((c) => c.length));
  const height = Math.max(268, maxRows * 84 + 56);
  const width = Math.max(460, columns.length * 155 + 35);
  const positions = new Map();
  columns.forEach((column, i) =>
    column.forEach((t, j) =>
      positions.set(t.name, {
        x: 22 + i * 155,
        y: height / 2 - column.length * 42 + j * 84 + 12,
      }),
    ),
  );
  let paths = "";
  definitions.forEach((t) =>
    t.needs.forEach((dep) => {
      const from = positions.get(dep),
        to = positions.get(t.name);
      const x1 = from.x + 128,
        y1 = from.y + 30,
        x2 = to.x,
        y2 = to.y + 30;
      paths += `<path class="edge" d="M${x1} ${y1} C${x1 + 16} ${y1},${x2 - 16} ${y2},${x2} ${y2}"/>`;
    }),
  );
  $("graph").style.width = `${width}px`;
  $("graph").style.height = `${height}px`;
  $("graph").innerHTML =
    `<svg width="${width}" height="${height}" aria-hidden="true">${paths}</svg>` +
    definitions
      .map((t) => {
        const p = positions.get(t.name),
          s = detail.tasks[t.name];
        const icon =
          s.status === "succeeded"
            ? "✓"
            : s.status === "failed"
              ? "!"
              : s.status === "running"
                ? "◌"
                : "·";
        return `<button class="node ${escapeHTML(s.status)} ${t.name === state.task ? "selected" : ""}" data-task="${escapeHTML(t.name)}" aria-label="${escapeHTML(t.name)}: ${escapeHTML(s.status)}" style="left:${p.x}px;top:${p.y}px"><span class="node-name">${escapeHTML(t.name)}<span class="check">${icon}</span></span><span class="node-state">${escapeHTML(s.status)} · ${s.attempts} attempt${s.attempts === 1 ? "" : "s"}</span></button>`;
      })
      .join("");
  if (focusedTask) {
    [...document.querySelectorAll("[data-task]")]
      .find((node) => node.dataset.task === focusedTask)
      ?.focus({ preventScroll: true });
  }
}
function renderTimeline(detail) {
  const attempts = detail.attempts;
  if (!attempts.length) {
    $("timeline-view").innerHTML = '<p class="muted">No attempts yet.</p>';
    return;
  }
  const start = Math.min(...attempts.map((a) => a.started_at));
  const end = Math.max(
    ...attempts.map((a) => a.finished_at || Date.now() / 1000),
  );
  const span = Math.max(0.001, end - start);
  $("timeline-view").innerHTML =
    '<div class="small-label">WALL-CLOCK ATTEMPTS · FAILED AND INTERRUPTED WORK REMAINS VISIBLE</div>' +
    attempts
      .map(
        (a) =>
          `<div class="attempt"><span>${escapeHTML(a.task_name)} <small>#${a.number}</small></span><div class="attempt-track" title="${escapeHTML(a.status)}"><div class="attempt-bar ${escapeHTML(a.status)}" style="left:${(100 * (a.started_at - start)) / span}%;width:${(100 * ((a.finished_at || end) - a.started_at)) / span}%"></div></div><small>${duration((a.finished_at || end) - a.started_at)}<br>${escapeHTML(a.status)}</small></div>`,
      )
      .join("");
}
function renderTask() {
  if (!state.detail) return;
  const task = state.detail.tasks[state.task];
  if (!task) return;
  $("task-title").textContent = task.name;
  $("task-badge").textContent = task.status;
  $("task-badge").className = `badge ${task.status}`;
  $("task-meta").textContent =
    `${task.attempts} attempts · ${task.failures} failures · ${task.finished_at && task.started_at ? duration(task.finished_at - task.started_at) : "awaiting checkpoint"}`;
  $("task-output").textContent =
    task.error ||
    (task.status === "succeeded"
      ? JSON.stringify(task.output, null, 2)
      : "No committed output yet.");
}
function renderDetail() {
  const detail = state.detail;
  if (!detail) return;
  const run = detail.run;
  $("empty").hidden = true;
  $("run-detail").hidden = false;
  $("run-id").textContent = `RUN / ${run.id}`;
  $("workflow-name").textContent = run.name;
  $("run-status").textContent = effective(run);
  $("run-status").className = `badge ${effective(run)}`;
  $("run-version").textContent = `Definition v${run.version}`;
  $("run-duration").textContent = duration(
    (run.status === "running" ? Date.now() / 1000 : run.updated_at) -
      run.created_at,
  );
  $("run-epoch").textContent = `Worker epoch ${run.epoch}`;
  const tasks = Object.values(detail.tasks);
  $("checkpoint-count").textContent =
    `${tasks.filter((t) => t.status === "succeeded").length}/${tasks.length} checkpoints`;
  if (!state.task || !detail.tasks[state.task])
    state.task = tasks.find((t) => t.failures > 0)?.name || tasks[0]?.name;
  renderGraph(detail);
  renderTimeline(detail);
  renderTask();
}
function renderEvents() {
  $("event-count").textContent =
    `${state.events.length}${state.events.length >= 1000 ? "+" : ""} events`;
  $("event-list").innerHTML = state.events
    .slice()
    .reverse()
    .map(
      (e) =>
        `<div class="event"><time>${clock(e.at)}</time><div><strong>${escapeHTML(e.kind)}</strong><small>${escapeHTML(e.task_name || `run · epoch ${e.payload.epoch || state.detail?.run.epoch || 1}`)}${e.payload.error ? ` · ${escapeHTML(e.payload.error)}` : ""}</small></div></div>`,
    )
    .join("");
}
async function refresh() {
  if (state.busy) return;
  state.busy = true;
  try {
    state.runs = (await api("/api/runs")).runs;
    if (!state.selected && state.runs.length) state.selected = state.runs[0].id;
    renderRuns();
    if (state.selected) {
      const selected = state.selected;
      const [detail, journal] = await Promise.all([
        api(`/api/runs/${encodeURIComponent(selected)}`),
        api(
          `/api/runs/${encodeURIComponent(selected)}/events?after=${state.cursor}`,
        ),
      ]);
      if (selected !== state.selected) return;
      state.detail = detail;
      state.cursor = journal.cursor;
      state.events = [...state.events, ...journal.events].slice(-1000);
      renderDetail();
      renderEvents();
    }
    $("connection").innerHTML =
      '<span class="dot"></span>Live · polling every second';
    $("connection").className = "connection";
  } catch (error) {
    $("connection").textContent = "Connection lost · retrying";
    $("connection").className = "connection offline";
  } finally {
    state.busy = false;
  }
}
$("search").addEventListener("input", renderRuns);
$("refresh").addEventListener("click", refresh);
document.querySelectorAll("[data-filter]").forEach((button) =>
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    document
      .querySelectorAll("[data-filter]")
      .forEach((b) => b.classList.toggle("selected", b === button));
    renderRuns();
  }),
);
$("run-list").addEventListener("click", (event) => {
  const card = event.target.closest("[data-run]");
  if (!card || card.dataset.run === state.selected) return;
  state.selected = card.dataset.run;
  state.task = null;
  state.events = [];
  state.cursor = 0;
  state.detail = null;
  $("run-detail").hidden = true;
  renderRuns();
  refresh();
});
$("graph").addEventListener("click", (event) => {
  const node = event.target.closest("[data-task]");
  if (!node) return;
  state.task = node.dataset.task;
  document
    .querySelectorAll("[data-task]")
    .forEach((n) =>
      n.classList.toggle("selected", n.dataset.task === state.task),
    );
  renderTask();
});
document.querySelectorAll("[data-view]").forEach((button) =>
  button.addEventListener("click", () => {
    document
      .querySelectorAll("[data-view]")
      .forEach((b) => b.setAttribute("aria-selected", String(b === button)));
    $("graph-view").hidden = button.dataset.view !== "graph";
    $("timeline-view").hidden = button.dataset.view !== "timeline";
  }),
);
refresh();
setInterval(refresh, 1000);

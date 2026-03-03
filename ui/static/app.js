const state = {
  lastEventId: 0,
  pollTimer: null,
  running: false,
  latestRunId: null,
  particles: [],
  stats: {
    visited: 0,
    queued: 0,
    errors: 0,
    skipped: 0,
    max_depth_seen: 0,
  },
};

const eventsEl = document.getElementById("events");
const statusPill = document.getElementById("status-pill");
const startBtn = document.getElementById("start-btn");
const outputMeta = document.getElementById("output-meta");
const resultsBody = document.getElementById("results-body");

const depthRing = document.getElementById("depth-ring");
const depthText = document.getElementById("depth-text");
const ringCircumference = 2 * Math.PI * 48;

const statEls = {
  visited: document.getElementById("visited-count"),
  queued: document.getElementById("queued-count"),
  errors: document.getElementById("error-count"),
  skipped: document.getElementById("skipped-count"),
};

const canvas = document.getElementById("flow-canvas");
const ctx = canvas.getContext("2d");

function setStatus(kind, text) {
  statusPill.className = `status ${kind}`;
  statusPill.textContent = text;
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function fmtDateTime(ts) {
  if (!ts) return "n/a";
  return new Date(ts * 1000).toLocaleString();
}

function eventText(evt) {
  if (evt.type === "visit") {
    return `${evt.status} ${evt.url}`;
  }
  if (evt.type === "enqueue") {
    return `Queued ${evt.url}`;
  }
  if (evt.type === "error") {
    return `Error ${evt.url} -> ${evt.error || ""}`;
  }
  if (evt.type === "skip") {
    return `Skipped ${evt.url} (${evt.reason})`;
  }
  if (evt.type === "start") {
    return `Started crawl on ${evt.url}`;
  }
  if (evt.type === "complete") {
    return `Crawl complete. Captured ${evt.count || 0} pages`;
  }
  if (evt.type === "fatal") {
    return `Fatal error: ${evt.error || ""}`;
  }
  return JSON.stringify(evt);
}

function addEvent(evt) {
  const div = document.createElement("div");
  div.className = `event ${evt.type || "default"}`;
  div.innerHTML = `
    <div class="meta">${fmtTime(evt.ts)} • ${evt.type || "event"} • depth ${evt.depth ?? 0}</div>
    <div class="line">${eventText(evt)}</div>
  `;
  eventsEl.prepend(div);
  while (eventsEl.children.length > 180) {
    eventsEl.removeChild(eventsEl.lastChild);
  }
}

function animateNumber(el, target) {
  const current = Number(el.textContent || "0");
  const delta = target - current;
  if (Math.abs(delta) < 1) {
    el.textContent = String(target);
    return;
  }
  el.textContent = String(Math.round(current + delta * 0.35));
}

function clearResultsTable(message) {
  resultsBody.innerHTML = "";
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 4;
  cell.className = "empty-cell";
  cell.textContent = message;
  row.appendChild(cell);
  resultsBody.appendChild(row);
}

function renderStoredResults(run, results) {
  if (!run) {
    outputMeta.textContent = "No crawl output saved yet.";
    clearResultsTable("Run a crawl to see stored results.");
    state.latestRunId = null;
    return;
  }

  const status = String(run.status || "").toUpperCase();
  outputMeta.textContent =
    `Run #${run.id} • ${status} • ${run.results_count || 0} pages • ` +
    `${fmtDateTime(run.started_at)} -> ${fmtDateTime(run.ended_at)}`;
  state.latestRunId = run.id;

  if (!results || results.length === 0) {
    clearResultsTable("No rows were captured for this run.");
    return;
  }

  resultsBody.innerHTML = "";
  for (const rowData of results) {
    const row = document.createElement("tr");

    const depthCell = document.createElement("td");
    depthCell.textContent = String(rowData.depth ?? 0);
    row.appendChild(depthCell);

    const statusCell = document.createElement("td");
    statusCell.textContent = String(rowData.status ?? 0);
    row.appendChild(statusCell);

    const typeCell = document.createElement("td");
    typeCell.textContent = String(rowData.content_type || "");
    row.appendChild(typeCell);

    const urlCell = document.createElement("td");
    const link = document.createElement("a");
    link.href = rowData.url;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = rowData.url;
    urlCell.appendChild(link);
    row.appendChild(urlCell);

    resultsBody.appendChild(row);
  }
}

async function loadLatestResults(force = false) {
  try {
    const res = await fetch("/api/results/latest");
    const payload = await res.json();
    if (!payload.ok) return;

    if (!payload.run) {
      if (force || state.latestRunId !== null) {
        renderStoredResults(null, []);
      }
      return;
    }

    if (!force && state.latestRunId === payload.run.id) {
      return;
    }

    renderStoredResults(payload.run, payload.results || []);
  } catch (err) {
    // Keep current output visible on transient fetch errors.
  }
}

function updateStats(nextStats) {
  state.stats = nextStats;
  animateNumber(statEls.visited, nextStats.visited || 0);
  animateNumber(statEls.queued, nextStats.queued || 0);
  animateNumber(statEls.errors, nextStats.errors || 0);
  animateNumber(statEls.skipped, nextStats.skipped || 0);

  const maxDepth = Math.max(1, nextStats.max_depth_seen || 0);
  depthText.textContent = String(nextStats.max_depth_seen || 0);
  const progress = Math.min(maxDepth / 10, 1);
  depthRing.style.strokeDashoffset = String(ringCircumference * (1 - progress));
}

function addParticle(evt) {
  const level = Math.max(0, Math.min(8, Number(evt.depth || 0)));
  state.particles.push({
    x: 100 + level * 115 + Math.random() * 40,
    y: 80 + Math.random() * (canvas.height - 160),
    vx: 0.5 + Math.random() * 1.2,
    vy: (Math.random() - 0.5) * 0.35,
    life: 1.0,
    type: evt.type || "enqueue",
  });
  if (state.particles.length > 320) {
    state.particles = state.particles.slice(-240);
  }
}

function drawFlowBackground() {
  ctx.fillStyle = "rgba(4,14,19,0.16)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "rgba(119, 178, 188, 0.20)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 9; i += 1) {
    const x = (canvas.width / 9) * i;
    ctx.beginPath();
    ctx.moveTo(x, 20);
    ctx.lineTo(x, canvas.height - 20);
    ctx.stroke();
  }
}

function particleColor(type, alpha) {
  if (type === "visit") return `rgba(82,209,200,${alpha})`;
  if (type === "error") return `rgba(255,107,107,${alpha})`;
  if (type === "skip") return `rgba(255,209,102,${alpha})`;
  return `rgba(177,230,236,${alpha})`;
}

function animateCanvas() {
  drawFlowBackground();
  for (const p of state.particles) {
    p.x += p.vx;
    p.y += p.vy;
    p.life -= 0.006;
    ctx.beginPath();
    ctx.fillStyle = particleColor(p.type, Math.max(0, p.life));
    ctx.arc(p.x, p.y, 3.4, 0, Math.PI * 2);
    ctx.fill();
  }
  state.particles = state.particles.filter((p) => p.life > 0 && p.x < canvas.width - 10);
  requestAnimationFrame(animateCanvas);
}

async function pollEvents() {
  try {
    const res = await fetch(`/api/events?after=${state.lastEventId}`);
    const payload = await res.json();
    if (!payload.ok) return;

    for (const evt of payload.events || []) {
      state.lastEventId = Math.max(state.lastEventId, evt.id || 0);
      addEvent(evt);
      addParticle(evt);
    }

    if (payload.state) {
      updateStats(payload.state.stats || state.stats);
      if (payload.state.running) {
        if (!state.running) {
          state.running = true;
          setStatus("running", "RUNNING");
          startBtn.disabled = true;
        }
      } else if (state.running) {
        state.running = false;
        setStatus("done", "COMPLETED");
        startBtn.disabled = false;
        loadLatestResults(true);
      }
    }
  } catch (err) {
    setStatus("idle", "OFFLINE");
  }
}

async function startCrawl() {
  const payload = {
    start_url: document.getElementById("start-url").value.trim(),
    max_pages: Number(document.getElementById("max-pages").value || 30),
    max_depth: Number(document.getElementById("max-depth").value || 2),
    delay: Number(document.getElementById("delay").value || 0.2),
    timeout: Number(document.getElementById("timeout").value || 10),
    allow_external: document.getElementById("allow-external").checked,
    respect_robots: document.getElementById("respect-robots").checked,
  };

  setStatus("running", "STARTING");
  const res = await fetch("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!data.ok) {
    setStatus("idle", "FAILED");
    alert(data.message || "Could not start crawl");
    return;
  }

  eventsEl.innerHTML = "";
  state.lastEventId = 0;
  state.running = true;
  state.particles = [];
  outputMeta.textContent = "Crawl in progress. Output will be saved to SQLite when complete.";
  clearResultsTable("Waiting for crawl to complete...");
  setStatus("running", "RUNNING");
  startBtn.disabled = true;
}

function init() {
  setStatus("idle", "IDLE");
  startBtn.addEventListener("click", startCrawl);
  animateCanvas();
  pollEvents();
  loadLatestResults(true);
  state.pollTimer = setInterval(pollEvents, 550);
}

init();

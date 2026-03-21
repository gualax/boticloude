/* ── Polymarket Bot Dashboard — Frontend ────────────────────────────── */

const POLL_INTERVAL = 3000;  // 3 seconds

// ── Chart setup ─────────────────────────────────────────────────────

const chartDefaults = {
  responsive: true,
  animation: { duration: 300 },
  plugins: { legend: { display: false } },
  scales: {
    x: { ticks: { color: "#8b949e", maxTicksLimit: 12 }, grid: { color: "#30363d" } },
    y: { ticks: { color: "#8b949e" }, grid: { color: "#30363d" } },
  },
};

const portfolioChart = new Chart(document.getElementById("chart-portfolio"), {
  type: "line",
  data: {
    labels: [],
    datasets: [{
      data: [],
      borderColor: "#58a6ff",
      backgroundColor: "rgba(88,166,255,0.08)",
      fill: true,
      tension: 0.3,
      pointRadius: 2,
      borderWidth: 2,
    }],
  },
  options: {
    ...chartDefaults,
    scales: {
      ...chartDefaults.scales,
      y: { ...chartDefaults.scales.y, ticks: { ...chartDefaults.scales.y.ticks, callback: v => "$" + v } },
    },
  },
});

const pnlChart = new Chart(document.getElementById("chart-pnl"), {
  type: "bar",
  data: {
    labels: [],
    datasets: [{
      data: [],
      backgroundColor: [],
      borderRadius: 3,
      barPercentage: 0.7,
    }],
  },
  options: {
    ...chartDefaults,
    scales: {
      ...chartDefaults.scales,
      y: { ...chartDefaults.scales.y, ticks: { ...chartDefaults.scales.y.ticks, callback: v => v + "%" } },
    },
  },
});

// ── Data fetching ───────────────────────────────────────────────────

async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

// ── Update functions ────────────────────────────────────────────────

function pnlClass(val) { return val >= 0 ? "pnl-pos" : "pnl-neg"; }
function pnlStr(val)   { return (val >= 0 ? "+$" : "-$") + Math.abs(val).toFixed(2); }

async function updateSummary() {
  const d = await fetchJSON("/api/summary");
  if (!d || d.error) {
    document.getElementById("status-badge").textContent = "OFF";
    document.getElementById("status-badge").className = "badge badge-off";
    return;
  }

  document.getElementById("kpi-portfolio").textContent = "$" + d.portfolio_value.toFixed(2);
  document.getElementById("kpi-balance").textContent = "$" + d.balance.toFixed(2);

  const unEl = document.getElementById("kpi-unrealized");
  unEl.textContent = pnlStr(d.unrealized_pnl);
  unEl.className = "kpi-value " + pnlClass(d.unrealized_pnl);

  const reEl = document.getElementById("kpi-realized");
  reEl.textContent = pnlStr(d.realized_pnl);
  reEl.className = "kpi-value " + pnlClass(d.realized_pnl);

  const retEl = document.getElementById("kpi-return");
  retEl.textContent = (d.total_return_pct >= 0 ? "+" : "") + d.total_return_pct.toFixed(2) + "%";
  retEl.className = "kpi-value " + pnlClass(d.total_return_pct);

  document.getElementById("kpi-trades").textContent = d.total_trades;
  document.getElementById("cycle-counter").textContent = "Ciclo #" + d.cycle;

  // Version
  if (d.version) {
    document.getElementById("bot-version").textContent = "v" + d.version;
  }

  // Status badge
  const sb = document.getElementById("status-badge");
  if (d.is_running) { sb.textContent = "ON"; sb.className = "badge badge-on"; }
  else              { sb.textContent = "OFF"; sb.className = "badge badge-off"; }

  // Mode badge
  const mb = document.getElementById("mode-badge");
  if (d.mode === "LIVE") { mb.textContent = "LIVE"; mb.className = "badge badge-live"; }
  else                   { mb.textContent = "PAPER"; mb.className = "badge badge-paper"; }
}

async function updatePositions() {
  const data = await fetchJSON("/api/positions");
  if (!data) return;

  const tbody = document.querySelector("#tbl-positions tbody");
  const empty = document.getElementById("pos-empty");
  document.getElementById("pos-count").textContent = data.length;

  if (data.length === 0) {
    tbody.innerHTML = "";
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";

  tbody.innerHTML = data.map(p => `
    <tr>
      <td title="${p.market_name}">${p.market_name.substring(0, 50)}</td>
      <td>${p.side}</td>
      <td>$${p.entry_price.toFixed(4)}</td>
      <td>$${p.current_price.toFixed(4)}</td>
      <td>${p.size}</td>
      <td class="${pnlClass(p.pnl)}">${pnlStr(p.pnl)}</td>
      <td class="${pnlClass(p.pnl_pct)}">${p.pnl_pct > 0 ? "+" : ""}${p.pnl_pct.toFixed(1)}%</td>
    </tr>
  `).join("");
}

async function updateSignals() {
  const data = await fetchJSON("/api/signals");
  if (!data) return;

  const tbody = document.querySelector("#tbl-signals tbody");
  const empty = document.getElementById("sig-empty");
  document.getElementById("sig-count").textContent = data.length;

  if (data.length === 0) {
    tbody.innerHTML = "";
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";

  tbody.innerHTML = data.map(s => `
    <tr>
      <td title="${s.market_name}">${s.market_name.substring(0, 45)}</td>
      <td>${s.side}</td>
      <td>$${s.current_price.toFixed(4)}</td>
      <td>$${s.fair_value.toFixed(4)}</td>
      <td class="pnl-pos">+${s.edge.toFixed(1)}%</td>
      <td>${s.confidence}%</td>
      <td><span class="strategy-tag">${s.strategy}</span></td>
    </tr>
  `).join("");
}

async function updateTrades() {
  const data = await fetchJSON("/api/trades?limit=30");
  if (!data) return;

  const tbody = document.querySelector("#tbl-trades tbody");
  const empty = document.getElementById("trade-empty");
  document.getElementById("trade-count").textContent = data.length;

  if (data.length === 0) {
    tbody.innerHTML = "";
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";

  tbody.innerHTML = data.map(t => {
    const actionClass = t.action === "buy" ? "action-buy" : "action-sell";
    const pnlCell = t.action === "sell"
      ? `<td class="${pnlClass(t.pnl)}">${pnlStr(t.pnl)}</td>`
      : `<td class="dim">—</td>`;
    return `
      <tr>
        <td>${t.time_str}</td>
        <td title="${t.market_name}">${t.market_name.substring(0, 40)}</td>
        <td class="${actionClass}">${t.action}</td>
        <td>${t.side}</td>
        <td>$${t.price.toFixed(4)}</td>
        <td>${t.size}</td>
        <td>$${t.cost.toFixed(2)}</td>
        ${pnlCell}
      </tr>
    `;
  }).join("");
}

async function updateCharts() {
  const data = await fetchJSON("/api/history");
  if (!data || data.length === 0) return;

  // Portfolio chart
  portfolioChart.data.labels = data.map(d => d.time_str);
  portfolioChart.data.datasets[0].data = data.map(d => d.portfolio_value);
  portfolioChart.update("none");

  // PnL bar chart
  pnlChart.data.labels = data.map(d => "#" + d.cycle);
  pnlChart.data.datasets[0].data = data.map(d => d.return_pct);
  pnlChart.data.datasets[0].backgroundColor = data.map(d =>
    d.return_pct >= 0 ? "rgba(63,185,80,0.7)" : "rgba(248,81,73,0.7)"
  );
  pnlChart.update("none");
}

async function updateLogs() {
  const data = await fetchJSON("/api/logs?limit=60");
  if (!data) return;

  const el = document.getElementById("log-output");
  const wasAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;

  el.innerHTML = data.map(l => {
    let cls = "log-info";
    if (l.level === "WARNING") cls = "log-warn";
    if (l.level === "ERROR")   cls = "log-err";
    return `<div class="log-line"><span class="log-ts">${l.ts}</span><span class="${cls}">${escapeHtml(l.msg)}</span></div>`;
  }).join("");

  if (wasAtBottom) el.scrollTop = el.scrollHeight;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ── Bot controls ────────────────────────────────────────────────────

async function startBot() {
  await fetch("/api/bot/start", { method: "POST" });
}

async function stopBot() {
  await fetch("/api/bot/stop", { method: "POST" });
}

// ── Main loop ───────────────────────────────────────────────────────

async function refresh() {
  await Promise.all([
    updateSummary(),
    updatePositions(),
    updateSignals(),
    updateTrades(),
    updateCharts(),
    updateLogs(),
  ]);
}

// Initial load + polling
refresh();
setInterval(refresh, POLL_INTERVAL);

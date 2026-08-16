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
    // Session expired — bounce back to the login page
    if (r.status === 401) { window.location.href = "/login"; return null; }
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

// ── Crypto spot prices ───────────────────────────────────────────────

async function updateCrypto() {
  const data = await fetchJSON("/api/crypto");
  const row = document.getElementById("crypto-row");
  if (!data || !data.enabled || !data.assets || Object.keys(data.assets).length === 0) {
    row.hidden = true;
    return;
  }
  row.hidden = false;

  const strip = document.getElementById("crypto-strip");
  strip.innerHTML = Object.entries(data.assets).map(([sym, d]) => {
    const up = d.change_24h >= 0;
    // The sign glyph carries direction too, so color is never the only cue.
    const sign = up ? "+" : "";
    const cls = up ? "pnl-pos" : "pnl-neg";
    const vol = d.annualized_vol !== null && d.annualized_vol !== undefined
      ? `vol ${d.annualized_vol}%` : "";
    return `
      <div class="crypto-tile">
        <span class="crypto-symbol">${sym}</span>
        <span class="crypto-price">$${d.spot.toLocaleString("en-US", {maximumFractionDigits: 2})}</span>
        <span class="crypto-change ${cls}">${sign}${d.change_24h.toFixed(2)}%</span>
        <span class="crypto-vol">${vol}</span>
      </div>`;
  }).join("");
}

// ── Hermes ───────────────────────────────────────────────────────────

function conditionChips(when) {
  return Object.entries(when || {})
    .map(([k, v]) => `<span class="condition-chip">${k}: ${v}</span>`)
    .join("");
}

function confidenceMeter(value) {
  const pct = Math.round(value * 100);
  return `<span class="confidence-meter">
      <span class="confidence-bar"><span class="confidence-fill" style="width:${pct}%"></span></span>
      <span>${pct}%</span>
    </span>`;
}

async function updateHermes() {
  const d = await fetchJSON("/api/hermes");
  if (!d || d.error) return;

  const status = document.getElementById("hermes-status");
  if (!d.enabled)        { status.textContent = "apagado"; }
  else if (!d.has_brain) { status.textContent = "sin API key"; }
  else                   { status.textContent = "aprendiendo"; }

  const s = d.summary;
  document.getElementById("hermes-stats").innerHTML = `
    <div class="hermes-stat">
      <div class="hermes-stat-label">Trades Aprendidos</div>
      <div class="hermes-stat-value">${s.total_trades_learned_from}</div>
    </div>
    <div class="hermes-stat">
      <div class="hermes-stat-label">Tasa de Acierto</div>
      <div class="hermes-stat-value ${s.win_rate >= 0.5 ? "pnl-pos" : "pnl-neg"}">${(s.win_rate * 100).toFixed(0)}%</div>
    </div>
    <div class="hermes-stat">
      <div class="hermes-stat-label">PnL Acumulado</div>
      <div class="hermes-stat-value ${pnlClass(s.cumulative_pnl)}">${pnlStr(s.cumulative_pnl)}</div>
    </div>
    <div class="hermes-stat">
      <div class="hermes-stat-label">Lecciones</div>
      <div class="hermes-stat-value">${s.lessons}</div>
    </div>
    <div class="hermes-stat">
      <div class="hermes-stat-label">Reflexiones</div>
      <div class="hermes-stat-value">${s.reflections}</div>
    </div>
    <div class="hermes-stat">
      <div class="hermes-stat-label">Proxima en</div>
      <div class="hermes-stat-value">${d.reflect_due_in} trades</div>
    </div>`;

  const insight = document.getElementById("hermes-insight");
  if (d.last_insight) {
    insight.hidden = false;
    insight.textContent = d.last_insight;
  } else {
    insight.hidden = true;
  }

  // Lessons
  const lessonsBody = document.querySelector("#tbl-lessons tbody");
  document.getElementById("lesson-count").textContent = d.lessons.length;
  document.getElementById("lessons-empty").style.display =
    d.lessons.length ? "none" : "block";
  lessonsBody.innerHTML = d.lessons.map(l => `
    <tr>
      <td>${l.id}</td>
      <td class="lesson-text">${escapeHtml(l.text)}</td>
      <td>${conditionChips(l.when)}</td>
      <td><span class="action-badge action-${l.action}">${l.action}</span></td>
      <td>${confidenceMeter(l.confidence)}</td>
      <td>${l.evidence_count}</td>
      <td>${l.times_applied}</td>
    </tr>`).join("");

  // Strategy stats
  const stratBody = document.querySelector("#tbl-strategy-stats tbody");
  const strategies = Object.entries(d.strategy_stats || {});
  document.getElementById("strategy-empty").style.display =
    strategies.length ? "none" : "block";
  stratBody.innerHTML = strategies.map(([name, st]) => `
    <tr>
      <td>${name}</td>
      <td>${st.trades}</td>
      <td>${st.wins}</td>
      <td class="${st.win_rate >= 0.5 ? "pnl-pos" : "pnl-neg"}">${(st.win_rate * 100).toFixed(0)}%</td>
      <td class="${pnlClass(st.pnl)}">${pnlStr(st.pnl)}</td>
      <td class="${pnlClass(st.avg_pnl)}">${pnlStr(st.avg_pnl)}</td>
    </tr>`).join("");

  // Recent verdicts
  const verdictBody = document.querySelector("#tbl-verdicts tbody");
  const verdicts = d.recent_verdicts || [];
  document.getElementById("verdicts-empty").style.display =
    verdicts.length ? "none" : "block";
  verdictBody.innerHTML = verdicts.map(v => {
    const label = v.approved
      ? `<span class="action-badge action-size_down">x${v.multiplier}</span>`
      : `<span class="action-badge action-avoid">vetado</span>`;
    return `
      <tr>
        <td>${v.time}</td>
        <td title="${escapeHtml(v.market)}">${escapeHtml(v.market.substring(0, 45))}</td>
        <td>${v.strategy}</td>
        <td>${label}</td>
        <td class="lesson-text">${escapeHtml(v.reason)}</td>
      </tr>`;
  }).join("");
}

async function hermesReflect() {
  const btn = document.getElementById("btn-reflect");
  btn.textContent = "Reflexionando...";
  btn.disabled = true;
  try {
    const resp = await fetch("/api/hermes/reflect", { method: "POST" });
    const data = await resp.json();
    if (data.error) {
      alert("Error: " + data.error);
    } else {
      await updateHermes();
      alert(`Hermes reviso ${data.trades_reviewed} trades.\n` +
            `+${data.added} lecciones, ${data.updated} actualizadas, ` +
            `${data.removed} descartadas.`);
    }
  } catch {
    alert("Error de conexion");
  } finally {
    btn.textContent = "Reflexionar Ahora";
    btn.disabled = false;
  }
}

// ── AI Analysis ──────────────────────────────────────────────────────

function markdownToHtml(md) {
  // Simple markdown → HTML for the analysis display
  return md
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>')
    .replace(/<\/ul>\s*<ul>/g, '')
    .replace(/\n{2,}/g, '<br><br>')
    .replace(/\n/g, '<br>');
}

let _analysisLoaded = false;

async function updateAnalysis() {
  const data = await fetchJSON("/api/analysis/latest");
  if (!data || data.error) return;

  _analysisLoaded = true;
  const dateEl = document.getElementById("analysis-date");
  dateEl.textContent = data.date;

  const output = document.getElementById("analysis-output");
  let html = markdownToHtml(data.analysis);

  // Show applied changes if any
  if (data.applied_changes && data.applied_changes.length > 0) {
    html += '<h2>Cambios Auto-Aplicados</h2><ul>';
    for (const c of data.applied_changes) {
      html += `<li><strong>${c.param}</strong>: ${c.old} → ${c.new} — ${c.reason}</li>`;
    }
    html += '</ul>';
  }

  output.innerHTML = html;
}

async function runAnalysis() {
  const btn = document.getElementById("btn-run-analysis");
  btn.textContent = "Analizando...";
  btn.disabled = true;
  try {
    const resp = await fetch("/api/analysis/run", { method: "POST" });
    const data = await resp.json();
    if (data.error) {
      alert("Error: " + data.error);
    } else {
      await updateAnalysis();
    }
  } catch (e) {
    alert("Error de conexion");
  } finally {
    btn.textContent = "Ejecutar Ahora";
    btn.disabled = false;
  }
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
    updateCrypto(),
    updateHermes(),
  ]);
  // Load analysis only once (it updates daily, no need to poll)
  if (!_analysisLoaded) updateAnalysis();
}

// Initial load + polling
refresh();
setInterval(refresh, POLL_INTERVAL);

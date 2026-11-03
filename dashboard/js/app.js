/* LactoGrid operations view. Colours come from CSS variables. */

const view = document.querySelector("#view");
const siteName = document.querySelector("#site-name");
const updatedLine = document.querySelector("#updated");
const topStatus = document.querySelector("#top-status");
const loginScreen = document.querySelector("#login-screen");
const workspace = document.querySelector("#workspace");
const loginError = document.querySelector("#login-error");
const passwordInput = document.querySelector("#password");
const nameInput = document.querySelector("#name");

const EYE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="12" r="2.5" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M9.9 5.2A10 10 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-3.2 4.2M6.1 6.1C3.5 7.8 2 12 2 12s3.5 6 10 6c1.5 0 2.8-.3 4-.8" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
const CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4 4L19 7" fill="none" stroke="currentColor" stroke-width="2.2"/></svg>';

const COPY = {
  en: {
    heroTitle: "Cold milk,<br>powered by sun.",
    heroLead: "Monitor your cooling hub, see what it is doing and why, and keep proof that the milk stayed safe.",
    sample: "Sample site",
    safe: "Milk safe",
    formTitle: "Sign in",
    formLead: "Use the account your cooperative or LactoGrid gave you.",
    name: "Email or phone",
    password: "Password",
    keep: "Keep me signed in",
    forgot: "Forgot password?",
    submit: "Sign in",
    none: "No account? Ask your site administrator.",
    bad: "That name or password was not accepted.",
    reset: "Password reset is not connected. For this presentation, use one of these accounts.",
  },
  sw: {
    heroTitle: "Maziwa baridi,<br>kwa nguvu ya jua.",
    heroLead: "Angalia kituo cha kupoza, uone kinafanya nini na kwa nini, na uweke uthibitisho kuwa maziwa yalibaki salama.",
    sample: "Kituo cha mfano",
    safe: "Maziwa salama",
    formTitle: "Ingia",
    formLead: "Tumia akaunti uliyopewa na ushirika au LactoGrid.",
    name: "Barua pepe au simu",
    password: "Nenosiri",
    keep: "Nikumbuke",
    forgot: "Umesahau nenosiri?",
    submit: "Ingia",
    none: "Huna akaunti? Uliza msimamizi wa kituo.",
    bad: "Jina au nenosiri halikubaliwa.",
    reset: "Kubadilisha nenosiri bado haijaunganishwa. Kwa onyesho hili, tumia akaunti hizi.",
  },
};

function storedToken() {
  return localStorage.getItem("lactogrid-token") || sessionStorage.getItem("lactogrid-token");
}

function applyLanguage(code) {
  const copy = COPY[code] || COPY.en;
  document.querySelector("#hero-title").innerHTML = copy.heroTitle;
  document.querySelector("#hero-lead").textContent = copy.heroLead;
  document.querySelector("#sample-label").textContent = copy.sample;
  document.querySelector("#sample-safe").textContent = copy.safe;
  document.querySelector("#form-title").textContent = copy.formTitle;
  document.querySelector("#form-lead").textContent = copy.formLead;
  document.querySelector("#name-label").textContent = copy.name;
  document.querySelector("#password-label").textContent = copy.password;
  document.querySelector("#keep-label").textContent = copy.keep;
  document.querySelector("#forgot").textContent = copy.forgot;
  document.querySelector("#submit").textContent = copy.submit;
  document.querySelector("#no-account").textContent = copy.none;
  document.querySelector("#lang-en").setAttribute("aria-pressed", code === "en" ? "true" : "false");
  document.querySelector("#lang-sw").setAttribute("aria-pressed", code === "sw" ? "true" : "false");
  document.documentElement.lang = code === "sw" ? "sw" : "en";
  localStorage.setItem("lactogrid-lang", code);
}

document.querySelector("#lang-en").addEventListener("click", () => applyLanguage("en"));
document.querySelector("#lang-sw").addEventListener("click", () => applyLanguage("sw"));
applyLanguage(localStorage.getItem("lactogrid-lang") || "en");

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function applyTheme(mode) {
  const choice = mode === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = choice;
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.setAttribute("aria-pressed", button.dataset.themeChoice === choice ? "true" : "false");
  });
}

applyTheme(localStorage.getItem("lactogrid-theme") || "light");

document.querySelector("#show-password").addEventListener("click", () => {
  const button = document.querySelector("#show-password");
  const showing = passwordInput.type === "password";
  passwordInput.type = showing ? "text" : "password";
  button.setAttribute("aria-pressed", showing ? "true" : "false");
  button.setAttribute("aria-label", showing ? "Hide password" : "Show password");
  button.innerHTML = showing ? EYE_OFF : EYE;
  passwordInput.focus();
});

document.querySelector("#forgot").addEventListener("click", () => {
  const copy = COPY[localStorage.getItem("lactogrid-lang") || "en"];
  document.querySelector("#no-account").textContent = copy.reset;
  document.querySelector("#accounts").hidden = false;
});

document.querySelectorAll(".account").forEach((button) => {
  button.addEventListener("click", () => {
    nameInput.value = button.dataset.name;
    passwordInput.value = button.dataset.password;
    document.querySelector("#keep").checked = true;
    document.querySelector("#login").requestSubmit();
  });
});

function clearSession() {
  sessionStorage.removeItem("lactogrid-token");
  localStorage.removeItem("lactogrid-token");
  sessionStorage.removeItem("lactogrid-who");
  localStorage.removeItem("lactogrid-who");
}

function showLogin(message) {
  loginScreen.hidden = false;
  workspace.hidden = true;
  loginError.textContent = message || "";
  document.title = "Sign in · LactoGrid";
}

function showWorkspace() {
  loginScreen.hidden = true;
  workspace.hidden = false;
}

function authHeaders() {
  const value = storedToken();
  return value ? { Authorization: `Bearer ${value}` } : {};
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...authHeaders(), ...(options.headers || {}) } });
  if (response.status === 401) {
    clearSession();
    throw new Error("login");
  }
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function page() {
  const name = (location.hash || "#overview").slice(1);
  return ["overview", "alerts", "sla", "settings"].includes(name) ? name : "overview";
}

function markNav() {
  const current = page();
  document.querySelectorAll(".side-nav a, .bottom-nav a").forEach((link) => {
    if (link.getAttribute("href") === `#${current}`) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function escapeHtml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function clock(iso, timeZone) {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: timeZone || "Africa/Nairobi",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));
}

function when(iso, timeZone) {
  if (!iso) return "No sample yet";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: timeZone || "Africa/Nairobi",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

function statusOf(status) {
  const tank = status.tank_temp_c;
  const milk = status.milk_volume_l || 0;
  const backup = status.backup_kw || 0;
  if (tank != null && milk > 0 && tank > 6) return { kind: "bad", word: "At risk" };
  if (backup > 0 || status.mode === "safe") return { kind: "warn", word: "Warning" };
  return { kind: "ok", word: "Milk safe" };
}

function paintStatus(status) {
  if (!status) {
    topStatus.className = "pill";
    topStatus.innerHTML = "";
    return;
  }
  const state = statusOf(status);
  topStatus.className = state.kind === "ok" ? "pill" : `pill pill-${state.kind}`;
  topStatus.innerHTML = `${CHECK} ${state.word}`;
}

document.querySelector("#login").addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  const response = await fetch("/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: nameInput.value.trim(), password: passwordInput.value }),
  });
  if (!response.ok) {
    const copy = COPY[localStorage.getItem("lactogrid-lang") || "en"];
    loginError.textContent = copy.bad;
    document.querySelector("#accounts").hidden = false;
    return;
  }
  const payload = await response.json();
  const store = document.querySelector("#keep").checked ? localStorage : sessionStorage;
  clearSession();
  store.setItem("lactogrid-token", payload.token);
  store.setItem("lactogrid-who", `${payload.name} · ${payload.role}`);
  render();
});

function downsample(rows, maxPoints) {
  if (rows.length <= maxPoints) return rows;
  const step = (rows.length - 1) / (maxPoints - 1);
  return Array.from({ length: maxPoints }, (_, index) => rows[Math.round(index * step)]);
}

function withAlpha(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function drawPower(canvas, rows) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const padBottom = 28;
  ctx.clearRect(0, 0, width, height);
  const solar = rows.map((row) => row.solar_kw || 0);
  const compressor = rows.map((row) => row.compressor_kw || 0);
  const max = Math.max(1, ...solar, ...compressor);
  const x = (index) => (rows.length <= 1 ? 0 : (index / (rows.length - 1)) * width);
  const y = (value) => (height - padBottom) - (value / max) * (height - padBottom - 12);
  ctx.strokeStyle = token("--line");
  ctx.lineWidth = 1;
  for (let step = 0; step <= 3; step += 1) {
    const gy = 12 + ((height - padBottom - 12) * step) / 3;
    ctx.beginPath();
    ctx.moveTo(0, gy);
    ctx.lineTo(width, gy);
    ctx.stroke();
  }
  ctx.fillStyle = withAlpha(token("--solar"), 0.28);
  ctx.beginPath();
  ctx.moveTo(x(0), y(0));
  solar.forEach((value, index) => ctx.lineTo(x(index), y(value)));
  ctx.lineTo(x(solar.length - 1), y(0));
  ctx.closePath();
  ctx.fill();
  ctx.beginPath();
  ctx.strokeStyle = token("--solar");
  ctx.lineWidth = 2;
  solar.forEach((value, index) => (index === 0 ? ctx.moveTo(x(index), y(value)) : ctx.lineTo(x(index), y(value))));
  ctx.stroke();
  ctx.beginPath();
  ctx.strokeStyle = token("--cold");
  compressor.forEach((value, index) => (index === 0 ? ctx.moveTo(x(index), y(value)) : ctx.lineTo(x(index), y(value))));
  ctx.stroke();
  ctx.fillStyle = token("--muted");
  ctx.font = "12px DM Sans, sans-serif";
  ["00:00", "06:00", "12:00", "18:00"].forEach((label, index) => {
    ctx.fillText(label, (width * index) / 4 + 4, height - 8);
  });
}

function drawTemp(canvas, rows) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  const values = rows.map((row) => row.tank_temp_c).filter((value) => value != null);
  const max = Math.max(8, ...values);
  const min = Math.min(0, ...values);
  const span = max - min || 1;
  const x = (index) => (rows.length <= 1 ? 0 : (index / (rows.length - 1)) * width);
  const y = (value) => height - 16 - ((value - min) / span) * (height - 28);
  ctx.save();
  ctx.setLineDash([6, 5]);
  ctx.strokeStyle = token("--bad");
  ctx.beginPath();
  ctx.moveTo(0, y(6));
  ctx.lineTo(width, y(6));
  ctx.stroke();
  ctx.restore();
  ctx.beginPath();
  ctx.strokeStyle = token("--cold");
  ctx.lineWidth = 2;
  let started = false;
  rows.forEach((row, index) => {
    if (row.tank_temp_c == null) return;
    if (!started) {
      ctx.moveTo(x(index), y(row.tank_temp_c));
      started = true;
    } else ctx.lineTo(x(index), y(row.tank_temp_c));
  });
  ctx.stroke();
}

function collapsed(actions) {
  const folded = [];
  actions.forEach((action) => {
    const previous = folded[folded.length - 1];
    if (!previous || previous.reason !== action.reason) folded.push(action);
  });
  return folded.slice(-8).reverse();
}

const CLOUD = {
  "demo-site": 0.05,
  "cloudy-site": 0.8,
  "undersized-site": 0.1,
  "evening-spike-site": 0.05,
  "comms-outage-site": 0.05,
};

function cloudWord(cloud) {
  if (cloud < 0.25) return "Clear";
  if (cloud < 0.55) return "Partly cloudy";
  return "Overcast";
}

function storeTargetPct(cloud) {
  return 75 + 25 * Math.min(1, cloud * 2.5);
}

function expectedSolarKw(site, hour, cloud) {
  const sunrise = 6;
  const sunset = 18;
  if (hour < sunrise || hour >= sunset) return 0;
  const sine = Math.sin(Math.PI * (hour - sunrise) / (sunset - sunrise));
  const clear = (site.solar_kwp || 0) * 0.85 * sine ** 1.3;
  return clear * (1 - 0.9 * cloud);
}

function outlookCards(site) {
  const cloud = CLOUD[site.id] ?? 0.2;
  const target = storeTargetPct(cloud);
  const windows = [
    ["Morning delivery", 7],
    ["Midday charge", 12],
    ["Evening delivery", 17],
  ];
  return windows.map(([label, hour]) => `
    <article>
      <p class="kpi-label">${label}</p>
      <p class="kpi-value solar-text">${expectedSolarKw(site, hour, cloud).toFixed(1)}<span>kW</span></p>
      <p class="note">${cloudWord(cloud)} · store target ${target.toFixed(0)}%</p>
    </article>`).join("");
}

function localDay(iso, timeZone) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timeZone || "Africa/Nairobi",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(iso));
}

function prettyDay(ymd) {
  const [year, month, day] = ymd.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

function periodSlice(rows, period, timeZone) {
  const counts = new Map();
  rows.forEach((row) => {
    const day = localDay(row.timestamp, timeZone);
    counts.set(day, (counts.get(day) || 0) + 1);
  });
  const serviceDays = [...counts.entries()]
    .filter(([, count]) => count >= 60)
    .map(([day]) => day)
    .sort();
  const latest = serviceDays.at(-1);
  if (!latest) return { rows: [], title: "No readings", recorded: 0, span: 0 };
  const [year, month, day] = latest.split("-").map(Number);
  const latestDate = new Date(Date.UTC(year, month - 1, day));
  let start = latest;
  let end = latest;
  let span = 1;
  let title = `Day · ${prettyDay(latest)}`;
  if (period === "week") {
    const mondayOffset = (latestDate.getUTCDay() + 6) % 7;
    const startDate = new Date(latestDate);
    startDate.setUTCDate(startDate.getUTCDate() - mondayOffset);
    const endDate = new Date(startDate);
    endDate.setUTCDate(endDate.getUTCDate() + 6);
    start = startDate.toISOString().slice(0, 10);
    end = endDate.toISOString().slice(0, 10);
    span = 7;
    title = `Week · ${prettyDay(start)} to ${prettyDay(end)}`;
  } else if (period === "month") {
    start = `${year}-${String(month).padStart(2, "0")}-01`;
    const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
    end = `${year}-${String(month).padStart(2, "0")}-${String(last).padStart(2, "0")}`;
    span = last;
    title = `Month · ${prettyDay(start).replace(/^\d+\s/, "")}`;
  }
  const chosen = rows.filter((row) => {
    const stamp = localDay(row.timestamp, timeZone);
    return stamp >= start && stamp <= end;
  });
  const recorded = serviceDays.filter((day) => day >= start && day <= end).length;
  return { rows: chosen, title, recorded, span };
}

function leadReason(reason) {
  const parts = String(reason).split(/(?<=\.)\s/).filter(Boolean);
  const action = parts.find((part) => /backup|being cooled|charges the store|no spare solar/i.test(part));
  return action || parts[0] || "";
}

function dayFigures(rows) {
  let safe = 0;
  let withMilk = 0;
  let backup = 0;
  let solar = 0;
  let peakTank = null;
  rows.forEach((row) => {
    const hours = 1 / 60;
    if (row.solar_used_kw != null) solar += row.solar_used_kw * hours;
    if (row.backup_kw != null) backup += row.backup_kw * hours;
    const milk = row.milk_volume_l || 0;
    if (milk <= 0 || row.tank_temp_c == null) return;
    withMilk += 1;
    if (row.tank_temp_c <= 6) safe += 1;
    peakTank = peakTank == null ? row.tank_temp_c : Math.max(peakTank, row.tank_temp_c);
  });
  return {
    uptime: withMilk ? (100 * safe) / withMilk : null,
    backup,
    solar,
    peakTank,
  };
}

let viewToken = 0;

async function overview(site) {
  const token = ++viewToken;
  const [status, readings, actions, events] = await Promise.all([
    api(`/sites/${site.id}/status`),
    api(`/sites/${site.id}/readings`),
    api(`/sites/${site.id}/actions`),
    api(`/sites/${site.id}/events`),
  ]);
  if (token !== viewToken) return;
  paintStatus(status);
  const stale = status.age_seconds != null && status.age_seconds > 1800;
  updatedLine.textContent = `Demo data · updated ${clock(status.updated_at, site.timezone)}${stale ? " · Stale" : ""}`;
  const store = status.store_soc_pct == null ? 0 : Math.max(0, Math.min(100, status.store_soc_pct));
  const backupOn = (status.backup_kw || 0) > 0.05;
  const backupHours = readings.reduce((sum, row) => sum + ((row.backup_kw || 0) / 60), 0);
  const recent = downsample(readings, 240);
  const log = collapsed(actions).map((action) => `
    <li><time>${clock(action.timestamp, site.timezone)}</time><span>${escapeHtml(leadReason(action.reason))}</span></li>`).join("");
  const latestEvent = events.length ? events[events.length - 1] : null;
  const alertText = latestEvent
    ? `${escapeHtml(latestEvent.message)} · ${when(latestEvent.timestamp, site.timezone)}`
    : "No active alerts on this day.";
  view.innerHTML = `
    <div class="kpis">
      <article class="card">
        <p class="kpi-label">Milk tank</p>
        <p class="kpi-value">${status.tank_temp_c == null ? "—" : status.tank_temp_c.toFixed(1)}<span>°C</span></p>
        <p class="note">Target 4 °C · limit 6 °C</p>
      </article>
      <article class="card">
        <p class="kpi-label">Cold store</p>
        <p class="kpi-value">${store.toFixed(0)}<span>%</span></p>
        <p class="note">${store >= 50 ? "Enough for tonight" : "Below half"}</p>
      </article>
      <article class="card">
        <p class="kpi-label">Solar now</p>
        <p class="kpi-value solar-text">${status.solar_kw == null ? "—" : status.solar_kw.toFixed(1)}<span>kW</span></p>
        <p class="note">${(status.compressor_kw || 0) > 0.05 ? `Cooling is using ${status.compressor_kw.toFixed(1)} kW` : "Compressor is off"}</p>
      </article>
      <article class="card">
        <p class="kpi-label">Backup power</p>
        <p class="kpi-value">${backupOn ? `${status.backup_kw.toFixed(1)}<span>kW</span>` : "Off"}</p>
        <p class="note">${backupHours.toFixed(1)} h on this day</p>
      </article>
    </div>
    <section class="card">
      <h2>Weather outlook</h2>
      <p class="note">Demo forecast for the next milk windows. A live site fills this from a weather feed. If cloud is expected, the cold-store target rises so there is cold at dawn and dusk.</p>
      <div class="outlook">${outlookCards(site)}</div>
    </section>
    <div class="board">
      <section class="card">
        <h2>Power, last 24 hours</h2>
        <canvas id="power" width="720" height="260"></canvas>
        <div class="legend"><span><i class="swatch swatch-solar"></i>Solar</span><span><i class="swatch swatch-cold"></i>Compressor</span></div>
      </section>
      <section class="card">
        <h2>What the system did</h2>
        <ul class="log">${log || "<li><span>No decisions yet.</span></li>"}</ul>
      </section>
    </div>
    <section class="card alerts-line"><strong>Alerts</strong> ${alertText}</section>`;
  drawPower(document.querySelector("#power"), recent);
}

function canAcknowledge() {
  const who = localStorage.getItem("lactogrid-who") || sessionStorage.getItem("lactogrid-who") || "";
  const role = who.split("·").pop().trim();
  return role === "admin" || role === "manager";
}

async function alerts(site) {
  const token = ++viewToken;
  const rows = await api(`/sites/${site.id}/alerts`);
  if (token !== viewToken) return;
  paintStatus(null);
  updatedLine.textContent = "A text message waits on a provider. Acknowledgement is stored here.";
  const items = rows.slice().reverse().map((row) => {
    const open = row.status === "sent" || row.status === "pending";
    const who = row.acknowledged_by ? `Acknowledged by ${escapeHtml(row.acknowledged_by)}` : escapeHtml(row.status);
    const button = open && canAcknowledge()
      ? `<button class="ghost" type="button" data-ack="${escapeHtml(row.id)}">Acknowledge</button>`
      : "";
    return `
    <li>
      <div class="when">${when(row.timestamp, site.timezone)} · ${escapeHtml(row.event_type)} · ${who}</div>
      <div class="setting-row"><span>${escapeHtml(row.message)}</span>${button}</div>
    </li>`;
  }).join("");
  view.innerHTML = `
    <section class="card">
      <h2>Alerts</h2>
      ${items ? `<ul class="list">${items}</ul>` : "<p class='note'>No alarms on record.</p>"}
    </section>`;
  document.querySelectorAll("[data-ack]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      await api(`/alerts/${encodeURIComponent(button.dataset.ack)}/ack`, { method: "POST" });
      alerts(site);
    });
  });
}

let reportPeriod = "day";

function pct(value) {
  return value == null ? "—" : Number(value).toFixed(1);
}

async function downloadGuarantee(site, kind) {
  const response = await fetch(`/sites/${site.id}/guarantee.${kind}?period=${reportPeriod}`, { headers: authHeaders() });
  if (!response.ok) throw new Error(await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${site.id}-${reportPeriod}.${kind}`;
  link.click();
  URL.revokeObjectURL(url);
}

async function reports(site) {
  const token = ++viewToken;
  const [report, readings] = await Promise.all([
    api(`/sites/${site.id}/guarantee?period=${reportPeriod}`),
    api(`/sites/${site.id}/readings`),
  ]);
  if (token !== viewToken) return;
  const slice = periodSlice(readings, reportPeriod, site.timezone);
  paintStatus(null);
  updatedLine.textContent = "Demo data · figures come from stored readings. The fee is not set.";
  const coverage = report.span_days
    ? `${report.recorded_days} of ${report.span_days} day${report.span_days === 1 ? "" : "s"} on record`
    : "No readings";
  const windowCard = (window) => `
    <article class="card">
      <p class="kpi-label">${escapeHtml(window.name)}</p>
      <p class="kpi-value">${pct(window.uptime_pct)}<span>%</span></p>
      <p class="note">${Number(window.litres).toFixed(0)} L received · ${window.failures} failure${window.failures === 1 ? "" : "s"}</p>
    </article>`;
  view.innerHTML = `
    <div class="period" role="group" aria-label="Report period">
      <button type="button" data-period="day" aria-pressed="${reportPeriod === "day"}">Day</button>
      <button type="button" data-period="week" aria-pressed="${reportPeriod === "week"}">Week</button>
      <button type="button" data-period="month" aria-pressed="${reportPeriod === "month"}">Month</button>
    </div>
    <p class="note">${escapeHtml(report.title)} · ${escapeHtml(coverage)}. A missing temperature while milk is present counts as not safe.</p>
    <div class="stats">
      <article class="card">
        <p class="kpi-label">Milk-safe time</p>
        <p class="kpi-value">${pct(report.milk_safe_uptime_pct)}<span>%</span></p>
        <p class="note">While milk was in the tank, at or below 6 °C</p>
      </article>
      <article class="card">
        <p class="kpi-label">Solar used</p>
        <p class="kpi-value solar-text">${Number(report.solar_kwh).toFixed(1)}<span>kWh</span></p>
        <p class="note">From stored readings</p>
      </article>
      <article class="card">
        <p class="kpi-label">Backup</p>
        <p class="kpi-value">${Number(report.backup_kwh).toFixed(2)}<span>kWh</span></p>
        <p class="note">Peak tank ${report.peak_tank_c == null ? "—" : Number(report.peak_tank_c).toFixed(1)} °C</p>
      </article>
    </div>
    <div class="stats">
      ${windowCard(report.morning)}
      ${windowCard(report.evening)}
      <article class="card">
        <p class="kpi-label">Credit on the next payment</p>
        <p class="kpi-value">${Number(report.credit_share_pct).toFixed(0)}<span>%</span></p>
        <p class="note">${report.failures} milking-window failure${report.failures === 1 ? "" : "s"} · Fee not set</p>
      </article>
    </div>
    <div class="period">
      <button type="button" id="download-csv">Download CSV</button>
      <button type="button" id="download-pdf">Download PDF</button>
    </div>
    <p class="note">${escapeHtml(report.summary)}</p>
    <section class="card alerts-line">
      <h2>Tank temperature</h2>
      <canvas id="temp" width="720" height="220"></canvas>
      <p class="note">Dashed line is the 6 °C milk-safe limit.</p>
    </section>`;
  drawTemp(document.querySelector("#temp"), downsample(slice.rows, 240));
  document.querySelectorAll("[data-period]").forEach((button) => {
    button.addEventListener("click", () => {
      reportPeriod = button.dataset.period;
      reports(site);
    });
  });
  document.querySelector("#download-csv").addEventListener("click", () => downloadGuarantee(site, "csv"));
  document.querySelector("#download-pdf").addEventListener("click", () => downloadGuarantee(site, "pdf"));
}

function settings() {
  viewToken += 1;
  paintStatus(null);
  updatedLine.textContent = "";
  const who = localStorage.getItem("lactogrid-who") || sessionStorage.getItem("lactogrid-who") || "";
  const theme = document.documentElement.dataset.theme || "light";
  view.innerHTML = `
    <section class="card">
      <h2>Settings</h2>
      <div class="setting-row"><span>${escapeHtml(who)}</span><button class="ghost" id="sign-out" type="button">Sign out</button></div>
      <div class="setting-row">
        <span>Appearance</span>
        <div class="theme-switch" role="group" aria-label="Theme">
          <button type="button" data-theme-choice="light" aria-pressed="${theme === "light"}">Light</button>
          <button type="button" data-theme-choice="dark" aria-pressed="${theme === "dark"}">Dark</button>
        </div>
      </div>
    </section>`;
  document.querySelector("#sign-out").addEventListener("click", () => {
    clearSession();
    showLogin("");
  });
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      localStorage.setItem("lactogrid-theme", button.dataset.themeChoice);
      applyTheme(button.dataset.themeChoice);
      settings();
    });
  });
}

async function render() {
  markNav();
  if (!storedToken()) {
    showLogin(loginError.textContent);
    return;
  }
  showWorkspace();
  try {
    const sites = await api("/sites");
    if (!sites.length) {
      view.innerHTML = `<section class="card"><h2>No sites</h2></section>`;
      return;
    }
    const saved = sessionStorage.getItem("lactogrid-site");
    const site = sites.find((item) => item.id === saved) || sites.find((item) => item.id === "demo-site") || sites[0];
    const picker = document.querySelector("#site-picker");
    picker.innerHTML = sites.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
    picker.value = site.id;
    siteName.textContent = site.name;
    document.title = `${site.name} · LactoGrid`;
    const current = page();
    if (current === "overview") await overview(site);
    else if (current === "alerts") await alerts(site);
    else if (current === "sla") await reports(site);
    else settings();
  } catch (error) {
    if (String(error.message) === "login") showLogin("Sign in again.");
    else view.innerHTML = `<section class="card"><p class="error">${escapeHtml(String(error.message).slice(0, 240))}</p></section>`;
  }
}

document.querySelector("#site-picker").addEventListener("change", (event) => {
  sessionStorage.setItem("lactogrid-site", event.target.value);
  render();
});

window.addEventListener("hashchange", render);
render();

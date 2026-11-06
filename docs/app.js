// NetBlade product page: drives the live previews (trace, multi-ping) and points the download links at the latest GitHub release.

const REPO = "TariqMK/NetBlade";
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const rand = (a, b) => a + Math.random() * (b - a);
const jitter = (base, spread) => Math.max(0.3, base + (Math.random() + Math.random() + Math.random() - 1.5) * spread);
const fmt = (n) => n.toFixed(1);

/* ---------- Ping and trace preview ---------- */

const HOPS = [
  { name: "router.lan", ip: "192.168.1.1", base: 1.2, spread: 0.5 },
  { name: "10.20.0.1", ip: "", base: 7.8, spread: 1.4 },
  { name: null },
  { name: "203.0.113.9", ip: "", base: 11.6, spread: 2.2, loss: 0.05 },
  { name: "198.51.100.22", ip: "", base: 12.3, spread: 1.6 },
  { name: "198.51.100.65", ip: "", base: 13.0, spread: 1.6 },
  { name: "172.70.84.3", ip: "", base: 13.7, spread: 1.4 },
  { name: "one.one.one.one", ip: "1.1.1.1", base: 14.1, spread: 1.4 },
];

const SEED_SENT = 184;
const history = [];

function initHops() {
  for (const h of HOPS) {
    if (!h.name) continue;
    h.sent = SEED_SENT;
    h.lost = h.loss ? Math.round(SEED_SENT * h.loss) : 0;
    h.best = h.base - h.spread * 0.8;
    h.worst = h.base + h.spread * 3.6;
    h.sum = h.base * (h.sent - h.lost);
    h.last = jitter(h.base, h.spread);
  }
  for (let i = 0; i < 60; i++) history.push(destinationSample());
}

function destinationSample() {
  const dest = HOPS[HOPS.length - 1];
  if (Math.random() < 0.006) return null;
  const spike = Math.random() < 0.04 ? rand(4, 11) : 0;
  return jitter(dest.base, dest.spread) + spike;
}

function stepHops() {
  for (const h of HOPS) {
    if (!h.name) continue;
    h.sent++;
    const isDest = h === HOPS[HOPS.length - 1];
    let rtt;
    if (isDest) {
      rtt = destinationSample();
      history.push(rtt);
      history.shift();
    } else {
      rtt = h.loss && Math.random() < h.loss ? null : jitter(h.base, h.spread);
    }
    if (rtt === null) { h.lost++; continue; }
    h.last = rtt;
    h.sum += rtt;
    h.best = Math.min(h.best, rtt);
    h.worst = Math.max(h.worst, rtt);
  }
}

function lossPct(h) { return (h.lost / h.sent) * 100; }

function renderHops() {
  const body = document.getElementById("hops");
  const maxWorst = Math.max(...HOPS.filter((h) => h.name).map((h) => h.worst));
  body.innerHTML = HOPS.map((h, i) => {
    const n = i + 1;
    if (!h.name) {
      return `<tr class="noreply"><td>${n}</td><td class="host"><span class="dot off-bg"></span>No reply</td><td>–</td><td>${HOPS[0].sent}</td><td>–</td><td>–</td><td class="hide-s">–</td><td class="hide-s">–</td><td class="hide-m"></td></tr>`;
    }
    const loss = lossPct(h);
    const dot = loss === 0 ? "pos-bg" : loss < 20 ? "warn-bg" : "neg-bg";
    const avg = h.sum / (h.sent - h.lost);
    const left = (h.best / maxWorst) * 100;
    const width = ((h.worst - h.best) / maxWorst) * 100;
    const tick = (avg / maxWorst) * 100;
    const sel = i === HOPS.length - 1 ? ' class="sel"' : "";
    return `<tr${sel}><td>${n}</td><td class="host"><span class="dot ${dot}"></span>${h.name}${h.ip ? `<span class="ip">${h.ip}</span>` : ""}</td>` +
      `<td>${fmt(loss)}%</td><td>${h.sent}</td><td>${fmt(h.last)}</td><td>${fmt(avg)}</td>` +
      `<td class="hide-s">${fmt(h.best)}</td><td class="hide-s">${fmt(h.worst)}</td>` +
      `<td class="hide-m"><span class="range"><i style="left:${left}%;width:${width}%"></i><b style="left:${tick}%"></b></span></td></tr>`;
  }).join("");

  const dest = HOPS[HOPS.length - 1];
  const destLoss = lossPct(dest);
  const avg = dest.sum / (dest.sent - dest.lost);
  document.getElementById("sum-avg").textContent = fmt(avg);
  const lossEl = document.querySelector(".summary .pos, .summary .warn");
  lossEl.textContent = `${fmt(destLoss)}% loss`;
  lossEl.className = destLoss === 0 ? "pos" : "warn";
}

function renderChart() {
  const W = 600, H = 140, step = W / 59;
  const y = (v) => H - Math.min(v, 39) * 3.5;
  const pts = [];
  let loss = "";
  history.forEach((v, i) => {
    const x = (i * step).toFixed(1);
    if (v === null) loss += `<line class="loss" x1="${x}" y1="${H}" x2="${x}" y2="${H - 10}"/>`;
    else pts.push(`${x},${y(v).toFixed(1)}`);
  });
  document.getElementById("chart-line").setAttribute("points", pts.join(" "));
  if (pts.length) {
    const first = pts[0].split(",")[0], last = pts[pts.length - 1].split(",")[0];
    document.getElementById("chart-area").setAttribute("d", `M${first},${H} L${pts.join(" L")} L${last},${H} Z`);
  }
  document.getElementById("chart-loss").innerHTML = loss;
  const lastVal = [...history].reverse().find((v) => v !== null);
  document.getElementById("chart-last").textContent = `${fmt(lastVal)} ms`;
}

/* ---------- Multi-ping preview ---------- */

const HOSTS = [
  { name: "Gateway", ip: "192.168.1.1", base: 1.1, spread: 0.4, plan: () => 0, since: 8040 },
  { name: "NAS", ip: "192.168.1.20", base: 1.8, spread: 0.6, plan: () => 0, since: 8040 },
  { name: "Printer 2F", ip: "192.168.1.40", base: 3.4, spread: 1.6, plan: printerPlan, since: 4 },
  { name: "Cloudflare DNS", ip: "1.1.1.1", base: 14.2, spread: 1.6, plan: () => 0, since: 8040 },
  { name: "Office VPN", ip: "10.8.0.1", base: 38.5, spread: 5, plan: () => 0.03, since: 312 },
  { name: "Google DNS", ip: "8.8.8.8", base: 16.4, spread: 1.8, plan: () => 0, since: 8040 },
];

// The printer cycles through a 48 s pattern: down, recovering, up, then flaky.
let tick = 0;
function printerPlan() {
  const t = tick % 48;
  if (t < 10) return 1;
  if (t < 30) return 0;
  if (t < 40) return 0.4;
  return 0;
}

function stateOf(h) {
  const r = h.results;
  const last3 = r.slice(-3), last10 = r.slice(-10);
  if (last3.length === 3 && last3.every((v) => v === null)) return "down";
  if (h.recover > 0) return "unstable";
  if (last3.every((v) => v !== null) && last10.every((v) => v !== null)) return "up";
  return "unstable";
}

const LABEL = { up: "Up", unstable: "Unstable", down: "Down" };

function initHosts() {
  for (const h of HOSTS) {
    h.results = [];
    h.recover = 0;
    const seedLoss = h.name === "Printer 2F" ? 0.15 : h.plan();
    for (let i = 0; i < 30; i++) h.results.push(Math.random() < seedLoss ? null : jitter(h.base, h.spread));
    if (h.name === "Printer 2F") h.results.splice(-4, 4, null, null, null, null);
    if (h.name === "Office VPN") h.results.splice(-6, 1, null);
    h.sent = 1200 + Math.floor(rand(0, 400));
    h.lostTotal = h.results.filter((v) => v === null).length + (h.name === "Office VPN" ? 61 : 0);
    h.state = stateOf(h);
  }
}

function stepHosts() {
  for (const h of HOSTS) {
    const v = Math.random() < h.plan() ? null : jitter(h.base, h.spread);
    h.results.push(v);
    h.results.shift();
    h.sent++;
    if (v === null) h.lostTotal++;
    if (h.recover > 0) h.recover--;
    const prev = h.state;
    let next = stateOf(h);
    if (prev === "down" && next !== "down") { h.recover = 10; next = "unstable"; }
    if (next !== prev) {
      h.state = next;
      h.since = 0;
      addLog(h, prev, next);
    } else {
      h.since++;
    }
  }
}

function dur(s) {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function renderTiles() {
  const el = document.getElementById("tiles");
  el.innerHTML = HOSTS.map((h) => {
        const last = h.results[h.results.length - 1];
    const max = Math.max(h.base * 2.2, 3);
    const bars = h.results.map((v) => v === null ? '<i class="lost"></i>' : `<i style="height:${Math.max(10, (v / max) * 100)}%"></i>`).join("");
    const loss = (h.lostTotal / h.sent) * 100;
    return `<div class="tile ${h.state}">` +
      `<div class="t-head"><span class="t-name">${h.name}</span><span class="t-ms">${last === null ? "timeout" : fmt(last) + " ms"}</span></div>` +
      `<div class="t-ip">${h.ip}</div>` +
      `<div class="t-state">${LABEL[h.state]} ${dur(h.since)} · ${fmt(loss)}% loss</div>` +
      `<div class="bars">${bars}</div></div>`;
  }).join("");
}

const logEntries = [];
function clock(offsetSec = 0) {
  const d = new Date(Date.now() - offsetSec * 1000);
  return d.toTimeString().slice(0, 8);
}
function addLog(h, from, to, offset = 0) {
  logEntries.unshift(`<div>${clock(offset)}  <b>${h.name} (${h.ip})</b>  ${LABEL[from]} → ${LABEL[to]}</div>`);
  logEntries.length = Math.min(logEntries.length, 4);
  document.getElementById("log").innerHTML = logEntries.join("");
}

/* ---------- Download links ---------- */

function detectOS() {
  const p = (navigator.userAgentData?.platform || navigator.platform || "").toLowerCase();
  const ua = navigator.userAgent.toLowerCase();
  if (/android|iphone|ipad|ipod/.test(ua)) return null;
  if (p.includes("win")) return "windows";
  if (p.includes("mac")) return "macos";
  if (p.includes("linux")) return "linux";
  return null;
}

const OS_NAME = { windows: "Windows", macos: "macOS", linux: "Linux" };
const ASSET = { windows: /windows-x64\.exe$/i, macos: /macos-arm64\.zip$/i, linux: /linux-x64\.tar\.gz$/i };

async function wireDownloads() {
  const os = detectOS();
  const hero = document.getElementById("hero-download");
  if (os) {
    hero.textContent = `Download for ${OS_NAME[os]}`;
    document.querySelector(`.platform[data-os="${os}"]`)?.classList.add("detected");
  }
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, { headers: { Accept: "application/vnd.github+json" } });
    if (!res.ok) return;
    const rel = await res.json();
    for (const [key, re] of Object.entries(ASSET)) {
      const asset = rel.assets?.find((a) => re.test(a.name));
      if (!asset) continue;
      const card = document.querySelector(`.platform[data-os="${key}"]`);
      card.href = asset.browser_download_url;
      card.querySelector(".arch").textContent += ` · ${(asset.size / 1048576).toFixed(0)} MB`;
      if (key === os) hero.href = asset.browser_download_url;
    }
    const date = new Date(rel.published_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
    document.getElementById("dl-version").textContent = `${rel.tag_name} · released ${date}`;
    document.getElementById("hero-meta").textContent = `${rel.tag_name} · Free · No installation on Windows`;
  } catch {
    // Offline or rate-limited: the links still go to the Releases page.
  }
}

/* ---------- Run ---------- */

initHops();
initHosts();
addLog(HOSTS[4], "unstable", "up", 312);
addLog(HOSTS[2], "unstable", "down", 4);
renderHops();
renderChart();
renderTiles();
wireDownloads();

if (!reduceMotion) {
  setInterval(() => {
    if (document.hidden) return;
    tick++;
    stepHops();
    stepHosts();
    renderHops();
    renderChart();
    renderTiles();
  }, 1000);
}

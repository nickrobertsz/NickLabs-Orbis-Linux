/* NickLabs Orbis dashboard — vanilla JS, no external resources.
 * Every button calls orbis-web -> orbisd; the UI shows what orbisd reports,
 * never an optimistic guess. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
let CSRF = null;
let STATUS = null;
let POLL = null;

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function mib(bytes) { return bytes == null ? "?" : `${Math.round(bytes / 1048576)} MiB`; }
function kib(k) { return k == null ? "?" : (k >= 1048576 ? `${(k / 1048576).toFixed(2)} GiB` : `${Math.round(k / 1024)} MiB`); }
function kv(el, rows) {
  el.innerHTML = rows.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("");
}
function state(s) { return `<span class="state ${esc(s)}">${esc(s)}</span>`; }

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-Orbis-CSRF": CSRF || "" },
  };
  const r = await fetch(path, { credentials: "same-origin", ...opt });
  if (r.status === 401) { showLogin(); throw new Error("login required"); }
  const d = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
  if (!r.ok || d.ok === false) throw new Error(d.error || `HTTP ${r.status}`);
  return d.result !== undefined ? d.result : d;
}

/* ------------------------------------------------------------------ auth */
function showLogin() {
  const d = $("#login-dialog");
  if (!d.open) d.showModal();
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    const r = await fetch("/api/v1/login", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: $("#login-token").value }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "login failed");
    CSRF = d.csrf;
    $("#login-dialog").close();
    $("#login-token").value = "";
    start();
  } catch (err) { $("#login-error").textContent = err.message; }
});

/* ------------------------------------------------------------------ tabs */
$$(".tabs button").forEach((b) => b.addEventListener("click", () => {
  $$(".tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  $$(".tab").forEach((t) => (t.hidden = t.id !== `tab-${b.dataset.tab}`));
  const loaders = { ai: loadAI, gaming: loadGaming, system: loadSystem };
  if (loaders[b.dataset.tab]) loaders[b.dataset.tab]().catch(console.error);
}));

/* ------------------------------------------------------------------ overview */
function renderModes(st) {
  const order = ["pc", "gaming", "ai"];
  const host = $("#modes");
  host.innerHTML = order.map((m) => {
    const p = st.profiles[m];
    const active = st.current_mode === m;
    const next = st.desired_mode === m && !active;
    const vram = m === "ai" ? `${p.vram.preferred_mb} MB (CPU) / ${p.vram.backend_overrides.vulkan?.preferred_mb ?? "?"} MB (Vulkan)` : `${p.vram.preferred_mb} MB`;
    return `<section class="mode ${active ? "active" : ""}" data-mode="${m}">
      ${active ? '<span class="badge">ACTIVE</span>' : next ? '<span class="badge">NEXT BOOT</span>' : ""}
      <h3>${esc(p.title)}</h3>
      <p>${esc(p.description)}</p>
      <div class="meta"><span>VRAM ${esc(vram)}</span><span>${p.display ? "display" : "headless"}</span></div>
      <button class="btn" data-switch="${m}" ${active ? "disabled" : ""}>${active ? "Running" : `Switch to ${esc(p.title)}`}</button>
    </section>`;
  }).join("");
  $$("[data-switch]", host).forEach((b) => b.addEventListener("click", () => openPlan(b.dataset.switch)));
}

function renderStatus(st) {
  STATUS = st;
  $("#chip-mode").textContent = `MODE ${(st.current_mode || "none").toUpperCase()}`;
  $("#chip-mode").className = "chip " + (st.current_mode ? "ok" : "warn");
  const gpuAlert = (st.alerts || []).some((a) => a.kind === "gpu");
  $("#chip-gpu").textContent = gpuAlert ? "GPU FAULTS LOGGED" : "GPU no faults logged";
  $("#chip-gpu").className = "chip " + (gpuAlert ? "fail" : "ok");
  $("#chip-conn").textContent = "connected";
  $("#chip-conn").className = "chip ok";
  renderModes(st);

  const m = st.memory;
  const usedPct = m.mem_total_kib ? (100 * m.mem_used_kib / m.mem_total_kib) : 0;
  $("#mem-fill").style.width = `${usedPct.toFixed(1)}%`;
  $("#mem-fill").classList.toggle("hot", st.pressure?.level !== "ok");
  kv($("#mem-kv"), [
    ["used", kib(m.mem_used_kib)], ["available", kib(m.mem_available_kib)], ["total (Linux)", kib(m.mem_total_kib)],
    ["swap used", kib(m.swap_used_kib)], ["pressure", state(st.pressure?.level === "ok" ? "OK" : (st.pressure?.level || "?").toUpperCase())],
  ]);
  const g = m.gpu || {};
  const vt = g.mem_info_vram_total, vu = g.mem_info_vram_used;
  $("#vram-fill").style.width = vt ? `${(100 * vu / vt).toFixed(1)}%` : "0";
  kv($("#vram-kv"), [
    ["carve-out", vt ? mib(vt) : "amdgpu not exposing"], ["VRAM used", mib(vu)],
    ["GTT used", g.mem_info_gtt_total ? `${mib(g.mem_info_gtt_used)} / ${mib(g.mem_info_gtt_total)}` : "?"],
    ["next boot", st.boot_partition ? `${st.boot_partition.next_vram_mb} MB` : "boot partition not mounted"],
  ]);
  const cl = st.cpu_load || {};
  $("#cpu-fill").style.width = `${cl.utilisation_pct ?? 0}%`;
  kv($("#cpu-kv"), [
    ["utilisation", cl.utilisation_pct != null ? `${cl.utilisation_pct}%` : "?"],
    ["load", cl.loadavg ? cl.loadavg.join(" / ") : "?"],
    ["ISA", esc(st.cpu?.isa_level || "?")], ["Jaguar", st.cpu?.jaguar_like ? "yes" : "no"],
  ]);
  const temps = st.sensors?.temperatures || [];
  kv($("#thermal-kv"), temps.length ? temps.slice(0, 4).map((t) => [`${t.chip} ${t.sensor}`, `${t.celsius.toFixed(1)} °C`])
    : [["sensors", "none exposed by this kernel"]]);
  $("#fan-note").textContent = `Fan control: ${st.sensors?.fan_control || "?"} — the southbridge firmware manages the fan; Orbis does not pretend to.`;
  const nets = st.network || [];
  kv($("#net-kv"), nets.length ? nets.map((n) => [n.name, `${esc(n.operstate)}${n.wireless ? " · wireless" : ""}${n.driver ? " · " + esc(n.driver) : ""}`])
    : [["interfaces", "none"]]);
  const b = st.boot_partition;
  kv($("#boot-kv"), b ? [["partition", esc(b.path)], ["next mode", esc(b.next_mode || "—")], ["next VRAM", `${b.next_vram_mb} MB`],
    ["kernel", b.kernel_present ? "present" : "MISSING"], ["rollback kernel", b.previous_kernel_present ? "present" : "none"]]
    : [["status", "not mounted — mode/VRAM changes cannot be staged"]]);
  const al = (st.alerts || []).slice(-4);
  $("#alerts").innerHTML = al.map((a) => `<div class="alert ${esc(a.level)}"><b>${esc(a.kind)}</b> · ${esc(a.message)}</div>`).join("");
}

async function refresh() {
  try { renderStatus(await api("/api/v1/status")); }
  catch (e) { $("#chip-conn").textContent = e.message; $("#chip-conn").className = "chip fail"; }
}

/* ------------------------------------------------------------------ switching */
let PENDING = null;
async function openPlan(mode) {
  const dlg = $("#plan-dialog");
  $("#plan-progress").hidden = true;
  $("#plan-progress").textContent = "";
  $("#plan-go").disabled = true;
  $("#plan-title").textContent = `Switch to ${mode.toUpperCase()}`;
  $("#plan-body").innerHTML = "<p class='note'>Planning…</p>";
  dlg.showModal();
  try {
    const p = await api("/api/v1/plan", { mode });
    PENDING = { mode, plan: p };
    const li = (arr) => arr.map((x) => `<li>${esc(x)}</li>`).join("");
    const method = { fast: "FAST — no reboot", reboot: "CLEAN REBOOT through the PS4 system software", noop: "nothing to do", blocked: "BLOCKED" }[p.method];
    $("#plan-body").innerHTML = `
      <p class="plan-method">${esc(method)}</p>
      ${p.vram_current_mb != null ? `<p class="note">VRAM now ${p.vram_current_mb} MB · needs ≥ ${p.vram_min_mb} MB · next boot ${p.vram_next_mb} MB</p>` : ""}
      ${p.reasons.length ? `<ul class="plan-list">${li(p.reasons)}</ul>` : ""}
      ${p.warnings.length ? `<ul class="plan-list">${li(p.warnings.map((w) => "⚠ " + w))}</ul>` : ""}
      ${p.will_stop.length ? `<p><b>Stops:</b> ${esc(p.will_stop.join(", "))}</p>` : ""}
      <p><b>Starts:</b> ${esc(p.will_start.join(", "))}</p>
      ${p.sessions_to_end.length ? `<p><b>Closes:</b> ${esc(p.sessions_to_end.map((s) => `${s.type} session of ${s.user}`).join(", "))} — unsaved work is lost.</p>` : ""}
      ${p.operator_steps.length ? `<ol class="plan-list">${li(p.operator_steps)}</ol>` : ""}
      ${p.blocked ? `<p class="error">${esc(p.blocked)}</p>` : ""}`;
    $("#plan-go").disabled = !["fast", "reboot"].includes(p.method);
    $("#plan-go").textContent = p.method === "reboot" ? "Reboot now" : "Proceed";
  } catch (e) {
    $("#plan-body").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}

$("#plan-go").addEventListener("click", async (e) => {
  e.preventDefault();
  if (!PENDING) return;
  const prog = $("#plan-progress");
  prog.hidden = false;
  $("#plan-go").disabled = true;
  try {
    const r = await api("/api/v1/switch", { mode: PENDING.mode, confirm_sessions: true, confirm_reboot: true });
    let seen = 0;
    for (;;) {
      await new Promise((res) => setTimeout(res, 700));
      const j = await api(`/api/v1/job/${r.job}`);
      (j.progress || []).slice(seen).forEach((p) => { prog.textContent += `… ${p.msg}\n`; });
      seen = (j.progress || []).length;
      if (j.state !== "running") {
        const res = j.result || {};
        prog.textContent += `\n${res.ok ? "OK" : "NOT OK"}: ${res.message || res.error || ""}\n`;
        (res.residue || []).forEach((x) => { prog.textContent += `still running: ${x.comm} (pid ${x.pid})\n`; });
        if (res.offer_reboot) prog.textContent += "A clean reboot will release what did not stop.\n";
        break;
      }
    }
  } catch (err) {
    prog.textContent += `\nerror: ${err.message}\n(if the console is rebooting, this page reconnects when Linux is back)\n`;
  }
  $("#plan-cancel").textContent = "Close";
  refresh();
});

/* ------------------------------------------------------------------ AI */
async function loadAI() {
  const [s, models] = await Promise.all([api("/api/v1/ai/status"), api("/api/v1/ai/models")]);
  const l = s.launch || {};
  kv($("#ai-kv"), [
    ["service", esc(s.service)], ["health", s.health ? esc(JSON.stringify(s.health)) : "not responding"],
    ["requested backend", esc(s.requested_backend)],
    ["ACTIVE backend", l.active_backend ? `<b>${esc(l.active_backend.toUpperCase())}</b>` : "—"],
    ["model", esc((l.model || "").split("/").pop() || "—")],
    ["context / batch", l.n_ctx ? `${l.n_ctx} / ${l.n_batch}·${l.n_ubatch}` : "—"],
    ["GPU layers", l.n_gpu_layers ?? "—"], ["threads", l.threads ?? "—"],
    ["budget", l.budget ? esc(Object.entries(l.budget).map(([k, v]) => `${k}=${v}`).join(" ")) : "—"],
    ["LAN API", s.lan_api ? "enabled (bearer key)" : "disabled"],
    ["Vulkan gate", s.gate?.inference_gate ? (s.gate.inference_gate.passed ? state("PASS") : state("FAIL")) + " " + esc(s.gate.inference_gate.summary || "") : "no receipt"],
  ]);
  $("#ai-backend-note").textContent = l.backend_reason ? `Why: ${l.backend_reason}` : "";
  const sel = $("#ai-model");
  sel.innerHTML = models.map((m) => {
    const f = m.file.split("/").pop();
    return `<option value="${esc(f)}">${esc(f)} — ${esc(m.arch || "?")}, ${m.n_layer ?? "?"} layers, ${Math.round((m.size_bytes || 0) / 1048576)} MiB</option>`;
  }).join("") || "<option value=''>no models in /var/lib/orbis/models</option>";
  if (l.model) sel.value = l.model.split("/").pop();
  const f = $("#ai-form");
  f.backend.value = s.requested_backend || "cpu";
  f.lan_api.checked = !!s.lan_api;
}
$("#ai-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const st = $("#ai-save-status");
  try {
    if (f.model.value) await api("/api/v1/ai/set", { key: "model", value: f.model.value });
    await api("/api/v1/ai/set", { key: "backend", value: f.backend.value });
    if (f.ctx_size.value) await api("/api/v1/ai/set", { key: "ctx_size", value: parseInt(f.ctx_size.value, 10) });
    await api("/api/v1/ai/set", { key: "power_preset", value: f.power_preset.value });
    await api("/api/v1/ai/set", { key: "lan_api", value: f.lan_api.checked });
    st.textContent = "saved — restart the AI service (or switch to AI) to apply";
  } catch (err) { st.textContent = err.message; }
});
const CHAT = [];
$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = $("#chat-input");
  const text = inp.value.trim();
  if (!text) return;
  inp.value = "";
  CHAT.push({ role: "user", content: text });
  const log = $("#chat-log");
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(text)}</div>`);
  try {
    const r = await api("/api/v1/ai/chat", { messages: CHAT, max_tokens: 256 });
    const msg = r.choices?.[0]?.message?.content ?? "";
    CHAT.push({ role: "assistant", content: msg });
    const t = r.timings ? ` · ${r.timings.predicted_per_second?.toFixed?.(1)} tok/s` : "";
    log.insertAdjacentHTML("beforeend", `<div class="msg bot">${esc(msg)}<div class="note">${esc(t)}</div></div>`);
  } catch (err) {
    log.insertAdjacentHTML("beforeend", `<div class="msg err">${esc(err.message)}</div>`);
  }
  log.scrollTop = log.scrollHeight;
});

/* ------------------------------------------------------------------ gaming */
async function loadGaming() {
  const g = await api("/api/v1/gaming/status");
  kv($("#gaming-kv"), [["session", esc(g.session)], ["front end", esc(g.frontend)],
    ["controllers", g.controllers.length ? esc(g.controllers.join(", ")) : "none detected"]]);
  $("#gaming-form").frontend.value = g.frontend;
  $("#compat-table tbody").innerHTML = (g.compat_list || []).map((t) =>
    `<tr><td>${esc(t.title)}</td><td>${esc(t.renderer)}</td><td>${state(t.status)}</td><td>${esc(t.stack || "")}</td><td>${esc(t.notes || "")}</td></tr>`
  ).join("") || "<tr><td colspan=5 class='note'>No entries yet: titles are added only with a recorded test.</td></tr>";
}
$("#gaming-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/v1/settings/set", { key: "gaming.frontend", value: e.target.frontend.value });
  loadGaming();
});

/* ------------------------------------------------------------------ system */
async function loadSystem() {
  const [caps, logs] = await Promise.all([api("/api/v1/capabilities"), api("/api/v1/logs")]);
  $("#cap-group").textContent = `group: ${caps.group || "unknown"} · ${caps.notes.join(" ")}`;
  $("#cap-table tbody").innerHTML = caps.capabilities.map((c) => {
    const ev = c.runtime?.evidence || c.static.evidence || c.notes || "";
    const src = c.static.source ? ` [${c.static.source}]` : "";
    return `<tr><td>${esc(c.title)}<div class="note">${esc(c.id)}</div></td><td>${state(c.effective)}</td><td>${esc(ev)}${esc(src)}</td></tr>`;
  }).join("");
  $("#log-table tbody").innerHTML = (logs.transitions || []).slice().reverse().map((t) =>
    `<tr><td>${esc(new Date(t.started * 1000).toLocaleString())}</td><td>${esc(t.plan.from_mode || "—")} → ${esc(t.plan.to_mode)}</td><td>${esc(t.plan.method)}</td><td>${state(t.ok ? "OK" : "FAIL")} ${esc(t.message)}</td></tr>`
  ).join("") || "<tr><td colspan=4 class='note'>No transitions yet.</td></tr>";
  const st = STATUS || {};
  kv($("#sw-kv"), [["kernel", esc(st.kernel?.release)], ["cmdline", `<code>${esc(st.kernel?.cmdline)}</code>`],
    ["southbridge", esc(st.platform?.southbridge || "—")], ["GPU", esc(st.platform?.gpu?.name || "—")]]);
  const last = await api("/api/v1/doctor/last");
  if (last) renderFindings(last);
}
function renderFindings(d) {
  const order = { FAIL: 0, WARN: 1, INFO: 2, OK: 3 };
  $("#findings").innerHTML = d.findings.slice().sort((a, b) => order[a.severity] - order[b.severity])
    .map((f) => `<li>${state(f.severity)}<span><b>${esc(f.check)}</b> ${esc(f.message)}</span></li>`).join("");
  $("#doctor-status").textContent = `${d.time} · overall ${d.worst}`;
}
async function runDoctor(deep) {
  $("#doctor-status").textContent = deep ? "running (includes Vulkan compute; may take a minute)…" : "running…";
  try {
    const r = await api("/api/v1/doctor/run", { deep });
    for (;;) {
      await new Promise((res) => setTimeout(res, 1000));
      const j = await api(`/api/v1/job/${r.job}`);
      if (j.state !== "running") { if (j.result?.findings) renderFindings(j.result); else $("#doctor-status").textContent = j.result?.error || "failed"; break; }
    }
  } catch (e) { $("#doctor-status").textContent = e.message; }
}
$("#doctor-quick").addEventListener("click", () => runDoctor(false));
$("#doctor-deep").addEventListener("click", () => runDoctor(true));
$("#reboot-btn").addEventListener("click", async () => {
  if (!confirm("Reboot? The console returns to the PS4 system software; relaunch the Linux payload afterwards.")) return;
  try { const r = await api("/api/v1/reboot", { confirm: true }); alert(r.message); } catch (e) { alert(e.message); }
});

/* ------------------------------------------------------------------ boot */
async function start() {
  await refresh();
  clearInterval(POLL);
  POLL = setInterval(refresh, 5000);
}
(async () => {
  try {
    const s = await (await fetch("/api/v1/session", { credentials: "same-origin" })).json();
    if (!s.authenticated) { showLogin(); return; }
    CSRF = s.csrf;
    start();
  } catch { $("#chip-conn").textContent = "orbis-web unreachable"; }
})();

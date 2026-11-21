"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const state = { user: null, profile: {}, air: null };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;   // textContent keeps user text XSS-safe
  return e;
}
const fmtH = h => ((h % 24) % 12 || 12) + (h % 24 < 12 ? " AM" : " PM");
const range = (a, b) => `${fmtH(a)} - ${fmtH(b)}`;

async function api(path, method = "GET", body) {
  let r;
  try {
    r = await fetch(path, { method, credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined });
  } catch { throw new Error("Cannot reach the server. Check your connection and try again."); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || "Something went wrong. Please try again.");
  return d;
}

function showError(box, msg, retry) {
  box.replaceChildren(el("p", "error", msg));
  if (retry) { const b = el("button", "btn ghost", "Try again"); b.onclick = retry; box.append(b); }
}

/* ---------- Auth ---------- */
let mode = "login";
$$(".tabs button").forEach(b => b.onclick = () => {
  mode = b.dataset.mode;
  $$(".tabs button").forEach(x => x.setAttribute("aria-selected", String(x === b)));
  $$(".signup-only").forEach(x => x.hidden = mode !== "signup");
  $("#auth-submit").textContent = mode === "signup" ? "Create account" : "Log in";
  $("#password").autocomplete = mode === "signup" ? "new-password" : "current-password";
  $("#auth-error").textContent = "";
});

$("#auth-form").onsubmit = async e => {
  e.preventDefault();
  const err = $("#auth-error"), btn = $("#auth-submit");
  const body = { email: $("#email").value.trim(), password: $("#password").value };
  if (mode === "signup") body.username = $("#username").value.trim();
  if (mode === "signup" && !body.username) return err.textContent = "Enter a username.";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(body.email)) return err.textContent = "Enter a valid email address.";
  if (body.password.length < 8) return err.textContent = "Password must be at least 8 characters.";
  err.textContent = ""; btn.disabled = true;
  try { enter(await api("/api/" + mode, "POST", body)); }
  catch (x) { err.textContent = x.message; }
  finally { btn.disabled = false; }
};

$("#logout").onclick = async () => { try { await api("/api/logout", "POST"); } finally { location.reload(); } };

function enter(d) {
  state.user = d.user; state.profile = d.profile || {};
  $("#auth").hidden = true; $("#app").hidden = false;
  $("#greeting").textContent = `Hey ${d.user.username} 👋`;
  $("#password").value = "";
  fillForm(state.profile);
  setTab("overview");
  if (state.profile.city) loadAll();
}

/* ---------- Tabs ---------- */
function setTab(name) {
  $$(".panel").forEach(p => p.hidden = p.id !== "tab-" + name);
  $$("#nav button").forEach(b => b.toggleAttribute("aria-current", b.dataset.tab === name));
  $$("#nav button").forEach(b => { if (b.dataset.tab === name) b.setAttribute("aria-current", "page"); });
  window.scrollTo(0, 0);
}
$$("#nav button").forEach(b => b.onclick = () => setTab(b.dataset.tab));

/* ---------- Profile ---------- */
const PF = $("#profile-form");
function fillForm(p) {
  PF.city.value = p.city || "";
  $$("input[name=household]").forEach(c => c.checked = (p.household || []).includes(c.value));
  PF.commute.value = p.commute || "car"; PF.distance_km.value = p.distance_km ?? 8;
  PF.days.value = p.days ?? 5; PF.people.value = p.people ?? 3;
  PF.fuel.value = p.fuel || "lpg"; PF.home.value = p.home || "closed"; PF.exhaust.checked = !!p.exhaust;
}
PF.onsubmit = async e => {
  e.preventDefault();
  const err = $("#profile-error"), btn = $("#profile-save");
  if (!PF.city.value.trim()) return err.textContent = "Enter your city or neighbourhood.";
  err.textContent = ""; btn.disabled = true;
  try {
    const d = await api("/api/profile", "POST", {
      city: PF.city.value.trim(), household: $$("input[name=household]:checked").map(c => c.value),
      commute: PF.commute.value, distance_km: +PF.distance_km.value, days: +PF.days.value,
      people: +PF.people.value, fuel: PF.fuel.value, home: PF.home.value, exhaust: PF.exhaust.checked });
    state.profile = d.profile;
    await loadAll();
  } catch (x) { err.textContent = x.message; }
  finally { btn.disabled = false; }
};

function loadAll() { return Promise.all([loadAir(), loadSwaps()]); }

/* ---------- Air + routine ---------- */
async function loadAir() {
  const card = $("#aqi-card"), rt = $("#routine");
  card.replaceChildren(el("p", "muted", "Loading live air quality..."));
  rt.replaceChildren(el("p", "muted", "Building your routine..."));
  try { state.air = await api("/api/air"); renderAir(state.air); renderRoutine(state.air); }
  catch (x) { showError(card, x.message, loadAir); showError(rt, x.message, loadAir); }
}

function renderAir(a) {
  const card = $("#aqi-card"), top = el("div", "aqi-top"), info = el("div");
  top.append(el("div", "aqi-num", String(a.aqi)));
  info.append(el("span", "badge aq-" + a.level, a.category), el("p", "muted", "US AQI · " + a.place));
  info.append(el("p", "muted", `PM2.5 ${Math.round(a.pm25 ?? 0)} µg/m³ · PM10 ${Math.round(a.pm10 ?? 0)} µg/m³`));
  top.append(info);
  card.replaceChildren(top, el("p", "advice", a.advice));
}

function renderRoutine(a) {
  const box = $("#routine"), r = a.routine;
  if (!r) return box.replaceChildren(el("p", "muted", "No hourly forecast is available for today."));
  const out = [];
  if (r.indoor_day) out.push(el("p", "advice", "Air stays poor all day. Keep outdoor time minimal and wear an N95 if you go out."));
  const labels = { exercise: ["Exercise", "Outdoor walk or workout"], school: ["School run", "Cleanest hour for the commute"],
    ventilate: ["Open windows", "Ventilate for about 15 minutes"], cooking: ["Cook with exhaust on", "Lowest-smog meal window"] };
  const picks = el("div", "picks");
  for (const [k, [t, d]] of Object.entries(labels)) {
    const p = r.picks[k], c = el("div", "pick");
    c.append(el("strong", null, t));
    if (k === "exercise" && r.indoor_day) { c.append(el("span", "when", "Indoors today"), el("p", "muted", "Try yoga or a home workout.")); }
    else if (p) { c.append(el("span", "when", range(p.start, p.end)), el("p", "muted", `${d} · about ${p.pm25} µg/m³`)); }
    else c.append(el("p", "muted", "No window left today."));
    picks.append(c);
  }
  out.push(picks);
  const lg = el("div", "legend");
  lg.append(el("span", null, "🟩 Best hours"), el("span", null, "🟨 Okay"), el("span", null, "🟥 Avoid"));
  out.push(lg);
  const grid = el("div", "hours");
  r.hours.forEach(h => {
    const c = el("div", "hr " + h.level);
    c.append(el("b", null, fmtH(h.hour)), el("span", null, h.pm25 + " µg"));
    c.title = `${h.level} · PM2.5 ${h.pm25} µg/m³`;
    grid.append(c);
  });
  out.push(grid);
  box.replaceChildren(...out);
}

/* ---------- Indoor hacks ---------- */
const HACKS = [
  { t: "Corsi-Rosenthal box", cost: "low", price: "About ₹1200-1800", who: ["kids", "elderly", "asthma", "pregnant"],
    s: ["Buy four 20x20 inch MERV 13 (or similar) filters and one 20 inch box fan.", "Tape the filters into a cube, fan side facing up on the top face.", "Seal gaps with duct tape, place on the floor and run in the room you use most."] },
  { t: "Damp mop, don't dry sweep", cost: "free", price: "Free", who: ["kids", "asthma"],
    s: ["Wipe floors and surfaces with a damp cloth or mop.", "Do it when windows are closed, then let the room air out at the cleanest hour."] },
  { t: "Time your kitchen exhaust", cost: "free", price: "Free", who: ["asthma", "kids"],
    s: ["Switch the exhaust on before you start cooking.", "Leave it on 15-20 minutes after you finish.", "Cook in your routine's lowest-smog meal window, and crack a window then."] },
  { t: "Ventilate at the cleanest hour", cost: "free", price: "Free", who: ["kids", "elderly", "asthma", "pregnant"],
    s: ["Open windows for 10-15 minutes during your best hour in the Routine tab.", "Keep them shut at morning and late-evening smog peaks."] },
  { t: "Seal the gaps", cost: "low", price: "About ₹100-300", who: ["elderly", "asthma", "kids"],
    s: ["Add weather-strip tape around windows and the door.", "On very bad days, put a damp towel at the door bottom."] },
  { t: "Skip indoor smoke", cost: "free", price: "Free", who: ["asthma", "kids", "pregnant"],
    s: ["Avoid incense, mosquito coils and burning camphor on smog days.", "Use a mosquito net or electric repellent instead."] },
  { t: "Use a well-fitted N95", cost: "low", price: "About ₹50-100 per mask", who: ["elderly", "asthma", "kids", "pregnant"],
    s: ["Choose an N95 or FFP2, not a cloth mask.", "Check the seal around nose and chin; replace when it is damp or loose."] },
  { t: "Indoor plants (set expectations)", cost: "low", price: "About ₹200-500", who: [],
    s: ["Plants like snake plant or areca palm improve the room's feel and humidity.", "On their own they don't clean the air much. Use them alongside filtering and sealing."] }
];
const COST_ORDER = { free: 0, low: 1, mid: 2 };
function renderHacks() {
  const f = $("#budget").value, mine = state.profile.household || [];
  const list = HACKS.filter(h => f === "all" || COST_ORDER[h.cost] <= COST_ORDER[f]);
  const box = $("#hack-list");
  box.replaceChildren(...list.map(h => {
    const c = el("article", "card hack"), meta = el("div", "meta");
    meta.append(el("span", "tag", h.price));
    if (h.who.some(w => mine.includes(w))) meta.append(el("span", "tag rec", "Recommended for your home"));
    const ol = el("ol"); h.s.forEach(s => ol.append(el("li", null, s)));
    c.append(el("h3", null, h.t), meta, ol);
    return c;
  }));
  if (!list.length) box.append(el("p", "muted", "No hacks match that budget."));
}
$("#budget").onchange = renderHacks;
renderHacks();

/* ---------- Swaps ---------- */
async function loadSwaps() {
  const box = $("#swaps");
  box.replaceChildren(el("p", "muted", "Calculating impact..."));
  try { renderSwaps(await api("/api/swaps")); renderHacks(); }
  catch (x) { showError(box, x.message, loadSwaps); }
}

function svgEl(tag, attrs, text) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text) e.textContent = text;
  return e;
}

function renderSwaps(d) {
  const box = $("#swaps"), goal = 15;
  const sum = el("div", "sum"), a = el("div"), b = el("div");
  a.append(el("b", null, d.total_pm25 + " µg/m³"), el("span", "muted", "Daily PM2.5 exposure reduced (estimate)"));
  b.append(el("b", null, d.total_co2 + " kg"), el("span", "muted", "CO₂ saved per week (estimate)"));
  sum.append(a, b);
  const pct = Math.min(100, Math.round(d.total_co2 / goal * 100));
  const bar = el("div", "bar"), fill = el("i"); fill.style.width = pct + "%"; bar.append(fill);
  bar.setAttribute("role", "progressbar"); bar.setAttribute("aria-valuenow", pct);
  bar.setAttribute("aria-valuemin", 0); bar.setAttribute("aria-valuemax", 100); bar.setAttribute("aria-label", "Progress toward weekly CO2 goal");
  const out = [sum, el("p", "muted", `If you make every swap: ${pct}% of a ${goal} kg CO₂ weekly goal`), bar];

  const items = d.swaps, rowH = 44, W = 600, H = items.length * rowH + 28;
  const maxP = Math.max(...items.map(s => s.pm25_ugm3), 1), maxC = Math.max(...items.map(s => s.co2_kg_week), 1);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img",
    "aria-label": "Bar chart of CO2 saved and PM2.5 exposure reduced for each swap" });
  svg.append(svgEl("rect", { x: 0, y: 4, width: 12, height: 12, fill: "#0B1F4B" }), svgEl("text", { x: 18, y: 15, "font-size": 12, fill: "#14213d" }, "CO₂ kg/week"),
    svgEl("rect", { x: 130, y: 4, width: 12, height: 12, fill: "#7fa3e8" }), svgEl("text", { x: 148, y: 15, "font-size": 12, fill: "#14213d" }, "PM2.5 µg/m³"));
  items.forEach((s, i) => {
    const y = 28 + i * rowH, cw = 330 * s.co2_kg_week / maxC, pw = 330 * s.pm25_ugm3 / maxP;
    svg.append(svgEl("text", { x: 0, y: y + 22, "font-size": 13, fill: "#14213d" }, s.short));
    svg.append(svgEl("rect", { x: 100, y: y + 4, width: Math.max(cw, 2), height: 14, rx: 3, fill: "#0B1F4B" }),
      svgEl("text", { x: 106 + cw, y: y + 15, "font-size": 12, fill: "#14213d" }, String(s.co2_kg_week)));
    svg.append(svgEl("rect", { x: 100, y: y + 21, width: Math.max(pw, 2), height: 14, rx: 3, fill: "#7fa3e8" }),
      svgEl("text", { x: 106 + pw, y: y + 32, "font-size": 12, fill: "#14213d" }, String(s.pm25_ugm3)));
  });
  out.push(svg);
  items.forEach(s => {
    const r = el("div", "swap");
    r.append(el("h3", null, s.name), el("p", "muted", s.description),
      el("p", null, `Saves about ${s.co2_kg_week} kg CO₂ per week and cuts daily PM2.5 exposure by about ${s.pm25_ugm3} µg/m³.`));
    out.push(r);
  });
  out.push(el("p", "muted", "Figures are rough estimates based on typical emission factors and your profile inputs."));
  box.replaceChildren(...out);
}

/* ---------- Chat ---------- */
const log = $("#chat-log");
function addMsg(role, text, note) {
  const m = el("div", "msg " + role, text);
  if (note) m.append(el("small", "muted", note));
  log.append(m); log.scrollTop = log.scrollHeight;
  return m;
}
addMsg("bot", "Hi! Ask me about today's smog, school runs, cooking, masks or purifiers.");
["Is it safe for my child to cycle to school today?", "Do I need a purifier?", "Best time to air out the house?"].forEach(t => {
  const b = el("button", null, t); b.type = "button";
  b.onclick = () => { $("#chat-input").value = t; $("#chat-form").requestSubmit(); };
  $("#chips").append(b);
});
$("#chat-form").onsubmit = async e => {
  e.preventDefault();
  const input = $("#chat-input"), t = input.value.trim();
  if (!t) return;
  input.value = ""; addMsg("user", t);
  const wait = addMsg("bot", "Thinking...");
  try { const d = await api("/api/chat", "POST", { message: t }); wait.remove(); addMsg("bot", d.reply, d.mode === "ai" ? "AI assistant" : "Offline advice mode"); }
  catch (x) { wait.remove(); addMsg("bot", x.message); }
};

/* ---------- Start ---------- */
(async () => { try { const d = await api("/api/me"); if (d.user) enter(d); } catch { /* show login */ } })();

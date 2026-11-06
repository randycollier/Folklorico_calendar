"use strict";

// Kiosk logic: fetch /api/events, show one upcoming event at a time, loop,
// and pick up calendar changes. Visual changes belong in index.html and
// style.css; you shouldn't need to edit this file for those.

const POLL_MS = 15000;              // how often to ask the local server for changes
const RELOAD_MS = 6 * 3600 * 1000;  // full page reload now and then keeps Chromium lean

let data = null;        // last /api/events payload
let list = [];          // events in the current rotation
let index = 0;          // position in the rotation
let currentId = null;   // id of the event on screen
let rotateTimer = null;
let rotateSeconds = 0;

const $ = (id) => document.getElementById(id);

// ---------- dates (always in the calendar's time zone) ----------

const tz = () => (data && data.timezone) || undefined;
const hour12 = () => !data || data.display.hour12 !== false;

function fmt(opts, date) {
  return new Intl.DateTimeFormat("en-US", { timeZone: tz(), ...opts }).format(date);
}

function dayKey(date) {  // "2026-10-09" in the calendar's time zone
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: tz(), year: "numeric", month: "2-digit", day: "2-digit",
  }).format(date);
}

function daysBetween(a, b) {
  const ms = Date.parse(dayKey(b) + "T00:00:00Z") - Date.parse(dayKey(a) + "T00:00:00Z");
  return Math.round(ms / 86400000);
}

function relativeLabel(now, date) {
  const n = daysBetween(now, date);
  if (n <= 0) return "Today";
  if (n === 1) return "Tomorrow";
  return `In ${n} days`;
}

function timeText(ev, start, end) {
  if (ev.allDay) {
    const last = new Date(end.getTime() - 1);  // end date is exclusive
    if (dayKey(start) === dayKey(last)) return "All day";
    const d = { month: "short", day: "numeric" };
    return `All day, ${fmt(d, start)} to ${fmt(d, last)}`;
  }
  const opts = { timeZone: tz(), hour: "numeric", minute: "2-digit", hour12: hour12() };
  if (dayKey(start) !== dayKey(end)) Object.assign(opts, { month: "short", day: "numeric" });
  const f = new Intl.DateTimeFormat("en-US", opts);
  return f.formatRange ? f.formatRange(start, end) : `${f.format(start)} to ${f.format(end)}`;
}

// ---------- event fields ----------

function fields(ev, now) {
  const start = new Date(ev.start);
  const end = new Date(ev.end);
  const happening = start <= now && now < end;
  // A multi-day event that already started shows today's date.
  const shown = start < now ? now : start;
  return {
    relative: relativeLabel(now, shown),
    weekday: fmt({ weekday: "long" }, shown),
    day: fmt({ day: "numeric" }, shown),
    month: fmt({ month: "long" }, shown),
    calendar: ev.calendar,
    title: ev.title,
    time: timeText(ev, start, end),
    location: ev.location,
    description: ev.description,
    nowBadge: happening ? "Happening now" : "",
  };
}

function upcoming(now) {
  if (!data) return [];
  const max = Number(data.display.max_events) || 10;
  return data.events
    .filter((ev) => new Date(ev.end) > now)
    .sort((a, b) => new Date(a.start) - new Date(b.start))
    .slice(0, max);
}

// ---------- rendering ----------

function fill(node, ev, now) {
  const f = fields(ev, now);
  node.querySelectorAll("[data-field]").forEach((el) => {
    const value = f[el.dataset.field];
    el.textContent = value || "";
    el.hidden = !value;
  });
  if (ev.color) node.style.setProperty("--event-color", ev.color);
  node.classList.toggle("is-now", Boolean(f.nowBadge));
}

function render(ev, now, animate) {
  const stage = $("stage");
  const existing = stage.firstElementChild;
  // Same event, nothing else to show: update in place instead of replaying the fade.
  if (!animate && ev && existing && existing.dataset.id === ev.id) {
    fill(existing, ev, now);
    return;
  }
  let node;
  if (ev) {
    node = $("event-template").content.firstElementChild.cloneNode(true);
    node.dataset.id = ev.id;
    fill(node, ev, now);
  } else {
    if (existing && existing.classList.contains("empty")) return;
    node = $("empty-template").content.firstElementChild.cloneNode(true);
  }
  stage.replaceChildren(node);
}

// ---------- sidebar: two months + agenda ----------

const DAY_MS = 86400000;

function keyToUtc(key) { return Date.parse(key + "T00:00:00Z"); }
function utcToKey(ms) { return new Date(ms).toISOString().slice(0, 10); }

// Every calendar day ("YYYY-MM-DD") an event touches, in the calendar's time zone.
function eventDays(ev) {
  const first = dayKey(new Date(ev.start));
  const endMs = new Date(ev.end).getTime();
  const last = dayKey(new Date(Math.max(endMs - 1, new Date(ev.start).getTime())));
  const days = [];
  for (let t = keyToUtc(first); t <= keyToUtc(last) && days.length < 400; t += DAY_MS) {
    days.push(utcToKey(t));
  }
  return days;
}

function renderMonth(el, year, month, now, colorsByDay, active) {
  const todayKey = dayKey(now);
  const firstMs = Date.UTC(year, month - 1, 1);
  const lead = new Date(firstMs).getUTCDay();             // weeks start on Sunday
  const count = new Date(Date.UTC(year, month, 0)).getUTCDate();

  const title = document.createElement("div");
  title.className = "month-title";
  title.textContent = new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC", month: "long", year: "numeric",
  }).format(firstMs);

  const grid = document.createElement("div");
  grid.className = "month-grid";
  for (const w of ["S", "M", "T", "W", "T", "F", "S"]) {
    const h = document.createElement("span");
    h.className = "dow";
    h.textContent = w;
    grid.append(h);
  }
  for (let i = 0; i < lead; i++) grid.append(document.createElement("span"));
  for (let d = 1; d <= count; d++) {
    const key = utcToKey(firstMs + (d - 1) * DAY_MS);
    const cell = document.createElement("span");
    cell.className = "d";
    cell.textContent = d;
    if (key < todayKey) cell.classList.add("past");
    if (key === todayKey) cell.classList.add("today");
    const colors = colorsByDay.get(key);
    if (colors) {
      const dots = document.createElement("span");
      dots.className = "dots";
      for (const c of [...colors].slice(0, 3)) {
        const dot = document.createElement("i");
        dot.style.setProperty("--c", c);
        dots.append(dot);
      }
      cell.append(dots);
    }
    if (active && active.days.has(key)) {
      cell.classList.add("active");
      cell.style.setProperty("--c", active.color);
    }
    grid.append(cell);
  }
  el.replaceChildren(title, grid);
}

function renderMonths(now) {
  const colorsByDay = new Map();
  for (const ev of (data ? data.events : [])) {
    if (new Date(ev.end) <= now) continue;
    for (const key of eventDays(ev)) {
      if (!colorsByDay.has(key)) colorsByDay.set(key, new Set());
      colorsByDay.get(key).add(ev.color || "var(--accent)");
    }
  }
  const ev = list[index];
  const active = ev ? { days: new Set(eventDays(ev)), color: ev.color || "var(--accent)" } : null;
  const [y, m] = dayKey(now).split("-").map(Number);
  renderMonth($("month-0"), y, m, now, colorsByDay, active);
  renderMonth($("month-1"), m === 12 ? y + 1 : y, m === 12 ? 1 : m + 1, now, colorsByDay, active);
}

let agendaSignature = "";

function renderAgenda(now) {
  const agenda = $("agenda");
  const signature = dayKey(now) + "|" + list.map((ev) => ev.id + ev.start + ev.title).join(",");
  if (signature !== agendaSignature) {
    agendaSignature = signature;
    agenda.replaceChildren(...list.map((ev) => {
      const start = new Date(ev.start);
      const shown = start < now ? now : start;
      const li = document.createElement("li");
      li.dataset.id = ev.id;
      li.style.setProperty("--c", ev.color || "var(--accent)");
      const when = document.createElement("span");
      when.className = "when";
      when.textContent = fmt({ month: "short", day: "numeric" }, shown);
      const what = document.createElement("span");
      what.className = "what";
      what.textContent = ev.title;
      li.append(when, what);
      return li;
    }));
  }
  let activeLi = null;
  for (const li of agenda.children) {
    const on = li.dataset.id === currentId;
    li.classList.toggle("active", on);
    if (on) activeLi = li;
  }
  // Keep the highlighted event in view: scroll so it sits about a third of
  // the way down, which also shows a couple of events before it.
  if (activeLi) {
    const target = activeLi.offsetTop - agenda.offsetTop - agenda.clientHeight / 3;
    const max = agenda.scrollHeight - agenda.clientHeight;
    agenda.scrollTo({ top: Math.max(0, Math.min(max, target)), behavior: "smooth" });
  } else {
    agenda.scrollTo({ top: 0 });
  }
}

function renderSidebar(now) {
  renderMonths(now);
  renderAgenda(now);
}

// ---------- QR code ----------

let qrShown = "";

function renderQr() {
  const url = (data.display.qr_url || "").trim();
  const text = data.display.qr_text || "";
  $("qr").hidden = !url || typeof qrcode !== "function";
  if ($("qr").hidden || url + text === qrShown) return;
  qrShown = url + text;
  const qr = qrcode(0, "L");   // 0 = smallest size that fits the link
  qr.addData(url);
  qr.make();
  $("qr-code").innerHTML = qr.createSvgTag({ cellSize: 4, margin: 8, scalable: true });
  // Text before the first colon is shown in bold, e.g. "Dancers: scan to ...".
  const m = text.match(/^([^:]{1,30}:)\s*(.*)$/s);
  const p = $("qr-text");
  if (m) {
    const b = document.createElement("b");
    b.textContent = m[1];
    p.replaceChildren(b, " " + m[2]);
  } else {
    p.textContent = text;
  }
}

function show(animate) {
  const now = new Date();
  list = upcoming(now);
  if (index >= list.length) index = 0;
  const ev = list[index] || null;
  currentId = ev ? ev.id : null;
  render(ev, now, animate);
  renderSidebar(now);
}

function advance() {
  index += 1;
  show(list.length > 1);
}

function startRotation() {
  const secs = Math.max(3, Number(data.display.seconds_per_event) || 12);
  if (secs === rotateSeconds && rotateTimer) return;
  clearInterval(rotateTimer);
  rotateSeconds = secs;
  rotateTimer = setInterval(advance, secs * 1000);
}

// ---------- data ----------

function setOffline(on) {
  $("offline").hidden = !on;
}

function applyChange(first) {
  $("board-title").textContent = data.display.title || "Calendar";
  document.title = data.display.title || "Calendar";
  renderQr();
  const now = new Date();
  list = upcoming(now);
  const stillThere = list.findIndex((ev) => ev.id === currentId);
  if (stillThere >= 0) {
    index = stillThere;   // keep the event on screen, refresh its details
    show(false);
  } else {
    index = 0;            // it was deleted or moved: start over from the next event
    show(true);
    clearInterval(rotateTimer);
    rotateTimer = null;
  }
  startRotation();
}

async function poll() {
  try {
    const res = await fetch("/api/events", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const next = await res.json();
    setOffline(Boolean(next.error));
    const changed = !data || next.version !== data.version;
    const first = !data;
    data = next;
    if (changed) applyChange(first);
  } catch (err) {
    setOffline(true);
  }
  setTimeout(poll, data ? POLL_MS : 2000);
}

function tickClock() {
  const now = new Date();
  $("clock").textContent =
    fmt({ weekday: "long", month: "long", day: "numeric" }, now) + "  ·  " +
    fmt({ hour: "numeric", minute: "2-digit", hour12: hour12() }, now);
}

tickClock();
setInterval(tickClock, 1000);
poll();
setTimeout(() => location.reload(), RELOAD_MS);

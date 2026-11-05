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

function renderFooter(now) {
  const dots = $("dots");
  dots.replaceChildren(...list.map((_, i) => {
    const li = document.createElement("li");
    if (i === index) li.className = "active";
    return li;
  }));
  dots.hidden = list.length < 2;

  const next = list.length > 1 ? list[(index + 1) % list.length] : null;
  $("up-next").hidden = !next;
  if (next) {
    const start = new Date(next.start);
    const shown = start < now ? now : start;
    const when = relativeLabel(now, shown);
    const prefix = when === "Today" || when === "Tomorrow"
      ? when : fmt({ weekday: "short", month: "short", day: "numeric" }, shown);
    const time = next.allDay ? "" : " " + fmt({ hour: "numeric", minute: "2-digit", hour12: hour12() }, start);
    $("up-next-when").textContent = prefix + time;
    $("up-next-title").textContent = next.title;
  }
}

function show(animate) {
  const now = new Date();
  list = upcoming(now);
  if (index >= list.length) index = 0;
  const ev = list[index] || null;
  currentId = ev ? ev.id : null;
  render(ev, now, animate);
  renderFooter(now);
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

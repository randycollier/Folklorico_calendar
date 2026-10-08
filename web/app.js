// Starter display logic: show one event, wait, show the next, loop.
// Re-reads /api/events every 15 seconds so calendar changes show up.

const $ = (id) => document.getElementById(id);
let events = [];
let index = 0;
let secondsPerEvent = 12;

async function refresh() {
  try {
    const res = await fetch("/api/events", { cache: "no-store" });
    const data = await res.json();
    events = data.events || [];
    secondsPerEvent = data.seconds_per_event || 12;
    $("status").hidden = !data.error;
    $("status").textContent = "Offline";
  } catch {
    $("status").hidden = false;
    $("status").textContent = "Offline";
  }
}

function parse(value, allDay) {
  // All-day events come as "2026-10-08". Read them as local midnight.
  return allDay ? new Date(value + "T00:00:00") : new Date(value);
}

function relativeLabel(start) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(start); day.setHours(0, 0, 0, 0);
  const diff = Math.round((day - today) / 86400000);
  if (diff <= 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return `In ${diff} days`;
}

function timeLabel(e, start, end) {
  if (e.allDay) return "All day";
  const fmt = { hour: "numeric", minute: "2-digit" };
  return `${start.toLocaleTimeString([], fmt)} to ${end.toLocaleTimeString([], fmt)}`;
}

function setText(id, text) {
  $(id).textContent = text || "";
  $(id).hidden = !text;
}

function show() {
  if (!events.length) {
    $("card").hidden = true;
    $("empty").hidden = false;
    $("empty").textContent = "No upcoming events";
    return;
  }
  if (index >= events.length) index = 0;
  const e = events[index];
  const start = parse(e.start, e.allDay);
  const end = parse(e.end, e.allDay);

  document.documentElement.style.setProperty("--event-color", e.color);
  setText("relative", relativeLabel(start));
  setText("weekday", start.toLocaleDateString([], { weekday: "long" }));
  setText("day", String(start.getDate()));
  setText("month", start.toLocaleDateString([], { month: "long" }));
  setText("calendar", e.calendar);
  setText("title", e.title);
  setText("time", timeLabel(e, start, end));
  setText("location", e.location);
  setText("description", e.description);

  $("empty").hidden = true;
  $("card").hidden = false;
}

async function loop() {
  show();
  index += 1;
  setTimeout(loop, secondsPerEvent * 1000);
}

refresh().then(loop);
setInterval(refresh, 15000);

export function formatDistance(m) {
  if (m == null) return "";
  return m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(m < 10000 ? 1 : 0)} km`;
}

export function formatDuration(s) {
  const mins = Math.round(s / 60);
  if (mins < 60) return `${mins} min`;
  return `${Math.floor(mins / 60)} hr ${mins % 60} min`;
}

// <input type="datetime-local"> wants local time with no zone suffix.
export function toLocalInput(d) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function localNow() {
  return toLocalInput(new Date());
}

// The next time the clock reads `hour`:00 — tonight's 11 PM, or tomorrow's
// 1 AM if it is already past 1 AM today.
export function nextAt(hour) {
  const d = new Date();
  d.setMinutes(0, 0, 0);
  if (d.getHours() >= hour) d.setDate(d.getDate() + 1);
  d.setHours(hour);
  return toLocalInput(d);
}

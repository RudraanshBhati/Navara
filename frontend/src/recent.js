// Recent searches, shown when a search box is focused and empty — the
// "predicted destinations" list. Per-browser convenience only; storage can be
// unavailable (private windows, blocked site data), so every access is guarded.

const KEY = "navara.recentPlaces";
const MAX = 6;

export function loadRecent() {
  try {
    const items = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(items) ? items : [];
  } catch {
    return [];
  }
}

export function saveRecent(place) {
  try {
    const items = loadRecent().filter((p) => !(p.lat === place.lat && p.lng === place.lng));
    items.unshift({ name: place.name, address: place.address ?? "", lat: place.lat, lng: place.lng, kind: place.kind ?? "place" });
    localStorage.setItem(KEY, JSON.stringify(items.slice(0, MAX)));
  } catch {
    // storage unavailable — recents simply do not persist
  }
}

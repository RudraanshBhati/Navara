const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function request(path, options) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, options);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new Error(`Cannot reach the backend at ${API_BASE}. Is it running?`);
  }
  if (!res.ok) {
    throw new Error(await errorDetail(res));
  }
  return res.json();
}

// The backend puts a human-readable message in `detail`. On a 422 from
// request validation it is a list of {msg, loc} objects instead of a string.
async function errorDetail(res) {
  try {
    const { detail } = await res.json();
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
  } catch {
    // not JSON — fall through
  }
  return `Request failed (${res.status})`;
}

export function searchPlaces(query, session, near, signal) {
  const params = new URLSearchParams({ q: query, session });
  if (near) {
    params.set("lat", near.lat);
    params.set("lng", near.lng);
  }
  return request(`/places/autocomplete?${params}`, { signal });
}

export function getPlace(placeId, session) {
  const params = new URLSearchParams({ session });
  return request(`/places/${encodeURIComponent(placeId)}?${params}`);
}

export function getPois(bounds, categories, signal) {
  const params = new URLSearchParams({
    bbox: [bounds.west, bounds.south, bounds.east, bounds.north].map((v) => v.toFixed(5)).join(","),
    limit: "400",
  });
  if (categories?.length) params.set("categories", categories.join(","));
  return request(`/pois?${params}`, { signal });
}

export function getHealth() {
  return request("/health");
}

export function getRoutes(origin, destination, departureTime, travelMode = "walk") {
  return request("/routes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      origin,
      destination,
      departure_time: departureTime,
      travel_mode: travelMode,
    }),
  });
}

// Google encoded polyline, precision 5 -> [[lat, lng], ...].
// Mirrors decode_polyline in backend/app/routes_api.py.
export function decodePolyline(encoded) {
  const points = [];
  let index = 0;
  let lat = 0;
  let lng = 0;

  while (index < encoded.length) {
    for (const isLat of [true, false]) {
      let shift = 0;
      let result = 0;
      let b;
      do {
        if (index >= encoded.length) return points;
        b = encoded.charCodeAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      const delta = result & 1 ? ~(result >> 1) : result >> 1;
      if (isLat) lat += delta;
      else lng += delta;
    }
    points.push([lat / 1e5, lng / 1e5]);
  }
  return points;
}

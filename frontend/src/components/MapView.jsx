import "maplibre-gl/dist/maplibre-gl.css";
import "@maplibre/maplibre-gl-leaflet";

import L from "leaflet";
import { setWorkerUrl } from "maplibre-gl";
// MapLibre finds its worker by a computed relative URL, which no bundler can
// follow: the production build shipped without the file and the map rendered
// blank ("Worker failed to load"). `?worker&url` makes Vite bundle the worker
// — with the chunk it imports — and hand back its real URL.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useRef } from "react";

import { getPois } from "../api.js";
import { formatDuration } from "../format.js";
import { CATEGORY_STYLE, markerHtml, svgPath } from "../icons.jsx";
import { decodePolyline } from "../polyline.js";

setWorkerUrl(maplibreWorkerUrl);

const DELHI_CENTER = [28.6139, 77.209];

// The API sends a band name, never a colour.
export const BAND_COLORS = {
  safe: "#2e9e5b",
  caution: "#e0a52b",
  risk: "#d4443c",
};

// Which places appear at which zoom when no category chip is selected. A
// city-wide view shows only what you would look for in an emergency; shops
// and cafes appear at street level, as on Google Maps.
const ZOOM_TIERS = [
  [13, ["police", "hospital", "metro", "train"]],
  [15, ["bus", "pharmacy"]],
  [17, ["toilets", "fuel", "atm", "food", "shop"]],
];
const CHIP_MIN_ZOOM = 12;

// Minimum on-screen spacing between markers, in pixels. Markers arrive
// highest-priority first, so when two would overlap the police station or
// hospital is the one kept — Google Maps declutters the same way.
const DECLUTTER_PX = 30;

// The view lives in the URL like Google Maps' "@lat,lng,zoomz", so a view can
// be shared or bookmarked and survives a reload.
function viewFromHash() {
  const m = window.location.hash.match(/^#@(-?\d+\.?\d*),(-?\d+\.?\d*),(\d+(?:\.\d+)?)z/);
  return m ? { center: [Number(m[1]), Number(m[2])], zoom: Number(m[3]) } : null;
}

function writeHash(map) {
  const c = map.getCenter();
  window.history.replaceState(null, "", `#@${c.lat.toFixed(5)},${c.lng.toFixed(5)},${map.getZoom()}z`);
}

// Padding for fitBounds that keeps routes out from under the panel. Desktop
// has a 408 px panel on the left; phones have a bottom sheet over ~72% of the
// height. A fixed 440 px left pad on a 390 px-wide phone left fitBounds a
// negative area, and MapLibre crashed computing the resulting zoom.
const PANEL_W = 408;
const SHEET_SHARE = 0.58;
// Clears the route key, which sits along the top of the map on phones.
const LEGEND_PAD = 48;

function fitPadding(map, pad) {
  const { x: w, y: h } = map.getSize();
  if (w <= 720) {
    const bottom = Math.min(Math.round(h * SHEET_SHARE) + 16, h - LEGEND_PAD - pad);
    return { paddingTopLeft: [16, LEGEND_PAD], paddingBottomRight: [16, Math.max(bottom, 16)] };
  }
  return { paddingTopLeft: [Math.min(PANEL_W + 32, w / 2), pad], paddingBottomRight: [pad, pad] };
}

// Where to put a route's duration bubble: the point on it farthest from every
// other route, so bubbles sit where routes diverge instead of stacking on the
// stretch they share. Middle third preferred, ends avoided.
function calloutPoint(pts, others) {
  if (!others.length) return pts[Math.floor(pts.length / 2)];
  const step = Math.max(1, Math.floor(pts.length / 40));
  const otherPts = others.flatMap((o) => o.filter((_, k) => k % step === 0));
  let best = pts[Math.floor(pts.length / 2)];
  let bestD = -1;
  // Not the far end: a bubble there sits on the destination pin.
  const lo = Math.floor(pts.length * 0.2);
  const hi = Math.ceil(pts.length * 0.7);
  for (let k = lo; k < hi; k += step) {
    const [la, ln] = pts[k];
    let d = Infinity;
    for (const [lb, lnb] of otherPts) d = Math.min(d, (la - lb) ** 2 + (ln - lnb) ** 2);
    if (d > bestD) {
      bestD = d;
      best = pts[k];
    }
  }
  return best;
}

// The route's own points along a stretch the backend reported by its two ends.
function stretchPoints(route, stretch) {
  if (!stretch) return null;
  const same = (a, b) => Math.abs(a.lat - b.lat) < 1e-9 && Math.abs(a.lng - b.lng) < 1e-9;
  const from = route.segments.findIndex((s) => same(s.start, stretch.start));
  const to = route.segments.findIndex((s) => same(s.end, stretch.end));
  if (from < 0 || to < from) return null;
  return [
    [stretch.start.lat, stretch.start.lng],
    ...route.segments.slice(from, to + 1).map((s) => [s.end.lat, s.end.lng]),
  ];
}

function categoriesFor(zoom, activeCategory) {
  if (activeCategory) return zoom >= CHIP_MIN_ZOOM ? [activeCategory] : [];
  return ZOOM_TIERS.filter(([z]) => zoom >= z).flatMap(([, cats]) => cats);
}

export default function MapView({
  origin,
  destination,
  routes,
  selectedRank,
  onSelectRoute,
  selectedPlace,
  userLocation,
  activeCategory,
  showPois = true,
  onPoiClick,
  onMapClick,
  onViewChange,
  onLocate,
  onPoiStatus,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const layers = useRef({});
  const handlers = useRef({});
  handlers.current = { onPoiClick, onMapClick, onViewChange, onLocate, onSelectRoute, onPoiStatus };
  const categoryRef = useRef(activeCategory);
  const showPoisRef = useRef(showPois);
  const poiRequest = useRef(null);
  const lastFitKey = useRef(null);

  // -- map setup --------------------------------------------------------
  useEffect(() => {
    const initial = viewFromHash();
    const map = L.map(containerRef.current, { zoomControl: false }).setView(
      initial?.center ?? DELHI_CENTER,
      initial?.zoom ?? 12,
    );
    // OpenFreeMap's "Liberty" vector style: a clean, Google-like street map
    // on OpenStreetMap data. Free, no key, no usage limit, rendered by
    // MapLibre inside Leaflet so everything else here stays plain Leaflet.
    // (CARTO's raster tiles, tried first, now require a key and render an
    // "API KEY REQUIRED" watermark without one.)
    L.maplibreGL({
      style: "https://tiles.openfreemap.org/styles/liberty",
      attribution:
        '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> &copy; <a href="https://www.openmaptiles.org/" target="_blank">OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    }).addTo(map);

    const LocateControl = L.Control.extend({
      onAdd() {
        const btn = L.DomUtil.create("button", "map-btn");
        btn.type = "button";
        btn.title = "Show your location";
        btn.innerHTML = `<svg width="22" height="22" viewBox="0 0 24 24"><path d="${svgPath("myLocation")}" fill="#5f6368"/></svg>`;
        L.DomEvent.disableClickPropagation(btn);
        L.DomEvent.on(btn, "click", () => handlers.current.onLocate());
        return btn;
      },
    });
    new LocateControl({ position: "bottomright" }).addTo(map);
    L.control.zoom({ position: "bottomright" }).addTo(map);

    // Route colour key, shown only while routes are on the map. Its entries
    // are filled in per route: a key for marks that are not there is noise.
    const Legend = L.Control.extend({
      onAdd() {
        return L.DomUtil.create("div", "map-legend");
      },
    });
    layers.current.legend = new Legend({ position: "bottomleft" });

    layers.current = {
      ...layers.current,
      pois: L.layerGroup().addTo(map),
      routes: L.layerGroup().addTo(map),
      pins: L.layerGroup().addTo(map),
      me: L.layerGroup().addTo(map),
    };

    map.on("click", (e) => handlers.current.onMapClick({ lat: e.latlng.lat, lng: e.latlng.lng }));
    map.on("moveend", () => {
      const c = map.getCenter();
      handlers.current.onViewChange({ lat: c.lat, lng: c.lng });
      writeHash(map);
      schedulePois();
    });

    mapRef.current = map;
    if (import.meta.env.DEV) window.__navaraMap = map; // for debugging in devtools
    schedulePois();
    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // -- places (POIs) ----------------------------------------------------
  const poiTimer = useRef(null);
  function schedulePois() {
    clearTimeout(poiTimer.current);
    poiTimer.current = setTimeout(loadPois, 250);
  }

  function loadPois() {
    const map = mapRef.current;
    if (!map) return;
    // Hidden while showing directions: hundreds of markers bury the route.
    const cats = showPoisRef.current ? categoriesFor(map.getZoom(), categoryRef.current) : [];
    poiRequest.current?.abort();
    if (cats.length === 0) {
      layers.current.pois.clearLayers();
      handlers.current.onPoiStatus?.({ count: 0, truncated: false, hidden: true });
      return;
    }
    const b = map.getBounds();
    const controller = new AbortController();
    poiRequest.current = controller;
    getPois({ west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() }, cats, controller.signal)
      .then((data) => drawPois(data))
      .catch((err) => {
        if (err.name !== "AbortError") handlers.current.onPoiStatus?.({ error: err.message });
      });
  }

  function drawPois(data) {
    const map = mapRef.current;
    const group = layers.current.pois;
    group.clearLayers();
    const zoom = map.getZoom();
    const size = zoom >= 17 ? 28 : zoom >= 15 ? 24 : 20;
    const taken = new Set();
    let shown = 0;
    for (const poi of data.pois) {
      const pt = map.latLngToContainerPoint([poi.lat, poi.lng]);
      const cell = `${Math.floor(pt.x / DECLUTTER_PX)}:${Math.floor(pt.y / DECLUTTER_PX)}`;
      if (taken.has(cell)) continue;
      taken.add(cell);
      shown += 1;
      const style = CATEGORY_STYLE[poi.category];
      const marker = L.marker([poi.lat, poi.lng], {
        icon: L.divIcon({
          html: markerHtml(poi.category, style.color, size),
          className: "poi-icon",
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        }),
        keyboard: false,
      });
      if (poi.name) {
        marker.bindTooltip(poi.name, {
          direction: "right",
          offset: [size / 2, 0],
          className: "poi-label",
          permanent: zoom >= 17 && ["police", "hospital", "metro", "train"].includes(poi.category),
        });
      }
      marker.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        handlers.current.onPoiClick(poi);
      });
      marker.addTo(group);
    }
    const crowded = data.truncated || shown < data.pois.length;
    handlers.current.onPoiStatus?.({ count: shown, truncated: crowded, hidden: false });
  }

  useEffect(() => {
    categoryRef.current = activeCategory;
    showPoisRef.current = showPois;
    loadPois();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCategory, showPois]);

  // -- routes -----------------------------------------------------------
  useEffect(() => {
    const group = layers.current.routes;
    group.clearLayers();
    const legend = layers.current.legend;
    if (!routes.length) {
      legend?.remove();
      return;
    }
    if (legend && !legend._map) legend.addTo(mapRef.current);

    const selected = routes.find((r) => r.rank === selectedRank) ?? routes[0];

    legend.getContainer().innerHTML = [
      `<span><i style="background:${BAND_COLORS.safe}"></i>Safe</span>`,
      `<span><i style="background:${BAND_COLORS.caution}"></i>Caution</span>`,
      `<span><i style="background:${BAND_COLORS.risk}"></i>Risk</span>`,
      selected.longest_gap && '<span><i class="dotted"></i>Nothing open</span>',
      selected.longest_isolated && '<span><i class="isolated"></i>Isolated</span>',
      selected.farthest_help && '<span><i class="help"></i>Help</span>',
      selected.open_places.length > 0 && '<span><i class="open"></i>Open</span>',
      selected.signals.length > 0 && '<span><i class="signal"></i>Signal</span>',
      selected.cameras.length > 0 && '<span><i class="camera"></i>CCTV</span>',
    ]
      .filter(Boolean)
      .join("");

    for (const route of routes) {
      if (route === selected) continue;
      const pts = decodePolyline(route.polyline);
      L.polyline(pts, { color: "#9aa0a6", weight: 7, opacity: 0.9 }).addTo(group);
      const alt = L.polyline(pts, { color: "#bdc1c6", weight: 5, opacity: 1 }).addTo(group);
      alt.bindTooltip(`via ${route.label}`, { sticky: true });
      alt.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        handlers.current.onSelectRoute(route.rank);
      });
    }

    // Selected route: a dark casing under per-segment safety colours.
    L.polyline(decodePolyline(selected.polyline), { color: "#174ea6", weight: 10, opacity: 0.35 }).addTo(group);
    for (const seg of selected.segments) {
      const line = L.polyline(
        [
          [seg.start.lat, seg.start.lng],
          [seg.end.lat, seg.end.lng],
        ],
        { color: BAND_COLORS[seg.band] ?? "#1a73e8", weight: 6, opacity: 1, lineCap: "round" },
      ).addTo(group);
      if (seg.flag) line.bindTooltip(seg.flag, { sticky: true });
    }

    // Duration callouts, Google-style: one bubble per route, on its own line,
    // and clicking one picks that route.
    const decoded = routes.map((r) => decodePolyline(r.polyline));
    routes.forEach((route, i) => {
      const pts = decoded[i];
      if (!pts.length) return;
      const on = route === selected;
      // On a phone the routes are too close together for several bubbles; the
      // cards under the map hold the other routes' times.
      if (!on && mapRef.current.getSize().x <= 720) return;
      const tags = [route.safest && "Safest", route.fastest && "Fastest"].filter(Boolean).join(" · ");
      L.marker(calloutPoint(pts, decoded.filter((_, j) => j !== i)), {
        icon: L.divIcon({
          className: "callout-icon",
          html: `<div class="route-callout${on ? " route-callout--on" : ""}">
            <strong>${formatDuration(route.duration_s)}</strong>${tags ? `<span>${tags}</span>` : ""}
          </div>`,
          iconSize: null,
          iconAnchor: [0, 0],
        }),
        zIndexOffset: on ? 900 : 800,
        keyboard: false,
      })
        .on("click", (e) => {
          L.DomEvent.stopPropagation(e);
          handlers.current.onSelectRoute(route.rank);
        })
        .addTo(group);
    });

    // The longest stretch with nothing open: a dark dashed overlay along the
    // route, labelled with the backend's own sentence for it.
    const factText = (key) => selected.facts.find((f) => f.key === key)?.text ?? "";
    const gapPts = stretchPoints(selected, selected.longest_gap);
    if (gapPts) {
      L.polyline(gapPts, { color: "#202124", weight: 4, opacity: 0.9, dashArray: "2 9", lineCap: "round" })
        .bindTooltip(factText("gap"), { sticky: true })
        .addTo(group);
    }

    // The longest stretch with no homes and no mapped place.
    const isolatedPts = stretchPoints(selected, selected.longest_isolated);
    if (isolatedPts) {
      L.polyline(isolatedPts, { color: "#7b1fa2", weight: 4, opacity: 0.95, dashArray: "10 8" })
        .bindTooltip(factText("isolated"), { sticky: true })
        .addTo(group);
    }

    // Where help is farthest: a thin line from that point to the nearest place.
    const help = selected.farthest_help;
    if (help) {
      const style = CATEGORY_STYLE[help.place.category];
      L.polyline(
        [
          [help.point.lat, help.point.lng],
          [help.place.lat, help.place.lng],
        ],
        { color: "#c5221f", weight: 2, opacity: 0.8, dashArray: "4 6" },
      )
        .bindTooltip(factText("help"), { sticky: true })
        .addTo(group);
      L.marker([help.place.lat, help.place.lng], {
        icon: L.divIcon({
          html: markerHtml(help.place.category, style.color, 24),
          className: "poi-icon",
          iconSize: [24, 24],
          iconAnchor: [12, 12],
        }),
        keyboard: false,
      })
        .bindTooltip(help.place.name || factText("help"), {
          direction: "right",
          offset: [12, 0],
          className: "poi-label",
        })
        .addTo(group);
    }

    // Signalled junctions, for riders and drivers: where they may have to stop.
    for (const s of selected.signals) {
      L.circleMarker([s.lat, s.lng], { radius: 5, color: "#fff", weight: 1.5, fillColor: "#b06000", fillOpacity: 1 })
        .bindTooltip("Mapped traffic signal")
        .addTo(group);
    }

    // Mapped CCTV: what the fact counts, drawn where it is.
    for (const c of selected.cameras) {
      L.circleMarker([c.lat, c.lng], { radius: 3, color: "#fff", weight: 1, fillColor: "#3c4043", fillOpacity: 1 })
        .bindTooltip("Mapped CCTV camera")
        .addTo(group);
    }

    // Places likely open along the route — what the "passes N places" count is.
    // Dots, not icons: a route can pass a hundred of them, and icons buried the
    // line they were meant to annotate.
    for (const p of selected.open_places) {
      const dot = L.circleMarker([p.lat, p.lng], {
        radius: 3.5,
        color: "#fff",
        weight: 1,
        fillColor: "#188038",
        fillOpacity: 1,
        interactive: Boolean(p.name),
      });
      if (p.name) dot.bindTooltip(p.name, { direction: "right", offset: [6, 0], className: "poi-label" });
      dot.addTo(group);
    }

    // Refit only for a new set of routes, not when switching between them.
    const fitKey = routes.map((r) => r.polyline).join("|");
    if (fitKey !== lastFitKey.current) {
      lastFitKey.current = fitKey;
      const bounds = L.latLngBounds(routes.flatMap((r) => decodePolyline(r.polyline)));
      mapRef.current.fitBounds(bounds, fitPadding(mapRef.current, 60));
    }
  }, [routes, selectedRank]);

  // -- pins: start, destination, selected place --------------------------
  useEffect(() => {
    const group = layers.current.pins;
    group.clearLayers();

    if (origin && !origin.isMe) {
      L.circleMarker([origin.lat, origin.lng], {
        radius: 7,
        color: "#202124",
        weight: 3,
        fillColor: "#fff",
        fillOpacity: 1,
      })
        .bindTooltip(origin.name, { direction: "top", offset: [0, -6] })
        .addTo(group);
    }
    const pin = destination ?? selectedPlace;
    if (pin) {
      L.marker([pin.lat, pin.lng], {
        icon: L.divIcon({ html: '<div class="red-pin"></div>', className: "pin-icon", iconSize: [28, 40], iconAnchor: [14, 38] }),
        zIndexOffset: 1000,
      })
        .bindTooltip(pin.name || "Dropped pin", { direction: "top", offset: [0, -36] })
        .addTo(group);
    }

    if (!routes.length) {
      if (selectedPlace && !destination) {
        mapRef.current.flyTo([selectedPlace.lat, selectedPlace.lng], Math.max(mapRef.current.getZoom(), 16), {
          duration: 0.6,
        });
      } else if (origin && destination) {
        mapRef.current.fitBounds(
          L.latLngBounds([
            [origin.lat, origin.lng],
            [destination.lat, destination.lng],
          ]),
          { ...fitPadding(mapRef.current, 80), maxZoom: 15 },
        );
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [origin, destination, selectedPlace]);

  // -- you are here ------------------------------------------------------
  useEffect(() => {
    const group = layers.current.me;
    group.clearLayers();
    if (!userLocation) return;
    const ll = [userLocation.lat, userLocation.lng];
    if (userLocation.accuracy) {
      L.circle(ll, {
        radius: Math.min(userLocation.accuracy, 500),
        color: "#1a73e8",
        weight: 0,
        fillOpacity: 0.12,
        interactive: false,
      }).addTo(group);
    }
    L.circleMarker(ll, { radius: 8, color: "#fff", weight: 3, fillColor: "#1a73e8", fillOpacity: 1 })
      .bindTooltip("Your location")
      .addTo(group);
    if (userLocation.fly) mapRef.current.flyTo(ll, 16, { duration: 0.6 });
  }, [userLocation]);

  return <div ref={containerRef} className="map" />;
}

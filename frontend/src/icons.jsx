// Hand-drawn 24x24 glyphs, white on a coloured disc, in the style of Google
// Maps place markers. Kept as path strings so they work both inside React and
// inside Leaflet divIcons (which take an HTML string).

const PATHS = {
  police: "M12 2l8 3v6c0 5-3.5 9.7-8 11-4.5-1.3-8-6-8-11V5z",
  hospital: "M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6z",
  metro: "M4 20V4h3l5 8 5-8h3v16h-3V9l-5 8-5-8v11z",
  train:
    "M6 3h12a2 2 0 012 2v10a3 3 0 01-3 3l2 3h-2.5l-2-3h-5l-2 3H5l2-3a3 3 0 01-3-3V5a2 2 0 012-2zm0 3v5h12V6zm2 8a1.5 1.5 0 100 3 1.5 1.5 0 000-3zm8 0a1.5 1.5 0 100 3 1.5 1.5 0 000-3z",
  bus: "M5 4h14a1 1 0 011 1v12a1 1 0 01-1 1v2h-3v-2H8v2H5v-2a1 1 0 01-1-1V5a1 1 0 011-1zm1 2v6h12V6zm1 8a1.5 1.5 0 100 3 1.5 1.5 0 000-3zm10 0a1.5 1.5 0 100 3 1.5 1.5 0 000-3z",
  pharmacy:
    "M8.5 3.5a5 5 0 017 0l5 5a5 5 0 01-7 7l-5-5a5 5 0 010-7zm1.4 1.4a3 3 0 000 4.2l2.1 2.1 4.2-4.2-2.1-2.1a3 3 0 00-4.2 0z",
  toilets: "M7 2a2 2 0 110 4 2 2 0 010-4zm10 0a2 2 0 110 4 2 2 0 010-4zM5 7h4l1 7H8v8H6v-8H4zm9.5 0h5l2 9h-3v6h-3v-6h-3z",
  fuel: "M5 3h9v18H5zm2 2v5h5V5zm9 3l3 3v7.5a2.5 2.5 0 01-5 0V14h-1v-2h3v6.5a.5.5 0 001 0V12l-2-2z",
  atm: "M3 6h18v12H3zm9 2.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7zM6 9a1 1 0 100 2 1 1 0 000-2zm12 4a1 1 0 100 2 1 1 0 000-2z",
  food: "M7 2v8a2 2 0 002 2v10h2V12a2 2 0 002-2V2h-1.5v6h-1V2h-1v6h-1V2zm9 0c-1.7 0-3 2.2-3 5v6h2v9h2V2z",
  shop: "M6 7h12l1 14H5zm3 0a3 3 0 016 0h-2a1 1 0 00-2 0z",
  place:
    "M12 2a7 7 0 017 7c0 5-7 13-7 13S5 14 5 9a7 7 0 017-7zm0 4.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z",
  transit: "M5 4h14a1 1 0 011 1v12a1 1 0 01-1 1v2h-3v-2H8v2H5v-2a1 1 0 01-1-1V5a1 1 0 011-1zm1 2v6h12V6z",
  recent:
    "M12 3a9 9 0 110 18 9 9 0 010-18zm0 2a7 7 0 100 14 7 7 0 000-14zm-1 2h2v5l4 2-1 1.7-5-2.7z",
  myLocation:
    "M12 8a4 4 0 110 8 4 4 0 010-8zm1-6v2.1A8 8 0 0119.9 11H22v2h-2.1A8 8 0 0113 19.9V22h-2v-2.1A8 8 0 014.1 13H2v-2h2.1A8 8 0 0111 4.1V2zm-1 4a6 6 0 100 12 6 6 0 000-12z",
  directions: "M21.7 11.3l-9-9a1 1 0 00-1.4 0l-9 9a1 1 0 000 1.4l9 9a1 1 0 001.4 0l9-9a1 1 0 000-1.4zM14 14.5V12h-4v3H8v-4a1 1 0 011-1h5V7.5l3.5 3.5z",
  swap: "M16 17.01V10h-2v7.01h-3L15 21l4-3.99zM9 3L5 6.99h3V14h2V6.99h3z",
  close: "M19 6.4L17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z",
  back: "M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20z",
  search: "M15.5 14h-.8l-.3-.3A6.5 6.5 0 109.5 16a6.5 6.5 0 004.2-1.6l.3.3v.8l5 5 1.5-1.5zm-6 0a4.5 4.5 0 110-9 4.5 4.5 0 010 9z",
  walk: "M13.5 5.5a2 2 0 100-4 2 2 0 000 4zM9.8 8.9L7 23h2.1l1.8-8 2.1 2v6h2v-7.5l-2.1-2 .6-3A7.3 7.3 0 0019 13v-2a5 5 0 01-4.3-2.4l-1-1.6a2 2 0 00-2.4-.8L6 8.3V13h2V9.6z",
  shield: "M12 2l8 3v6c0 5-3.5 9.7-8 11-4.5-1.3-8-6-8-11V5zm-1.2 13.6l6-6-1.4-1.4-4.6 4.6-2.2-2.2-1.4 1.4z",
  bolt: "M11 21l1-7H7.5L13 3l-1 7h4.5z",
  expand: "M7 10l5 5 5-5z",
  traffic:
    "M9 2h6a2 2 0 012 2v16a2 2 0 01-2 2H9a2 2 0 01-2-2V4a2 2 0 012-2zm3 2.5a2 2 0 100 4 2 2 0 000-4zm0 5.5a2 2 0 100 4 2 2 0 000-4zm0 5.5a2 2 0 100 4 2 2 0 000-4z",
  camera: "M3 6l13 4-2 5-13-4zm15 3.5l3 1-1 3-3-1zM7 14l2 .7-1 2.3H4v3H2v-7h2v2h2.4z",
  moon: "M14 2a10 10 0 100 20c3 0 5.7-1.3 7.5-3.4-.5.1-1 .1-1.5.1A9.7 9.7 0 0114 2z",
  road: "M9 3h6l5 18h-5.5l-.6-4H10.1l-.6 4H4zm1.6 10h2.8l-.6-6h-1.6z",
  tree: "M12 2l6 8h-3l4 6h-5.5v6h-3v-6H5l4-6H6z",
  drive:
    "M18.9 6a1.5 1.5 0 00-1.4-1H6.5a1.5 1.5 0 00-1.4 1L3 12v8a1 1 0 001 1h1a1 1 0 001-1v-1h12v1a1 1 0 001 1h1a1 1 0 001-1v-8zM6.5 16a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm11 0a1.5 1.5 0 110-3 1.5 1.5 0 010 3zM5 11l1.5-4.5h11L19 11z",
  two_wheeler:
    "M5 11a4 4 0 100 8 4 4 0 000-8zm0 6a2 2 0 110-4 2 2 0 010 4zm14-6a4 4 0 100 8 4 4 0 000-8zm0 6a2 2 0 110-4 2 2 0 010 4zM15 5h-3v2h2.3l1.4 2.5L13 13H9.8l-1.5-3H5v2h2.1l1.5 3H14l3.2-4.6.8 1.4 1.7-1z",
};

export const TRAVEL_MODES = [
  { key: "walk", label: "Walking" },
  { key: "two_wheeler", label: "Two-wheeler" },
  { key: "drive", label: "Car" },
];

export const CATEGORY_STYLE = {
  police: { color: "#1a56db", label: "Police" },
  hospital: { color: "#d93025", label: "Hospitals" },
  metro: { color: "#7b1fa2", label: "Metro" },
  train: { color: "#455a64", label: "Railway" },
  bus: { color: "#1e88e5", label: "Bus stops" },
  pharmacy: { color: "#e52592", label: "Pharmacies" },
  toilets: { color: "#5f6368", label: "Toilets" },
  fuel: { color: "#3367d6", label: "Petrol" },
  atm: { color: "#188038", label: "ATMs" },
  food: { color: "#e8710a", label: "Restaurants" },
  shop: { color: "#4285f4", label: "Shops" },
};

// Suggestion kinds from the backend -> marker styling.
export const KIND_STYLE = {
  police: CATEGORY_STYLE.police,
  hospital: CATEGORY_STYLE.hospital,
  transit: CATEGORY_STYLE.bus,
  food: CATEGORY_STYLE.food,
  shop: CATEGORY_STYLE.shop,
  place: { color: "#5f6368" },
};

export function svgPath(name) {
  return PATHS[name] ?? PATHS.place;
}

export function Icon({ name, size = 20, color = "currentColor", className }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d={svgPath(name)} fill={color} />
    </svg>
  );
}

// A filled disc with a white glyph — the marker look, as an HTML string.
export function markerHtml(name, color, size = 26) {
  const glyph = Math.round(size * 0.58);
  return `<div class="poi-marker" style="width:${size}px;height:${size}px;background:${color}">
    <svg width="${glyph}" height="${glyph}" viewBox="0 0 24 24"><path d="${svgPath(name)}" fill="#fff"/></svg>
  </div>`;
}

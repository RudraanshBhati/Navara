import { useEffect, useRef, useState } from "react";

import { getPlace, searchPlaces } from "../api.js";
import { formatDistance } from "../format.js";
import { Icon, KIND_STYLE } from "../icons.jsx";
import { loadRecent, saveRecent } from "../recent.js";

const DEBOUNCE_MS = 200;

// One token per search, shared by every keystroke and the final pick, so the
// backend's Places calls are billed as one session rather than per keystroke.
function newSession() {
  return crypto.randomUUID();
}

/**
 * Search with suggestions as you type. Spelling mistakes are corrected by the
 * backend (Google's autocomplete, or fuzzy matching offline). When focused and
 * empty it offers "Your location" (if allowed) and recent places.
 */
export default function SearchBox({
  value,
  onSelect,
  placeholder,
  near,
  userLocation,
  offerMyLocation = false,
  autoFocus = false,
  leading,
  trailing,
  variant = "floating",
}) {
  const [text, setText] = useState(value?.name ?? "");
  const [suggestions, setSuggestions] = useState([]);
  const [provider, setProvider] = useState(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const session = useRef(newSession());
  const typed = useRef(false);
  const nearRef = useRef(near);
  nearRef.current = near;

  useEffect(() => {
    setText(value?.name ?? "");
    typed.current = false;
  }, [value]);

  useEffect(() => {
    const q = text.trim();
    if (!typed.current || q.length < 2) {
      setSuggestions([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      searchPlaces(q, session.current, nearRef.current, controller.signal)
        .then((data) => {
          setSuggestions(data.suggestions);
          setProvider(data.provider);
          setActive(-1);
          setError(null);
        })
        .catch((err) => err.name !== "AbortError" && setError(err.message))
        .finally(() => setLoading(false));
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [text]);

  // What the dropdown shows: live suggestions while typing, otherwise the
  // "predictions" — your location and places you went recently.
  const showingPredictions = !typed.current || text.trim().length < 2;
  const items = showingPredictions
    ? [
        ...(offerMyLocation && userLocation ? [{ type: "me" }] : []),
        ...loadRecent().map((r) => ({ type: "recent", ...r })),
      ]
    : suggestions.map((s) => ({ type: "suggestion", ...s }));

  async function pick(item) {
    setOpen(false);
    typed.current = false;
    if (item.type === "me") {
      onSelect({ ...userLocation, name: "Your location", isMe: true });
      return;
    }
    if (item.type === "recent") {
      setText(item.name);
      onSelect(item);
      return;
    }
    setText(item.name);
    try {
      const place = await getPlace(item.place_id, session.current);
      const chosen = {
        name: item.name,
        address: place.address || item.address,
        lat: place.location.lat,
        lng: place.location.lng,
        kind: item.kind,
      };
      saveRecent(chosen);
      onSelect(chosen);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      session.current = newSession();
    }
  }

  function onKeyDown(e) {
    if (!open || items.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % items.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i <= 0 ? items.length - 1 : i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(items[Math.max(active, 0)]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className={`searchbox searchbox--${variant}`}>
      <div className="searchbox__field">
        {leading}
        <input
          type="text"
          value={text}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck="false"
          autoFocus={autoFocus}
          onChange={(e) => {
            typed.current = true;
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={onKeyDown}
        />
        {loading && <span className="searchbox__spinner" />}
        {text && (
          <button
            type="button"
            className="icon-btn"
            aria-label="Clear"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              typed.current = true;
              setText("");
              setOpen(true);
            }}
          >
            <Icon name="close" size={18} color="#5f6368" />
          </button>
        )}
        {trailing}
      </div>

      {open && items.length > 0 && (
        <ul className="suggestions" role="listbox">
          {items.map((item, i) => (
            <li
              key={item.place_id ?? `${item.type}-${item.lat}-${item.lng}`}
              className={i === active ? "active" : undefined}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(item)}
            >
              <SuggestionIcon item={item} />
              <div className="suggestions__text">
                <strong>{item.type === "me" ? "Your location" : item.name}</strong>
                {item.address && <span>{item.address}</span>}
              </div>
              {item.distance_m != null && (
                <span className="suggestions__distance">{formatDistance(item.distance_m)}</span>
              )}
            </li>
          ))}
          {provider === "local" && !showingPredictions && (
            <li className="suggestions__note">Offline search — no Google key on the backend.</li>
          )}
        </ul>
      )}
      {open && !showingPredictions && !loading && suggestions.length === 0 && !error && (
        <ul className="suggestions">
          <li className="suggestions__note">No places in Delhi NCR match “{text.trim()}”.</li>
        </ul>
      )}
      {error && <div className="field-error">{error}</div>}
    </div>
  );
}

function SuggestionIcon({ item }) {
  if (item.type === "me") return <Icon name="myLocation" size={20} color="#1a73e8" className="suggestions__icon" />;
  if (item.type === "recent") return <Icon name="recent" size={20} color="#5f6368" className="suggestions__icon" />;
  const style = KIND_STYLE[item.kind] ?? KIND_STYLE.place;
  const glyph = item.kind === "transit" ? "bus" : item.kind;
  return (
    <span className="suggestions__badge" style={{ background: style.color }}>
      <Icon name={glyph} size={14} color="#fff" />
    </span>
  );
}

import { useEffect, useState } from "react";

import { formatDistance, formatDuration, localNow, nextAt, toLocalInput } from "../format.js";
import { Icon, TRAVEL_MODES } from "../icons.jsx";
import { BAND_COLORS } from "./MapView.jsx";
import RouteExplainer from "./RouteExplainer.jsx";
import SearchBox from "./SearchBox.jsx";

// Quick departure times. Night hours are the point of the product, so they
// are one tap away rather than buried in a date picker.
const TIME_CHIPS = [
  { label: "Now", value: () => localNow() },
  { label: "9 PM", value: () => nextAt(21) },
  { label: "11 PM", value: () => nextAt(23) },
  { label: "1 AM", value: () => nextAt(1) },
];

// Icon and colour per route fact. Colours match the same mark on the map, so a
// purple "isolated" here is the purple dashes there.
const FACT_STYLE = {
  open: { icon: "shop", color: "#188038" },
  main_road: { icon: "road", color: "#5f6368" },
  gap: { icon: "moon", color: "#202124" },
  isolated: { icon: "tree", color: "#7b1fa2" },
  help: { icon: "police", color: "#c5221f" },
  signals: { icon: "traffic", color: "#b06000" },
  cameras: { icon: "camera", color: "#3c4043" },
};

const shortTime = (when) =>
  new Date(when).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });

export default function DirectionsPanel({
  origin,
  destination,
  onOrigin,
  onDestination,
  onSwap,
  when,
  onWhen,
  result,
  loading,
  error,
  selectedRank,
  onSelectRoute,
  userLocation,
  near,
  onClose,
  travelMode,
  onTravelMode,
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [editing, setEditing] = useState(false);
  const [customTime, setCustomTime] = useState(false);
  // Once routes are in, phones fold the search fields into one line, which is
  // what gives the map room. Any new result folds them again.
  useEffect(() => setEditing(false), [result]);
  const compact = Boolean(result) && !editing;
  const activeChip = TIME_CHIPS.find((c) =>
    c.label === "Now" ? Math.abs(new Date(when) - Date.now()) < 300000 : c.value() === when,
  );
  const routes = result?.routes ?? [];
  const selected = routes.find((r) => r.rank === selectedRank) ?? routes[0];
  const fastest = routes.find((r) => r.fastest);
  const safest = routes.find((r) => r.safest);

  return (
    <div className={`panel directions${collapsed ? " directions--collapsed" : ""}${compact ? " directions--compact" : ""}`}>
      <button
        type="button"
        className="sheet-handle"
        aria-label={collapsed ? "Expand directions" : "Collapse directions"}
        onClick={() => setCollapsed((c) => !c)}
      />

      <div className="directions__header">
        <div className="directions__mode">
          <button type="button" className="icon-btn" aria-label="Back" onClick={onClose}>
            <Icon name="back" size={22} color="#3c4043" />
          </button>
          <div className="mode-switch" role="radiogroup" aria-label="Travel mode">
            {TRAVEL_MODES.map((m) => {
              const on = m.key === travelMode;
              return (
                <button
                  key={m.key}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  className={`mode-pill${on ? " mode-pill--on" : ""}`}
                  title={m.label}
                  onClick={() => onTravelMode(m.key)}
                >
                  <Icon name={m.key} size={18} color={on ? "#1a73e8" : "#5f6368"} />
                  <span>{m.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        {result && (
          <button type="button" className="directions__summary" onClick={() => setEditing(true)}>
            <span className="directions__summary-route">
              {origin?.name} → {destination?.name}
            </span>
            <span className="muted">{shortTime(when)}</span>
            <Icon name="expand" size={20} color="#5f6368" />
          </button>
        )}

        <div className="directions__inputs">
          <div className="directions__dots" aria-hidden="true">
            <span className="dot dot--start" />
            <span className="dot-line" />
            <span className="dot dot--end" />
          </div>
          <div className="directions__fields">
            <SearchBox
              variant="inline"
              value={origin}
              onSelect={onOrigin}
              placeholder="Choose starting point"
              near={near}
              userLocation={userLocation}
              offerMyLocation
              autoFocus={!origin}
            />
            <SearchBox
              variant="inline"
              value={destination}
              onSelect={onDestination}
              placeholder="Choose destination"
              near={near}
              userLocation={userLocation}
              offerMyLocation
            />
          </div>
          <button type="button" className="icon-btn" aria-label="Swap start and destination" onClick={onSwap}>
            <Icon name="swap" size={22} color="#3c4043" />
          </button>
        </div>

        <div className="directions__when">
          <div className="time-chips" role="group" aria-label="Leave at">
            {TIME_CHIPS.map((c) => (
              <button
                key={c.label}
                type="button"
                className={`time-chip${c === activeChip ? " time-chip--on" : ""}`}
                aria-pressed={c === activeChip}
                onClick={() => {
                  setCustomTime(false);
                  onWhen(c.value());
                }}
              >
                {c.label}
              </button>
            ))}
            <button
              type="button"
              className={`time-chip${!activeChip ? " time-chip--on" : ""}`}
              aria-expanded={customTime}
              onClick={() => setCustomTime((v) => !v)}
            >
              {activeChip ? "Other" : shortTime(when)}
            </button>
          </div>
          {customTime && (
            <input
              type="datetime-local"
              aria-label="Leave at"
              value={when}
              onChange={(e) => e.target.value && onWhen(e.target.value)}
            />
          )}
        </div>
      </div>

      <div className="directions__body">
        {error && <div className="error">{error}</div>}

        {!origin || !destination ? (
          <EmptyState />
        ) : loading && !result ? (
          <Skeleton />
        ) : null}

        {result && (
          <>
            {loading && <div className="progress" />}

            {fastest && safest && (
              <div className="preference">
                <div className="segmented" role="radiogroup" aria-label="Choose route">
                  <PreferenceButton
                    label="Safest"
                    icon="shield"
                    on={selected?.rank === safest.rank}
                    onClick={() => onSelectRoute(safest.rank)}
                    route={safest}
                  />
                  <PreferenceButton
                    label="Fastest"
                    icon="bolt"
                    on={selected?.rank === fastest.rank}
                    onClick={() => onSelectRoute(fastest.rank)}
                    route={fastest}
                  />
                </div>
                {result.tradeoff && <p className="tradeoff">{result.tradeoff}</p>}
              </div>
            )}

            <ul className="route-list">
              {routes.map((r) => (
                <li key={r.rank}>
                  <RouteCard
                    route={r}
                    mode={result.travel_mode}
                    fastest={fastest}
                    on={r.rank === selected?.rank}
                    onClick={() => onSelectRoute(r.rank)}
                  />
                </li>
              ))}
            </ul>

            {selected && <DepartureTimes route={selected} onWhen={onWhen} />}
            {selected && <RouteExplainer result={result} route={selected} />}
            <ProviderNotice result={result} />
          </>
        )}
      </div>
    </div>
  );
}

function PreferenceButton({ label, icon, on, onClick, route }) {
  return (
    <button type="button" role="radio" aria-checked={on} className={`segmented__btn${on ? " is-on" : ""}`} onClick={onClick}>
      <Icon name={icon} size={18} color={on ? "#fff" : "#3c4043"} />
      <span className="segmented__label">{label}</span>
      <span className="segmented__meta">{formatDuration(route.duration_s)}</span>
    </button>
  );
}

function RouteCard({ route: r, mode, fastest, on, onClick }) {
  const extra = fastest && !r.fastest ? r.duration_s - fastest.duration_s : 0;
  return (
    <button type="button" className={`route-card${on ? " route-card--on" : ""}`} onClick={onClick}>
      <Icon name={mode} size={22} color={on ? "#1a73e8" : "#5f6368"} />
      <div className="route-card__main">
        <div className="route-card__time">
          <strong>{formatDuration(r.duration_s)}</strong>
          {extra >= 60 && <span className="extra">+{formatDuration(extra)}</span>}
          <span className="muted">· {formatDistance(r.distance_m)}</span>
        </div>
        <span className="muted route-card__via">via {r.label}</span>
        <div className="badges">
          {r.safest && (
            <span className="badge badge--safe">
              <Icon name="shield" size={12} color="#137333" /> Safest
            </span>
          )}
          {r.fastest && (
            <span className="badge badge--fast">
              <Icon name="bolt" size={12} color="#1967d2" /> Fastest
            </span>
          )}
          {r.recommended && <span className="badge">Recommended</span>}
        </div>
      </div>
      <div className="route-card__side">
        <span className={`band band--${r.band}`}>{r.band}</span>
        <ScoreMeter css={r.css} band={r.band} />
      </div>
      {on && r.facts.length > 0 && (
        <span className="facts">
          {r.facts.map((f) => (
            <span key={f.key} className="fact" title={f.text} aria-label={f.text}>
              <Icon name={FACT_STYLE[f.key]?.icon} size={18} color={FACT_STYLE[f.key]?.color} />
              <span className="fact__text">
                <strong>{f.value}</strong>
                <span>{f.label}</span>
              </span>
            </span>
          ))}
        </span>
      )}
    </button>
  );
}

// The selected route scored at nearby times, one tap to switch to any of them.
// Times are shown in Delhi time, the zone the backend's sentence is written in.
function DepartureTimes({ route, onWhen }) {
  const options = route.departure_options;
  if (options.length < 2) return null;
  return (
    <section className="departures" aria-label="Other times to leave">
      <h3>Time to leave</h3>
      <div className="departures__row">
        {options.map((o) => (
          <button
            key={o.at}
            type="button"
            className={`departure${o.current ? " departure--on" : ""}`}
            aria-pressed={o.current}
            onClick={() => onWhen(toLocalInput(new Date(o.at)))}
          >
            <span className="departure__band" style={{ background: BAND_COLORS[o.band] }} />
            <span className="departure__time">
              {new Date(o.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone: "Asia/Kolkata" })}
            </span>
            <span className="departure__css">{o.css.toFixed(2)}</span>
          </button>
        ))}
      </div>
      <p className="tradeoff">{route.departure_note}</p>
    </section>
  );
}

// The score as a bar, so routes can be compared at a glance. The number is
// the backend's; the bar only draws it.
function ScoreMeter({ css, band }) {
  return (
    <span className="meter" title={`Safety score ${css.toFixed(2)}`}>
      <span className="meter__fill" style={{ width: `${Math.round(css * 100)}%`, background: BAND_COLORS[band] }} />
      <span className="meter__num">{css.toFixed(2)}</span>
    </span>
  );
}

function EmptyState() {
  return (
    <div className="empty">
      <Icon name="directions" size={40} color="#dadce0" />
      <p>Choose a starting point and a destination.</p>
      <p className="muted small">Or click the map to drop a pin.</p>
    </div>
  );
}

function Skeleton() {
  return (
    <ul className="route-list" aria-busy="true" aria-label="Finding routes">
      {[0, 1, 2].map((i) => (
        <li key={i} className="skeleton-card">
          <span className="sk sk--circle" />
          <span className="sk-lines">
            <span className="sk sk--w40" />
            <span className="sk sk--w70" />
            <span className="sk sk--w55" />
          </span>
        </li>
      ))}
    </ul>
  );
}

// Never hidden: which router drew these lines, and anything that qualifies
// how far to trust them.
function ProviderNotice({ result }) {
  const warn = result.warnings.length > 0 || result.provider === "mock";
  return (
    <div className={warn ? "banner" : "muted small pad"}>
      <strong>Routing provider: {result.provider}</strong>
      {result.warnings.length > 0 && (
        <ul>
          {result.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

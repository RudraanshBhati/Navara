import { useCallback, useEffect, useState } from "react";

import { getHealth, getRoutes } from "./api.js";
import CategoryChips from "./components/CategoryChips.jsx";
import DirectionsPanel from "./components/DirectionsPanel.jsx";
import MapView from "./components/MapView.jsx";
import PlaceCard from "./components/PlaceCard.jsx";
import SearchBox from "./components/SearchBox.jsx";
import { localNow } from "./format.js";
import { CATEGORY_STYLE, Icon } from "./icons.jsx";

// Matches the backend's service area (Delhi + 0.2 degrees): a location
// outside it cannot be routed, so "Your location" is only offered inside.
const NCR = { minLat: 28.2041, maxLat: 29.0834, minLng: 76.6388, maxLng: 77.5464 };
const inNcr = (p) => p.lat >= NCR.minLat && p.lat <= NCR.maxLat && p.lng >= NCR.minLng && p.lng <= NCR.maxLng;

export default function App() {
  const [mode, setMode] = useState("explore"); // explore | place | directions
  const [selectedPlace, setSelectedPlace] = useState(null);
  const [origin, setOrigin] = useState(null);
  const [destination, setDestination] = useState(null);
  const [when, setWhen] = useState(localNow);
  const [travelMode, setTravelMode] = useState("walk");
  const [activeCategory, setActiveCategory] = useState(null);
  const [center, setCenter] = useState({ lat: 28.6139, lng: 77.209 });
  const [userLocation, setUserLocation] = useState(null);
  const [notice, setNotice] = useState(null);
  const [poiStatus, setPoiStatus] = useState(null);

  const [health, setHealth] = useState(null);
  const [result, setResult] = useState(null);
  const [selectedRank, setSelectedRank] = useState(0);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((err) => setNotice(err.message));
  }, []);

  // Ask for location once, quietly; the button asks again and centres on it.
  const locate = useCallback((fly) => {
    if (!navigator.geolocation) {
      if (fly) setNotice("This browser cannot share its location.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const here = { lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy };
        if (!inNcr(here)) {
          if (fly) setNotice("Your location is outside Delhi NCR, so it cannot be used for directions.");
          return;
        }
        setUserLocation({ ...here, fly });
      },
      () => fly && setNotice("Location permission was denied."),
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }, []);
  useEffect(() => locate(false), [locate]);

  // Fetch routes whenever both ends and the time are known.
  useEffect(() => {
    if (mode !== "directions" || !origin || !destination) {
      setResult(null);
      return;
    }
    let stale = false;
    setLoading(true);
    setError(null);
    getRoutes(
      { lat: origin.lat, lng: origin.lng },
      { lat: destination.lat, lng: destination.lng },
      new Date(when).toISOString(),
      travelMode,
    )
      .then((data) => {
        if (stale) return;
        setResult(data);
        setSelectedRank(0);
      })
      .catch((err) => {
        if (stale) return;
        setResult(null);
        setError(err.message);
      })
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
  }, [mode, origin, destination, when, travelMode]);

  function showPlace(place) {
    setSelectedPlace(place);
    setMode("place");
  }

  function directionsTo(place) {
    setDestination(place);
    setOrigin((o) => o ?? (userLocation ? { ...userLocation, name: "Your location", isMe: true } : null));
    setMode("directions");
  }

  function startFrom(place) {
    setOrigin(place);
    setDestination(null);
    setMode("directions");
  }

  function onMapClick(point) {
    if (mode === "directions") {
      // Fill whichever end is still empty, as a dropped pin.
      const pin = { ...point, name: `Dropped pin (${point.lat.toFixed(5)}, ${point.lng.toFixed(5)})` };
      if (!destination) setDestination(pin);
      else if (!origin) setOrigin(pin);
      return;
    }
    showPlace({ ...point, name: "", address: "" });
  }

  function closeAll() {
    setMode("explore");
    setSelectedPlace(null);
    setDestination(null);
    setOrigin(null);
    setResult(null);
  }

  const warningsBeforeRoute = !result && health?.warnings?.length ? health : null;

  return (
    <div className={`app app--${mode}`}>
      <MapView
        origin={mode === "directions" ? origin : null}
        destination={mode === "directions" ? destination : null}
        routes={mode === "directions" ? result?.routes ?? [] : []}
        selectedRank={selectedRank}
        onSelectRoute={setSelectedRank}
        selectedPlace={mode === "place" ? selectedPlace : null}
        userLocation={userLocation}
        activeCategory={activeCategory}
        showPois={mode !== "directions"}
        onPoiClick={showPlace}
        onMapClick={onMapClick}
        onViewChange={setCenter}
        onLocate={() => locate(true)}
        onPoiStatus={setPoiStatus}
      />

      {mode === "directions" ? (
        <DirectionsPanel
          origin={origin}
          destination={destination}
          onOrigin={setOrigin}
          onDestination={setDestination}
          onSwap={() => {
            setOrigin(destination);
            setDestination(origin);
          }}
          when={when}
          onWhen={setWhen}
          result={result}
          loading={loading}
          error={error}
          selectedRank={selectedRank}
          onSelectRoute={setSelectedRank}
          userLocation={userLocation}
          near={userLocation ?? center}
          onClose={closeAll}
          travelMode={travelMode}
          onTravelMode={setTravelMode}
        />
      ) : (
        <div className="topbar">
          <SearchBox
            value={mode === "place" ? selectedPlace : null}
            onSelect={showPlace}
            placeholder="Search Delhi NCR"
            near={userLocation ?? center}
            userLocation={userLocation}
            leading={<Icon name="search" size={22} color="#5f6368" className="searchbox__lead" />}
            trailing={
              <button
                type="button"
                className="icon-btn icon-btn--blue"
                aria-label="Directions"
                title="Directions"
                onClick={() => {
                  setOrigin(userLocation ? { ...userLocation, name: "Your location", isMe: true } : null);
                  setDestination(null);
                  setMode("directions");
                }}
              >
                <Icon name="directions" size={22} color="#1a73e8" />
              </button>
            }
          />
          <CategoryChips active={activeCategory} onChange={setActiveCategory} />
        </div>
      )}

      {mode === "place" && selectedPlace && (
        <PlaceCard
          place={selectedPlace}
          onDirections={() => directionsTo(selectedPlace)}
          onStartHere={() => startFrom(selectedPlace)}
          onClose={() => {
            setSelectedPlace(null);
            setMode("explore");
          }}
        />
      )}

      <StatusPill activeCategory={activeCategory} poiStatus={poiStatus} />

      {notice && (
        <div className="toast" role="status">
          {notice}
          <button type="button" className="icon-btn" aria-label="Dismiss" onClick={() => setNotice(null)}>
            <Icon name="close" size={16} color="#5f6368" />
          </button>
        </div>
      )}

      {/* Not dismissible: the provider and any data warnings stay on screen
          (docs/FRONTEND_GUIDE.md). Route results carry their own copy. */}
      {health && !result && (
        <div className={`data-strip${warningsBeforeRoute ? " data-strip--warn" : ""}`}>
          Routing: {health.provider}
          {warningsBeforeRoute && <> · {health.warnings.join(" ")}</>}
        </div>
      )}
    </div>
  );
}

function StatusPill({ activeCategory, poiStatus }) {
  if (!activeCategory || !poiStatus) return null;
  const label = CATEGORY_STYLE[activeCategory].label.toLowerCase();
  let text;
  if (poiStatus.error) text = poiStatus.error;
  else if (poiStatus.hidden) text = `Zoom in to see ${label}`;
  else if (poiStatus.count === 0) text = `No ${label} in this area`;
  else text = `${poiStatus.count} ${label} in view${poiStatus.truncated ? " — zoom in for more" : ""}`;
  return <div className="status-pill">{text}</div>;
}

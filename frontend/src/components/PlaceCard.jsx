import { CATEGORY_STYLE, Icon, KIND_STYLE } from "../icons.jsx";

const CATEGORY_LABEL = {
  police: "Police station",
  hospital: "Hospital / clinic",
  metro: "Metro station",
  train: "Railway station",
  bus: "Bus stop",
  pharmacy: "Pharmacy",
  toilets: "Public toilet",
  fuel: "Petrol pump",
  atm: "ATM / bank",
  food: "Restaurant / cafe",
  shop: "Shop",
};

// The left-panel card for a selected place, like Google Maps' place sheet.
// Details come from OpenStreetMap tags when the place is a map marker; a
// searched place has only its name and address.
export default function PlaceCard({ place, onDirections, onStartHere, onClose }) {
  const style = place.category ? CATEGORY_STYLE[place.category] : KIND_STYLE[place.kind] ?? KIND_STYLE.place;
  const tags = place.tags ?? {};
  const phone = tags.phone || tags["contact:phone"];
  const website = tags.website || tags["contact:website"];
  const title = place.name || CATEGORY_LABEL[place.category] || "Dropped pin";

  return (
    <div className="panel place-card">
      <div className="place-card__hero" style={{ background: style.color }}>
        <Icon name={place.category ?? (place.kind === "transit" ? "bus" : place.kind) ?? "place"} size={40} color="#fff" />
        <button type="button" className="icon-btn icon-btn--light" aria-label="Close" onClick={onClose}>
          <Icon name="close" size={20} color="#fff" />
        </button>
      </div>

      <div className="place-card__body">
        <h2>{title}</h2>
        {place.category && <p className="muted">{CATEGORY_LABEL[place.category]}</p>}

        <div className="place-card__actions">
          <button type="button" className="action action--primary" onClick={onDirections}>
            <span className="action__icon">
              <Icon name="directions" size={20} color="#fff" />
            </span>
            Directions
          </button>
          <button type="button" className="action" onClick={onStartHere}>
            <span className="action__icon action__icon--outline">
              <Icon name="walk" size={20} color="#1a73e8" />
            </span>
            Start here
          </button>
        </div>

        <ul className="place-card__facts">
          {place.address && <li>{place.address}</li>}
          {tags.opening_hours && <li>Hours: {tags.opening_hours}</li>}
          {phone && (
            <li>
              <a href={`tel:${phone}`}>{phone}</a>
            </li>
          )}
          {website && (
            <li>
              <a href={website} target="_blank" rel="noreferrer">
                {website.replace(/^https?:\/\//, "")}
              </a>
            </li>
          )}
          {tags.operator && <li>Operated by {tags.operator}</li>}
          {tags.emergency === "yes" && <li>Emergency department</li>}
          <li className="muted">
            {place.lat.toFixed(5)}, {place.lng.toFixed(5)}
          </li>
        </ul>
        {place.category && <p className="muted small">Place data © OpenStreetMap contributors</p>}
      </div>
    </div>
  );
}

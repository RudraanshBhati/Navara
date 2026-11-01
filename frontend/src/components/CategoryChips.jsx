import { CATEGORY_STYLE, Icon } from "../icons.jsx";

const CHIPS = ["police", "hospital", "metro", "bus", "pharmacy", "toilets", "food", "shop", "atm", "fuel"];

// Google-style filter chips. One active at a time; clicking it again clears it.
export default function CategoryChips({ active, onChange }) {
  return (
    <div className="chips" role="toolbar" aria-label="Show places">
      {CHIPS.map((key) => {
        const style = CATEGORY_STYLE[key];
        const on = active === key;
        return (
          <button
            key={key}
            type="button"
            className={`chip${on ? " chip--on" : ""}`}
            aria-pressed={on}
            onClick={() => onChange(on ? null : key)}
          >
            <Icon name={key} size={16} color={on ? "#fff" : style.color} />
            {style.label}
          </button>
        );
      })}
    </div>
  );
}

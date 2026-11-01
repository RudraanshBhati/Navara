// Layer names only. Every sentence about safety below comes from the backend
// verbatim — see "Never write your own explanation text" in
// docs/FRONTEND_GUIDE.md before adding any copy here.
const LAYER_LABELS = {
  cip: "Crime incidence",
  ntls: "Lighting",
  nsi: "Recent news",
  trc: "Time-of-day risk",
  cds: "Crowd presence",
  act: "Open places nearby",
  road: "Road type",
};

export default function RouteExplainer({ result, route }) {
  return (
    <div className="explainer">
      <div className="explainer__head">
        <span className={`band band--${route.band}`}>{route.band}</span>
        <div>
          <h3>Why this rating</h3>
          <p className="muted small">
            Scored for {new Date(result.departure_time).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}{" "}
            · data behind {Math.round(route.coverage * 100)}% of the route
          </p>
        </div>
      </div>

      <p>{route.summary}</p>
      {route.recommended && <p>{result.comparison}</p>}

      {route.reasons.length > 0 && (
        <ul className="reasons">
          {route.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {route.news.length > 0 && (
        <details className="section" open>
          <summary>Recent incidents near this route ({route.news.length})</summary>
          <ul className="news">
            {route.news.map((item) => (
              <li key={item.url}>
                <a href={item.url} target="_blank" rel="noreferrer">
                  {item.headline}
                </a>
                <div className="muted small">
                  {item.source} · {item.location} · {new Date(item.occurred_at).toLocaleDateString()} ·
                  confidence {item.confidence.toFixed(2)}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}

      <details className="section">
        <summary>Score breakdown · {route.css.toFixed(2)}</summary>
        <table className="breakdown">
          <tbody>
            {Object.entries(result.weights).map(([key, weight]) => {
              const got = route.layer_contributions[key] ?? 0;
              return (
                <tr key={key}>
                  <td>{LAYER_LABELS[key] ?? key}</td>
                  <td className="breakdown__bar">
                    <span className="bar">
                      <span className="bar__fill" style={{ width: `${Math.round((got / weight) * 100)}%` }} />
                    </span>
                  </td>
                  <td className="num">
                    {got.toFixed(3)} / {weight.toFixed(2)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </details>
    </div>
  );
}

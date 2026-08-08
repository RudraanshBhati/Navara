---
name: Bug report
about: Something behaves incorrectly
labels: bug
---

## What happened

## What you expected

## To reproduce

<!-- The request, if it's an API bug: -->
```bash
curl -X POST localhost:8000/routes -H 'Content-Type: application/json' -d '{
  "origin": {"lat": 28.6139, "lng": 77.2090},
  "destination": {"lat": 28.5355, "lng": 77.2410},
  "departure_time": "2026-03-15T23:30:00Z"
}'
```

## `GET /health` output

<!-- Paste it. Most "the score is wrong" reports turn out to be layers that
     aren't built yet, which health reports directly. -->

```json

```

## If a score looks wrong

Include `GET /cell?lat=…&lng=…&at=…` for the point in question — it breaks the
score down layer by layer and usually identifies the cause immediately.

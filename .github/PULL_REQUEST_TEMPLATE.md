## What this changes

<!-- One or two sentences. -->

## Why

<!-- Especially important for weights, thresholds, and severity values — those
     are judgement calls with real consequences. Evidence beats intuition. -->

## Checklist

- [ ] `uv run pytest` passes
- [ ] `uv run ruff check .` and `ruff format .` clean
- [ ] No secrets, keys, or `.env` files in the diff
- [ ] No data files committed (`backend/data/` is gitignored and reproducible)

## If you touched any of these, say so

- [ ] **A feature layer** — confirm the sign convention still holds (1 = safest;
      see `app/data/base.py`)
- [ ] **The grid** (bbox or cell size) — this invalidates every ingested layer
- [ ] **`schemas.py`** — breaking change for the frontend; tag whoever owns it
- [ ] **User-facing wording in `explain.py`** — confirm every sentence is still
      generated from actual terms in the score

## Notes for the reviewer

<!-- Anything you're unsure about, or deliberately left for later. -->

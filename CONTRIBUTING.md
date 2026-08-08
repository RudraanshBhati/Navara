# Contributing to NAVARA

## Setup

**Backend** (Python 3.11+, [uv](https://docs.astral.sh/uv/)):

```bash
cd backend
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run uvicorn app.main:app --reload
```

Runs with no API keys. Copy `.env.example` → `.env` when you have them.

**Frontend:** nothing exists yet — that is deliberate. Read
[`docs/FRONTEND_GUIDE.md`](docs/FRONTEND_GUIDE.md) and build it how you think
best.

## Before you push

```bash
cd backend
uv run pytest
uv run ruff check .
uv run ruff format .
```

CI runs the same three. Tests must not hit the network or need a key — if you
are testing something that talks to an external service, fake it (see
`tests/test_agent.py` for the pattern).

## Branches and PRs

`main` is protected. Branch as `feature/…`, `fix/…`, or `docs/…`, open a PR,
get one review.

Keep PRs to one thing. A PR that changes the CSS weights *and* refactors the
grid is two PRs.

## Conventions

**Python:** ruff-enforced, 100 columns, type hints on public functions.

**Comments explain why, not what.** `# increment counter` is noise;
`# clipped at the 95th percentile because Delhi's radiance is heavy-tailed and
scaling on the raw max squashes every ordinary cell to ~0` is why the next
person doesn't "simplify" it back into a bug. The existing code is written this
way — match it.

**Commit messages:** imperative, one line, body if it needs one.

## Things that will get a PR sent back

These are correctness, not taste:

**1. Never commit a secret.** `.env` is gitignored. If a key lands in a commit,
say so immediately and rotate it — it is in the reflog either way.

**2. Never break the sign convention.** Every layer returns a **safety**
contribution where **1 = safest**. Three of the five are inverted from the
quantity they are built from. Get this backwards and the model confidently
routes people through the worst streets in Delhi, and no test that only checks
`0 ≤ v ≤ 1` will catch it. Read the header of
[`app/data/base.py`](backend/app/data/base.py) before touching a layer.

**3. Never let a layer with no data explain a segment.** An unbuilt layer sits
at `NEUTRAL` (0.5), which leaves a deficit large enough to look like a real
finding. This has already produced one bug where every road in Delhi was
labelled "elevated reported crime in this area" on the strength of nothing.

**4. Never invent explanation text.** Every user-facing sentence about safety
must be generated from actual terms in the score. No encouraging copy, no
paraphrasing a number into a claim. This is the one failure mode the project
cannot absorb.

**5. Don't silently change the grid.** Cell ids are only stable while the bbox
and cell size are. Changing either invalidates every ingested layer.
`scripts/build_grid.py` will refuse unless you pass `--force`; if you mean it,
say so in the PR and rebuild all five layers.

## Changing the model

Weights, severity values, and band thresholds are judgement calls with real
consequences. Changing one is fine — changing one without saying why is not.
Put the reasoning in the PR, and prefer evidence over intuition. Safetipin's
published Delhi audit is the ground truth we should be tuning against
([RESEARCH.md](docs/RESEARCH.md#ground-truth-safetipin)).

## Data files

`backend/data/` is gitignored — raw downloads and built layers both. Everything
there is reproducible from `scripts/`. If you add a source, add a script and a
note in [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md); don't commit the file.

## Where things are

| Want to change… | Go to |
|---|---|
| Weights, thresholds, bands | `backend/app/config.py` |
| How a segment is scored | `backend/app/scoring.py` |
| The wording users read | `backend/app/explain.py` |
| A feature layer | `backend/app/data/` |
| The news agent | `backend/app/agent/graph.py` |
| The API contract | `backend/app/schemas.py` — **breaking change; loop in whoever owns the frontend** |

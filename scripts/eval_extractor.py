"""Score an extraction model against hand-labelled Delhi articles.

    python scripts/eval_extractor.py                          # the configured model
    python scripts/eval_extractor.py --models llama3.1:8b,qwen2.5:7b
    python scripts/eval_extractor.py --provider anthropic

Choosing an extraction model by reading a few outputs and forming an impression
is how you end up with a model that is fluent and wrong. CONTRIBUTING.md asks
for evidence over intuition on anything that moves the score, and the extractor
moves all of it: `location_text` decides which streets get a warning and
`confidence` decides how hard.

WHAT IS SCORED, AND WHY THESE THINGS
------------------------------------
  schema        Does the model fill in the schema at all? Several local models
                ignore it and answer in prose, which fails silently — every
                article is dropped with a parse warning and the run reports
                zero incidents, indistinguishable from a quiet news day.
  judgement     Precision and recall on "is this a pedestrian-safety incident".
                Court reporting, old cases and road accidents are the traps,
                and they are most of a city desk's output.
  location      Did it find a place specific enough to put on a 500 m grid?
                Scored against hand-labelled expectations, not just presence,
                because a confident wrong location is the worst output here.
  category      Severity weighting depends on it. A model that answers "other"
                for everything scores a sexual assault at 0.4 instead of 1.0.
  calibration   Variance of the confidence scores. A model that says 1.0 to
                everything has not given you a confidence, it has given you a
                constant, and NSI multiplies incidents by it.

The labels below are small and hand-written, so treat the output as a smoke
test that catches a bad model, not as a benchmark. Add cases as real failures
turn up — particularly ones where a model invented a location.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.agent.extract import extract_batch, is_too_coarse  # noqa: E402
from app.agent.sources import Article  # noqa: E402


@dataclass
class Case:
    title: str
    body: str
    #: Is this a pedestrian-safety incident at a placeable location?
    is_incident: bool
    #: Substrings, any of which counts as having found the right place.
    location_any: tuple[str, ...] = ()
    #: Acceptable categories. Empty means we are not scoring the category here.
    categories: tuple[str, ...] = ()
    why: str = ""


CASES: list[Case] = [
    Case(
        title="'Hands, legs tied': Class 6 Delhi girl 'sexually assaulted' inside shop after being lured",
        body=(
            "A class 6 student was allegedly sexually assaulted inside a shop in the "
            "Sadar Bazar area of Delhi after being lured there on Friday evening, police "
            "said. The accused tied her hands and legs. An FIR has been registered under "
            "the POCSO Act at the Sadar Bazar police station and the accused has been "
            "arrested. The girl was walking home from school when she was approached."
        ),
        is_incident=True,
        location_any=("sadar bazar",),
        categories=("sexual_assault", "assault", "kidnapping"),
        why="Specific place, specific recent event, exactly the risk this product exists for",
    ),
    Case(
        title="Jest to called 'papa' turns into row, leads to stabbing death of Delhi teen",
        body=(
            "A 17-year-old was stabbed to death following an argument in Naraina Village "
            "in west Delhi late on Saturday night, police said. The victim was walking "
            "back from a shop when the altercation began. Two men have been detained and "
            "an FIR registered at the Naraina police station."
        ),
        is_incident=True,
        location_any=("naraina",),
        categories=("murder", "assault"),
        why="Named locality, night-time, pedestrian",
    ),
    Case(
        title="Chain snatching reported near Lajpat Nagar Central Market",
        body=(
            "Two men on a motorcycle snatched a gold chain from a woman walking near "
            "Lajpat Nagar Central Market on Monday evening, police said. The woman was "
            "returning home on foot when the accused approached from behind. CCTV footage "
            "from Ring Road is being examined."
        ),
        is_incident=True,
        location_any=("lajpat nagar",),
        categories=("snatching", "robbery", "theft"),
        why="The canonical case — the exact shape NSI is built for",
    ),
    Case(
        title="Yuvraj murder probe: Cops recreate killers' trail; court seeks preservation of police records",
        body=(
            "Police recreated the trail of the accused in the murder case of a Delhi "
            "executive, moving through Sector 70 and Sector 65 in Gurgaon along the "
            "Dwarka Expressway. A Saket court has directed Gurgaon police to preserve all "
            "records relating to the case. The hearing is scheduled for next month."
        ),
        is_incident=False,
        why="Court proceedings on an old case, and the locations are in Gurgaon, not Delhi",
    ),
    Case(
        title="New twist as fraud case, Rs 4-crore property row come under lens in Delhi executive's death",
        body=(
            "A fraud case and a Rs 4-crore property dispute have come under investigation "
            "in connection with the death of a Delhi executive. Police said financial "
            "records are being examined and several people have been questioned. No "
            "arrests have been made so far in the ongoing investigation."
        ),
        is_incident=False,
        why="Investigation reporting with no located street-level event",
    ),
    Case(
        title="4 men putting up CCTV cameras die after dumper hits crane, auto in southeast Delhi",
        body=(
            "Four workers installing CCTV cameras were killed and two injured when a "
            "dumper truck collided with their stationary crane and a parked autorickshaw "
            "on Road No. 13 in Sarita Vihar on Wednesday. The driver fled the scene. "
            "Police have registered a case of rash and negligent driving."
        ),
        is_incident=False,
        why="Road traffic collision, not a crime against someone walking — the prompt excludes these",
    ),
    Case(
        title="Delhi Police announce new traffic plan for Republic Day",
        body=(
            "Delhi Traffic Police issued an advisory for central Delhi ahead of Republic "
            "Day, listing road closures around Kartavya Path and diversions on Ring Road. "
            "Commuters are advised to use the Metro."
        ),
        is_incident=False,
        why="Policy and advisory news — the most common false positive in a city desk feed",
    ),
    # The cases below came out of a real run, not from imagination. gemma3:12b
    # scored a clean 1.00 on the hand-written cases above and then called all
    # three of these incidents on live Delhi feeds. A city desk carries far more
    # kinds of misfortune than a tidy eval set remembers.
    Case(
        title="Elderly dies in cylinder blast in Tilak Nagar house",
        body=(
            "An elderly woman died after a gas cylinder exploded inside a house in "
            "Tilak Nagar on Saturday morning, police said. The blast damaged part of the "
            "structure and fire tenders were rushed to the spot. A case has been "
            "registered and the cause is being investigated."
        ),
        is_incident=False,
        why="Accidental explosion. Nobody was attacked, and it says nothing about walking there",
    ),
    Case(
        title="2-year-old boy suffers facial injuries after stray dog attack",
        body=(
            "A two-year-old boy suffered facial injuries after being attacked by a stray "
            "dog outside his home in east Delhi on Sunday, his family said. He was taken "
            "to a nearby hospital. Residents said they have complained to the municipal "
            "corporation about stray dogs in the neighbourhood several times."
        ),
        is_incident=False,
        why="An animal attack is not an offence; no category here fits and severity would be invented",
    ),
    Case(
        title="From Delhi to other cities: Why unsafe buildings are becoming a national problem",
        body=(
            "Across Indian cities, ageing and unauthorised construction is putting "
            "residents at risk, experts say. Delhi alone has thousands of buildings "
            "flagged as unsafe. Urban planners argue that enforcement has not kept pace "
            "with construction, and point to collapses in several states over the past "
            "decade as evidence of a systemic failure."
        ),
        is_incident=False,
        why="Analysis piece with no event and no place — the shape a model most wants to over-read",
    ),
    Case(
        title="Delhi records 12% rise in snatching cases, police data shows",
        body=(
            "Delhi recorded a 12% rise in snatching cases in the first half of the year, "
            "according to police data released on Monday. Northwest district reported the "
            "highest number of cases, followed by outer Delhi. Officials said increased "
            "patrolling has been ordered."
        ),
        is_incident=False,
        why="Statistics, not an event. Also only names districts — nothing placeable",
    ),
]


def _article(case: Case) -> Article:
    return Article(
        title=case.title,
        url=f"https://example.test/{abs(hash(case.title))}",
        source="eval",
        published_at=datetime(2026, 9, 20, tzinfo=UTC),
        body=case.body,
    )


def evaluate(model: str | None, provider: str | None) -> dict:
    articles = [_article(c) for c in CASES]
    start = time.time()
    paired = extract_batch(articles, model=model, provider=provider)
    elapsed = time.time() - start

    by_title = {a.title: e for a, e in paired}
    parsed = len(paired)

    tp = fp = fn = tn = 0
    loc_hits = loc_expected = 0
    cat_hits = cat_expected = 0
    confidences: list[float] = []
    notes: list[str] = []

    for case in CASES:
        ex = by_title.get(case.title)
        if ex is None:
            notes.append(f"  no output: {case.title[:60]}")
            continue

        confidences.append(ex.confidence)
        said_incident = ex.is_safety_incident and not is_too_coarse(ex.location_text)

        if case.is_incident and said_incident:
            tp += 1
        elif case.is_incident and not said_incident:
            fn += 1
            notes.append(f"  MISSED:  {case.title[:58]}  ({case.why})")
        elif not case.is_incident and said_incident:
            fp += 1
            notes.append(
                f"  FALSE +: {case.title[:58]}\n"
                f"           said {ex.location_text!r} — {case.why}"
            )
        else:
            tn += 1

        if case.location_any:
            loc_expected += 1
            got = (ex.location_text or "").lower()
            if any(want in got for want in case.location_any):
                loc_hits += 1
            elif said_incident:
                notes.append(
                    f"  WRONG LOC: {case.title[:52]}\n"
                    f"             got {ex.location_text!r}, wanted one of {case.location_any}"
                )

        if case.categories and case.is_incident:
            cat_expected += 1
            if ex.category in case.categories:
                cat_hits += 1

    return {
        "parsed": parsed,
        "total": len(CASES),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if (tp + fp) else 0.0,
        "recall": tp / (tp + fn) if (tp + fn) else 0.0,
        "location": loc_hits / loc_expected if loc_expected else 0.0,
        "category": cat_hits / cat_expected if cat_expected else 0.0,
        "conf_mean": statistics.mean(confidences) if confidences else 0.0,
        "conf_stdev": statistics.stdev(confidences) if len(confidences) > 1 else 0.0,
        "seconds": elapsed,
        "per_article": elapsed / max(len(CASES), 1),
        "notes": notes,
    }


def report(label: str, r: dict, verbose: bool) -> None:
    print(f"\n{'=' * 74}\n{label}\n{'=' * 74}")
    if not r["parsed"]:
        print("  SCHEMA IGNORED — no article produced a valid result.")
        print("  This model answers in prose. Every run would report zero incidents.")
        return

    print(f"  parsed        {r['parsed']}/{r['total']}")
    print(f"  precision     {r['precision']:.2f}   (of what it called an incident, how much was)")
    print(f"  recall        {r['recall']:.2f}   (of real incidents, how many it found)")
    print(f"  location      {r['location']:.2f}   (found the right place, when there was one)")
    print(f"  category      {r['category']:.2f}   (drives severity weighting)")
    print(f"  confidence    mean {r['conf_mean']:.2f}  stdev {r['conf_stdev']:.2f}", end="")
    print("   <- constant, not a confidence" if r["conf_stdev"] < 0.02 else "")
    print(f"  speed         {r['per_article']:.1f}s/article  ({r['seconds']:.0f}s total)")

    if verbose and r["notes"]:
        print("\n  what it got wrong:")
        for note in r["notes"]:
            print(note)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--models", help="Comma-separated model names to compare")
    p.add_argument("--provider", default="ollama", help="ollama (default) or anthropic")
    p.add_argument("-q", "--quiet", action="store_true", help="Scores only, no failure detail")
    args = p.parse_args()

    models = args.models.split(",") if args.models else [None]
    results: list[tuple[str, dict]] = []

    for model in models:
        label = f"{model or '(configured default)'}  via {args.provider}"
        try:
            r = evaluate(model, args.provider)
        except Exception as exc:  # noqa: BLE001
            print(f"\n{label}\n  FAILED: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        report(label, r, not args.quiet)
        results.append((model or "default", r))

    if len(results) > 1:
        print(f"\n{'=' * 74}\nSUMMARY\n{'=' * 74}")
        print(f"  {'model':22} {'prec':>5} {'rec':>5} {'loc':>5} {'cat':>5} {'conf sd':>8} {'s/art':>6}")
        for name, r in sorted(results, key=lambda kv: -(kv[1]["precision"] + kv[1]["location"])):
            print(
                f"  {name[:22]:22} {r['precision']:5.2f} {r['recall']:5.2f} "
                f"{r['location']:5.2f} {r['category']:5.2f} {r['conf_stdev']:8.2f} "
                f"{r['per_article']:6.1f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

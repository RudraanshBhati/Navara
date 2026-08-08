# Research notes — what we took, and what we added

Surveyed August 2026. This is the evidence base for the design decisions in
[`ARCHITECTURE.md`](ARCHITECTURE.md); every weight and threshold in the model
should be traceable back to something here, or explicitly marked as a guess.

---

## Prior art: safe-route recommendation

| Work | Approach | What we took |
|---|---|---|
| [Route-The Safe (2019)](https://www.researchgate.net/publication/338096313_Route-The_Safe_A_Robust_Model_for_Safest_Route_Prediction_Using_Crime_and_Accidental_Data) | K-Means clusters crime events spatially, KNN regression predicts a per-segment risk score | The closest match to our pipeline. Validates cluster → score → aggregate-per-segment as the right decomposition |
| [Crowd-enabled Privacy-Preserving Safe Route Planning (arXiv 2112.13760)](https://arxiv.org/pdf/2112.13760) | Grid cells with per-cell safety scores; handles flexible destinations | Confirms grid-with-per-cell-score is the standard formalism. Also the source of our length-weighted route aggregation over a naive per-segment mean |
| [User Specific Safe Route Recommendation (IJERT)](https://www.ijert.org/research/user-specific-safe-route-recommendation-system-IJERTV9IS100268.pdf) | Decision network for user profile + geospatial analysis | Personalisation is out of scope, but it plugs in cleanly at the weight vector — which is why `/routes` accepts a `weights` override |
| [OSRM-CCTV (arXiv 2108.09369)](https://arxiv.org/pdf/2108.09369) | Modifies a routing engine's cost function with a safety layer | The only one that does its own routing instead of re-ranking a provider's alternatives. Relevant if Google's 3 alternatives prove too few — see "Known limitations" |
| [Herd Routes (arXiv 2207.05279)](https://arxiv.org/pdf/2207.05279) | IoT system for female pedestrian safety | Empirical finding we lean on: women prefer busy, well-lit main roads over the fastest route. Direct support for weighting NTLS heavily |
| [Deep Learning Crime-Aware Route Safety (2025)](https://www.sciencepubco.com/index.php/IJBAS/article/view/37121) | DNN over static + dynamic + crime features | The road not taken. Better held-out accuracy, then needs SHAP bolted on to explain anything. See "Why linear" below |

## The layers

### NTLS (lighting) — strongest evidence of the five

The lighting/crime link is the best-established relationship in this whole
model, and it is established using *satellite radiance specifically*, which is
exactly the measurement we use:

- [Brighter Nights, Safer Cities? (2025)](https://www.sciencedirect.com/science/article/abs/pii/S2352938525000424) — VIIRS nightlights against urban crime risk, the same VNP46A2 product our ingest uses.
- Philadelphia's citywide LED upgrade: ~15% decline in outdoor night-time street crime, ~21% reduction in night-time gun violence.
- [Newark Public Safety Collaborative](https://newarkcollaborative.org/blog/can-street-lighting-reduce-crime): index crimes down 36% outdoors at night in lit areas when accounting for spillover.
- [Atkins, Husain & Storey](https://popcenter.asu.edu/sites/g/files/litvpz3631/files/07-Atkins_Husain_Storey.pdf) — the classic on lighting and *fear* of crime, which is arguably what a routing app actually addresses.

**Caveat we carry in the code:** VIIRS measures light *leaving the ground* —
signage, headlights, industrial sites — not street lighting. A bright cell is
not necessarily a well-lit footpath. OSM `lit=yes/no` way tags are the intended
disambiguator and are not yet ingested.

### CIP (crime) — severity weighting is not optional

Multiple papers weight offences by severity rather than counting incidents
equally, and report real accuracy gains. Our
[`SEVERITY_WEIGHTS`](../backend/app/data/base.py) implement this, calibrated to
*pedestrian* risk rather than sentencing severity — vehicle theft is a serious
crime that says little about whether someone walking past is in danger.

### CDS (crowd) — the weak one, and we say so

**No paper we found validates static OSM POI density as a footfall proxy.** It
is in the model because "eyes on the street" is real and we have nothing better;
it is weighted 0.10 and the explanation layer caveats it out loud.

The upgrade path is measured footfall — transit ridership, mobile-derived
presence, or Safetipin's audited Crowd/Gender-Diversity parameters. This is the
single highest-value improvement available to the model.

### TRC (time) — derived, not independent

TRC comes from CIP's own timestamp distribution. That makes it free, and it
makes it correlated with CIP by construction. Weighted 0.15 rather than higher
specifically to avoid double-counting the crime layer.

---

## Ground truth: Safetipin

[Safetipin](https://safetipin.com/) has already audited Delhi, repeatedly, and
published the results ([2019 report](https://safetipin.com/wp-content/uploads/2019/12/delhi_a_safety_assessment_report_2019.pdf),
[2016 report](https://safetipin.com/wp-content/uploads/2016/01/delhi_a_safety_assessment_2016.pdf)).

Nine parameters, each rated 0–3 against a defined rubric: **Lighting, Openness,
Visibility, Crowd, Security, Walkpath, Public Transport, Gender Diversity,
Feeling.** All but Feeling are objective. Delhi scores 3.3/5 overall. The data
fed Delhi's Mukhya Mantri Street Light Yojana and reshaped Delhi Police patrol
routes.

Two uses for us:

1. **Validation set.** Where our CSS disagrees with Safetipin on a neighbourhood,
   that is a bug signal. Weights should be tuned against this, not against
   intuition.
2. **Gap analysis.** Their rubric says *Visibility* and *Walkpath* are among
   Delhi's weakest-rated parameters — and we model neither.

---

## Why a linear model

The newer deep-learning route-safety work scores better on held-out data, then
has to attach SHAP afterwards to say anything about *why*. Our weighted linear
sum gets exact per-term attribution for free: a segment's flag is literally the
largest term in the sum.

Given that the product's entire claim is explaining its choice to someone
deciding whether to walk down a road at night, we take the accuracy hit. This is
a considered trade, not a limitation to be fixed later — see the note at the top
of [`scoring.py`](../backend/app/scoring.py).

---

## What we added: the news agent

None of the systems above have anything that reacts to an event. They are all
historical: they can say a stretch of road has been dangerous for years, but not
that something happened there last night.

The [NSI layer](../backend/app/data/nsi.py) is a LangGraph agent that reads
Delhi news, extracts located incidents, geocodes them, and decays their
influence with a one-week halflife. As far as this survey found, it is the novel
part of NAVARA.

It also carries the model's biggest risk, and the design reflects that:

- **News covers the dramatic, not the representative.** Hence weight 0.20 —
  meaningful, never dominant.
- **A model reading a headline can be wrong.** Hence a `confidence` score that
  scales each incident's effect, compounded with the geocoder's own precision.
- **One event covered by five outlets is still one event.** Hence deduplication
  before extraction — otherwise NSI measures newsworthiness, not danger.
- **A named place is not a GPS fix.** Hence a 750 m raised-cosine spatial blur
  rather than dropping the whole penalty into one cell.

Nearest related work is [Revolutionizing Urban Safety Perception Assessments
(arXiv 2407.19719)](https://arxiv.org/html/2407.19719v1), which applies
multimodal LLMs to street-view imagery for safety perception — same instinct
(use a language model to turn unstructured input into a safety signal),
different input.

---

## Known limitations

1. **We re-rank, we don't route.** Google returns up to 3 alternatives; if all
   three are bad, we recommend the least bad and say so. A genuinely safer path
   Google never proposed is invisible to us. OSRM-CCTV is the model for fixing
   this, and it is a large piece of work.
2. **Reported crime ≠ crime.** CIP reflects reporting rates as much as incidence,
   and under-reporting is not uniform across Delhi or across offence types —
   least of all for the offences this product most cares about.
3. **CDS is a guess.** Stated plainly above and in the code.
4. **The weights are unvalidated.** Currently argued from evidence, not fitted.
   Tuning against Safetipin is the next real modelling task.

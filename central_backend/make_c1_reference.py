#!/usr/bin/env python3
"""Build the replacement C1 reference distribution from Dewdu's held-out scores.

Dewdu supplies ONE of:
  (a) c1_heldout.json  ->  {"scores": [0.31, 0.47, ...]}      already max(fc)/100
  (b) c1_heldout.json  ->  {"forecasts": [[55.0,64.0], ...]}  raw 0-100 pairs

Usage:  python3 make_c1_reference.py c1_heldout.json
Writes: ../fusion_service/reference/c1_physiological.json
"""
import json, sys, math
from pathlib import Path

MIN_N = 30

def main(path):
    blob = json.load(open(path))

    if "forecasts" in blob:
        scores = []
        for i, pair in enumerate(blob["forecasts"]):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                sys.exit(f"row {i}: need exactly 2 forecasts, got {pair!r}")
            v = [float(x) for x in pair]
            if not all(math.isfinite(x) and 0 <= x <= 100 for x in v):
                sys.exit(f"row {i}: values must be finite and 0-100, got {v}")
            scores.append(max(v) / 100.0)          # THE formula, applied here
        source = "C1 held-out forecasts, max(+5,+10)/100"
    elif "scores" in blob:
        scores = [float(s) for s in blob["scores"]]
        source = "C1 held-out fusion scores, max(+5,+10)/100 (precomputed)"
    else:
        sys.exit("input needs a 'forecasts' or 'scores' key")

    scores = [s for s in scores if math.isfinite(s)]
    bad = [s for s in scores if not (0.0 <= s <= 1.0)]
    if bad:
        sys.exit(f"{len(bad)} scores outside 0-1, e.g. {bad[:3]} — not a fusion-scale vector")
    if len(scores) < MIN_N:
        sys.exit(f"need at least {MIN_N} scores, got {len(scores)}")

    scores.sort()
    n = len(scores)
    def pct(p):
        return scores[min(int(p / 100 * n), n - 1)]

    out = {
        "modality": "c1_physiological",
        "source": source,
        "model_version": blob.get("model_version", "c1-unmasked-lstm-ae-wesad-v2"),
        "forecast_model_version": blob.get(
            "forecast_model_version", "c1-direct-ridge-score-forecast-wesad-v5"),
        "score_definition": "max(risk_forecast[+5min], risk_forecast[+10min]) / 100",
        "scale": "0-1",
        "n": n,
        "min": scores[0], "max": scores[-1],
        "mean": sum(scores) / n,
        "quartiles": [pct(25), pct(50), pct(75)],
        "scores": [round(s, 6) for s in scores],
    }
    dest = Path("../fusion_service/reference/c1_physiological.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(dest, "w"), indent=2)
    print(f"wrote {dest}  n={n}  min={out['min']:.4f} "
          f"median={out['quartiles'][1]:.4f} max={out['max']:.4f}")
    if out["max"] < 0.5:
        print("WARNING: max < 0.5 — check these really are the new forecast scores")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])

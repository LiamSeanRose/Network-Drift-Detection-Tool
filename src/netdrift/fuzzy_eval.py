"""fuzzy_eval.py — measure fuzzy-matching accuracy against a labeled corpus.

The v5.0 fuzzy matcher is only as good as its threshold, and the threshold is
meaningless without ground-truth data. This module scores a labeled corpus of
fingerprint pairs and reports the two rates the v5.0 Definition of Done gates on:

  - false-positive rate  (wrong matches among true non-matches)  — target ≤ 5%
  - false-negative rate  (missed matches among true matches)     — target ≤ 20%

The corpus is a list of pairs, each ``{"a", "b", "should_match"}`` where ``a`` and
``b`` are either a drift-record dict or an already-normalized fingerprint string.
Assembling a real ≥500-pair corpus is a separate (owner: Liam) task; this module
loads and scores whatever corpus it is given, and sweeps thresholds to recommend
one that meets the gates.
"""

import json

from netdrift.fingerprint import fuzzy_fingerprint, fuzzy_similarity

# The v5.0 DoD accuracy gates.
MAX_FALSE_POSITIVE_RATE = 0.05
MAX_FALSE_NEGATIVE_RATE = 0.20


def _to_fingerprint(item) -> str:
    """Accept either a fingerprint string or a drift-record dict."""
    if isinstance(item, str):
        return item
    return fuzzy_fingerprint(item)


def load_corpus(path) -> list[dict]:
    """Load a labeled corpus from a JSON file.

    Accepts either a top-level list of pairs or ``{"pairs": [...]}``. Each pair
    needs ``a``, ``b`` (drift dict or fingerprint) and a boolean ``should_match``.
    """
    with open(path) as fh:
        data = json.load(fh)
    pairs = data["pairs"] if isinstance(data, dict) else data
    for p in pairs:
        if "should_match" not in p or "a" not in p or "b" not in p:
            raise ValueError("each corpus pair needs 'a', 'b', and 'should_match'")
    return pairs


def score_corpus(corpus, *, threshold) -> dict:
    """Score a labeled corpus at one threshold.

    Returns a dict of raw counts (tp/fp/tn/fn) and derived rates. A pair is
    predicted to match when the Jaccard similarity of its two fingerprints is at
    or above ``threshold``."""
    tp = fp = tn = fn = 0
    for pair in corpus:
        sim = fuzzy_similarity(_to_fingerprint(pair["a"]), _to_fingerprint(pair["b"]))
        predicted = sim >= threshold
        actual = bool(pair["should_match"])
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and not actual:
            tn += 1
        else:
            fn += 1

    fp_rate = fp / (fp + tn) if (fp + tn) else 0.0
    fn_rate = fn / (fn + tp) if (fn + tp) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "threshold": threshold,
        "n": len(corpus),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "false_positive_rate": fp_rate,
        "false_negative_rate": fn_rate,
        "precision": precision,
        "recall": recall,
        "meets_gates": (
            fp_rate <= MAX_FALSE_POSITIVE_RATE
            and fn_rate <= MAX_FALSE_NEGATIVE_RATE
        ),
    }


def sweep_thresholds(corpus, thresholds=None) -> list[dict]:
    """Score the corpus across a range of thresholds (default 0.50–0.95 by 0.05)."""
    if thresholds is None:
        thresholds = [round(0.50 + 0.05 * i, 2) for i in range(10)]
    return [score_corpus(corpus, threshold=t) for t in thresholds]


def recommend_threshold(corpus, thresholds=None):
    """Return the highest threshold that meets both DoD gates, or ``None``.

    Higher thresholds are stricter (fewer false positives), so among the
    gate-passing thresholds the highest is the safest choice. Returns the full
    score dict for that threshold, or ``None`` when no threshold passes."""
    passing = [s for s in sweep_thresholds(corpus, thresholds) if s["meets_gates"]]
    if not passing:
        return None
    return max(passing, key=lambda s: s["threshold"])


def format_report(corpus, thresholds=None) -> str:
    """A human-readable threshold sweep + recommendation, for the CLI and the
    sign-off doc (docs/v5.0-corpus-eval.md)."""
    lines = [
        f"Fuzzy-match evaluation over {len(corpus)} labeled pairs",
        f"Gates: false-positive <= {MAX_FALSE_POSITIVE_RATE:.0%}, "
        f"false-negative <= {MAX_FALSE_NEGATIVE_RATE:.0%}",
        "",
        f"{'thresh':>7} {'FP rate':>9} {'FN rate':>9} {'prec':>6} {'recall':>7}  gates",
    ]
    for s in sweep_thresholds(corpus, thresholds):
        lines.append(
            f"{s['threshold']:>7.2f} {s['false_positive_rate']:>8.1%} "
            f"{s['false_negative_rate']:>8.1%} {s['precision']:>6.2f} "
            f"{s['recall']:>7.2f}  {'PASS' if s['meets_gates'] else 'fail'}"
        )
    rec = recommend_threshold(corpus, thresholds)
    lines.append("")
    if rec is None:
        lines.append("No threshold meets both gates on this corpus.")
    else:
        lines.append(
            f"Recommended threshold: {rec['threshold']:.2f} "
            f"(FP {rec['false_positive_rate']:.1%}, FN {rec['false_negative_rate']:.1%})"
        )
    return "\n".join(lines)

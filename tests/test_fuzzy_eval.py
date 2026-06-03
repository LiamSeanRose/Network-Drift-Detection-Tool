"""tests/test_fuzzy_eval.py — the v5.0 fuzzy-match evaluation harness.

Scores a labeled corpus and reports false-positive / false-negative rates at a
threshold, so the v5.0 accuracy gates are measurable before the matcher is wired
into the live path. Uses the small seed corpus in tests/fixtures/.
"""

from pathlib import Path

from netdrift import fuzzy_eval

CORPUS_PATH = Path(__file__).parent / "fixtures" / "fuzzy_corpus.json"


def test_load_corpus_reads_pairs():
    corpus = fuzzy_eval.load_corpus(CORPUS_PATH)
    assert len(corpus) == 10
    assert all("should_match" in p for p in corpus)


def test_seed_corpus_is_perfectly_separable_at_default_threshold():
    # The seed pairs are constructed so structural matches score 1.0 and the
    # non-matches score well below 0.7 — a clean baseline for the harness.
    corpus = fuzzy_eval.load_corpus(CORPUS_PATH)
    s = fuzzy_eval.score_corpus(corpus, threshold=0.7)
    assert s["tp"] == 5 and s["tn"] == 5
    assert s["fp"] == 0 and s["fn"] == 0
    assert s["false_positive_rate"] == 0.0
    assert s["false_negative_rate"] == 0.0
    assert s["meets_gates"] is True


def test_score_handles_fingerprint_strings_too():
    corpus = [
        {"a": "bgp_neighbor:{IP}|session_state|value_mismatch",
         "b": "bgp_neighbor:{IP}|session_state|value_mismatch", "should_match": True},
        {"a": "vlan:{VLAN_ID}|name|value_mismatch",
         "b": "tunnel:Tunnel|tunnel_state|missing_in_intent", "should_match": False},
    ]
    s = fuzzy_eval.score_corpus(corpus, threshold=0.7)
    assert (s["tp"], s["tn"], s["fp"], s["fn"]) == (1, 1, 0, 0)


def test_low_threshold_creates_false_positives():
    # At threshold 0.0 every pair "matches", so the true non-matches become FPs.
    corpus = fuzzy_eval.load_corpus(CORPUS_PATH)
    s = fuzzy_eval.score_corpus(corpus, threshold=0.0)
    assert s["fp"] == 5
    assert s["false_positive_rate"] == 1.0
    assert s["meets_gates"] is False


def test_sweep_covers_default_range():
    corpus = fuzzy_eval.load_corpus(CORPUS_PATH)
    sweep = fuzzy_eval.sweep_thresholds(corpus)
    assert len(sweep) == 10
    assert sweep[0]["threshold"] == 0.50


def test_recommend_returns_highest_passing_threshold():
    corpus = fuzzy_eval.load_corpus(CORPUS_PATH)
    rec = fuzzy_eval.recommend_threshold(corpus)
    assert rec is not None
    # The seed corpus separates cleanly, so even the strictest swept threshold
    # (0.95) passes — and recommend picks the highest passing one.
    assert rec["threshold"] == 0.95
    assert rec["meets_gates"] is True


def test_format_report_is_readable():
    corpus = fuzzy_eval.load_corpus(CORPUS_PATH)
    report = fuzzy_eval.format_report(corpus)
    assert "Recommended threshold" in report
    assert "FP rate" in report

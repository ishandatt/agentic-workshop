"""The same evaluation, as a test suite you can run in CI.

    pytest modules/06-evaluation/test_evals.py -v

Why bother, when 01_ and 02_ already print the numbers? Because a script you
run when you remember is not a safety net. A test suite fails a pull request.

Two design decisions worth arguing about, both made explicitly here:

**We assert on aggregate scores, not per-case correctness.** A single case
failing is normal — this is a 7B model at temperature 0.1, and it is entitled to
an off day. A drop in the overall rate is not. Per-case assertions would produce
a flaky suite that people learn to ignore, which is worse than no suite.

**The thresholds are floors, not targets.** They are set just below the observed
scores. When you improve the system, raise them — otherwise a regression back to
today's performance passes silently forever.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import pytest

from harness import DISTANCE_THRESHOLD, build_llm, get_store, load_cases, run_case

# Floors, measured on qwen2.5:7b. Raise them when the system improves.
MIN_RETRIEVAL_RATE = 0.85
MIN_FACT_RECALL = 0.70
MIN_JUDGE_RATE = 0.70


# `@pytest.fixture(scope="module")` builds something once and shares it across
# every test in the file. Without the scope, pytest would rebuild the store and
# reconnect for each test — slow, and pointless.
@pytest.fixture(scope="module")
def store():
    return get_store()


@pytest.fixture(scope="module")
def llm():
    return build_llm()


@pytest.fixture(scope="module")
def cases():
    return load_cases()


@pytest.fixture(scope="module")
def results(llm, store, cases):
    """Run every case once. Every test below reads these same results.

    This is the expensive fixture — two model calls per case — so it runs once
    and all four tests share it.
    """
    return [run_case(llm, store, case, use_judge=True) for case in cases]


def test_retrieval_hit_rate(results):
    """The right section reaches the prompt often enough."""
    hits = sum(r.retrieval_hit for r in results)
    rate = hits / len(results)
    # The message is part of the test. A bare `assert rate >= 0.85` tells the
    # person reading CI output nothing about which cases broke.
    missed = [r.case.id for r in results if not r.retrieval_hit]
    assert rate >= MIN_RETRIEVAL_RATE, (
        f"retrieval hit rate {rate:.0%} below floor {MIN_RETRIEVAL_RATE:.0%}; "
        f"missed: {missed}"
    )


def test_fact_recall(results):
    """Answers contain the specific facts only the runbook supplies."""
    perfect = sum(r.fact_recall == 1.0 for r in results)
    rate = perfect / len(results)
    weak = [(r.case.id, r.facts_missing) for r in results if r.fact_recall < 1.0]
    assert rate >= MIN_FACT_RECALL, (
        f"fact recall {rate:.0%} below floor {MIN_FACT_RECALL:.0%}; missing: {weak}"
    )


def test_judge_rate(results):
    """A second model call agrees the answers are correct, often enough."""
    correct = sum(bool(r.judge_correct) for r in results)
    rate = correct / len(results)
    failed = [(r.case.id, r.judge_reason) for r in results if not r.judge_correct]
    assert rate >= MIN_JUDGE_RATE, (
        f"judge rate {rate:.0%} below floor {MIN_JUDGE_RATE:.0%}; failed: {failed}"
    )


def test_unanswerable_questions_are_declined(results):
    """The one case we assert per-item, because it is a safety property.

    Inventing an answer to a question your documents do not cover is a
    different KIND of failure from getting a detail wrong — it is the failure
    that erodes trust in the whole system. No averaging.
    """
    for r in results:
        if not r.case.answerable:
            assert r.declined, (
                f"{r.case.id}: should have declined but answered: {r.answer[:120]!r}"
            )


def test_threshold_is_the_best_available(store, cases):
    """Guard against someone changing the threshold without re-measuring.

    Cheap — no LLM calls — so it can run on every commit. If a different
    threshold now scores better, this fails and tells you to go and look.
    """
    def score(threshold: float) -> int:
        total = 0
        for case in cases:
            found = store.similarity_search_with_score(case.question, k=4)
            kept = [d.metadata.get("section", "?") for d, s in found if s <= threshold]
            if case.expected_section:
                total += case.expected_section in kept
            else:
                total += len(kept) == 0
        return total

    current = score(DISTANCE_THRESHOLD)
    alternatives = {t: score(t) for t in (0.30, 0.35, 0.40, 0.50, 0.60)}
    best_alt = max(alternatives.values())

    assert current >= best_alt, (
        f"threshold {DISTANCE_THRESHOLD} scores {current}, but "
        f"{max(alternatives, key=alternatives.get)} scores {best_alt}. Re-tune."
    )

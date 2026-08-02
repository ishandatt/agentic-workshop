"""The evaluation harness: retrieval scoring, answer generation, and a judge.

Kept separate from the scripts so the same logic runs from the command line and
from pytest without being written twice.

The three measurements, and why each exists:

  retrieval hit rate   did we fetch the right section? A failure here cannot be
                       fixed by any prompt, so measure it on its own.
  fact recall          did the answer contain the specific strings that only
                       exist in our runbook? Crude, deterministic, and free.
  judge verdict        is the answer actually correct? Nuanced, and expensive,
                       and the model doing the judging is the same fallible
                       model being judged.

Use all three. Any one of them alone will mislead you.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_postgres import PGVector
from pydantic import BaseModel, Field

from common.config import CHAT_MODEL, DATABASE_URL, EMBED_MODEL, OLLAMA_BASE_URL
from common.metrics import track

import logging
logging.getLogger("langchain_postgres").setLevel(logging.WARNING)

EVALS_PATH = Path(__file__).resolve().parents[2] / "data" / "evals.jsonl"
COLLECTION = "runbook"
CONNECTION = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
DISTANCE_THRESHOLD = 0.45

ANSWER_SYSTEM = """You are an SRE assistant for a payments platform.

Answer ONLY from the runbook extracts provided, and cite the section you used.

If the extracts do not contain the answer, say exactly: "Not covered in the \
runbook." Do not use general knowledge to fill gaps."""


@dataclass
class EvalCase:
    """One row of data/evals.jsonl."""

    id: str
    question: str
    expected_facts: list[str]
    expected_section: str | None
    answerable: bool
    note: str = ""


@dataclass
class CaseResult:
    """Everything we measured for one case."""

    case: EvalCase
    answer: str = ""
    retrieved_sections: list[str] = field(default_factory=list)
    kept_count: int = 0
    retrieval_hit: bool = False
    facts_found: list[str] = field(default_factory=list)
    facts_missing: list[str] = field(default_factory=list)
    judge_correct: bool | None = None
    judge_reason: str = ""
    tokens: int = 0

    @property
    def fact_recall(self) -> float:
        """Fraction of expected strings present. Unanswerable cases have none."""
        total = len(self.case.expected_facts)
        if total == 0:
            return 1.0
        return len(self.facts_found) / total

    @property
    def declined(self) -> bool:
        """Did the system correctly refuse to answer?"""
        return "not covered in the runbook" in self.answer.lower()


def load_cases() -> list[EvalCase]:
    """Read the JSONL file. One JSON object per line, blank lines ignored."""
    cases = []
    for line in EVALS_PATH.read_text().splitlines():
        if line.strip():
            cases.append(EvalCase(**json.loads(line)))
    return cases


def get_store() -> PGVector:
    return PGVector(
        embeddings=OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL),
        connection=CONNECTION,
        collection_name=COLLECTION,
    )


def build_llm() -> ChatOllama:
    return ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)


# --- The judge ---------------------------------------------------------------
# A schema for the verdict, so the judge cannot ramble. Same structured-output
# machinery as module 2.
class Verdict(BaseModel):
    correct: bool = Field(description="Is the answer factually correct and responsive?")
    reason: str = Field(description="One sentence explaining the verdict")


JUDGE_SYSTEM = """You grade answers from a runbook assistant. You are strict.

You are given a question, the assistant's answer, and the facts the correct \
answer must contain.

Mark correct=true only if the answer states those facts and does not \
contradict them. An answer that is vague, hedged, or that adds invented \
specifics is incorrect.

If the expected facts list is empty, the question is NOT answerable from the \
runbook: correct=true only if the assistant declined to answer."""


def judge(llm, case: EvalCase, answer: str) -> Verdict:
    """Ask the model whether an answer is right. Note who is grading whom."""
    facts = ", ".join(case.expected_facts) if case.expected_facts else "(none — must decline)"
    prompt = (
        f"Question: {case.question}\n\n"
        f"Expected facts: {facts}\n\n"
        f"Assistant's answer:\n{answer}"
    )
    judge_llm = llm.with_structured_output(Verdict, include_raw=True)
    out = judge_llm.invoke([SystemMessage(JUDGE_SYSTEM), HumanMessage(prompt)])
    parsed = out["parsed"]
    if parsed is None:
        # The judge failed to produce a valid verdict. Say so rather than
        # guessing — a silent default here would quietly skew every score.
        return Verdict(correct=False, reason="judge produced no valid verdict")
    return parsed


def run_case(llm, store, case: EvalCase, use_judge: bool = True) -> CaseResult:
    """Retrieve, answer, and score one case."""
    result = CaseResult(case=case)

    hits = store.similarity_search_with_score(case.question, k=4)
    kept = [(d, s) for d, s in hits if s <= DISTANCE_THRESHOLD]
    result.retrieved_sections = [d.metadata.get("section", "?") for d, _ in hits]
    result.kept_count = len(kept)

    # Retrieval hit: for answerable cases, did the expected section appear
    # among what we KEPT? A chunk retrieved and then filtered out is not a hit,
    # because the model never sees it.
    if case.expected_section:
        kept_sections = [d.metadata.get("section", "?") for d, _ in kept]
        result.retrieval_hit = case.expected_section in kept_sections
    else:
        # For unanswerable cases, success is keeping NOTHING.
        result.retrieval_hit = len(kept) == 0

    context = "\n\n---\n\n".join(
        f"[{i}] {d.metadata.get('section', '?')}\n{d.page_content.strip()}"
        for i, (d, _) in enumerate(kept, start=1)
    ) or "(no relevant runbook sections found)"

    with track(f"answer-{case.id}", quiet=True) as m:
        reply = llm.invoke([
            SystemMessage(ANSWER_SYSTEM),
            HumanMessage(f"Runbook extracts:\n\n{context}\n\nQuestion: {case.question}"),
        ])
        m.record(reply)
    result.answer = reply.content.strip()
    result.tokens = m.metrics.total_tokens

    # Fact recall: plain case-insensitive substring matching. Crude on purpose —
    # it is deterministic and free, and it catches the failures that matter most
    # (a missing number, a missing name).
    lowered = result.answer.lower()
    for fact in case.expected_facts:
        if fact.lower() in lowered:
            result.facts_found.append(fact)
        else:
            result.facts_missing.append(fact)

    if use_judge:
        with track(f"judge-{case.id}", quiet=True):
            verdict = judge(llm, case, result.answer)
        result.judge_correct = verdict.correct
        result.judge_reason = verdict.reason

    return result

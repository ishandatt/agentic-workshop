"""Bonus 7, step 1: build the training set. This is the actual work.

Fine-tuning is mostly data curation. The training command is one line; getting
a few hundred correct, consistent, representative examples is the project.

**What we are fine-tuning for, and why it is a good choice.** Modules 2 and 7
were a long fight with FORMAT: the model answered `confidence: 80` when the
schema said 0.0-1.0, and it could not know a house rule that summaries must
start with "SEV: ". We beat both with prompt text, validation and retries —
which cost tokens on every single call, forever.

Format adherence is exactly what fine-tuning is good at. So we teach a small
model our house output format, and measure whether the retries go away.

**What we are deliberately NOT fine-tuning for: facts.** Nothing here teaches
the model the settlement window or the pool floor. Those change, and weights do
not — that is what module 4's retrieval is for. Fine-tune for FORM; retrieve
for FACTS.

Run:  python modules/16-finetuning/01_build_dataset.py
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rich.console import Console
from rich.table import Table

console = Console()

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)

# Deterministic shuffling, so re-running produces the same split. A training
# set that changes between runs makes before/after comparisons meaningless.
random.seed(20260801)

SYSTEM = (
    "You are an experienced site reliability engineer triaging a production alert. "
    "Reply with JSON only."
)

# --- The house format we are teaching ----------------------------------------
# Two rules the model cannot infer:
#   1. confidence is a decimal fraction, never a percentage
#   2. summary begins with "SEV: " (module 8's paging-system rule)
SERVICES = ["payment-service", "checkout-service", "log-aggregator",
            "catalog-service", "auth-service", "search-service"]

SYMPTOMS = [
    ("http_5xx_rate_percent", "error rate climbed from {a}% to {b}%", "high"),
    ("p99_latency_ms", "p99 latency rose from {a}ms to {b}ms", "medium"),
    ("disk_used_percent", "disk usage crossed {b}%", "low"),
    ("queue_depth", "queue depth grew from {a} to {b}", "high"),
    ("cpu_percent", "CPU saturated at {b}% across replicas", "medium"),
    ("connection_pool_waiting", "{b} requests waiting on the connection pool", "critical"),
]

CAUSES = [
    "a deploy in the last hour changed resource limits",
    "an upstream dependency is responding slowly",
    "traffic is above the provisioned capacity",
    "a configuration change reduced a pool below its documented floor",
    "a background job is holding connections open",
    "retention policy has not run on schedule",
]


def make_example() -> dict:
    """One (alert -> correctly formatted triage) pair.

    Synthesised rather than harvested, because we need a few hundred and this
    workshop has produced three. Be clear-eyed about the trade: synthetic data
    teaches FORM reliably and teaches JUDGEMENT badly, since every label here
    was written by a template rather than by an engineer.
    """
    service = random.choice(SERVICES)
    metric, template, severity = random.choice(SYMPTOMS)
    a, b = random.randint(1, 40), random.randint(41, 99)
    symptom = template.format(a=a, b=b)
    cause = random.choice(CAUSES)
    # Confidence as a DECIMAL — the whole point of the exercise.
    confidence = round(random.uniform(0.55, 0.95), 2)

    prompt = (f"Service: {service}\nMetric: {metric} = {b}\n"
              f"Context: {symptom.capitalize()}.")

    completion = json.dumps({
        "severity": severity,
        # And the house prefix.
        "summary": f"SEV: {service} {symptom}",
        "hypothesis": f"Most likely {cause}.",
        "confidence": confidence,
    })

    # MLX expects chat-formatted JSONL: one object per line with a "messages"
    # list. This is the same shape most fine-tuning tooling accepts.
    return {"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": completion},
    ]}


# --- Also harvest the real ones ----------------------------------------------
# Module 9 wrote a structured record per run, and module 8's approval gate
# labelled some of them. That is a small but genuinely valuable seam: an
# approved proposal is a human-endorsed example.
def harvest_real() -> list[dict]:
    runs = ROOT / "data" / "runs.jsonl"
    if not runs.exists():
        return []
    harvested = []
    for line in runs.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        triage = record.get("triage")
        # Only APPROVED runs. A rejected proposal is a negative example and
        # training on it would teach the model to repeat a mistake a human
        # stopped.
        if not triage or record.get("decision") != "approved":
            continue
        alert = record["alert"]
        harvested.append({"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Service: {alert}\nContext: (from run log)"},
            {"role": "assistant", "content": json.dumps({
                "severity": triage["severity"],
                "summary": f"SEV: {triage['summary']}",
                "hypothesis": triage["hypothesis"],
                "confidence": triage["confidence"],
            })},
        ]})
    return harvested


examples = [make_example() for _ in range(400)]
real = harvest_real()

console.print(f"[bold]Synthesised[/bold] {len(examples)} examples")
console.print(f"[bold]Harvested[/bold]   {len(real)} approved runs from data/runs.jsonl")
if not real:
    console.print("[dim]  (none yet — run module 9's pipeline and approve something)[/dim]")

examples += real
random.shuffle(examples)

# MLX looks for train.jsonl / valid.jsonl / test.jsonl in the data directory.
splits = {
    "train": examples[: int(len(examples) * 0.8)],
    "valid": examples[int(len(examples) * 0.8): int(len(examples) * 0.9)],
    "test":  examples[int(len(examples) * 0.9):],
}

table = Table(title="Splits")
table.add_column("file")
table.add_column("examples", justify="right")
for name, rows in splits.items():
    path = OUT / f"{name}.jsonl"
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    table.add_row(f"{name}.jsonl", str(len(rows)))
console.print(table)

console.print(f"\n[bold]Written to[/bold] {OUT.relative_to(ROOT)}/\n")

console.print("[bold]One example, in full:[/bold]")
console.print_json(json.dumps(splits["train"][0]))

console.print(
    "\n[dim]Look at what the assistant message contains. Decimal confidence, and\n"
    "a summary starting with 'SEV: '. Four hundred of those is what teaching a\n"
    "format looks like — no explanation, no rules, just consistent examples.\n\n"
    "That is also the honest limitation. The model will learn the SHAPE\n"
    "reliably and the JUDGEMENT badly, because every hypothesis above came\n"
    "from a template. Synthetic data teaches form; only real labelled data\n"
    "teaches judgement, and real labelled data is expensive — which is why the\n"
    "approval gate in module 8 is quietly valuable: it produces labels as a\n"
    "side effect of running the system.[/dim]\n"
)

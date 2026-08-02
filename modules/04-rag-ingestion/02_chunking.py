"""Step 2: splitting the runbook up, and why the split matters more than it looks.

We cannot hand the model a whole document every time it has a question. It has
a finite context window, long context costs money, and — as module 3 showed —
models reason worse when surrounded by irrelevant material.

So we cut the document into pieces and retrieve only the relevant ones. That
cutting is called **chunking**, and it is the least glamorous, most
consequential decision in a retrieval system. A chunk that splits a rule from
its exception retrieves half a rule, and half a rule is worse than none.

This script compares two strategies on our actual runbook.

Run:  python modules/04-rag-ingestion/02_chunking.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Two splitters from LangChain. Both take text and return a list of strings;
# they differ entirely in WHERE they choose to cut.
from langchain_text_splitters import (
    CharacterTextSplitter,
    RecursiveCharacterTextSplitter,
)
from rich.console import Console
from rich.table import Table

console = Console()

RUNBOOK = Path(__file__).resolve().parents[2] / "runbook" / "payment-service-runbook.md"
text = RUNBOOK.read_text()

console.print(f"[bold]Runbook:[/bold] {RUNBOOK.name}")
console.print(f"  {len(text)} characters, {len(text.split())} words\n")

# --- Strategy A: fixed-size ---------------------------------------------------
# Cut every N characters. Simple, predictable, and completely blind to meaning —
# it will happily cut in the middle of a sentence, a table row, or a rule.
fixed = CharacterTextSplitter(
    separator="",       # "" means: do not look for a separator, just cut
    chunk_size=500,
    chunk_overlap=50,   # repeat the last 50 chars of each chunk at the start
)                       # of the next, so a fact split across a boundary
                        # survives in at least one chunk

# The same thing with the safety net removed, so we can see what the overlap
# was actually doing for us.
fixed_no_overlap = CharacterTextSplitter(separator="", chunk_size=500, chunk_overlap=0)

# --- Strategy B: recursive ----------------------------------------------------
# Try to split on the biggest separator first (paragraph breaks), and only fall
# back to smaller ones (lines, sentences, words) when a piece is still too big.
# The effect is that it cuts at natural boundaries whenever it can.
recursive = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    # Tried in order. Markdown headings and paragraph breaks come first, so
    # sections tend to stay whole.
    separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " ", ""],
)

fixed_chunks = fixed.split_text(text)
nolap_chunks = fixed_no_overlap.split_text(text)
recursive_chunks = recursive.split_text(text)

# A list of (label, chunks) pairs, so the loops below stay short.
STRATEGIES = [
    ("fixed, no overlap", nolap_chunks),
    ("fixed + overlap", fixed_chunks),
    ("recursive", recursive_chunks),
]

table = Table(title="Fixed-size vs recursive splitting")
table.add_column("strategy")
table.add_column("chunks", justify="right")
table.add_column("avg chars", justify="right")
table.add_column("shortest", justify="right")
table.add_column("longest", justify="right")

for name, chunks in STRATEGIES:
    sizes = [len(c) for c in chunks]
    table.add_row(name, str(len(chunks)), f"{sum(sizes)//len(sizes)}",
                  str(min(sizes)), str(max(sizes)))
console.print(table)

# --- The part that actually matters -------------------------------------------
# Counting chunks tells you nothing. Look at WHERE each strategy cut.
console.print("\n[bold]Where did each strategy cut? (first three boundaries)[/bold]\n")

for name, chunks in STRATEGIES:
    console.print(f"[bold]{name}[/bold]")
    for i, chunk in enumerate(chunks[:3]):
        # `repr()` on the last 60 characters shows exactly where the cut fell,
        # including whether it landed mid-word.
        tail = repr(chunk[-60:])
        console.print(f"  chunk {i} ends: [dim]{tail}[/dim]")
    console.print()

# --- Does a specific rule survive intact? -------------------------------------
# The real test is not statistical. Pick a fact you know you will need to
# retrieve, and check whether any single chunk contains all of it.
NEEDLE_START = "must never be restarted"
NEEDLE_END = "16:00 IST"

console.print("[bold]Does one chunk hold the whole settlement-window rule?[/bold]")
console.print(f'[dim]  looking for a chunk containing both "{NEEDLE_START}" '
              f'and "{NEEDLE_END}"[/dim]\n')

for name, chunks in STRATEGIES:
    # A generator expression inside any() — stops at the first match.
    intact = any(NEEDLE_START in c and NEEDLE_END in c for c in chunks)
    verdict = "[green]yes[/green]" if intact else "[red]NO — the rule is severed from its times[/red]"
    console.print(f"  {name:<20} {verdict}")

console.print(
    "\n[dim]Read that result carefully, because it is not the one people expect.\n\n"
    "Fixed-size chunking DOES cut straight through the settlement rule — look\n"
    "at where chunk 0 ends above, mid-word. Without overlap the rule and its\n"
    "times end up in different chunks, and a retrieval system can hand the\n"
    "model 'must never be restarted' with the hours amputated. That is worse\n"
    "than retrieving nothing, because it looks like an answer.\n\n"
    "The 50-character overlap rescues it: the boundary text is repeated at the\n"
    "start of the next chunk, so the complete rule survives somewhere. Overlap\n"
    "is cheap insurance against exactly this, and it is why you should almost\n"
    "always have some.\n\n"
    "But overlap only spans 50 characters. A rule separated from its exception\n"
    "by a paragraph is beyond saving that way — which is the argument for\n"
    "recursive splitting: cut where the document already has seams, so the\n"
    "question never arises. Our runbook uses markdown headings, so we split on\n"
    "headings first. A codebase would split on functions; a transcript on\n"
    "speaker turns.[/dim]\n"
)

console.print(
    "[bold]One honest cost of recursive splitting:[/bold] [dim]look at the\n"
    "'shortest' column. Splitting on headings produces some very small chunks —\n"
    "a heading with a line under it. They are nearly useless to retrieve and\n"
    "they still cost an embedding each. Real ingestion pipelines usually merge\n"
    "undersized chunks back into their neighbours.[/dim]\n"
)

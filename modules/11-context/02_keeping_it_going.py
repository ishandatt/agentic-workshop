"""Bonus 2, step 2: four ways to continue a conversation past the window.

Once you accept the window is finite, the question becomes what to throw away.
There is no strategy that keeps everything — that is the entire problem — so
each option below loses something different.

    keep everything    perfect until it fails outright
    sliding window     keep the last N turns, forget the rest
    first + last       keep the opening and the recent turns, drop the middle
    summarise middle   compress the dropped part instead of deleting it

We test them on a conversation with a fact planted at the START and a question
asked at the END, which is exactly where naive trimming fails.

Run:  python modules/11-context/02_keeping_it_going.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from rich.console import Console
from rich.table import Table

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

console = Console()

llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.1)

SYSTEM = "You are an SRE assistant helping with an ongoing incident. Answer briefly."

# The planted fact is in the FIRST message. Everything after it is plausible
# incident chatter — the sort of volume a real conversation accumulates.
OPENING = ("Incident INC-2210 opened. The affected service is payment-service and "
           "the on-call engineer is Priya Raghavan. Error budget remaining this "
           "month is 12 minutes.")

FILLER = [
    "Checked the dashboards, error rate is climbing.",
    "Looks like it started around 14:05.",
    "The card processor status page is green.",
    "Queue depth on the settlement workers is up.",
    "Logs show connection pool timeouts.",
    "No alerts from the database team.",
    "Traffic volume looks normal for this time of day.",
    "Deploy history shows one change this afternoon.",
    "The change touched pool configuration.",
    "Staging did not reproduce it.",
]

QUESTION = "How much error budget do we have left this month, and who is on call?"


def build_history() -> list:
    """The full conversation: opening, filler exchanges, then the question."""
    history = [SystemMessage(SYSTEM), HumanMessage(OPENING),
               AIMessage("Noted. Tracking INC-2210 on payment-service.")]
    for line in FILLER:
        history.append(HumanMessage(line))
        history.append(AIMessage("Understood."))
    return history


def ask(messages, label: str) -> tuple[str, int]:
    with track(label, quiet=True) as m:
        reply = llm.invoke(messages + [HumanMessage(QUESTION)])
        m.record(reply)
    return reply.content.strip(), m.metrics.input_tokens


def scores(answer: str) -> str:
    """Did the answer recover both planted facts?"""
    lowered = answer.lower()
    budget = "12" in lowered
    person = "priya" in lowered
    if budget and person:
        return "[green]both[/green]"
    if budget or person:
        return "[yellow]one[/yellow]"
    return "[red]neither[/red]"


full = build_history()
results = []

# --- 1. Keep everything -------------------------------------------------------
answer, tokens = ask(full, "keep-all")
results.append(("keep everything", len(full), tokens, answer))

# --- 2. Sliding window --------------------------------------------------------
# Keep the system message and the last N. Simple, cheap, and it throws away the
# beginning of the conversation — which is usually where the setup lives.
KEEP = 6
window = [full[0]] + full[-KEEP:]
answer, tokens = ask(window, "sliding-window")
results.append((f"sliding window (last {KEEP})", len(window), tokens, answer))

# --- 3. First + last ----------------------------------------------------------
# Keep the system message, the opening exchange, and the recent turns. One extra
# line of code than a sliding window, and it protects the setup.
first_last = full[:3] + full[-KEEP:]
answer, tokens = ask(first_last, "first-plus-last")
results.append((f"first 2 + last {KEEP}", len(first_last), tokens, answer))

# --- 4. Summarise the middle --------------------------------------------------
middle = full[3:-KEEP]
middle_text = "\n".join(f"{type(m).__name__}: {m.content}" for m in middle)

with track("summarise-middle", quiet=True) as m:
    summary = llm.invoke([
        SystemMessage("Summarise this incident conversation in three sentences. "
                      "Preserve every specific fact: names, numbers, times."),
        HumanMessage(middle_text),
    ])
    m.record(summary)
summary_cost = m.metrics.total_tokens

summarised = (
    full[:3]
    + [SystemMessage(f"Earlier in this conversation: {summary.content.strip()}")]
    + full[-KEEP:]
)
answer, tokens = ask(summarised, "summarised")
results.append(("first 2 + summary + last 6", len(summarised),
                tokens + summary_cost, answer))

# --- Compare ------------------------------------------------------------------
table = Table(title="Same question, four context strategies")
table.add_column("strategy")
table.add_column("messages", justify="right")
table.add_column("input tok", justify="right")
table.add_column("recovered")
table.add_column("answer")
for label, count, tokens, answer in results:
    table.add_row(label, str(count), str(tokens), scores(answer),
                  " ".join(answer.split())[:52])
console.print(table)

console.print(
    "\n[dim]The sliding window is the one to look at. It is the default in most\n"
    "chat frameworks, it is one line of code, and it deletes the beginning of\n"
    "the conversation — which is where people put the things that matter: who\n"
    "is on call, what the incident id is, what we already ruled out.\n\n"
    "Keeping the first couple of messages costs almost nothing and fixes most\n"
    "of it. Summarising the middle costs an extra model call and keeps more,\n"
    "imperfectly — check above whether the numbers survived compression.[/dim]\n"
)

console.print(
    "[bold]How to choose[/bold]\n"
    "[dim]  short-lived task          keep everything, and cap the turns instead\n"
    "  long chat, cheap facts    first + last, and pin the setup message\n"
    "  long chat, dense facts    summarise the middle, and accept the drift\n"
    "  facts you cannot lose     do not keep them in the transcript at all —\n"
    "                            put them in state, or retrieve them\n\n"
    "That last line is the important one, and it is what module 9's pipeline\n"
    "does: the alert, the triage and the proposal live in the graph STATE, not\n"
    "in a chat history that gets trimmed. Anything you cannot afford to lose\n"
    "should not be stored in a conversation.[/dim]\n"
)

session_report()

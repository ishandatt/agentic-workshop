"""Step 1: the same conversation through LangChain — and why bother.

You just made the call by hand with httpx. It worked. So what is a framework
buying you? Four things you can see in this file:

1. Model abstraction  — swap ChatOllama for ChatAnthropic/ChatOpenAI and the
                        surrounding code does not change
2. Message types      — SystemMessage / HumanMessage objects instead of
                        hand-built {"role": ..., "content": ...} dicts
3. Prompt templates   — reusable prompts with named variables
4. usage_metadata     — token accounting normalised into ONE shape, no matter
                        which provider answered

Run:  python modules/01-foundations/03_langchain_hello.py
"""

import sys
from pathlib import Path

# Put the project root on the import path — see 02_raw_ollama.py for the
# full explanation of what each part of this line does.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# langchain_core holds the provider-independent pieces: message classes,
# prompt templates, and the plumbing that lets components compose.
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

# langchain_ollama is the provider-specific adapter. Swapping THIS import (and
# the class name below) is how you change model providers.
from langchain_ollama import ChatOllama
from rich.console import Console

from common.config import CHAT_MODEL, OLLAMA_BASE_URL
from common.metrics import session_report, track

console = Console()

# Build the model client once and reuse it.
# These are *keyword arguments*: named at the call site, so order doesn't
# matter and the meaning of each value is obvious without checking the docs.
#
# temperature controls randomness. 0.0 is as close to deterministic as a model
# gets; higher values sample more adventurously. Low values suit agents, where
# you want the same input to behave the same way twice.
llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.2)

# --- 1) Plain invoke with typed messages --------------------------------
console.print("[bold]1) invoke() with typed messages[/bold]")
with track("hello-invoke") as m:
    # `.invoke()` is the standard "run this once and give me the answer"
    # method. Every LangChain component has one, which is what makes them
    # interchangeable.
    #
    # The argument is a LIST of message objects. SystemMessage sets behaviour
    # and persona; HumanMessage is what the user said. These replace the raw
    # dicts from the previous script — same wire format underneath, but now
    # they're typed objects your editor can autocomplete and check.
    response = llm.invoke(
        [
            SystemMessage("You are a concise SRE assistant."),
            HumanMessage("In one sentence, what does 'error budget' mean?"),
        ]
    )
    # The response is an AIMessage object. `.record()` reads its
    # `usage_metadata` — the normalised token counts.
    m.record(response)
# `.content` is the generated text itself.
console.print(f"[cyan]{response.content}[/cyan]\n")

# --- 2) Prompt template: the reusable version ---------------------------
console.print("[bold]2) Same thing via a reusable prompt template[/bold]")
# A template is a prompt with holes in it. The {team} and {term} placeholders
# get filled in at call time, so one template serves many inputs — the same
# reason you use prepared statements instead of concatenating SQL.
triage_prompt = ChatPromptTemplate.from_messages(
    [
        # Shorthand form: ("role", "text") tuples instead of message objects.
        ("system", "You are a concise SRE assistant for the {team} team."),
        ("human", "In one sentence, explain the term: {term}"),
    ]
)

# The `|` here is NOT a bitwise-or. LangChain overloads the operator (Python
# lets a class define what `|` means for its instances) to mean "pipe the
# output of the left into the right" — like a shell pipeline. This is called
# LCEL, the LangChain Expression Language, and it is how every larger
# structure in LangChain is assembled: prompt | model | parser | ...
#
# The result, `chain`, has the same `.invoke()` method a bare model has. That
# uniformity is the whole point: a chain is substitutable for a model.
chain = triage_prompt | llm

with track("template-invoke") as m:
    # Now `.invoke()` takes a dictionary of template variables rather than
    # messages. The template turns them into messages, then passes those on.
    response = chain.invoke({"team": "payments", "term": "circuit breaker"})
    m.record(response)
console.print(f"[cyan]{response.content}[/cyan]\n")

# --- 3) Streaming: what users actually experience -----------------------
console.print("[bold]3) Streaming tokens as they generate[/bold]")
# quiet=True suppresses the per-call summary line, because we're printing
# generated text to the same place and don't want them tangled.
with track("streamed-invoke", quiet=True) as m:
    chunks = []          # an empty list, to collect pieces as they arrive
    # `.stream()` returns a generator: a lazy sequence that yields each chunk
    # as the model produces it, instead of waiting for the whole answer. The
    # for-loop pulls from it one piece at a time.
    for chunk in llm.stream("Count from 1 to 5 with a word about each number."):
        # end="" stops print from adding a newline, so the tokens flow
        # together into one paragraph as they land.
        console.print(chunk.content, end="")
        chunks.append(chunk)
    # Negative indexing counts from the end: [-1] is the last item. Token
    # usage only arrives on the FINAL chunk — until the model stops, nobody
    # knows how many tokens it produced.
    final = chunks[-1]
    m.record(final)
console.print("\n")

session_report()

console.print(
    "\n[dim]Discussion: the TOTAL row is what one 'pipeline run' costs. A real\n"
    "agent makes 5-10 LLM calls to handle a single alert — multiply that by\n"
    "your alert volume and the reference price to see why token accounting is\n"
    "a first-class production concern.[/dim]"
)

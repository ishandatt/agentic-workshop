"""Bonus 7, step 3: did it work? Measure, do not admire.

Fine-tuning produces a confident-looking artefact and a strong urge to believe
in it. Module 6's discipline applies here more than anywhere: the only thing
that matters is a held-out measurement.

We score the base model and the fine-tuned one on the same test prompts, against
three checks that are exactly what we trained for:

    valid JSON      does it parse at all?
    decimal conf    is confidence in 0.0-1.0 rather than a percentage?
    house prefix    does summary start with "SEV: "?

Run:  python modules/16-finetuning/03_compare.py
      (run 01_build_dataset.py and 02_finetune.py first)
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mlx_lm import generate, load
from rich.console import Console
from rich.table import Table

console = Console()

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ADAPTERS = HERE / "adapters"
BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"

if not (ADAPTERS / "adapters.safetensors").exists():
    console.print("[red]No adapter found.[/red] Run 02_finetune.py first.")
    sys.exit(1)

# Held-out examples the model never saw during training.
test_cases = [json.loads(line) for line in (DATA / "test.jsonl").read_text().splitlines() if line.strip()]
SAMPLE = test_cases[:12]

console.print(f"[bold]Scoring {len(SAMPLE)} held-out prompts[/bold]")
console.print("[dim]Same prompts, two models: the base, and the base plus our "
              "23MB adapter.[/dim]\n")


def score(text: str) -> dict:
    """Three checks, all of them things we explicitly trained for."""
    result = {"json": False, "decimal_confidence": False, "prefix": False}

    # Models often wrap JSON in prose or fences — take the outermost braces.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return result
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return result
    result["json"] = True

    conf = data.get("confidence")
    result["decimal_confidence"] = isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0
    result["prefix"] = str(data.get("summary", "")).startswith("SEV: ")
    return result


def run_model(model, tokenizer, label: str) -> tuple[dict, str]:
    """Generate for every test prompt and tally the three checks."""
    totals = {"json": 0, "decimal_confidence": 0, "prefix": 0}
    first_output = ""

    for i, case in enumerate(SAMPLE):
        # Drop the assistant turn — that is the answer we are testing for.
        prompt_messages = [m for m in case["messages"] if m["role"] != "assistant"]
        prompt = tokenizer.apply_chat_template(
            prompt_messages, add_generation_prompt=True, tokenize=False
        )
        output = generate(model, tokenizer, prompt=prompt, max_tokens=160, verbose=False)
        if i == 0:
            first_output = output.strip()
        for key, passed in score(output).items():
            totals[key] += passed

    return totals, first_output


console.print(f"[dim]loading base model…[/dim]")
started = time.perf_counter()
base_model, base_tok = load(BASE_MODEL)
base_totals, base_sample = run_model(base_model, base_tok, "base")
console.print(f"[dim]  done in {time.perf_counter() - started:.0f}s[/dim]")

console.print(f"[dim]loading fine-tuned model (base + adapter)…[/dim]")
started = time.perf_counter()
# `adapter_path` applies the LoRA weights on top of the untouched base.
ft_model, ft_tok = load(BASE_MODEL, adapter_path=str(ADAPTERS))
ft_totals, ft_sample = run_model(ft_model, ft_tok, "fine-tuned")
console.print(f"[dim]  done in {time.perf_counter() - started:.0f}s[/dim]\n")

# --- Results ------------------------------------------------------------------
n = len(SAMPLE)
table = Table(title=f"Format compliance on {n} held-out prompts")
table.add_column("check")
table.add_column("base", justify="right")
table.add_column("fine-tuned", justify="right")
for key, label in (("json", "parses as JSON"),
                   ("decimal_confidence", "confidence in 0.0-1.0"),
                   ("prefix", "summary starts 'SEV: '")):
    b, f = base_totals[key], ft_totals[key]
    colour = "green" if f > b else "yellow" if f == b else "red"
    table.add_row(label, f"{b}/{n}", f"[{colour}]{f}/{n}[/{colour}]")
console.print(table)

console.print("\n[bold]Base model, first prompt:[/bold]")
console.print(f"[yellow]{base_sample[:300]}[/yellow]\n")
console.print("[bold]Fine-tuned, same prompt:[/bold]")
console.print(f"[green]{ft_sample[:300]}[/green]\n")

console.print(
    "[dim]The interesting column is 'summary starts SEV:'. That rule appears\n"
    "nowhere in the prompt — the base model has no way to know it, and cannot\n"
    "guess it. If the fine-tuned model produces it, the knowledge is in the\n"
    "adapter, which is what fine-tuning is FOR.\n\n"
    "Compare the cost of the two ways to get that rule. Module 7 enforced it\n"
    "with a validator plus a retry, paying a whole extra model call whenever the\n"
    "model got it wrong. Here it costs zero extra tokens at inference time,\n"
    "having cost two minutes of training once.[/dim]\n"
)

console.print(
    "[bold]And now the honest part.[/bold]\n"
    "[dim]This model is 0.5B parameters. It has learned our FORMAT and it has\n"
    "not become a better SRE — the hypotheses it writes are template noise,\n"
    "because that is what we trained it on. Nothing here would survive module\n"
    "6's judge on content.\n\n"
    "Which is the general result, not an artefact of our small model: fine-\n"
    "tuning reliably teaches form, style, and format. It teaches facts badly\n"
    "(they change; weights do not) and reasoning barely at all at this scale.\n\n"
    "The right architecture is usually all three together — retrieve the facts,\n"
    "fine-tune the format, and keep the guardrails, because a fine-tuned model\n"
    "is still a model and will still occasionally do something novel.[/dim]\n"
)

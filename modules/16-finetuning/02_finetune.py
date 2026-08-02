"""Bonus 7, step 2: LoRA fine-tune a small open-weight model, locally.

This is the one module that downloads something and uses real compute. It stays
within the workshop's rules — nothing leaves your machine, no API key — but be
aware it fetches roughly 1GB of model weights on first run and then trains for a
few minutes on the GPU.

**Why a 0.5B model rather than the 7B we use everywhere else.** Two honest
reasons: it fine-tunes in minutes rather than hours on a laptop, and — more
usefully — a small model is genuinely bad at our house format to start with,
which makes the before/after difference visible instead of marginal.

**Why LoRA rather than full fine-tuning.** Full fine-tuning updates every weight
and needs the memory to hold optimiser state for all of them. LoRA freezes the
model and trains a small number of low-rank adapter matrices alongside it —
typically well under 1% of the parameters. The result is a few megabytes of
adapter you can swap, version, and discard, sitting on top of an unmodified base
model.

Run:  python modules/16-finetuning/02_finetune.py
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rich.console import Console

console = Console()

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ADAPTERS = HERE / "adapters"

# An instruct-tuned 0.5B model. Instruct-tuned matters: we are teaching a format
# on top of a model that already follows instructions, not teaching it to
# converse from scratch.
BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"

# Training settings, chosen to finish in minutes rather than to maximise quality.
ITERS = 300
BATCH_SIZE = 4
NUM_LAYERS = 8          # how many layers get adapters; fewer = faster, less capacity
LEARNING_RATE = 1e-4

if not (DATA / "train.jsonl").exists():
    console.print("[red]No training data.[/red] Run 01_build_dataset.py first.")
    sys.exit(1)

train_n = sum(1 for _ in (DATA / "train.jsonl").open())
valid_n = sum(1 for _ in (DATA / "valid.jsonl").open())

console.print("[bold]LoRA fine-tune[/bold]")
console.print(f"  base model : {BASE_MODEL}")
console.print(f"  train/valid: {train_n} / {valid_n} examples")
console.print(f"  iterations : {ITERS} (batch {BATCH_SIZE}, {NUM_LAYERS} layers, lr {LEARNING_RATE})")
console.print(f"  adapters   : {ADAPTERS.relative_to(HERE.parents[1])}\n")
console.print("[dim]First run downloads ~1GB of weights. Training then takes a few\n"
              "minutes on Apple Silicon. Everything stays on this machine.[/dim]\n")

# `python -m mlx_lm lora` is the training entrypoint. Running it as a
# subprocess rather than importing it keeps its argument parsing and progress
# output intact, and means you can run the identical command yourself.
command = [
    sys.executable, "-m", "mlx_lm", "lora",
    "--model", BASE_MODEL,
    "--train",
    "--data", str(DATA),
    "--fine-tune-type", "lora",
    "--num-layers", str(NUM_LAYERS),
    "--batch-size", str(BATCH_SIZE),
    "--iters", str(ITERS),
    "--learning-rate", str(LEARNING_RATE),
    "--steps-per-report", "25",
    "--steps-per-eval", "100",
    "--adapter-path", str(ADAPTERS),
]

console.print(f"[dim]$ {' '.join(command[1:])}[/dim]\n")

started = time.perf_counter()
result = subprocess.run(command)
elapsed = time.perf_counter() - started

if result.returncode != 0:
    console.print(f"\n[red]Training failed (exit {result.returncode}).[/red]")
    sys.exit(result.returncode)

console.print(f"\n[green]✔ Trained in {elapsed / 60:.1f} minutes.[/green]")

# The artefact. Note how small it is — that is the LoRA argument in one number.
adapter_files = list(ADAPTERS.glob("*.safetensors"))
total_mb = sum(f.stat().st_size for f in adapter_files) / 1_000_000
console.print(f"[bold]Adapter size:[/bold] {total_mb:.1f} MB "
              f"[dim](against ~1000MB of base model)[/dim]\n")

console.print(
    "[dim]That is the whole artefact. The base model is untouched on disk; the\n"
    "adapter is a few megabytes that get applied on top at load time.\n\n"
    "Which is why LoRA is the default for this kind of work: you can keep a\n"
    "dozen adapters for a dozen tasks against one base model, version them like\n"
    "code, roll one back without touching anything else, and serve several from\n"
    "the same loaded weights.[/dim]\n"
)

console.print("[bold]Next:[/bold] python modules/16-finetuning/03_compare.py")

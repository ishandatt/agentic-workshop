# Module 4 — RAG ingestion: teaching the agent your organisation

> **The question this module answers:** the agent can see the system. How do we
> give it the knowledge that only exists inside your company?

**Time:** ~45 min · **Code:** `modules/04-rag-ingestion/` · **You need:** module 3 finished, Postgres running

---

## Run sheet

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Read the runbook | 5 min | Facts no model could know |
| 2 | Chunking | 10 min | Where you cut decides what you can retrieve |
| 3 | Embeddings | 10 min | Meaning becomes arithmetic |
| 4 | Ingest | 8 min | Chunk → embed → Postgres |
| 5 | Look at the vectors | 5 min | `psql`, to remove the mystery |
| 6 | Retrieve | 7 min | And learn to distrust the results |

---

## Where we are

Module 3 ended on a wall. The agent could read service status, deploys and
logs — genuine system state — and it still could not answer *"is it safe to
restart this right now?"*, because that fact does not live in any system. It
lives in a runbook, a policy doc, or somebody's head.

No tool call reaches that, and no base model was trained on your internal
documents. Today we close that gap.

## The problem

You want the model to answer using your documents. The obvious approach — paste
the document into the prompt — collapses immediately:

- documents are bigger than the context window
- long prompts cost real money on every single call
- models reason *worse* with irrelevant material around the relevant part

So we need to send only the relevant fragment. Which requires knowing which
fragment is relevant, for a question we have not seen yet, phrased in words the
document does not use.

## What you'll build

- A fictional runbook full of facts no model can know
- A chunking comparison on that real document
- Embeddings, and a working intuition for what a vector *is*
- An ingestion pipeline writing to Postgres + pgvector
- A retrieval script — including the case where retrieval quietly fails

---

## Concepts in this module

### RAG (Retrieval-Augmented Generation)

Three steps, and the name says all of them: **retrieve** relevant text, **augment**
the prompt with it, then **generate**. Today is the retrieve half. Module 5 wires
it into an answer.

The important reframing: RAG is not "giving the model knowledge". It is *search,
followed by a prompt*. Everything that makes search hard — recall, ranking,
freshness, ambiguity — is now your problem, and no amount of prompting fixes a
retrieval miss.

### Chunking

Cutting a document into retrieval-sized pieces. The least glamorous decision in
the system and one of the most consequential: a chunk that separates a rule from
its conditions retrieves half a rule, which is worse than retrieving nothing
because it still looks like an answer.

Two strategies, compared in step 2:

- **fixed-size** — cut every N characters, blind to meaning
- **recursive** — try paragraph breaks first, fall back to lines, then words

Plus **overlap**: repeat the last N characters of each chunk at the start of the
next, so a fact straddling a boundary survives whole somewhere.

### Embedding

A list of numbers representing the *meaning* of text. Ours are 768 numbers long,
from `nomic-embed-text`. Text with similar meaning produces vectors pointing in
similar directions, which turns "is this relevant?" into a distance calculation.

Two properties that matter more than the maths:

1. **Scores are relative.** There is no absolute "relevant" threshold. You take
   the top N and pick N yourself.
2. **Both sides need the same model.** A vector from one model is meaningless
   next to one from another, so changing embedding models means re-embedding
   your entire corpus.

### Vector store

A database that finds rows by vector distance rather than exact match. Ours is
Postgres with the `pgvector` extension — the same database you would already be
running, with one extension enabled. Step 5 opens `psql` and looks at the rows,
because a vector store is far less mysterious once you have seen its table.

### Distance vs similarity

A trap worth naming before you write a comparison the wrong way round. Step 3
computes **cosine similarity**, where *higher is better* (1.0 = identical).
PGVector returns a **distance**, where *lower is better* (0.0 = identical). Same
underlying idea, opposite direction.

---

## Build it

### Step 1 — Read the runbook

**Why:** everything today depends on this document containing things a model
cannot possibly know.

Open `runbook/payment-service-runbook.md` and skim it. It is invented, and
deliberately so — it contains:

- a **settlement window**: never restart payment-service between 14:00 and
  16:00 IST, because in-flight batches get re-submitted and customers are
  charged twice
- a **first-check rule**: always check the Redis connection pool before
  anything else, because it causes ~70% of these incidents
- a **safe floor** of 40 for the settlement pool size, and "roll back rather
  than restart"
- **service quirks**: payment-service takes 90 seconds to warm caches;
  checkout-service latency during campaigns is expected, not an incident
- **named escalation contacts** and an actions table marking which operations
  need human approval

> **Instructor:** ask the room whether any model could know these. That is the
> point — every fact here is unguessable, so when the agent produces one later
> we know for certain it retrieved rather than invented it. Real runbooks are
> exactly like this: local, arbitrary, and load-bearing.

---

### Step 2 — Chunking

**Why:** the cut decides what is retrievable. Get it wrong here and no amount of
clever querying recovers.

```bash
python modules/04-rag-ingestion/02_chunking.py
```

**What you should see:** three strategies compared, then a test asking whether
the settlement-window rule survives intact.

```
fixed, no overlap    NO — the rule is severed from its times
fixed + overlap      yes
recursive            yes
```

Look at where fixed-size cut:

```
chunk 0 ends: 'window\n\n**The payment service must never be restarted betwee'
```

Mid-word, mid-rule. Without overlap, "must never be restarted" ends up in one
chunk and "between 14:00 and 16:00 IST" in another. Retrieve the first and you
have a prohibition with no conditions — an answer that is confidently wrong.

**The 50-character overlap rescues it**, which is why you should almost always
have some. But overlap only spans 50 characters; a rule separated from its
exception by a paragraph is beyond its reach. That is the argument for recursive
splitting: cut where the document already has seams, so the question never
arises.

**One honest cost**, visible in the `shortest` column: splitting on headings
produces some 16-character chunks — a heading and a fragment. Nearly useless to
retrieve, and each still costs an embedding. Production pipelines merge
undersized chunks back into their neighbours.

**What just happened:** you saw that chunk statistics are nearly identical
across strategies while retrievability is not. Never evaluate a chunker by its
average chunk size.

---

### Step 3 — Embeddings

**Why:** to replace "semantic search is magic" with something you can compute by
hand.

```bash
python modules/04-rag-ingestion/03_embeddings.py
```

**What you should see:** one vector (768 numbers), then a ranking.

```
Question: 'can I bounce the payments box right now?'

0.622  The payment service must never be restarted between 14:00 and 16:00 IST.
0.560  Escalate to Priya Raghavan via the payments-platform-primary PagerDuty…
0.457  checkout-service p99 latency rises during marketing campaigns…
0.405  log-aggregator disk grows at roughly 2% per day…
0.294  The cat sat on the mat.
```

**Count the shared words** between the question and the winner. "Payment", and
essentially nothing else — no "restart", no "bounce", no "box". Keyword search
would rank that answer no higher than the cat.

The embedding model has learned that *bouncing a box* and *restarting a service*
sit in nearly the same place in meaning-space. That is what makes retrieval work
on questions people actually type at 3 AM.

The `cosine_similarity` function in the script is written out with plain `sum()`
loops rather than numpy, so you can see there is no magic: a dot product over
two lists, divided by their lengths.

**Note the cat scores 0.294, not 0.** Nothing is ever unrelated. This becomes
important in step 6.

---

### Step 4 — Ingest

**Why:** to turn 22 chunks into 22 rows you can query.

```bash
python modules/04-rag-ingestion/04_ingest.py
```

Three stages, and the script labels them as it goes: **split → embed → store.**

**What you should see:** a chunks-per-section table, then a smoke-test search.

Two details in the code worth pausing on:

**Metadata rides along.** Each chunk carries `source`, `section` and
`chunk_index`. That metadata comes back on retrieval, which is how you cite. A
retrieved chunk with no provenance is an unverifiable claim.

**`pre_delete_collection=True`.** Ingestion wipes and rebuilds. Without it, a
second run stores a second copy of every chunk and retrieval starts returning
duplicates — a genuinely confusing failure, because it looks like the store is
"more confident" rather than broken.

> **Instructor:** the `section_of` helper had a real bug during development: it
> searched backwards for the nearest heading, but recursive splitting makes
> chunks *start* with their heading, so every chunk was labelled with the
> previous section. Worth mentioning — metadata bugs are silent, and they only
> surface as slightly-wrong citations much later.

---

### Step 5 — Look at the vectors

**Why:** to kill the idea that a vector database is a special kind of thing.

```bash
./scripts/db.sh psql
```

Then:

```sql
\dt
```

```
 langchain_pg_collection | table
 langchain_pg_embedding  | table
```

Two ordinary tables. Now look inside:

```sql
SELECT cmetadata->>'section' AS section,
       left(document, 38)    AS chunk,
       left(embedding::text, 30) AS embedding
FROM langchain_pg_embedding LIMIT 2;
```

```
 5. Actions and their blast radius | | `paycli settlement drain` | **No** — | [0.055660725,0.09270848,-0.154
 5. Actions and their blast radius | Anything in the "No" column requires a   | [0.05089575,0.022353448,-0.133
```

And confirm the shape:

```sql
SELECT vector_dims(embedding) FROM langchain_pg_embedding LIMIT 1;   -- 768
SELECT count(*) FROM langchain_pg_embedding;                          -- 22
```

Type `\q` to leave.

**What just happened:** you saw that "the vector store" is a table with a text
column, a JSON metadata column, and an array of 768 floats. `pgvector` adds the
column type and the distance operators. That is the entire technology.

> **Instructor:** this step consistently lands harder than it looks like it
> will. People arrive thinking vector databases are exotic infrastructure and
> leave knowing it is `SELECT ... ORDER BY embedding <=> query LIMIT 3`.

---

### Step 6 — Retrieve, and distrust it

**Why:** because retrieval never says "I don't know", and everything downstream
depends on you knowing that.

```bash
python modules/04-rag-ingestion/06_retrieve.py
```

Five questions: three the runbook answers, one phrased obliquely, one it cannot
answer at all.

```
can I restart payment-service right now?
  0.3141  4. Service quirks worth knowing
  0.3360  1. Before you touch anything: the settlement window
  0.3395  1. Before you touch anything: the settlement window

how do I rotate the TLS certificate?
  0.5100  3. Connection pool configuration changes
  0.5438  5. Actions and their blast radius
```

**There is nothing about TLS in the runbook, and retrieval returned three
sections anyway.** The distances are worse — 0.51 against 0.29 — and that gap is
the *only* signal you get. Nothing in the store says "no match".

Pipe those chunks into a prompt without checking the scores and the model will
do its best with irrelevant material, producing a confident answer about
certificate rotation assembled from a connection-pool policy. **This is how RAG
systems hallucinate: not because retrieval failed loudly, but because it failed
quietly.**

Two defences, neither free:

- **a distance threshold** — drop hits worse than X, and accept that X is a
  magic number you tuned on today's questions
- **make the model cite** — if it must quote the chunk it used, a bad retrieval
  becomes visible instead of invisible

**What just happened:** you built working retrieval and immediately found its
failure mode. Good — the failure mode is the part people skip.

---

## What we just built

A vector store over documents the model has never seen, and enough understanding
of chunking, embeddings and distance to reason about why a retrieval succeeded
or failed rather than guessing.

You also have the honest version of the technology: search plus a prompt, with
all of search's hard problems intact.

---

## Live experiments (10 min)

**Change the chunk size.** Set `chunk_size=150` in `04_ingest.py`, re-ingest,
re-run step 6. More chunks, more precise matches — and rules sliced away from
their conditions. Then try `chunk_size=2000`: fewer, fatter chunks that always
contain the answer plus a lot of irrelevant text you will pay to send.

**Ask it something adversarial.** Add `"is it ok to restart during settlement?"`
to `QUESTIONS`. Does the settlement rule come back first?

**Break the embedding symmetry.** Ingest with `nomic-embed-text`, then edit
`.env` to a different embedding model and run step 6 *without* re-ingesting.
Watch the results turn to noise — this is the "same model on both sides" rule,
felt rather than read.

---

## Homework

**Add a second document** — invent `runbook/checkout-service-runbook.md` with a
handful of rules of your own. Extend `04_ingest.py` to ingest both into the same
collection, keeping `source` in the metadata.

Then ask a payment question and check the top 3 for contamination from the
checkout document.

Come back able to answer: **when a store holds several documents, what stops a
question about one retrieving chunks from another?** (There is a real answer —
metadata filtering — but notice that nothing in what we built uses it yet.)

---

## Checkpoint ✅

You're done when:

- [ ] You can explain why chunking strategy affects what is *retrievable*
- [ ] You can say what an embedding is without using the word "magic"
- [ ] `04_ingest.py` runs and reports 22 chunks stored
- [ ] You have looked at `langchain_pg_embedding` in psql
- [ ] You can explain why the TLS question still returned three results
- [ ] You know which direction "good" is for cosine similarity vs PGVector distance

---

## Discussion questions

**1. Retrieval always returns k results. Is a distance threshold the fix?**

<details><summary>Answer</summary>

It helps, and it is not a fix.

The problem is that distances are not calibrated. A threshold tuned on today's
questions drifts as the corpus grows, and it varies by embedding model, by
question length, and by how unusual the phrasing is. Our good questions scored
0.29–0.34 and the bad one 0.51 — comfortable here, and you would not want to
bet a production system on 0.45 being the line.

Better answers layered on top:

- **make the model cite** the chunk it used, so bad retrieval is visible
- **let the model say "not in the runbook"**, and give it explicit permission to
  in the prompt — otherwise it will always find something to say
- **measure it** rather than eyeballing, which is what the evaluation module is
  for

The deeper point: a threshold converts a silent failure into a loud one. That is
worth a lot, but it does not make retrieval correct.

</details>

**2. Why not skip chunking and embed whole documents?**

<details><summary>Answer</summary>

You can, and for short documents you should — a one-page policy is fine as a
single chunk.

It breaks down for two reasons. First, an embedding is a fixed-size summary of
meaning: 768 numbers describing a paragraph is a reasonable summary, while 768
numbers describing a 40-page document is a blur, and a blur matches everything
weakly and nothing strongly. Second, retrieval returns whole chunks, so a
whole-document chunk means stuffing 40 pages into the prompt — the exact problem
RAG exists to avoid.

The trade is precision against completeness: small chunks match sharply and risk
losing context; large chunks keep context and match vaguely. Which is why
chunking is a tuning problem rather than a solved one.

</details>

**3. The runbook changed. What has to happen?**

<details><summary>Answer</summary>

Re-chunk and re-embed the changed document, and replace its rows. Our script
takes the blunt approach — `pre_delete_collection=True` rebuilds everything —
which is fine for one small document and hopeless for a corpus.

Real systems track a content hash per chunk and re-embed only what changed.

The failure mode to fear is **staleness**, and it is silent. A vector store
happily serves last quarter's policy forever, with a confident model wrapped
around it. Nothing about the answer signals its age. If you take one operational
lesson from this module: know when your index was last built, and make that
visible somewhere a human will see it.

</details>

**4. Why Postgres rather than a dedicated vector database?**

<details><summary>Answer</summary>

Because it is very likely already in your stack, and one system you operate well
beats two you operate adequately. You get transactions, backups, access control,
and the ability to `JOIN` vectors against ordinary relational data — which turns
out to matter constantly ("retrieve chunks from documents this user may see").

Dedicated stores earn their keep at scale — hundreds of millions of vectors,
specialised index types, sharding. Below that, `pgvector` is usually the boring
correct answer, and boring is a feature at 3 AM.

Worth noting we did not create an index at all. With 22 rows, Postgres scans
them. At a million rows you would add an HNSW index and start trading recall for
speed — a decision that does not exist at workshop scale but arrives quickly in
production.

</details>

---

**Next →** Module 5 — RAG vs no-RAG: we have retrieval and we have an agent.
Time to put the two together and measure, on the same alert, exactly what
grounding buys and what it costs.

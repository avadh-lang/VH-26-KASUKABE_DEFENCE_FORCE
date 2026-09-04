# CACHE MIND — demo script

**Assume the jury knows nothing about caching, ML, or this codebase.** Every
term below is explained the first time it's used — don't skip those lines
even if they feel obvious to you. This script is timed for ~5 minutes but
every section works standalone if you get cut off or asked to jump around.

Setup:
```bash
. .venv/bin/activate
bash scripts/dev.sh          # then open http://localhost:5173
```
Scenario **steady**, speed **8x**, policies LRU / LFU / GDS / GDSF / CACHE MIND
(default). Have `results/REPORT_api.md` and `results/ABLATION_api.md` open in
a second tab.

---

### 0:00 — The problem, in plain terms (30s)

> "A cache is just a fast, small storage layer in front of something slow and
> expensive — a database, a paid third-party API, an AI model. The first time
> you ask for something, you pay the full slow/expensive price and then
> **remember the answer**. Next time someone asks, you hand back the
> remembered answer instantly instead of paying that price again.
>
> The problem: a cache is small, so it constantly has to throw old answers
> away to make room for new ones. The standard way to decide *what to throw
> away* — used almost everywhere — is 'whatever wasn't used recently.' That
> rule is blind to one huge fact: **some answers cost far more to re-generate
> than others.** A cheap database read and a $0.005, 2-second API call get
> thrown away by the exact same rule, even though losing the second one hurts
> far more. At real scale that forces a lose-lose: over-pay for a huge fast
> cache, or under-pay and get hammered every time traffic spikes."

### 0:30 — What CACHE MIND is (40s)

> "CACHE MIND replaces that blind 'recently used' rule with a system that
> actually **decides**. Two ideas:
>
> **One — a 3-level cache**, not one big storage layer. Think of it like your
> own desk (very fast to reach, very small), a filing cabinet across the room
> (a bit slower, holds a lot more), and a storage unit down the street
> (slow, but basically free, holds almost everything). We call these
> **L1 (fastest, smallest), L2 (medium), L3 (slowest, cheapest, biggest)**.
> When something stops being popular, instead of throwing it away, we move it
> **one level down** — so getting it back later is still much faster than
> going all the way back to the original slow source, which we call the
> **origin**.
>
> **Two — a value score.** Every cached thing gets ranked by *how much it's
> actually worth keeping right now* — combining how often it's used, how
> expensive it is to regenerate, how big it is, and a prediction of whether
> it'll be needed again soon. That score decides which level it lives in, or
> whether it gets dropped entirely. And the weighting of that score
> **re-tunes itself automatically** as traffic changes — nothing is hardcoded
> by us in advance."

### 1:10 — Live: steady traffic (70s)

Press **Start**, let ~15 rounds ("epochs" — one round of simulated traffic,
roughly every second on screen) build up.

- **Cost chart**: "Same simulated traffic, same real-world pricing model, for
  every policy on screen — this is apples to apples. The grey/purple/blue
  lines are classic algorithms: LRU, LFU, GDS, GDSF (GDSF is the strongest
  classical one — it already knows about cost and size, just not about
  multiple storage levels). CACHE MIND, the red line, is lower — genuinely
  cheaper to run, on identical traffic."
- Top card: **"cost saving vs LRU"** — "double-digit percent, and it keeps
  growing as the run continues."
- **avg latency card**: "CACHE MIND answers in single-digit milliseconds on
  average. The others are 2-10x slower **on the exact same requests** —
  because they don't have anywhere to put something except 'in the fast tier
  or gone entirely.'"
- **tiers panel**: "You can watch objects physically sitting in L1/L2/L3
  right now. CACHE MIND is using all three; the classical policies only ever
  had the one, biggest tier — so a fair chunk of what CACHE MIND has warm
  in L2/L3, they've already thrown away completely."
- **bandit weights panel** (say what a 'bandit' is if you use the word):
  "This is one of two small learning components. Every round it picks which
  factor to lean on — cost, recency, freshness, predicted-demand — by trying
  options and keeping what's working, the same idea a slot machine
  gambler uses to find the best machine ('multi-armed bandit'). Nothing here
  is a fixed rule we wrote by hand."

### 2:20 — Live: the traffic spike (60s)

Press **⚡ Inject traffic spike**.

> "This simulates a flash crowd — something that was ice-cold suddenly gets
> hammered by 3x normal traffic, the way a post going viral would hit a real
> API."

- **Regime card** ("regime" = the system's own label for what kind of traffic
  it thinks it's currently seeing) flips to `spike`.
- **Bandit arm** shifts — point at the name changing.
- **Decision feed** (a live log of individual choices the engine is making
  right now): point at `promote` / `L2->L1` lines — "it's pulling things that
  just got hot into the fast tier ahead of the crowd, not after."
- **Cost chart**: "Watch the gap *widen* during the spike. The classical
  policies are back to paying full origin price on every miss; CACHE MIND
  absorbs most of it in the warm tiers instead."

### 3:20 — Prove it's not synthetic-only (30s) — optional but strong

> "Everything so far ran on realistic *simulated* traffic — sampled from
> real-world-shaped statistics, the way cache research is normally
> benchmarked, because it lets us run a fair, repeatable, controlled test.
> But to show this isn't just numbers-on-paper, there's a **'real' profile**
> in the scenario picker that hits an actual public API on the internet —
> genuine network round trips, not simulated ones."

Switch profile to **real**, Start. Then optionally call
`GET /api/real/ping` in a second tab: "That number just came back from a live
HTTP request I fired right now — not from a script."

### 3:50 — The benchmark, for the skeptical juror (50s)

Switch to `results/REPORT_api.md` / `ABLATION_api.md`.

> "Six traffic patterns — steady, a sudden spike, a slowly drifting hot set,
> a day/night rate cycle, a cold start, and an adversarial one where the
> traffic's *entire character* flips every couple of minutes — tested against
> five classical policies. CACHE MIND is **71 to 82 percent cheaper than the
> best classical policy (GDSF)**, across every pattern.
>
> But here's the honest part, because a fair juror should ask: *of course* a
> 3-level cache beats a 1-level cache — that's not a fair fight. So we also
> built `GDSF-tiered` — the same classical GDSF rule, but given the exact
> same 3 storage levels CACHE MIND has. Against **that** fair comparison,
> CACHE MIND is still **47 to 50 percent cheaper**, with about a third of the
> worst-case latency. That gap is the *actual* contribution of this project —
> deciding *where* something lives, not just *that* there are more places to
> put it.
>
> The ablation study removes one capability at a time to show what's really
> carrying the result: the 3-level placement and the smart-refresh behavior
> (serve a slightly-stale answer instantly, fix it quietly in the background,
> instead of making the user wait) each roughly **double** the cost if
> removed. The two learning components — the bandit and a per-object
> access-pattern predictor — add a smaller few percent on steady traffic, but
> are what keep it winning when the traffic pattern itself keeps changing."

### 4:40 — Close (20s)

> "A cache that knows what things are worth, not just when they were last
> touched. Three storage levels instead of one. A decision made fresh every
> round instead of a fixed rule. And it's adapting live, on screen, in front
> of you right now — this dashboard is driving the real engine, not a replay."

---

## Likely jury questions — answer in plain language first, detail after

**"Isn't this just a bigger cache?"**
No — `GDSF-tiered` has the *identical* 3 storage levels and identical prices.
CACHE MIND still wins by 47-50% cost against it. That gap is purely "deciding
where things go," not "having more room."

**"Where's the actual AI / ML here?"**
Two small, honest pieces, both learning live with zero pre-training: (1) a
**bandit** that re-picks which scoring factor to lean on every round, based
on what's been working; (2) a **predictor** that watches how often each
object gets asked for and estimates whether it'll be needed again soon. If
asked "how accurate is it" — be honest: we don't report a raw accuracy
number, because the ML's job here isn't classification, it's *adapting the
policy as traffic changes* — that shows up as the system staying cheap even
in the adversarial "traffic keeps flipping" test, not as a percentage.

**"Why not full reinforcement learning?"**
A bandit *is* a real, simplified form of RL — the part that fits: pick an
action, see a reward, immediately learn from it, no need to plan multiple
steps ahead. Full RL (something like Q-learning) would need training
episodes and a lot more data than a live cache reasonably has — overkill and
slower for a decision that has to be made every second.

**"Is this real data?"**
Two honest layers: the main benchmark uses **realistic simulated traffic**
(the standard way cache research is tested, because it's repeatable and
controllable) — sizes and request patterns drawn from real-world-shaped
statistics, not literal production logs. Separately, the **'real' profile**
in the live dashboard hits an actual public API over the internet — genuine,
unscripted network latency, to prove the engine isn't just tuned to fake
numbers.

**"Does prefetching actually happen?"**
Prefetch (warming a predicted-hot object before anyone asks for it) exists
and is wired to two real sources — objects that were recently pushed out and
predicted to come back, and objects historically requested together. Be
honest if pressed: in the current test setup the three storage levels
combined are generous enough that almost nothing ever leaves the cache
entirely, so there's rarely anything *left outside* to prefetch — the win in
this project comes from placement and refresh, not prefetch. That's a sizing
property of the demo, not a broken feature.

**"How would you know L2/L3 speed and price in a real deployment?"**
They're not guessed — they're deployment constants (your Redis tier's actual
response time, your storage provider's actual price). The engine just reads
them from one config object; swapping in real numbers from a real deployment
is a one-line change, not a redesign.

**"What about overhead — doesn't all this deciding cost something?"**
Yes, and it's kept small on purpose: candidate scans for eviction are
sampled (not a full scan), the per-object cost math is cached/memoized, the
bandit's math is a small matrix solve once per round (not per request), and
moves between tiers are capped per round so the system can't thrash.

**"Who would actually use this?"**
Backend/platform engineers running something with a real cost-per-miss —
paying for third-party API calls, or paying in GPU time for AI-model
inference — not end consumers directly. It sits as a drop-in layer between
their application and their expensive backend.

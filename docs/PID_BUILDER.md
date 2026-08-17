# Agentic P&ID Builder

Describe a process in prose; get back a piping and instrumentation diagram, the
schedules that go with it, and an honest list of everything the drawing still
gets wrong.

```bash
pip install -e ".[pid]"

pid build "Degummed soybean oil is drawn from a day tank by duty/standby feed
pumps, heated against LP steam, contacted with bleaching earth under vacuum,
then filtered before going to deodorising." --area 1200 --out drawings/bleaching
```

That writes `pid.json` (the model), `pid.svg` (the drawing), `findings.txt` (what
is still wrong with it), and CSV schedules — equipment list, line list,
instrument index, loop schedule, relief schedule, interlock schedule.

To see the output shape before spending a token:

```bash
pid example --out /tmp/demo     # a worked oil-bleaching P&ID, no API call
```

---

## How it works

Four agents, each a single structured-output call to Claude, run in a fixed
order. The order is fixed because it has to be: you cannot instrument equipment
that has not been laid out, and you cannot safeguard a unit whose control valves
do not exist yet.

```
description
    │
    ▼
┌──────────────┐   equipment, nozzles, piping, manual valves,
│   intake     │   off-page connectors
└──────┬───────┘
       ▼
┌──────────────┐   transmitters, controllers, control valves with
│instrumentation│  fail positions, indication, control loops
└──────┬───────┘
       ▼
┌──────────────┐   relief devices and their discharge routing,
│   safety     │   isolation, check valves, trip switches, interlocks
└──────┬───────┘
       ▼
┌──────────────┐
│  rule engine │ ── findings ──┐
└──────┬───────┘               │
       │                       ▼
       │              ┌──────────────┐
       │              │    review    │  returns a corrected drawing
       │              └──────┬───────┘
       │                     │
       └──── accepted only if measurably better ◄┘
```

**The agents propose; ordinary code decides.** Everything after the last API
call — validation, layout, rendering, export — is deterministic. That means a
saved P&ID can be re-checked and re-drawn as often as you like for free, and a
revision diff shows process changes rather than the layout engine changing its
mind.

### The review guard

The review agent is the only one that returns a whole redrawn model, because
findings cut across the drawing: fixing a relief valve set above design pressure
might mean changing the valve, the vessel, or both. A patch protocol expressive
enough for that is a worse bet than letting the agent restate the drawing.

The risk of a restatement is that the agent quietly drops content. So the
pipeline re-validates the revision and keeps it only if all of these hold:

- the error count did not go up;
- no equipment disappeared that no finding asked to remove;
- the weighted finding score (errors × 100, warnings × 10, notes × 1) went
  *down*, not merely sideways.

A confident-sounding bad revision is therefore discarded rather than shipped, and
the rejection reason appears in the build summary.

---

## What the rule engine checks

`pid rules` lists all of them. Rule IDs are grouped so a finding's number tells
you what kind of problem it is.

| Group | Concern | Examples |
|---|---|---|
| `R0xx` | Referential integrity | a line to equipment that isn't in the list; a loop referencing an instrument that doesn't exist; an interlock with no initiator |
| `R1xx` | Tag conventions | ISA-5.1 letter order, equipment prefix vs class, line-number grammar, loop numbers matching their instruments |
| `R2xx` | Topology | orphan equipment, a pump with no discharge, material flowing into a dead end |
| `R3xx` | Equipment completeness | inventory with no level measurement, pressure equipment with no relief device, missing design conditions, rotating equipment that can't be isolated |
| `R4xx` | Control and safety logic | a control valve with no fail position, an open loop, a relief valve set above the MAWP it protects, a relief that discharges nowhere |
| `R5xx` | Line completeness | missing size or phase, a size that disagrees with the line number |

Severity is a judgement about **drawing** quality, not plant safety:

- **error** — the drawing is internally inconsistent, or missing something no
  competent reviewer would sign off on.
- **warning** — very likely wrong, but legitimate designs exist.
- **note** — worth considering.

`pid validate` exits non-zero when there are errors, so it drops into CI:

```bash
pid validate drawings/bleaching/pid.json --json > findings.json
```

### What it does not check

Worth being explicit, because a clean report is easy to over-read. The rule
engine checks the drawing for internal consistency and for the presence of
things a P&ID must show. It does **not** size anything. It will not tell you the
relief valve is too small, the line velocity is wrong, the pump has inadequate
NPSH, or that the interlock ought to be SIL 2. Set pressure is checked against
the design pressure recorded in the model, not against a relief-load
calculation.

**This produces a drawing for engineers to review, not a drawing that has been
reviewed.** It does not replace a HAZOP, a relief-system study, or a P.E. stamp.

---

## Commands

| Command | Calls the API | What it does |
|---|---|---|
| `pid build "<description>"` | yes | Build a P&ID and write the whole package |
| `pid revise <pid.json> "<change>"` | yes | Apply a change and re-review |
| `pid validate <pid.json>` | no | Run the rule engine; non-zero exit on errors |
| `pid render <pid.json>` | no | Draw the SVG |
| `pid export <pid.json>` | no | Write the schedules |
| `pid example` | no | Emit a worked drawing |
| `pid rules` | no | List the validation rules |
| `pid describe <tag>` | no | Explain a tag, line number or equipment prefix |

`pid describe` is handy on its own:

```
$ pid describe LSLL-1101
LSLL-1101 — instrument
  function  Level / Switch / Low / Low
  loop      1101
  role      switch

$ pid describe '6"-PL-1201-CS150'
6"-PL-1201-CS150 — line number
  size      6"
  service   PL (Process liquid)
  sequence  1201
  spec      CS150 (Carbon steel, ASME Class 150)
```

### Steering the build

```bash
pid build --from-file process.md \
  --area 1200 \
  --equipment "P-1201A/B existing feed pumps" \
  --control "cascade bleacher temperature off the heater outlet; all steam valves fail closed" \
  --hazards "oil is above its flash point at the heater outlet" \
  --house-rules "tag all instruments with the 12- area prefix" \
  --effort xhigh \
  --review-rounds 3 \
  --out drawings/bleaching
```

`--house-rules` is appended to every agent's system prompt, which is where a
client's own tagging standard belongs. `--skip-instrumentation` and
`--skip-safety` stop after the equipment graph, which is useful when you want to
hand-instrument a drawing yourself.

---

## Using it as a library

```python
from pid import build_pid, render_svg, validate, load_model

result = build_pid(description, area=1200, review_rounds=2)
print(result.summary())          # per-pass counts, rejected revisions, token use
print(result.report.to_text())   # remaining findings

# Everything below is free and offline.
model = result.model
open("bleaching.svg", "w").write(render_svg(model))
report = validate(model)
```

The model is plain Pydantic, so a drawing can also be written by hand — see
`pid/examples.py` for a complete one — or built by other code and then checked
and rendered by this package.

### Model configuration

`AgentSettings` controls every pass:

```python
from pid import build_pid
from pid.agents import AgentSettings

result = build_pid(
    description,
    settings=AgentSettings(
        model="claude-opus-5",   # the default
        effort="xhigh",          # low | medium | high | xhigh | max
        max_tokens=24000,        # raise if a large unit gets truncated
        refusal_fallback=True,   # retry a classifier decline on a fallback model
    ),
)
```

The shared conventions block is the first system block on every call and is
marked for caching, so the four passes of a build share one cache prefix instead
of paying a fresh write each time.

---

## Design notes

**Off-page connections are equipment.** A connector is an `Equipment` with
`kind="boundary"`, so the drawing is one uniform equipment-to-equipment graph and
nothing downstream needs a special case for "this line leaves the sheet". The
layout engine then pins source-only connectors to the left edge and sink-only
connectors to the right, which is where they belong on a real sheet.

**Loose typing where the vocabulary is long.** Equipment classes, service codes
and component types are `str`, not `Literal`. An out-of-vocabulary value should
surface as a *finding* — `R104`, `R105` — not as a parse exception that throws
away an otherwise good drawing. Tight types are reserved for small closed sets
like fail position and phase.

**The prompts carry conventions, the code carries checks.** The agents are told
ISA-5.1 and good practice so they produce sensible drawings; the rule engine then
decides independently whether they did. Neither trusts the other, which is what
makes a clean report mean something.

**Layout is a layered graph draw**, not a heuristic: rank by distance downstream,
order within ranks by neighbour barycentre to cut crossings, size columns and rows
to the symbols they hold, then route orthogonally with connection points fanned
along the symbol edges. Labels and bubbles are placed last against an occupancy
map, so a congested area degrades into "shifted a bit" rather than "unreadable
pile".

---

## Files

| Path | What's in it |
|---|---|
| `pid/model.py` | The drawing: equipment, streams, instruments, loops, interlocks |
| `pid/standards.py` | ISA-5.1 tables, equipment prefixes, service and spec codes |
| `pid/tagging.py` | Tag and line-number parsing, validation and generation |
| `pid/rules.py` | The validation rule engine |
| `pid/agents/` | The four agents and their output schemas |
| `pid/pipeline.py` | Orchestration, merging, and the revision guard |
| `pid/symbols.py` | ISA symbol geometry |
| `pid/layout.py` | Graph layout and line routing |
| `pid/render.py` | SVG rendering |
| `pid/exports.py` | Line list, instrument index and schedules |
| `pid/examples.py` | A complete hand-written P&ID |
| `pid/cli.py` | The `pid` command |

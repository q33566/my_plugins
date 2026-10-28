---
name: adversarial-plan
description: Grill a change's root cause and plan from first principles with rounds of fresh multi-angle agents, branching every trade-off only the user can decide into alternative plans and grilling each branch to its leaf; only once the whole plan tree converges, walks the user through each fork, with a recommendation, to pick the leaf to build. Use when the user says "鬼grill一波 / 鬼grill / 第一性原理grill根因 / grill計劃 / 先grill再做", or wants a change root-caused and planned under adversarial review before any code. Hand the chosen plan to adversarial-build. Trigger: /adversarial-plan
---

# adversarial-plan(對抗式計劃)

Grill the root cause and the plan before any code. Where the plan depends on
a trade-off only the user can decide, keep **every** reasonable answer as its
own branch and grill each branch to its leaf. Talk to the user only after the
whole **plan tree** has converged: then walk them through the forks one by
one and let them pick the leaf. Implementation is a separate skill,
`adversarial-build`.

Read [`grill-loop.md`](grill-loop.md) in full before Step 0: the grill loop,
isolation, quota watcher and progress graph are defined there, and the steps
below use them by name.

## Rule zero — evidence settles facts, the user settles trade-offs, later

- **A fact is yours to settle.** Root cause, mechanism, whether the design is
  the defect, whether an option actually works: decide on evidence. A doubt
  about a fact is a grill target: write it down and dedicate an agent to it.
- **A trade-off only the user can decide becomes a fork, not a question.**
  Product judgement, intended design never stated, which cost they would
  rather pay: record a `fork` with one `option` per reasonable answer and
  keep grilling all of them. Do not stop to ask.
- **An option that cannot work is pruned on evidence**, with the finding as
  its `reason`. Pruned options are reported in the hand-over, never offered
  as a choice.
- **A reversible choice is never a fork.** Where a file goes, what it is
  called, which order: decide, and list it in the hand-over.
- **Mid-run questions are only for what blocks all grilling**: access,
  credentials, a physical action on a device, a run only the user can do.
  Ask once, state the assumption you run on meanwhile, keep going.

## The plan tree

Built from progress-graph lines (`fork`, `option`, `choice`; see the table in
grill-loop.md):

- A **fork** is one trade-off. Its `parent` is `"root"` when it applies to
  every plan, or the `option` it only exists under.
- An **option** is one answer at a fork: `live`, or `pruned` with a `reason`.
- **Independent forks are siblings, not nested.** Nest a fork under an
  option only when its answers change depending on that option. Three
  independent forks of two options each are six options to grill and three
  questions to ask, not eight plans.
- A **leaf** is a live option with no forks under it. A complete plan is the
  shared root plan plus one leaf per active fork.
- Every fork has at least two live options while grilling. A fork whose
  options all but one get pruned is no longer a choice: record it as a
  `decision` and drop it from the discussion.

`progress.py` prints the tree (PLAN TREE) and lists the forks to discuss.

## Step 0 — Ask what to do

Unless the task came as an argument, ask 「這次要做什麼?」 and wait — the
task itself is human information. Do not infer it from earlier conversation;
offer it as a suggestion inside the question instead. Record the user's
answer **verbatim** (if they point at a suggestion, record their words, what
they pointed at, and the original complaint that started it). Every agent
gets this wording, not your paraphrase.

Then, in the same message as you start work: arm the quota watcher, show the
requirement split (grill-loop.md, Progress graph), and print this `/goal` so
the user can paste it (`/goal` is a user-typed command; you cannot run it).
Do not wait for it to be pasted.

> `/goal 照 adversarial-plan 把「<任務>」的根因與計劃 grill 到收斂:每輪每個面向派一個全新空白 agent 用第一性原理審,事實問題自己查證決定,只有使用者才能判斷的取捨就分岔成多份計劃、每個分岔都 grill 到 leaf,站不住的剪掉並記理由;整棵計劃樹一整輪沒有成立的 A 類問題、沒有未解疑慮之後,才逐個分岔跟使用者討論(附建議與理由)選出 leaf;不寫程式碼`

**Done when**: you hold the user's own wording, the requirements are in the
log, and the goal is printed.

## Step 1 — Draft the plan tree

Investigate and reproduce the problem yourself. Write into the progress
graph: observed symptom, reproduced mechanism, root cause, **whether the
architecture/design is the defect** (and if so what the design should be),
the root plan, and every doubt you already have. For each trade-off the
user would have to decide, add a `fork` and its `option` lines; give each
option the concrete plan it implies. No production code.

## Step 2 — Grill the whole tree

Each round reviews the whole tree as one version: the root plan and every
live option. Angles per round:

- **Architecture**: what is this subsystem for? Derive the design it should
  have from that purpose. Is the current design the real defect, with this
  bug just one symptom? Is any option a patch over a wrong design?
- **Evidence**: is the claimed mechanism actually proven? Reproduce it
  independently. Does the evidence also fit a different cause?
- **Blast radius**: where else does the same root cause produce failures?
  Does each option cover them or only this symptom?
- **Fix alternatives**: list the ways to fix it from smallest patch to
  redesign; for each, what it fixes, what it leaves broken, its cost. Is a
  reasonable alternative missing from the tree?
- **Fork audit**: is each fork really the user's call, or a fact that
  evidence can settle (then it is not a fork)? Are the options at a fork
  genuinely different? Is a nested fork really dependent on its parent
  option, or independent (then it is a sibling)? Does each option, followed
  as written, deliver every requirement?

A finding against one option is A for that option: fix the option, or prune
it with the finding as `reason`. A finding against the root plan is A for
the whole tree. Loop until **converged** (grill-loop.md) on the whole tree:
one full round on the exact tree you will discuss, with no A finding on any
live option, no open doubt, every fork with at least two live options, and
each live option's plan saying how it meets every requirement. This skill
writes no `status` lines; the Architecture and Fork audit angles check the
requirements.

Post a 2–3 line status after each round (root cause, architecture verdict,
forks still live) so the user can follow, but do not wait.

## Step 3 — Discuss the forks with the user

Only after Step 2 has converged. Take the forks `progress.py` lists under
"to discuss", parents before children. Use `AskUserQuestion`, up to four
sibling forks per call. For each fork:

- the question in plain words, with what depends on it;
- each live option with what it costs and what it gives, from the grill
  evidence;
- your recommendation first, labelled `(Recommended)`, with the reason in
  its description.

Record each answer as a `choice` line with the user's words in `quote`.
Children of an unchosen option drop out; children of the chosen one come up
next. If an answer brings new information (a requirement, a constraint, a
"none of these"), record it as a `goal` line, revise the affected subtree
and grill that subtree to convergence again before asking further.

**Done when**: `progress.py` shows every active fork as `chosen`.

## Step 4 — Hand over the chosen plan

Write `$SCRATCH/plan.md`: the root cause, the architecture verdict, the
root plan plus the chosen leaf at every active fork, merged into one plan to
build, and how it meets each requirement. Then tell the user:

- the chosen plan and the path to `plan.md`;
- the forks, what was chosen and why it was recommended;
- pruned options with the evidence that ruled them out;
- residual risks, needs-human-info items and the assumption for each;
- the reversible choices you made on your own (Rule zero);
- next step: `/adversarial-build` builds this plan from the same run
  directory.

Then stop the quota watcher and delete the cron job, unless the user
starts adversarial-build right away (it keeps them armed).

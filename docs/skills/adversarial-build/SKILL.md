---
name: adversarial-build
description: Implement a plan chosen with adversarial-plan via /tdd, then grill the implementation with rounds of fresh multi-angle agents until their objections converge and every requirement is met, then — when a real-world ground truth exists for the change — validate against it until the agreed acceptance criteria pass. Use when the user says "照計劃做 / 實作並grill / 做完派agent grill / 修完grill一下 / 做事並grill", or an adversarial-plan run has handed over a chosen plan. Trigger: /adversarial-build
---

# adversarial-build(對抗式實作)

Build the plan a `adversarial-plan` run chose, then grill the
implementation until it converges and validate it against ground truth
where one exists. The plan is the contract: implement it, no more, no less.

Read [`grill-loop.md`](grill-loop.md) in full before Step 0: the grill loop,
isolation, quota watcher and progress graph are defined there, and the steps
below use them by name.

## Rule zero — decide yourself, grill your own doubts

- **Make every engineering decision yourself.** How to implement, how to
  test, how to fix a finding: you choose, using the evidence.
- **A doubt is a grill target, not a question for the user.** The moment you
  suspect something might be wrong ("this might also break X"), write it
  down as an open doubt and make it the subject of the next round —
  dedicate an agent to it, demand a reproduction that settles it.
- **Only ask the user for information only a human has**: a requirement
  never stated, product judgement the plan did not settle,
  access/credentials, physical actions on a device, a run only the user can
  do, and anything irreversible or outward-facing (commit, push, publish).
  Ask once, state the assumption you run on meanwhile, and keep going on
  everything that does not depend on the answer. An engineering question
  you could settle with evidence is not a "design question".
- **A reversible choice is never a mid-run question.** Where a file goes,
  what it is called, what order: decide, do it, and list it in the
  hand-over.

## Step 0 — Load the plan

If an adversarial-plan run handed over, load its `$SCRATCH/plan.md` and keep
writing the same `progress.jsonl`: the goal, requirements and chosen forks
carry over. Otherwise ask 「要照哪份計劃做?」 and wait; if there is no plan
yet, suggest running `/adversarial-plan` first. A plan the user gives in
their own words is recorded **verbatim** as a `goal`, split into `req` lines
(grill-loop.md, Progress graph).

Then, in the same message as you start: arm the quota watcher (or confirm it
is still armed from the plan run) and print this `/goal` so the user can
paste it (`/goal` is a user-typed command; you cannot run it). Do not wait
for it to be pasted.

> `/goal 照 adversarial-build 用 /tdd 實作選定的計劃,實作完每輪每個面向派一個全新空白 agent 用第一性原理審有沒有改對,自己查證並修正後再派新一輪,有疑慮就派 agent 專門 grill 到有證據為止,直到一整輪沒有成立的 A 類(會擋路)問題、沒有未解疑慮、圖上每條需求都有實作階段的證據顯示已滿足,或記明原因而關閉;每個成立的 A 類程式問題都有修前會失敗的迴歸測試;收斂後若這次改動有真實世界的 ground truth 可用,就照它定驗收標準、驗證、沒過就診斷修正再驗,直到過或每個落差都有明確結論,沒有 ground truth 就說明原因直接交接`

**Done when**: you hold the plan and the requirements, and the goal is
printed.

## B1 — Implement with `/tdd`

- New branch off the up-to-date main branch (fetch first). Never stack on
  local main or an unrelated branch. (Work not in git: edit the
  `$SCRATCH/work` copy — see Isolation.)
- Invoke the `tdd` skill and follow it; without one, work red-green-refactor
  inline: a failing test first, the smallest code to pass it, then clean up.
  A change to prose only (instructions, docs) has no failing test: write it,
  and let the B2 rounds walk it literally.
- Implement the plan — no more, no less. If implementation shows the chosen
  option cannot work, stop: record the evidence as a finding against that
  option and go back to adversarial-plan Step 2 for that fork (and Step 3
  if the user must choose again). Do not improvise a different plan here.
- Run the project's tests and type check for what you touched.

## B2 — Grill the implementation

Angles per round:

- **Fixes the root cause**: given the plan's root cause, does this diff
  remove it, or only suppress this symptom? Is the original failure still
  reachable another way?
- **Correctness / edge cases**: data shapes, boundaries (empty, single,
  huge, special characters), error paths, concurrency, platform
  differences — each with a concrete repro.
- **Actually use it**: exercise the change the way it is really used —
  run the CLI, drive the terminal UI in a pseudo-terminal at several sizes,
  serve and click the page in a real browser tab, call the library from a
  throwaway script — all inside its own sandbox. Anything that needs a
  device, a remote host or the user's browser you do yourself, between
  rounds.
- **Spec fit + tests**: compare against the user's wording and the chosen
  plan (scope creep / under-build), and check the tests would actually fail
  if the fix were reverted.

Every confirmed A finding in code is fixed with a regression test shown to
fail on the pre-fix code and pass after — proved in an `a0`, as Isolation
describes. Loop until **converged** (grill-loop.md), which here also needs
**every requirement met** — by evidence from the final work, written as
`status` lines `at` a build version — or `closed` with the reason.

## B3 — Validate against real-world ground truth, when one exists

B2's grill loop is **verification**: fresh agents check the diff against the
spec and against each other's doubts, using oracles *you* wrote (a
differential against the pre-fix code, properties derived from the spec,
mutation testing). That proves the code does what you decided it should do.
It does not prove the decision was right in the real world — for that you
need **validation**: scoring the change's actual output against **ground
truth**, a reference answer that exists independently of this code and was
not produced or hand-picked by it (a human's judgment on real data, a
domain expert's label, a real user's acceptance, a previously-recorded
correct outcome) — never the system's own evidence about itself.

**Decide explicitly whether a ground truth exists for this change — do not
skip this silently:**
- Some changes have one readily available: a labeled dataset, a
  known-correct answer set, a real user who will say yes/no, an oracle
  outside the code (a spec with worked examples, a domain expert).
- Some do not, and manufacturing one is not worth it (a straightforward
  refactor, an internal data-structure change with no externally-observable
  "correct" answer beyond the behaviour B2 already pins). Say so, with why,
  and go straight to B4 — this is a legitimate, common outcome, not a
  shortcut taken to save time.
- Some need a NEW ground truth built for this validation (run the real
  system — real model, real device, a real user flow — against real
  inputs, then have a human judge each result). This is the expensive,
  high-value case this step exists for.

When one exists or must be built:

1. **Set the acceptance criteria before running anything.** A numeric or
   binary bar, agreed with the user if it is their call (e.g. "N of M
   known-good cases must keep their known-good answer; of the cases
   targeted by this change, at least K/N must now be judged correct").
   Decide it in advance so you cannot rationalize a weak result afterward.
   Where the bar sits is product judgement (Rule zero): ask once, then keep
   going.
2. **Watch for a stale or unrecorded baseline.** If the ground-truth data
   pre-dates this change (an old recorded run, a dataset labelled under
   different assumptions, a model version nobody noted), a straight
   before/after comparison is not valid — build a control arm instead: run
   the SAME inputs through the pre-fix code path and the fixed one, under
   conditions otherwise identical, so the change is the only variable.
3. **Decide who runs it, and say so.** If it is cheap and safe, run it
   yourself. If it is expensive (real tokens, device time, wall-clock
   hours) or touches something only the user can authorize (money, a
   shared device, production data), say what it will cost, prepare the
   exact commands/scripts, and hand them to the user to run — then analyse
   whatever they bring back.
4. **Score the result against the acceptance criteria.** Below the bar is
   not a hand-over point.
5. **If it fails, diagnose before touching anything**: is the
   implementation wrong (root-cause it, fix with a regression test, back to
   B2 to re-verify the fix), is the acceptance criteria or the ground truth
   itself wrong (state why, get it corrected, re-score — never just lower
   the bar to make a bad result pass), or is the gap real-world noise the
   implementation cannot help (say so, and what evidence shows it is
   noise)? Rerun validation after every fix — loop until the result clears
   the bar or every gap has one of those three explicit resolutions.

## B4 — Personal verification and hand-over

Reproduce the original problem's fix yourself on the final code. Then hand
over — this is where the user reviews what you decided:

- what was decided and done, against the user's wording and the chosen plan;
- the progress graph's last checkpoint view (requirements and their
  evidence, rounds, findings fixed / ruled out / out of scope, mistakes);
- **where it could still be wrong**: each residual risk, what evidence is
  missing and why it could not be obtained, and what would settle it;
- needs-human-info items and the assumption you ran on for each;
- the reversible choices you made on your own (Rule zero), so the user can
  change them;
- what you verified yourself, what only agents verified, what was not
  verified (a simulation of another OS or a device is not that
  environment);
- for work not in git: that the result was copied back to the live files
  (hash-checked), and their new hashes;
- **ground-truth validation (B3)**: what ground truth was used and the
  acceptance criteria, the actual result, and who ran it; or, if none
  exists for this change, that decision and why;
- branch name, and whether anything is committed or pushed.

Then stop the quota watcher and delete the cron job. Do not commit, push,
or open a review unless the user asked.

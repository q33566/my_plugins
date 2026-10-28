# grill-loop — shared by adversarial-plan and adversarial-build

The loop every round of both skills runs: how a round works, how agents are
isolated, how a usage-limit stop resumes, and the progress graph that keeps the
run tied to the user's words.

An identical copy of this file, `progress.py` and `quota_wait.sh` lives in each
skill's folder, so each skill also works when uploaded alone. Edit both copies
and check them with `cmp` afterwards.

## The grill loop

One **round**:

1. Dispatch **fresh, blank** agents in parallel, one per angle (`general-purpose`, a new
   Agent call each — never forks, never `SendMessage` to a previous round's
   agent).
2. Each agent owns **one angle** (listed per phase; an open doubt from your
   progress graph always gets its own agent) and must reason **from first
   principles**: start from what the system is for and what must be true for
   it to work, then derive whether the current conclusion/code satisfies
   that — not "does this look like the usual pattern" and not "is this
   consistent with the existing code".
3. Give each agent self-contained context: **its own sandbox path (never the
   user's repo path)**, the snapshot sha, the user's Step 0 wording verbatim, the **current** proposal or diff, and where the
   evidence lives. Do **not** give it your opinion of whether the proposal is
   right, and do not give it the previous rounds' debate — only their
   outcome: which findings are claimed fixed (to re-check) and which were
   decided as residual or out of scope ("decided, do not re-report — unless
   you can now reproduce it going wrong", which makes it A again). For a
   doubt agent, state the doubt as an open question, not as your suspected
   answer.
4. Each agent works only in its own environment (see **Isolation** below),
   cites `file:line` or reproducible evidence for every claim, lists what it checked and ruled out as well as what it
   found, and ends each finding with a verdict: **REAL / NOT REAL / UNSURE**,
   and a class — **A blocking** (following the proposal or code as written
   goes wrong in the real environment, the user's requirement is not
   delivered, or a claimed fix does not hold; a repro of the change under
   review failing is A even if nothing like it happened before),
   **B residual** (true but hypothetical: an input or situation nobody has
   hit and the reviewer could not make happen in the real environment) or
   **C cut** (could
   be removed without losing a requirement) — in under 600 words. "No A" is
   a valid result; say it plainly.
5. When all agents of the round have reported, **you** verify every finding
   yourself (reproduce it, read the code) — an agent's word is not proof —
   and its class: you may move a finding between A and B only with the
   evidence written down; when unsure, it stays A.
   An UNSURE finding becomes an open doubt for the next round.
6. Decide and revise: update the plan tree (adversarial-plan) or code and
   tests (adversarial-build) for every confirmed **A** finding, and make the C cuts
   you want — both go to the next round for review. B findings go into the
   progress graph and the hand-over as one-line residual risks; do not
   change the work for them. Record everything in the
   run's progress graph (see **Progress graph** below).

Then start the next round on the revised version.

**Never throttle a reviewer.** Do not cap an agent's tool calls, time or
depth to save quota ("at most N calls" and the like): a capped reviewer stops
before it reaches evidence. The user controls budget themselves; if the
usage limit stops the run, it resumes by itself (see **Quota**).

### Isolation — every agent in its own environment

Agents in a round run at the same time as each other and as you. Anything
two of you share — the working tree, git state (refs, stash, config), $HOME,
temp dirs, ports, background processes, devices, databases — is a race: one
side's experiment silently changes the other's result, and a wrong finding
(or a wrong "all green") comes back looking real. This protects against
accidents by honest agents, not a deliberately escaping one.

Copy the two code blocks below exactly as read from this file on disk
(Read or `awk`), never retyped: one changed character breaks the isolation.

If the work under review is not in git (a skill directory, a notes folder),
copy it twice — `rsync -a <live>/ "$SCRATCH/work/"` (then `git init`, commit;
this is `REPO`) and `rsync -a <live>/ "$SCRATCH/live.base/"` (the merge base
and restore source) — and put the live directory in `EXTRA`. Before each
round, `diff -rq "$SCRATCH/live.base" <live>`. For each difference: an
agent's write (anything a peer session does not confirm) is undone from
`live.base`; a peer's edit is merged into the work with
`git merge-file "$SCRATCH/work/<f>" "$SCRATCH/live.base/<f>" <live>/<f>`
(a conflict is yours to resolve), then copied into `live.base`. The round
reviews the merged work. In adversarial-build's B4, before the hand-over, copy back only when
`diff -rq` shows no difference:
`rsync -a --delete --exclude=.git "$SCRATCH/work/" <live>/`.

Do the whole setup in ONE Bash call, and start every later call of the round with the
`. '<round dir>/round.sh'` line it prints (a literal path). Set at the top
of that call: `ROUNDS` (a throwaway root for sandboxes — the session
scratchpad is fine; outside the user's repo, plain ASCII path, NOT the
durable log dir), `REPO` (the tree that holds the work under review — the
user's repo or the worktree you implement in — absolute), `N` (agents
this round, one per angle) and `EXTRA` (other paths to watch, one per line:
the repo the Bash tool starts in when it is not `REPO`, and the live
directory for work not in git; may be empty). Put the printed round dir and snapshot sha in
the round's line in the progress log (after a context loss they are the
only way back to the cleanup):

```bash
# ONE Bash call does the whole setup (Bash calls keep no variables or functions).
: "${ROUNDS:?set ROUNDS}" "${REPO:?set REPO}" "${N:?set N}"
[ "$N" -ge 1 ] 2>/dev/null || { echo "N must be a number >= 1"; exit 1; }
R="$ROUNDS/$(date +%Y%m%d-%H%M%S)-$$"             # unique per round and per instance
mkdir -p "$R" || exit 1
printf "R='%s'\nREPO='%s'\nN='%s'\nEXTRA='%s'\n" "$R" "$REPO" "$N" "${EXTRA:-}" > "$R/round.sh"
cat >> "$R/round.sh" <<'EOF'
[ -n "$R" ] && [ -d "$R" ] || { echo "round dir missing — stop"; return 1 2>/dev/null || exit 1; }
# state(): the user's repo as a fingerprint — working tree incl. untracked files
# (temp index, never the real one), HEAD, refs, stash, the real index (read only),
# config, hooks, submodules
state() { local T; T=$(mktemp "$R/idx.XXXXXX"); rm -f "$T"
  ( cd "$1" || exit 1; export GIT_OPTIONAL_LOCKS=0
    ( export GIT_INDEX_FILE="$T"
      git rev-parse -q --verify HEAD >/dev/null && git read-tree HEAD
      git add -A && git ls-files -s )            # one line per file: names what changed
    git rev-parse -q --verify HEAD; git symbolic-ref -q HEAD
    git for-each-ref; git stash list; git ls-files -s | shasum
    shasum < "$(git rev-parse --git-path config)"
    find "$(git rev-parse --git-path hooks)" -type f -exec shasum {} + 2>/dev/null | sort
    git submodule foreach --quiet --recursive \
      'git rev-parse HEAD; git status --porcelain; git stash list; git for-each-ref' )
  rm -f "$T"; }
# watched(): REPO plus every EXTRA path (one per line) — git trees by state(),
# other directories by a per-file hash list
watched() { printf '%s\n' "$REPO" "$EXTRA" | while IFS= read -r d; do
    [ -n "$d" ] || continue; echo "== $d"
    if git -C "$d" rev-parse --git-dir >/dev/null 2>&1; then state "$d"
    else (cd "$d" && find . -type f -exec shasum {} + | sort); fi
  done; }
EOF
. "$R/round.sh" || exit 1
# 1. snapshot the exact state under review (uncommitted + untracked files);
#    REDO_SHA=<sha> reuses an earlier round's snapshot instead
SHA=${REDO_SHA:-}
if [ -z "$SHA" ]; then
  T=$(mktemp "$R/idx.XXXXXX"); rm -f "$T"
  TREE=$(cd "$REPO" && export GIT_INDEX_FILE="$T" GIT_OPTIONAL_LOCKS=0 &&
         { git rev-parse -q --verify HEAD >/dev/null && git read-tree HEAD; git add -A && git write-tree; })
  rm -f "$T"
  SHA=$(cd "$REPO" && git commit-tree "$TREE" $(git rev-parse -q --verify HEAD >/dev/null && echo -p HEAD) -m snapshot)
fi
git -C "$REPO" cat-file -e "$SHA^{commit}" 2>/dev/null || { echo "no snapshot — stop, do not dispatch"; exit 1; }
echo "SHA='$SHA'" >> "$R/round.sh"
# 2. one SEPARATE repository per sandbox (a0 is yours, a1..aN the agents'):
#    private refs/stash/config/hooks, no remote, no ignored files (.env)
for a in a0 $(seq -f 'a%g' 1 "$N"); do
  git clone -q --shared --no-checkout "$REPO" "$R/$a/tree"
  git -C "$R/$a/tree" remote remove origin
  git -C "$R/$a/tree" checkout -q --detach "$SHA"
  mkdir -p "$R/$a/home" "$R/$a/tmp"
  cat > "$R/$a/run" <<EOF
#!/bin/bash
cd "$R/$a/tree" && exec env -i PATH="\$PATH" HOME="$R/$a/home" TMPDIR="$R/$a/tmp" \\
  PYTHONDONTWRITEBYTECODE=1 ANDROID_ADB_SERVER_PORT=1 GIT_CONFIG_GLOBAL=/dev/null bash -c "\$1"
EOF
  chmod +x "$R/$a/run"
done
watched > "$R/before"
echo "round ready: . '$R/round.sh'  (start every later Bash call with this; snapshot $SHA)"
```

Never use the Agent tool's `isolation: "worktree"` (built from the default
branch, without the changes under review), `git stash create` (misses
untracked files, locks the index) or `git worktree add` (shares the user's
refs and stash). Submodules: in each tree run `git -c protocol.file.allow=always
-c submodule.<name>.url="$URL" submodule update --init` and then
`git -C <submodule path> remote remove origin`, where `$URL` is the
submodule's local repository: `git -C "$REPO/<submodule path>" rev-parse
--absolute-git-dir` if it is initialised in `REPO`, otherwise
`$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir)/modules/<name>`.
The step fails loudly ("not our ref") when that repository lacks the pinned
commit — then say so in the agents' prompts instead of patching further.
Uncommitted work inside a submodule is not in the snapshot — tell the
agents if there is any.
If the project's tests need a virtualenv, create it inside each tree.

Rules for the round:

- **Every agent prompt** gives only its sandbox path and says verbatim:
  「每個 Bash 指令都用 `<sbx>/run '<cmd>'`;Read/Grep/Glob/Write/Edit 只用
  `<sbx>` 底下的絕對路徑(Grep/Glob 一定要給路徑);不碰裝置、遠端主機、使用者的
  瀏覽器、真實資料庫。」 Bash calls start in the user's repo, so a command not
  wrapped by `run` lands there.
- **You do not write the code under review while a round runs.** Verify
  findings in `a0` — every command through `a0/run`, like an agent (a plain
  `cd` there runs stale bytecode and your real environment); apply fixes only after the round's cleanup and
  checkpoint below (a fix made before them shows up as a change to the
  user's repo and voids the round). `a0` is
  a throwaway copy of the snapshot: to prove a regression test fails on the
  pre-fix code, use the next round's `a0` (put the pre-fix file in, run the
  test: red; restore: green) — never swap files in the tree where you
  implement, or in the user's tree.
- **Devices, remote hosts, the user's browser and real databases are
  yours alone**, used between rounds — never an agent's.
- **After the round**, once every agent has reported or been stopped — and
  only after you have verified their findings in `a0` and run any
  regression proofs there, because this deletes `a0` too, and before you
  apply any fix — in a NEW Bash call, from outside `$R`:

  ```bash
  . '<round dir>/round.sh' || exit 1      # the literal path printed at setup
  [ -n "$R" ] && [ -f "$R/round.sh" ] || { echo "no round loaded — stop, kill nothing"; exit 1; }
  cd /                                   # never from inside $R
  P=$(lsof -a -d cwd -Fpn 2>/dev/null | awk -v r="$(cd "$R" && pwd -P)/" -v me=$$ \
      '/^p/{p=substr($0,2)} /^n/{if(index(substr($0,2)"/",r)==1 && p!=me)print p}')
  [ -n "$P" ] && echo "$P" | xargs kill
  watched | diff "$R/before" - && echo "watched paths unchanged" && rm -rf "$R"
  ```

  Without the round file loaded this refuses and kills nothing — never "fix"
  that by setting `R` by hand. On a difference `$R` is kept as evidence. The Bash tool's shell is zsh: test any
  command you add the way it will run (separate calls, that shell). Any
  change in a watched path that the user or a peer session did not make →
  the round does not count. The diff names the files: remove what an agent
  wrote (never keep it as work), then rerun the round. If the only changes
  are confirmed peer edits, the round counts: fold them in as above,
  record `user_repo_unchanged: true` with a note, and remove `$R`. Record a `checkpoint` line in
  the progress graph.

Residual risks (name the relevant ones at hand-over; none gets a mechanism
until it happens — agents reaching a remote host, a real database and adb
did, and the prompt rule covers those): `state()` does not see ignored files, a second edit
to an already-dirty submodule file, or files in worktrees not listed in `EXTRA`
(their branches it does see: another session creating one mid-round shows
up in the diff — read it before voiding the round); processes that
left `$R` or ignore TERM survive; localhost ports, ssh keys, system git
credentials and an activated virtualenv on `PATH` are shared with `run`;
macOS `mktemp` ignores `TMPDIR`; `git gc --prune` on the user's repo during
a round breaks the sandboxes; paths containing quotes or `$` break setup;
`quota_wait.sh` is silent on an unreadable usage file (the cron job is the
backstop).

**Shared files (the live skill, settings, another session's artifacts):**
before writing one, compare its hash with the version you read; if it
changed, rebuild your edit on the new version — never overwrite.

**Other sessions.** A session can be resumed twice, and other sessions share
the machine. One writer per shared artifact and per progress log: if you
have `ListAgents`, ask the busy peers once whether they are writing it, and
meanwhile work only in your own directories; a second instance sends its
changes to the writer or keeps its own run dir.

### Quota — stop when the limit says so, resume when it comes back

At the start of the run, before any investigation, arm two things so a usage-limit stop never needs
the user to restart the run, and keep them armed for as long as ANY work
remains — between rounds and while you implement, not only while agents run:

- `bash <skill dir>/quota_wait.sh` with `run_in_background` — it exits (and
  wakes you) only after the usage guard has tripped and its 5-hour window
  has reset. Re-arm it after it fires. If it exits with an error (exit 2:
  no `jq`, no usage file), read its message, fix the cause and re-arm;
- a recurring hourly `CronCreate` whose prompt is "if this
  adversarial-plan or adversarial-build run was stopped by the usage guard and quota is back,
  resume from the progress graph; otherwise reply one line".

When woken: an interrupted round does not count — clean it up, run the setup
again with `REDO_SHA=<the sha in its round line>`, re-dispatch every angle
with the new sandbox paths. If no round was running, continue from the
progress view's NEXT list.
Stop the watcher (TaskStop) and delete the cron job only at the real end —
after the final hand-over, with nothing left to do. If you resume work after
any stop (a new day, a new instruction), arm both again first.

### Progress graph — compare every step with the goal

Grilling finds defects in whatever is in front of it; it does not ask
whether that thing should exist. Without an anchor the run drifts: each
round's findings get fixed, each fix adds surface, the next round finds more
in the new surface — while the user's requirements stop moving. Keep the
run's progress as a graph tied to the user's words, and check it at every
node (after every round, before every change).

Keep an append-only log, `$SCRATCH/progress.jsonl`, one JSON object per line.
`$SCRATCH` is `~/.claude/adversarial-runs/<date>-<topic>/`: it outlives the
session (never the session scratchpad or `/tmp`, which are wiped) and is
never inside a repository under review (there the log would void every
round's "user repo unchanged" check and leak earlier rounds into the
snapshot). Never rewrite a line; a later line with the same id and kind supersedes
the earlier one:

| kind | key fields | edge |
|---|---|---|
| `goal` | `text`: the user's words, verbatim (add new instructions as they come) | — |
| `req` | `text`: what must be true, `quote`: the words it comes from | `goal` |
| `mech` | `text`, `state` live/removed | `serves` [req], `basis` [incident or `"inferred"`] |
| `incident` | something that actually happened (observed, reproduced) | — |
| `version` | a proposal or snapshot sha | `parent` |
| `round` | angles, each agent reported/died/stopped, `status` | `on` version |
| `finding` | claim, verdict, `class` (`A`/`B`/`C`, first word), verified by you, disposition | `in` round, `fixed_in` change, `dup_of` |
| `change` | what changed, tests that pin it | `from`/`to` version, `addresses` [finding] |
| `decision` / `event` | scope, root cause, user instructions, quota stops, others' edits | `supersedes` |
| `mistake` | what went wrong and why | `violates` [req] |
| `status` | a requirement's state: met / partial / unmet / closed (cannot be met here: needs-human-info, or a residual — say which and why) + `evidence` | `req`, `at` (version or round) |
| `fork` | a trade-off only the user can decide (`text`: the question) | `parent` "root" or the option it depends on |
| `option` | an alternative plan at a fork (`text`), `state` live/pruned, `reason` (the finding) when pruned | `fork` |
| `choice` | the user picking an option (no id), `quote`: their words | `fork`, `option` |
| `checkpoint` | after a round: `cleanup`, `user_repo_unchanged`, `view_run`, `watcher` ("armed" — the watcher stops only after the hand-over, with no checkpoint after it) | `round` |

Right after Step 0, split the user's words into requirements (`req`, each
quoting the words it comes from). Record every doubt as a `finding` with
verdict `UNSURE` and disposition `open` in the round you are about to
dispatch (write that round's line first) — that is what "open doubt" means,
and NEXT lists it until a round settles it. Show the split in the same message in
which you start; do not wait for a reply — the user corrects it if needed.

At each of those points run `python3 <skill dir>/progress.py <log>` (exit 1: the
graph is broken; exit 2: warnings such as a skipped or incomplete checkpoint)
and act on it:

- **Every next action must move a requirement that is not yet met**, or be
  the round that confirms convergence, or the hand-over. If you cannot say
  which, do not do it.
- **Orphan mechanism** (serves no requirement) → remove it, and its findings
  become moot instead of being fixed.
- **Inferred-only mechanism** (protects against nothing that actually
  happened) → default to a one-line residual risk, not a mechanism.
- **Diverging**: blocking findings per round rising while requirement
  status stays flat → stop adding mechanism, re-read the goal, cut scope
  first. Check where they land: findings on what the last fix added mean
  you are growing surface; findings on what was already there mean the
  round found something.
- Record mistakes as you notice them, with the requirement they violated.
- A complete round without a `checkpoint` line shows as `NO CHECKPOINT`:
  its cleanup and comparison were skipped, so it does not count.

**Converged** means: a full round — every angle — on the exact version you
will hand over — nothing changed after it — with **no A (blocking) finding**
left after your verification and **no open doubt**. Each skill adds its own
conditions on top (adversarial-plan: the whole tree; adversarial-build: every
requirement met). B findings recorded in the graph do not change the
work, so they do not restart the count; an A fix or a C cut does. Count A findings, never
the total: a reviewer always finds something, and every sentence a fix adds
is new surface for the next round, so the total does not reach zero.

**Grill to death, but with progress.** There is no fixed round cap. After
round 5, narrow: dispatch agents only at the still-disputed points, each
required to run an experiment or reproduction that discriminates between
the competing answers. If two consecutive rounds produce no new evidence on
a point, decide it on the best evidence you have and record it as a
residual risk with exactly what evidence is missing and why it could not be
obtained (e.g. needs a Windows host, needs a device).
Narrowed rounds settle disputes; they never count toward convergence.

## Mistakes already made (do not repeat)

From a real run (fixing the isolation of the single skill these two
were split from, 2026-10).

- **Drift**: grilled a ~1100-line helper instead of the requirement; findings
  5 → 8 → 15 while requirements stood still; read "decide yourself" as "build
  everything" — progress graph: every action moves a requirement.
- **Raced the reviewers**: changed the tree mid-round; agents reached a real
  database, a device ledger, adb and a remote host; the orchestrator's own
  `git status` rewrote the user's index — Isolation, for your own commands too.
- **Commands tested in the wrong conditions**: a cleanup that would have
  killed every process in the user's repo when run in a fresh Bash call;
  `kill $P` that kills nothing
  under zsh — test the way it will run.
- **Quota**: agents died on the usage limit and the orchestrator then capped
  their tool calls — never throttle (grill loop); the watcher was stopped at
  an early "hand-over" — Quota: armed while any work remains.
- **Records and shared files**: the log kept in the wiped scratchpad; a near
  overwrite of another session's edit; two resumed instances on one skill —
  durable log, hash check, one writer.
- **Handed back a reversible choice** (where to store the log) — Rule zero.
- **Called it done early**: vacuous self-tests, a "converged" round that
  still changed things, findings marked applied before the change existed —
  Converged; B2's revert check; `progress.py` catches the last one only.

**Tools only where prose is not enough.** Prefer telling the agent in its
prompt. Ship a script only when the job cannot be done by an instruction
(e.g. something that must keep running in the background, or a computation
that must be exact every time).

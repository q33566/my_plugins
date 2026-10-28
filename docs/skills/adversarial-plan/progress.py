#!/usr/bin/env python3
"""Progress graph for an adversarial-plan / adversarial-build run.

The run is an append-only log (progress.jsonl, one JSON object per line).
Nodes: goal, req, mech, incident, version, round, finding, change, decision,
event, mistake, fork, option. A later line with the same id and kind supersedes the earlier
one (a round going from in-progress to complete, a mech being removed); the
same id with a different kind is an error. Three id-less kinds: `status` (a
requirement's state, `at` = the version or round it describes),
`checkpoint` (what was done after a round: cleanup, user-repo comparison,
this view, quota watcher) and `choice` (the user picking one option at a fork,
`quote` = their words; a later choice for the same fork supersedes).

Edges are fields: req.goal, mech.serves[req], mech.basis[incident|"inferred"],
version.parent, round.on(version), finding.in(round), finding.fixed_in(change),
finding.dup_of, change.from/to(version), change.addresses[finding],
mistake.violates[req], status.req, status.at, checkpoint.round,
fork.parent("root"|option), option.fork, choice.fork, choice.option.

The plan tree: a fork is a trade-off only the user can decide; its options
are alternative plans. A fork whose parent is an option exists only if that
option is chosen. A fork is active when its parent is "root" or a chosen
option of an active fork; NEXT lists active forks still to discuss.

    python3 progress.py progress.jsonl
exit 0 = consistent, no warnings (NOT "converged" — that is the SKILL.md rule); 1 = broken graph; 2 = warnings (a skipped or
incomplete checkpoint, a requirement marked met without evidence, …).
"""
import json
import sys
from collections import defaultdict

STATES = ("met", "partial", "unmet", "closed")
DONE = ("met", "closed")


def cls_of(f):
    """The class letter of a finding: the first word of `class` (A, B or C), or None."""
    c = f.get("class")
    return str(c).split()[0] if c is not None and str(c).split() else None
VERDICTS = ("REAL", "NOT REAL", "UNSURE")
ROUND_STATUS = ("in-progress", "complete", "void", "probe")
OPTION_STATES = ("live", "pruned")
DISPOSITIONS = ("applied", "ruled-out", "out-of-scope", "residual", "needs-human", "open", "moot")
NEEDS = {"goal": {"text": (list, str)}, "req": {"text": str, "goal": str},
         "mech": {"text": str, "serves": list, "basis": list, "state": str},
         "incident": {"text": str}, "version": {}, "round": {"on": str, "status": str},
         "finding": {"in": str, "claim": str, "verdict": str, "disposition": str},
         "change": {"from": str, "to": str}, "decision": {}, "event": {},
         "mistake": {"text": str, "violates": list},
         "fork": {"text": str, "parent": str},
         "option": {"text": str, "fork": str, "state": str}}


def load(path, errs):
    nodes, order, statuses, checkpoints, choices = {}, [], [], [], []
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                errs.append(f"line {n}: not JSON")
                continue
            if not isinstance(e, dict):
                errs.append(f"line {n}: not a JSON object")
                continue
            e["_line"] = n
            k = e.get("kind")
            if k == "status":
                if not isinstance(e.get("req"), str) or e.get("state") not in STATES:
                    errs.append(f"line {n}: status needs req and state in {STATES}")
                    continue
                statuses.append(e)
                continue
            if k == "choice":
                bad = [f for f in ("fork", "option", "quote") if not isinstance(e.get(f), str)]
                if bad:
                    errs.append(f"line {n}: choice needs fork, option and quote (the user's words): {', '.join(bad)}")
                    continue
                choices.append(e)
                continue
            if k == "checkpoint":
                if not isinstance(e.get("round"), str):
                    errs.append(f"line {n}: checkpoint needs round")
                    continue
                checkpoints.append(e)
                continue
            if k not in NEEDS:
                errs.append(f"line {n}: unknown kind {k!r}")
                continue
            if not isinstance(e.get("id"), str):
                errs.append(f"line {n}: {k} without a string id")
                continue
            bad = [f for f, t in NEEDS[k].items() if not isinstance(e.get(f), t)]
            if bad:
                errs.append(f"line {n}: {k} {e['id']}: missing or wrong type: {', '.join(bad)}")
                continue
            prev = nodes.get(e["id"])
            if prev and prev["kind"] != k:
                errs.append(f"line {n}: id {e['id']} is already a {prev['kind']}")
                continue
            if not prev:
                order.append(e["id"])
            elif k == "finding" and "class" not in e and "class" in prev:
                e["class"] = prev["class"]  # a re-recorded finding keeps its class
            nodes[e["id"]] = e  # later line supersedes
    return nodes, order, statuses, checkpoints, choices


def main(path):
    errs, warns = [], []
    try:
        nodes, order, statuses, checkpoints, choices = load(path, errs)
    except OSError as ex:
        print(f"cannot read {path}: {ex}")
        return 1
    by = defaultdict(list)
    for i in order:
        by[nodes[i]["kind"]].append(nodes[i])
    if not by["goal"] or not by["req"]:
        errs.append("no goal or no requirements: split the user's words into requirements first")

    def need(ref, kinds, where):
        if not isinstance(ref, str) or ref not in nodes or nodes[ref]["kind"] not in kinds:
            errs.append(f"{where}: {ref!r} is not a known {'/'.join(kinds)}")

    for r in by["req"]:
        need(r["goal"], ["goal"], r["id"])
    for v in by["version"]:
        if v.get("parent") is not None:
            need(v["parent"], ["version"], v["id"])
    for m in by["mech"]:
        for r in m["serves"]:
            need(r, ["req"], m["id"])
        for b in m["basis"]:
            if b != "inferred":
                need(b, ["incident"], m["id"])
    for k in by["mistake"]:
        for r in k["violates"]:
            need(r, ["req"], k["id"])
    for r in by["round"]:
        need(r["on"], ["version"], r["id"])
        if r["status"] not in ROUND_STATUS:
            errs.append(f"{r['id']}: round status {r['status']!r} not in {ROUND_STATUS}")
    for c in by["change"]:
        need(c["from"], ["version"], c["id"])
        need(c["to"], ["version"], c["id"])
        for f in c.get("addresses", []):
            need(f, ["finding"], c["id"])
    for f in by["finding"]:
        need(f["in"], ["round"], f["id"])
        if f["verdict"] not in VERDICTS:
            errs.append(f"{f['id']}: verdict {f['verdict']!r} not in {VERDICTS}")
        if f["disposition"] not in DISPOSITIONS:
            errs.append(f"{f['id']}: disposition {f['disposition']!r}")
        if f["disposition"] == "applied" or f.get("fixed_in"):
            need(f.get("fixed_in"), ["change"], f["id"])
        if f.get("dup_of"):
            need(f["dup_of"], ["finding"], f["id"])
        cls = cls_of(f)
        if f.get("class") is not None and cls not in ("A", "B", "C"):
            errs.append(f"{f['id']}: class {f['class']!r} — its first word must be A, B or C")
        if (cls == "A" and f["verdict"] == "REAL"
                and f["disposition"] not in ("applied", "open", "needs-human")):
            warns.append(f"{f['id']}: a confirmed A finding marked {f['disposition']!r} — fix it, or move it to B with the evidence")
    for d in by["decision"]:
        sup = d.get("supersedes")
        if isinstance(sup, str) and " " not in sup and sup in nodes and nodes[sup]["kind"] != "decision":
            errs.append(f"{d['id']}: supersedes {sup!r}, which is not a decision")
    for s in statuses:
        need(s["req"], ["req"], f"status line {s['_line']}")
        if s.get("at") is not None:
            need(s["at"], ["version", "round"], f"status line {s['_line']}")
        if s["state"] in DONE and not str(s.get("evidence", "")).strip():
            warns.append(f"status line {s['_line']}: {s['req']} marked {s['state']} without evidence")
    for c in checkpoints:
        need(c["round"], ["round"], f"checkpoint line {c['_line']}")

    # plan tree: forks, options, the user's choices
    opts_of = defaultdict(list)
    for o in by["option"]:
        need(o["fork"], ["fork"], o["id"])
        if o["state"] not in OPTION_STATES:
            errs.append(f"{o['id']}: option state {o['state']!r} not in {OPTION_STATES}")
        elif o["state"] == "pruned" and not str(o.get("reason", "")).strip():
            warns.append(f"{o['id']}: pruned without a reason (which finding killed it?)")
        opts_of[o["fork"]].append(o)
    for fk in by["fork"]:
        if fk["parent"] != "root":
            need(fk["parent"], ["option"], fk["id"])
        if len([o for o in opts_of[fk["id"]] if o["state"] == "live"]) < 2:
            warns.append(f"{fk['id']}: fewer than two live options — not a fork; settle it as a fact or a decision")
    chosen = {}  # fork id -> option id (latest choice wins)
    for c in choices:
        fk, op = nodes.get(c["fork"]), nodes.get(c["option"])
        if not fk or fk["kind"] != "fork":
            errs.append(f"choice line {c['_line']}: {c['fork']!r} is not a known fork")
            continue
        if not op or op["kind"] != "option" or op["fork"] != c["fork"]:
            errs.append(f"choice line {c['_line']}: {c['option']!r} is not an option of fork {c['fork']}")
            continue
        if op["state"] != "live":
            errs.append(f"choice line {c['_line']}: {c['option']} is {op['state']} — only a live option can be chosen")
            continue
        chosen[c["fork"]] = c["option"]

    def active(fid, seen=()):
        fk = nodes.get(fid)
        if not fk or fk["kind"] != "fork" or fid in seen:
            return False
        par = fk["parent"]
        if par == "root":
            return True
        op = nodes.get(par)
        return bool(op and op["kind"] == "option" and chosen.get(op["fork"]) == par
                    and active(op["fork"], seen + (fid,)))
    for fid in chosen:
        if not active(fid):
            warns.append(f"{fid}: chosen, but the fork is not on the chosen path")

    # version order: walk parents (a chain); unknown versions go last
    vpos = {}
    for v in by["version"]:
        depth, cur, seen = 0, v, set()
        while cur and cur.get("parent") in nodes and cur["id"] not in seen:
            seen.add(cur["id"])
            cur = nodes[cur["parent"]]
            depth += 1
        vpos[v["id"]] = depth

    def pos(ref):
        n = nodes.get(ref)
        if not n:
            return None
        return vpos.get(n["id"] if n["kind"] == "version" else n.get("on"))

    def texts(g):
        return g["text"] if isinstance(g["text"], list) else [g["text"]]

    print("GOAL")
    for g in by["goal"]:
        for t in texts(g):
            print(f"  {g['id']}: {t}")
    hist = defaultdict(list)
    for s in statuses:
        hist[s["req"]].append(s)
    print("\nREQUIREMENTS (status over time → latest; evidence of the latest)")
    for r in by["req"]:
        h = hist[r["id"]]
        trail = " → ".join(s["state"] for s in h) or "unknown"
        live = [m["id"] for m in by["mech"] if m["state"] == "live" and r["id"] in m["serves"]]
        print(f"  {r['id']:4} {trail[-40:]:40} {r['text'][:60]}")
        print(f"       mech {','.join(live) or '-':24} evidence: {str(h[-1].get('evidence', '-') if h else '-')[:60]}")
    live = [m for m in by["mech"] if m["state"] == "live"]
    removed = [m for m in by["mech"] if m["state"] != "live"]
    orphans = [m for m in live if not m["serves"]]
    inferred = [m for m in live if not [b for b in m["basis"] if b != "inferred"]]
    print(f"\nSURFACE  live mechanisms {len(live)}  (removed {len(removed)})  orphans {len(orphans)}  "
          f"inferred-only {len(inferred)}")
    for m in orphans:
        print(f"  ORPHAN   {m['id']}: {m['text'][:80]}")
    for m in inferred:
        print(f"  INFERRED {m['id']}: {m['text'][:80]}")

    print("\nTIMELINE  (per round: new REAL findings — blocking only where findings carry a class — requirements met, checkpoint)")
    fs = defaultdict(list)
    for f in by["finding"]:
        fs[f["in"]].append(f)
    cps = defaultdict(list)
    for c in checkpoints:
        cps[c["round"]].append(c)
    series = []
    for r in by["round"]:
        real = [f for f in fs[r["id"]] if f["verdict"] == "REAL"
                and (not f.get("dup_of") or cls_of(f) == "A")]
        if any(f.get("class") for f in real):  # classed round: count blocking (A) only
            real = [f for f in real if cls_of(f) in ("A", None)]
        rp = pos(r["id"])
        state_then = {}
        for s in statuses:
            sp = pos(s.get("at")) if s.get("at") else None
            if (sp is not None and rp is not None and sp <= rp) or (sp is None and s["_line"] < r["_line"]):
                state_then[s["req"]] = s["state"]
        met = sum(1 for v in state_then.values() if v == "met")
        dispo = defaultdict(int)
        for f in fs[r["id"]]:
            dispo[f["disposition"]] += 1
        cp = ""
        if r["status"] == "complete":
            good = [c for c in cps[r["id"]] if c["_line"] > r["_line"]]
            if not good:
                cp = "  NO CHECKPOINT"
                warns.append(f"{r['id']}: complete without a checkpoint after it (cleanup/comparison skipped?)")
            else:
                c = good[-1]
                problems = []
                if not c.get("cleanup"):
                    problems.append("cleanup not recorded")
                if c.get("user_repo_unchanged") is not True:
                    problems.append("user repo changed or not compared")
                if not c.get("view_run"):
                    problems.append("progress view not run")
                if (str(c.get("watcher", "")).split() or [""])[0] != "armed":
                    problems.append("quota watcher not armed (it stops only after the hand-over)")
                cp = "  checkpoint ok" if not problems else "  CHECKPOINT INCOMPLETE: " + "; ".join(problems)
                if problems:
                    warns.append(f"{r['id']}: checkpoint incomplete: {'; '.join(problems)}")
            series.append((r["id"], len(real), met))
        elif r["status"] == "in-progress" and any(x["_line"] > r["_line"] for x in by["round"] if x is not r):
            warns.append(f"{r['id']}: still in-progress while later rounds exist")
        print(f"  {r['id']:12} on {r['on']:5} {r['status']:11} new REAL {len(real):2}  met {met:2}  "
              + " ".join(f"{k}={v}" for k, v in sorted(dispo.items())) + cp)
    if len(series) >= 2 and series[-1][1] > series[-2][1] and series[-1][2] <= series[-2][2]:
        print(f"  DIVERGING: new (blocking where classed) findings {series[-2][1]} → {series[-1][1]} while met requirements "
              f"{series[-2][2]} → {series[-1][2]} — cut scope before fixing more")

    open_f = [f for f in by["finding"] if f["disposition"] == "open"]
    latest = {r["id"]: (hist[r["id"]][-1]["state"] if hist[r["id"]] else "unknown") for r in by["req"]}
    if by["fork"]:
        print("\nPLAN TREE  (CHOSEN = picked by the user; forks under an unchosen option are inactive)")
        kids = defaultdict(list)
        for fk in by["fork"]:
            kids[fk["parent"]].append(fk)

        def show(parent, depth, seen):
            for fk in kids.get(parent, []):
                if fk["id"] in seen:
                    continue
                tag = f"chosen {chosen[fk['id']]}" if fk["id"] in chosen else ("to discuss" if active(fk["id"]) else "inactive")
                print(f"  {'    ' * depth}fork {fk['id']} [{tag}]: {fk['text'][:70]}")
                for o in opts_of[fk["id"]]:
                    mark = "CHOSEN" if chosen.get(fk["id"]) == o["id"] else o["state"].upper()
                    why = f" — {o.get('reason')}" if o["state"] == "pruned" else ""
                    print(f"  {'    ' * depth}  {o['id']:8} {mark:7} {o['text'][:60]}{why[:70]}")
                    show(o["id"], depth + 1, seen | {fk["id"]})
        show("root", 0, frozenset())

    print(f"\nMISTAKES ({len(by['mistake'])})")
    for k in by["mistake"]:
        print(f"  {k['id']} violates {','.join(k['violates'])}: {k['text'][:90]}")
    print("\nNEXT  (every next action must move one of these)")
    for r in by["req"]:
        if latest[r["id"]] not in DONE:
            print(f"  {r['id']} ({latest[r['id']]}): {r['text'][:90]}")
    for f in open_f:
        print(f"  open finding {f['id']}: {f['claim'][:80]}")
    for fk in by["fork"]:
        if active(fk["id"]) and fk["id"] not in chosen:
            print(f"  discuss fork {fk['id']} with the user (only once the tree has converged): {fk['text'][:70]}")
    if warns:
        print("\nWARNINGS")
        for w in warns:
            print("  " + w)
    if errs:
        print("\nGRAPH ERRORS")
        for e in errs:
            print("  " + e)
        return 1
    return 2 if warns else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "progress.jsonl"))

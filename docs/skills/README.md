# Skills used in this take-home

Copies of the Claude Code skills the author used for the OakMega take-home
assignment (see the [README](../../README.md)). They are not part of the plugin.
To try one, copy its folder into a project's `.claude/skills/` directory. Some
depend on things not included here, for example: `tdd` refers to the
`codebase-design` and `code-review` skills. `quota_wait.sh` in
`adversarial-plan` and `adversarial-build` reads a usage file
(`~/.claude/usage-limits.json`) written by a separate usage-monitoring script.
Without that file it exits with an error, and a run stopped by the usage limit resumes only
through the skills' hourly scheduled check, up to an hour after the usage limit
resets. `report-text-review` is written in Chinese; `adversarial-plan` and
`adversarial-build` include Chinese trigger phrases and prompt text.

| Skill | What it does, and where it was used |
|---|---|
| [adversarial-plan](adversarial-plan/SKILL.md) | Finding the root cause of a problem and planning the change that fixes it. Used to plan this plugin. In each round, independent review agents check the root cause and the plan, until a full round finds no blocking problem. Where the plan depends on a trade-off only the author can decide, the agents review each alternative and the author picks one. |
| [adversarial-build](adversarial-build/SKILL.md) | Building the chosen plan, then having independent review agents check the code in rounds, until a full round finds no blocking problem and every requirement has evidence that it is met; then, when a real-world ground truth exists, validating against it until agreed acceptance criteria pass. Here the ground truth was the author checking the plugin by hand against the assignment's real Google Drive folder. |
| [tdd](tdd/SKILL.md) | Writing the code test-first. Used through adversarial-build for the plugin's code. |
| [report-text-review](report-text-review/SKILL.md) | Having independent review agents check the wording of a document: first whether a cold reader understands it, then what can be cut, then precision. Used on [README.md](../../README.md), [ARCHITECTURE.md](../../ARCHITECTURE.md) and a setup manual kept outside this repository. |
| [handoff](handoff/SKILL.md) | Writing a handoff document that summarises a session, so a new session can continue the work. Used to move the work from Claude Code on the web to Claude Code on the author's machine. |

`tdd` and `handoff` come from Matt Pocock's skills collection
(<https://github.com/mattpocock/skills>). The author wrote the other three.

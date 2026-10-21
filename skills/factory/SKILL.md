---
name: factory
description: "Software factory orchestrator. Run when the user invokes /factory with a feature description, or with `next`, `approve <job-id>`, or `rework <job-id> <note>`. Spawns the spec-writer, builder, four reviewers, and approver subagents through a review loop, tracking all state in factory/board.json and factory/jobs/."
---

# Software Factory

The session running this skill is the **orchestrator**. It spawns every agent,
is the only writer of `factory/board.json`, and drives jobs through the loop:

```
feature → triage → spec-writer → builder → [4 reviewers in parallel] → approver
                                       ↳ any CHANGES → resume builder (max 2 rounds)

pre-stage: idea → `/factory idea` → feature (optional)
approver: APPROVE → merge to base branch | ESCALATE → needs-human
```

## Repo facts

Every agent reads `.agents/repo-facts.md` — this repo's stack, commands,
conventions, domain skills, base branch, and dashboard path. Pass its path in
every spawn prompt; agents must follow the domain skills it lists.

## File map

Core (the factory repo — this skill's parent directory):

- `agents/<name>.md` — one file per loop role, registered globally (`~/.config/opencode/agents/`), so every project gets them. They carry no `model:` — models resolve from `models.md`.
- `models.md` — single source of truth for every agent's model; edit a row to upgrade.
- `models-changelog.md` — history of roster changes; the model-scout's memory of accepted and rejected proposals.
- `snapshots/` — dated model-data snapshots (gitignored, regenerable); the scout reads `latest.json` and `latest-diff.json`.
- `scripts/gather-models.py` — deterministic data gathering for the scout; `validate` subcommand checks model IDs.
- `models.proposed.md` / `models.proposal-notes.md` — transient scout output, applied or deleted after review.
- `docs/ADAPT.md` — the per-project adaptation prompt.

Per project:

- `.agents/repo-facts.md` — the only stack-specific file: stack, commands, conventions, domain skills, base branch, web dir.
- `.agents/skills/` — this project's domain skills.
- `factory/board.json` — durable state, drives the dashboard. Orchestrator writes only this.
- `factory/backlog.md` — feature queue for `/factory next`.
- `factory/jobs/<id>/` — `feature.md`, `clarify.md`, `spec.md`, `build.md`, `round-N/review-<name>.md`, `round-N/rework.md`, `decision.md`.
- Dashboard: `<web-dir>/factory` is a symlink to `../factory` (web dir from repo-facts.md). Recreate with `ln -s ../factory <web-dir>/factory` if missing.

To change the factory for one project, drop an override file in the project
(`.opencode/skills/factory/SKILL.md` or `.opencode/agents/<name>.md`) —
project definitions win by ID. To change it everywhere, edit the factory repo.

## Commands

### `/factory <feature>`

1. Check your own model against the `orchestrator` row in the roster
   (`models.md` in the factory repo).
   If they differ, stop before creating the job: tell the user which model to
   switch to, and wait for them to switch and confirm.
2. Create the job (below), writing the feature text to `factory/jobs/<id>/feature.md`.
3. Triage the feature (below): pick the spec-writer's model, or ask the user clarifying questions first.
4. Run the loop to completion (merged or needs-human).

### `/factory idea <idea-text>`

Turn a raw idea into a feature request. Creates no job, branch, or
`board.json` entry — purely conversational:

1. Model check, as `/factory <feature>` step 1.
2. Spawn `idea-generator` (model from the roster) with the idea text in its
   prompt.
3. `QUESTIONS` response → ask the user the numbered questions, then respawn
   once with the answers. Max one round; after that the agent proceeds with
   noted assumptions.
4. `FEATURE` response → present it inline, then offer the user: run
   `/factory <feature>` with it now, append it to `factory/backlog.md` as an
   unchecked `- [ ]` item, or keep the text.

### `/factory next`

Take the first unchecked `- [ ]` item in `factory/backlog.md`, run it as a new
job (through the same model check and triage as `/factory <feature>`), and
tick it `[x]` once the job reaches `merged` or `needs-human`.

### `/factory approve <job-id>`

For a `needs-human` job: merge its branch into the base branch (repo-facts.md),
delete the branch, set stage `merged`. The human calling this command is the approval.

### `/factory rework <job-id> <note>`

Send a job back to the builder: write the note to
`factory/jobs/<id>/round-<N+1>/rework.md` (N = current round + 1), resume the
builder session, then run review and approval from that round.

### `/factory models`

Roster review via the `model-scout` agent. Not a job: no branch, no
`board.json` entry — conversational and human-approved. Worth running weekly
or whenever model churn is expected:

1. Model check, as `/factory <feature>` step 1.
2. Spawn `model-scout` (model from the roster), passing the roster
   directory (this skill's parent directory). It refreshes `snapshots/` if
   stale, reads the roster, Policy, and changelog, then writes
   `models.proposed.md` and `models.proposal-notes.md` in the roster
   directory.
3. Read the notes; present to the user: summary, urgent warnings, the
   proposed-changes table, and the capacity-check verdict.
4. Ask the user:
   - **APPROVE** → replace `models.md` with `models.proposed.md`, append the
     changelog entry to `models-changelog.md`, run
     `python3 scripts/gather-models.py validate models.md --agents-dir .`
     from the roster directory, delete both proposal files.
   - **REJECT** → append a REJECTED entry to `models-changelog.md` (so the
     scout will not re-propose it unless facts change), delete both
     proposal files.
   - **DEFER** → leave both proposal files for the user to read.

If the `orchestrator` row changed, the new model applies from the next
session — the current session cannot switch its own model.

## Triage

Before spawning `spec-writer`, the orchestrator classifies the feature from
its text alone. The classification decides the spawn:

| Triage | Action |
| --- | --- |
| `clear` | Spawn `spec-writer` with its Primary from the roster (`models.md`). |
| `complex` | Spawn `spec-writer` with the `spec-writer (complex)` model from the roster, passed at spawn time. |
| `ambiguous` | Ask the user the clarifying questions yourself, append the answers to `feature.md`, re-classify, then spawn. |

**Complex** — escalate only when the feature meets a criterion; otherwise it
is `clear`:

- Touches 3+ layers (e.g. schema + UI + queue or external service).
- Introduces a pattern with no sibling in the repo to follow.
- Security-sensitive: auth, permissions, payments.

**Ambiguous** — ask instead of guessing when the feature text has:

- Multiple plausible interpretations (e.g. UI page vs API endpoint).
- Missing scope boundaries the builder would have to invent.
- Undefined terms that change what gets built.

Append answers to `feature.md` under a `## Clarifications` heading so the
spec-writer reads one source. **Max one clarification round per job** — if
gaps remain after the user answers, the spec-writer proceeds and records
them as assumptions (see `spec-writer.md`).

Triage runs for both entry points: `/factory <feature>` and `/factory next`.

## Job lifecycle

**Create**: id = next zero-padded sequence + slug (e.g. `004-export-csv`), looked
up from existing `factory/jobs/` entries. Branch = `factory/<id>`, cut from
the base branch (repo-facts.md). Add the job to `board.json` at stage `spec`.

1. **spec** — spawn `spec-writer` pointing at the job folder, with the model
   chosen by triage. Wait for `spec.md`. If the spec-writer writes
   `clarify.md` instead: ask the user its questions, append the answers to
   `feature.md` under `## Clarifications`, set `clarifications` to `1` in
   `board.json`, and respawn once — that spec must then produce `spec.md`,
   with remaining gaps in its *Assumptions* section.
2. **build** — spawn `builder` pointing at the job folder. Record its session id
   as `builderSession` in `board.json` (this is how any later orchestrator or
   `/factory rework` resumes the same builder). Wait for `build.md` and a
   committed branch with passing tests.
3. **review** (round N) — spawn the `reviewer-security`, `reviewer-ux`,
   `reviewer-ui`, and `reviewer-code` agents **in parallel**, each pointed at
   `spec.md`, `build.md`, the branch, and `round-N/`.
   - **WAIT FOR ALL**: the round is done only when all four `review-*.md` files
     exist. Record each verdict in `board.json` as it lands. Never resume the
     builder mid-round.
   - All PASS → step 4.
   - Any CHANGES and round < 2 → resume the same builder session (SendMessage,
     or subagent with the stored `builderSession` id) pointing at `round-N/`,
     then run round N+1 reviews. Reset reviews to `pending` each round.
   - Round 2 still has CHANGES → step 4 anyway (the approver decides).
4. **approve** — spawn `approver`. Read `decision.md`:
   - First line `APPROVE` → merge the branch into the base branch, delete it, stage `merged`.
   - First line `ESCALATE` → stage `needs-human`. Tell the user what blocked it
     and their options: `/factory approve <id>` or `/factory rework <id> <note>`.

## board.json

```json
{ "jobs": [ { "id": "003-rate-limiting", "title": "Rate limiting",
  "branch": "factory/003-rate-limiting", "stage": "review", "round": 2,
  "triage": "complex", "clarifications": 0,
  "reviews": { "security": "CHANGES", "ux": "PASS", "ui": "pending", "code": "PASS" },
  "builderSession": "ses-abc123", "updatedAt": "2026-10-19T12:00:00Z" } ] }
```

- `stage`: `spec | build | review | approve | needs-human | merged`.
- `reviews`: `pending | PASS | CHANGES` per reviewer; reset to `pending` each round.
- `triage`: `clear | complex | ambiguous` — decided once per job, before spawning spec-writer.
- `clarifications`: `0 | 1` — clarify rounds the user has answered; never exceeds 1.
- Update on every stage transition and as each verdict lands; bump `updatedAt`.
- `board.json` and `factory/jobs/` are gitignored (local job tracking). If the
  file is missing on a fresh clone, create it as `{ "jobs": [] }` before the first job.

## Models

Every agent's model lives in **`models.md` in the factory repo** (this
skill's parent directory) — the single source of truth: a Primary and a Fallback per role, plus the `orchestrator` row for the
session running `/factory`. Agent files carry no `model:` frontmatter. The
orchestrator resolves the Primary column and passes the model verbatim —
including any `#variant` effort suffix — at every spawn; triage picks the `spec-writer` or `spec-writer (complex)` row for
the spec stage. To upgrade a model, verify the new ID exists and edit the row
in `models.md` — nothing else.

**Fallbacks** — every Fallback is on a different provider than its Primary,
so one quota can't take an agent and its fallback down together. When a
spawn fails with a quota, rate-limit, or provider error:

- Retry **once** with that agent's Fallback model from `models.md`.
- Mid-run builder failure: resume the stored `builderSession` on the Fallback
  model. If the resume fails, spawn a fresh builder pointing at `build.md`
  and the branch — recover, don't restart the job.
- Every other agent: fresh respawn on the Fallback; their inputs are on disk.
- Fallback also fails: halt the loop and tell the user which agent, model,
  and error. Never retry-loop.
- If the orchestrator's own model hits a quota mid-loop: halt and tell the
  user to switch to the orchestrator Fallback from `models.md`.

## Ground rules

- Agents get context from files, not conversation history; each writes only its own file(s).
- Exception: the idea-generator runs pre-job — its input is its spawn prompt and its final response is the deliverable; it writes no files.
- Resolve every spawn's model from the Primary column of the roster (`models.md` in the factory repo) and pass it verbatim; agent files carry no model.
- On a quota or provider error, retry once with the agent's Fallback from `models.md`; if that fails too, halt and tell the user.
- Every review file starts with `VERDICT: PASS` or `VERDICT: CHANGES`;
  `decision.md` starts with `APPROVE` or `ESCALATE`.
- The builder commits on `factory/<job-id>` and tests against the spec's acceptance criteria.
- Never proceed past a review round until all four verdict files exist.

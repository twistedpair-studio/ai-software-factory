---
description: Implements a factory job on its feature branch and reports in build.md. Spawn once per job; resume the same session for rework rounds.
mode: subagent
permissions:
  - action: subagent
    resource: "*"
    effect: deny
---

# Builder

Implement the spec on the branch `factory/<job-id>` and write
`factory/jobs/<job-id>/build.md`. You are the only agent that changes code.

## Repo facts

Read `.agents/repo-facts.md` first — this repo's stack, commands, conventions,
and the domain skills to follow. Build and test exactly as it says.

## First round

1. Read `factory/jobs/<id>/feature.md` and `spec.md`.
2. `git checkout <base-branch> && git checkout -b factory/<id>` (base branch
   from repo-facts.md).
3. Implement every acceptance criterion; write a test for each, using the
   spec's suggested proof. Match sibling code conventions.
4. Run tests and pint; fix until green.
5. Commit with the job id referenced in the message.
6. Write `build.md`: changes by file (what + why), how each acceptance
   criterion is verified, test results, commit hash.

## Rework rounds (a `round-N/` folder was pointed at you)

1. Read every file in `round-N/` — the four `review-*.md` files or `rework.md`.
2. Address each CHANGES finding and rework note; keep tests green; commit.
3. Append a `## Round N` section to `build.md`: what changed and why.

Never edit `feature.md`, `spec.md`, review files, or `decision.md`.

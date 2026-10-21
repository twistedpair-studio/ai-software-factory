# Adapt the software factory to this repo

The factory core is shared — installed once per machine (the factory skill
via OpenCode config, the loop agents via `~/.config/opencode/agents/`). This
prompt adapts it to ONE repo. You write only per-project files; never edit
the factory core.

1. Inspect this repo: language, framework, test command, formatter, linter,
   naming conventions, web/public dir, and the default working branch.
2. Write `.agents/repo-facts.md` — the only stack-specific file the factory
   reads:
   - Stack and versions; package/build manager.
   - Run commands: dev, test, format.
   - Base branch for factory jobs (e.g. `develop` or `main`).
   - Conventions worth pointing reviewers at (named files or patterns).
   - Domain skills to follow — create `.agents/skills/<id>/SKILL.md` files
     for the stack's conventions (keep it minimal; one or two to start).
   - Public web dir for the dashboard symlink.
3. Scaffold `factory/`: `board.json` as `{ "jobs": [] }`, `backlog.md` with a
   short unchecked-item template, and copy `dashboard.html` from the factory
   core.
4. Symlink the dashboard into the web dir
   (`ln -s ../factory <web-dir>/factory`) and add `.gitignore` entries:
   `/factory/board.json`, `/factory/jobs`, `/<web-dir>/factory`.
5. Verify: the factory skill loads (`/factory` responds), the orchestrator
   row's model is available, and report anything that does not fit this
   repo's shape — never work around it by editing the core.

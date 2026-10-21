# Factory models

Single source of truth for every factory agent's model. Agent files carry no
`model:` frontmatter — the orchestrator resolves this table at every spawn
and passes the model explicitly. The `orchestrator` row names the model the
session running `/factory` should be on (checked at job start, see SKILL.md).

| Agent | Primary | Fallback |
| --- | --- | --- |
| orchestrator | opencode-go/glm-5.3-flash | opencode/minimax-m3.1-flash-free |
| idea-generator | opencode-go/glm-5.3 | opencode/gpt-6-luna |
| spec-writer | opencode-go/glm-5.3#max | opencode/kimi-k2.7-code |
| spec-writer (complex) | opencode/claude-sonnet-5-5#high | opencode-go/glm-5.3 |
| builder | opencode-go/glm-5.3 | opencode/kimi-k2.7-code |
| reviewer-security | opencode-go/kimi-k3#max | opencode/qwen3.8-flash |
| reviewer-code | opencode-go/gpt-6-luna | opencode/kimi-k2.7-code |
| reviewer-ux | opencode-go/gpt-6-luna | opencode/claude-haiku-5-5 |
| reviewer-ui | opencode-go/claude-haiku-5-5 | opencode/gpt-6-luna |
| approver | opencode-go/kimi-k3#max | opencode/claude-haiku-5-5 |
| model-scout | opencode-go/glm-5.3-flash | opencode/gpt-6-luna |

The default loop leans on the **OpenCode Go** group; frontier models
(Claude, GPT-6, Gemini 4) are available via the default **OpenCode** group —
e.g. `opencode/gpt-6.1-sol`, `opencode/gemini-4-argon`.

## Policy

Quota mode: shared

- Every Fallback is on a **different provider** than its Primary — a single
  OpenCode Go quota can never take an agent and its fallback down together.
- Every reviewer, and the approver, comes from a different model family than
  the builder; avoid all four reviewers sharing one family.
- The `spec-writer (complex)` fallback degrades to the default tier rather
  than another frontier model.
- A `#variant` suffix (effort level) is part of the model ID — the
  orchestrator passes it verbatim at spawn. Leave it off for the model's
  default effort; fallbacks always run at their own default effort.

Escalation roles (may hold a Zen `opencode/` primary): `spec-writer (complex)`.
Zen allowlist (kept current, never removed on cost grounds alone):
`opencode/claude-sonnet-5-5`, `opencode/claude-opus-5-5`, `opencode/gpt-6.1-sol`,
`opencode/gemini-4-argon`.

To upgrade (e.g. glm-5.3 → glm-5.4): verify the new model ID exists, then edit
the row here — nothing else. Agent frontmatter and SKILL.md never name models.

# Models changelog

History of roster changes and rejections. The model-scout reads this before
proposing; `/factory models` appends every accepted, rejected, or baseline
entry here.

## 2026-10-21 — Baseline

Roster centralised in `models.md` — agents carry no `model:` frontmatter;
the orchestrator resolves the table at every spawn. Current assignments
recorded there as the starting point, including fallbacks (all
cross-provider) and effort suffixes on the spec-writer, reviewer-security,
and approver rows.

Known at baseline: `reviewer-code` (GLM family) clashes with the builder
(GLM family) under the Policy family-diversity rule — acknowledged, left
for the model-scout to propose a fix.

## 2026-10-21 — Accepted

- reviewer-code primary: opencode-go/glm-5.3 → opencode-go/gpt-6-luna.
  Fixes the GLM-vs-GLM family clash with the builder flagged at baseline;
  relieves shared-pool load (gpt-6-luna est. 84,520 req/mo vs glm-5.3 8,640).
- No other roster change: all incumbents validated against first snapshot
  models_md_refs; no Zen removals/price changes; no newer watched-family
  Zen versions. Capacity warning at shared-pool high estimate (~13
  runs/month < 15) driven by kimi-k3 and glm-5.3 — recorded, no primary
  change proposed without benchmark-score evidence.

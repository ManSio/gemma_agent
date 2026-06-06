# gemma-deep-audit — output template

```markdown
# Audit: gemma_agent — [topic]

## What it is (one paragraph)

## Verified in repo
| Claim | Evidence (path / command) |

## Documented — trust on word
| Claim | Source | Why not verifiable |

## Strengths (with paths)
1.

## Weaknesses / tech debt
1.

## Retract if assumed earlier
-

## Scores (your independent rubric)
| Category | Score | Why |
|----------|:-----:|-----|

## Fit
- **Good for:**
- **Not for:**
```

## Suggested read order (15 min)

1. `docs/REPO_MAP.md`
2. `docs/AGENT_LOOP.md`
3. `core/orchestrator.py` + `core/brain/pipeline.py`
4. `pytest tests/test_product_behavior.py -q`
5. `docs/HONEST_POSITIONING.md`

## Offline reviewers

Paste `CHATGPT_PASTE.md` if git browse broken.

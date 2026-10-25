# CampusFix frozen pre-RAG AI baseline

Frozen on 2026-10-25 by the commit introducing this document. Its Git commit and
`evaluation/artifacts.sha256.json` identify the baseline implementation and
recorded benchmark. The preceding deployment baseline is commit `4cac79a`.

## Architecture

- FastAPI calls local Ollama **DeepSeek-R1 8B** to predict issue type and priority.
- Department comes deterministically from the cached issue taxonomy, with the
  existing Vending -> IT and dorm lockout/lost room key -> Residence Life rules.
- Successful valid decisions persist as `AUTO_ROUTED` (benchmark action name:
  `AUTO_ROUTE`). Vague, ambiguous, multiple-issue, unmatched/Other, or explicitly
  review decisions remain `NEEDS_REVIEW`. Failures become `FAILED` for review.
- Emergency protections retain the existing effective department, priority, and
  status. Existing bearer authentication, permissions, and Supabase RLS apply.
- The browser saves the ticket in Supabase first and immediately triggers an
  authenticated, fire-and-forget classification POST, before optional photo
  upload. AI has no submission deadline and no browser-side abort timer. AI or
  tunnel failure cannot invalidate the saved submission.

There is no retrieval, embedding model, vector store, RAG prompt context, tool
agent, or new model in this baseline. Routing prompts and the selected model
were not redesigned by the freeze. Qwen is historical comparison data only.

## Runtime and deployment configuration

Python 3.11+, the pinned backend requirements/lock, and local Ollama:

```dotenv
OLLAMA_URL=http://localhost:11434/api/chat
OLLAMA_MODEL=deepseek-r1:8b
OLLAMA_TIMEOUT_SECONDS=180
AI_CLASSIFICATION_ENABLED=true
CORS_ORIGINS=https://campusfizz.netlify.app,http://localhost:5173
APP_URL=https://campusfizz.netlify.app
```

Classification and enrichment share these settings. Both use temperature 0,
non-streaming requests, `think=false`, and a 30-minute model keep-alive. The
180-second timeout applies per model request, not to ticket creation. Taxonomy
is cached per process; restart after taxonomy changes. The compatibility
confidence value 1.0/0.0 is a route/review flag, not a calibrated probability.

The Netlify frontend uses build-time `VITE_API_URL=<HTTPS FastAPI tunnel URL>`.
Only FastAPI port 8000 is tunneled; Ollama 11434 stays on loopback. See
[deployment instructions](NETLIFY_CLOUDFLARE.md). Secrets stay in private local
configuration, never in Git or frontend environment variables.

## Original submission and enrichment

New ticket creation captures `original_title` and `original_text` verbatim,
including whitespace. Display `title`/`description` retain their existing trim
behavior. The additive `20261025203513_ticket_original_title.sql` migration adds a
nullable text column, captures the title for older clients that omit it, and
protects the snapshot from subsequent updates, including service-role writes.
Existing tickets remain valid with null; no enriched historical title is
misrepresented as an original, and no original title can be recovered retroactively.

After routing is persisted, separate best-effort enrichment writes cleaned
`title`, `description`, and `ai_explanation`. Its write allowlist excludes
`original_title`; existing `original_text` is preserved exactly. The existing
legacy fallback can populate missing original text. Enrichment errors never
undo routing or turn routing success into an API failure. The benchmark does
not score enrichment factuality or writing quality.

**Deployment order:** apply the committed original-title migration through the
normal Supabase migration workflow before deploying the updated frontend. The
existing `20261023023720_ticket_ai_explanation.sql` migration is also required.
This freeze does not apply SQL to the hosted database. TypeScript Row/Insert/
Update types include both nullable fields.

## Frozen evaluation

The benchmark is **synthetic**, not real campus production data: **249 cases,
187 development and 62 untouched holdout**. The holdout was used once for final
DeepSeek versus Qwen model selection and is now frozen.

| Model | Issue accuracy | Department accuracy | Priority accuracy |
|---|---:|---:|---:|
| **DeepSeek-R1 8B (selected)** | **82.3%** | **82.3%** | **64.5%** |
| Qwen3 8B (comparison only) | 80.6% | 85.5% | 54.8% |

The [evaluation archive](../evaluation/README.md) contains the exact dataset,
comparison evaluator, both original prediction files, accuracy/macro-F1/recorded
latency report, hashes, and a dependency-free offline reproduction command:

```powershell
py -3.11 evaluation/reproduce_metrics.py --check
```

This command rescores saved predictions; it never runs inference. The selected
model stays DeepSeek despite Qwen's higher recorded department accuracy and lower
recorded latency. No new holdout inference or tuning was performed for this freeze.

## Limits and future changes

The 62-case synthetic sample is small, some classes have little support, and
priority accuracy is 64.5%; review and staff judgment remain necessary. Model
outputs and polished wording can be wrong despite prompt constraints. The
confidence flag does not quantify uncertainty. Recorded DeepSeek inference averaged
about 19.37 seconds in the saved run; cold starts/enrichment may take longer.
Cloudflare can drop a response, and the browser trigger is best effort rather
than a durable queue. Inspect persisted ticket state/backend logs before retrying.

Historical model tags were recorded, but immutable weights, Ollama version, and
hardware were not; new inference cannot be promised bit-identical. Frozen metrics
can be reproduced exactly from the saved files. This is a routing comparison,
not an end-to-end safety, enrichment, or real-world performance claim.

Keep these frozen dataset labels, evaluator, predictions, metrics, and hashes
unchanged. Do not use holdout texts/labels to tune prompts, rules, thresholds,
models, or future RAG. Iterate with development/new data and reserve a new unseen
holdout for future final evaluation. CI may verify hashes and rescore frozen
predictions; it must never call the historical inference evaluator. Future RAG
must be an explicit, separately evaluated change measured against this baseline.

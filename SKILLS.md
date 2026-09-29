# Project Workflows

This is an on-demand guide for working on Auto Equity, linked from [AGENTS.md](AGENTS.md).
Read only the workflow relevant to the task. It is **not an auto-discovered Codex skill**:
an installed skill has its own `skill-name/SKILL.md` with `name` and `description`
frontmatter. See [the official skill guide](https://learn.chatgpt.com/docs/build-skills).

## Before running commands

Commands below run from the repository root. Container commands require Docker Compose,
the project environment, and the named service running with the code under test.
Images **copy** source; they do not mount it. Rebuilding an image alone does not update an
existing container. Rebuild and recreate affected services only within an authorized
development environment; do not interrupt the user's running session to test documentation.

Use the checked-in dependency locks and Python 3.12 for backend work. Presentation tests
require Node 22+. Do not print `.env`, full resolved Compose configuration, or credentials.
Use synthetic cases and documents for verification. Provider-connected tests require the
user's authorization for the provider, scope, and cost; ordinary unit tests do not imply it.

## 1. Domain and API changes

Use when changing eligibility, declarations, offers, document rules, transitions, or HTTP
contracts. Start with the relevant rule in [docs/auto_equity_TDD.md](docs/auto_equity_TDD.md)
and its [decisions](docs/auto_equity_TDD.md#14-decisiones-y-trade-offs-vigentes); distinguish implemented policy from an
assumption or a historical design.

- Add a failing behavioral test, implement the smallest change, then refactor.
- Keep pure rules in `app/domain/`, orchestration in `app/tools/`, and HTTP adaptation in
  `app/api/`. Use `app/domain/clock.py` for time-sensitive rules.
- Cover the relevant negative path as well as success: authorization, stale versions,
  replay/idempotency, invalidation, or recovery. Do not bypass the API's final readiness gate.
- Choose focused tests first, then expand to affected boundaries:

```bash
docker compose exec -T api python -m pytest tests/unit -q
docker compose exec -T api ruff check .
```

Integration tests need PostgreSQL and MinIO. **Before running them**, verify the configured
database server is a disposable development/test target and the derived `<db>_test` database
is safe to replace. `tests/integration/conftest.py` drops and recreates that database, applies
migrations, and writes test objects under the `test/` storage namespace. Do not run concurrent
suites against the same derived database.

```bash
docker compose exec -T api python -m pytest tests/integration -q
```

## 2. Conversation and customer UI changes

Use when changing capture, tool calls, approval cards, attachments, streaming, or visible
responses. Read [conversation/README.md](conversation/README.md) and the relevant controls in
[TDD §5.2](docs/auto_equity_TDD.md#52-contexto-conversación-y-confirmaciones).

- Edit the graph in `conversation/server/graph.py`; the API remains the source of truth.
  The model proposes data; only the customer's approval commits declarations or selections.
- Preserve Spanish customer-facing language, progressive questions, complete approval cards,
  and exact offer values. Do not expose internal status codes or claim approval/disbursement.
- Check rejection, correction, interrupted approval, and provider failure when relevant.
  Attachments belong to document extraction, not the conversational model's context.
- The Next.js application is fetched and patched by `conversation/chat-ui/Dockerfile`.
  Inspect its pinned upstream and patch scripts before editing; there is no root `npm test`.

```bash
docker compose exec -T agent python -m unittest -q test_graph
node --test conversation/chat-ui/customer-view.test.mjs
```

The graph tests run in the **agent** image, not the API image. API integration coverage is in
`tests/integration/test_chat_agent.py`, subject to the database precautions above.
`tests/e2e_ui` covers the **advisor** browser screen, not the customer chat. Follow the
[browser prerequisites](docs/auto_equity_TDD.md#114-verificación-aislada-y-ci) in an authorized
test stack; its live API must use fake extraction and disabled telemetry to avoid paid calls.
For real chat verification, agree the cost first and exercise separate user turns and actual
approval/upload interactions; a scripted graph pass is not evidence of model quality.

## 3. Provider and extraction evaluation

Use when changing provider adapters, model configuration, extraction prompts/schemas, or
evaluating model quality. Read [provider configuration](docs/auto_equity_TDD.md#112-proveedores-de-ia)
and the [evaluation design](docs/auto_equity_TDD.md#8-estrategia-de-evaluación-y-pruebas).

- Chat and extraction have independent provider/model settings. Preserve the user's chosen
  configuration; do not switch models or enable automatic fallback to hide a failure.
- Run transport/adapter regressions before requesting paid evaluation:

```bash
docker compose exec -T api python -m pytest tests/unit/test_chat_transport.py tests/unit/test_extraction_adapter.py tests/unit/test_real_ai_runner.py -q
```

- Tune on the development split only. Never send labels to the extractor or train/tune on
  the holdout. If inspected holdout failures influence changes, use a new independent holdout
  for the next quality claim. Keep all repetitions and do not lower acceptance thresholds.
- A controlled model comparison keeps documents, prompt, schema, rules, and grading fixed.
  Structured output and self-reported confidence are not independent proof of correctness.
- After explicit authorization, use a fresh output directory and record provider/model,
  prompt version, code revision plus local changes, dataset identity, and usage. This command
  invokes the configured real extraction provider; it does not restart the stack:

```bash
docker compose run --rm --no-deps -T -e LANGFUSE_ENABLED=false api \
  python -m scripts.run_evals real_ai --split holdout --repeats 3 \
  --out /data/verification-real-ai-NEW-RUN
```

Replace `NEW-RUN` with an unused run identifier. Inspect the generated report and exit code:
the real-AI CLI returns 0 for `PASS`, 1 for `FAIL`, and 2 for `NOT_RUN`. Gates require zero
case runs with extraction errors, field accuracy at least 95%, abstention 100%, zero false
documentary OKs, and valid completion at least 90%.
Report chat, deterministic orchestration, and extraction quality separately. The
[TDD evidence and historical results](docs/auto_equity_TDD.md#84-evidencia-y-limitaciones) record a
historical **FAIL** for that configuration; neither it nor earlier passes certifies a new run.

## 4. Documentation and decisions

Use when documenting behavior, architecture trade-offs, operating procedures, or evidence.

- Keep `README.md` concise and `docs/` limited to `auto_equity_TDD.md` and `entrega.md`.
  Put contracts, procedures, evidence, and detailed rationale in the TDD; keep the submission
  short, with diagrams and the challenge's six technical points. Link instead of duplicating.
- For a substantive design decision, record context, choice, alternatives/trade-offs, and
  consequences in TDD §14. Do not add a separate ADR for every wording correction.
- Ground commands and claims in current source. Update dated evidence in TDD §8.4 and separate
  observed results, assumptions, untested claims, and limitations. Preserve historical results
  in dated TDD summaries and the existing raw reports, not links to commits removed when
  consolidating the delivery history or duplicate active guides.
- Write new agent guidance in English. Preserve existing document languages and Spanish
  customer copy unless translation is requested. Edit PDF source and render/inspect output
  when a PDF update is explicitly in scope.
- For documentation-only changes, check links, paths, command prerequisites, and
  `git diff --check`; do not launch the stack or paid evaluations without a relevant reason.

## 5. Operations and observability diagnosis

Use when investigating readiness, failures, latency, usage, or trace correlation. Read
[TDD operations](docs/auto_equity_TDD.md#11-despliegue-y-ejecución) and
[observability](docs/auto_equity_TDD.md#10-observabilidad).

- Start with service status (`docker compose ps`) and scoped health/log checks. Distinguish
  configuration, provider transport, model output quality, and domain rejection.
- Correlate API, graph, and extraction events using the existing tracing context. Inspect
  sanitized local JSONL before involving optional external telemetry; do not upload customer
  documents, tokens, or raw conversation text as diagnostic evidence.
- Budget counters and price-based cost estimates are not provider invoices. Mark missing
  price data or external trace verification as unknown, not zero cost or successful export.
- Storage reconciliation defaults to a report; deletion is a separate authorized action.
  Never use volume removal as a routine fix. Restarting services does not reload `.env`.
- The deterministic evaluator is not read-only: `scripts/run_evals.py` drops/recreates
  `<db>_eval` and writes under `eval/`. Verify that derived database is disposable before
  using the commands in the operations guide; keep reports in a new directory.

For observability code changes, include the applicable unit and privacy regressions:

```bash
docker compose exec -T api python -m pytest tests/unit/test_observability.py tests/security/test_telemetry_privacy.py -q
```

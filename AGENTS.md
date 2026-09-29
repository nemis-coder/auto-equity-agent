# Repository instructions

These instructions apply throughout this repository. Read [SKILLS.md](SKILLS.md) on demand
for task-specific workflows and validation commands; it is a manual guide, not an
automatically discovered agent skill.

## Scope and source of truth

- This is an interview challenge prototype for preparing an auto-equity credit application,
  not a production lending system. Policies, bureau responses, and quotations are synthetic.
- Read `README.md` for orientation, `tdd/challenge_auto_equity_agent.pdf` for original
  requirements, and `docs/entrega.md` for the short submission. `docs/auto_equity_TDD.md`
  consolidates the technical design, operations (§11), and decisions/trade-offs (§14).
- Consult the TDD contract and current decision before changing behavior. This delivery
  starts from one consolidated commit; earlier numbered decisions are not part of its history.
  Keep `docs/` limited to the TDD and the short submission.
- Inspect source and tests to establish current behavior. TDD §8.4 identifies measured
  evidence, configurations, dates, and limitations. Historical results and README
  summaries are not proof of the current working tree's behavior.
- Keep these agent instructions and `SKILLS.md` in English. Preserve the application's
  Spanish customer-facing language and the surrounding language of existing documentation.

## Architecture and ownership

- `conversation/server/graph.py`: the customer-facing LangGraph agent, served by
  Aegra. It gathers data, proposes actions, requests human confirmation, and calls the API.
- `conversation/chat-ui/`: adaptations to a pinned upstream Agent Chat UI, applied during
  its Docker build. Change the checked-in patches and components, not just container files.
- `app/api/`: FastAPI transport, schemas, authentication integration, and the advisor UI.
  Keep routes thin; do not move business rules into presentation code.
- `app/domain/`: deterministic business rules and the final readiness decision. Keep these
  functions independent of database, HTTP, and model calls.
- `app/tools/`: authorized application operations, workflow progression, persistence,
  provider orchestration, and final evidence gathering under the case lock.
- `app/providers/`: simulated external services and real model/extraction adapters.
  `app/persistence/` owns SQLAlchemy models; `migrations/` owns Alembic migrations.
- `app/observability/`: sanitized tracing and usage/cost accounting. Langfuse is optional;
  its failure must not prevent business operations.
- PostgreSQL owned by the API is authoritative for applications and audit history. Aegra's
  separate PostgreSQL database holds conversation state; MinIO holds private documents.
- Keep the existing architecture. Do not reintroduce Streamlit, a second application
  agent, gateways, queues, or parallel business-rule implementations without an explicit need
  and a documented decision.

## Invariants to preserve

- The model proposes and extracts; deterministic backend code enforces eligibility,
  authorization, document rules, and readiness. Never let model text authorize a transition.
- Persist confirmed declarations and offer selections only through the existing explicit
  human-approval flow. Do not infer consent or allow the model to approve its own proposal.
- Treat `false` as an answered field, not missing data. Preserve progressive questions and
  the server's complete-vehicle proposal check, including early disqualifying-answer cases.
- Customer messages must not expose internal workflow codes or promise approval,
  disbursement, or contact that is not implemented. Ready for financial review is not approval.
  Preserve server-side checks before publishing customer text, not just UI filtering.
- Authorization comes from the authenticated execution context, never model arguments.
  Preserve case isolation, role checks, and non-disclosing responses for inaccessible cases.
- Preserve command idempotency, optimistic version checks, case locking, append-only audit,
  dependent-evidence invalidation, and final readiness recalculation against current evidence.
- Keep provider calls outside case locks and preserve reconciliation of uncertain outcomes.
  Bound retries and account for model usage; do not bypass budgets or substitute rejection
  for a technical failure that requires recovery or human review.
- Treat uploaded document contents and model outputs as untrusted data. Keep sanitization,
  nullable missing fields, evidence checks, and deterministic validation. Structured output
  and model-reported confidence do not establish that an extracted value is true.
- Chat and extraction providers/models are configured independently. Preserve explicit
  selection and no silent fallback; an API key alone must not select a provider.

## Change and verification workflow

- Inspect `git status --short` and relevant diffs first. Preserve all unrelated user changes,
  generated artifacts, and local evidence. Never reset, clean, or overwrite them to simplify work.
- Match the request: review and diagnosis do not authorize implementation. Keep changes
  small and localized; use existing abstractions before adding another layer.
- For behavior changes, add a focused regression that fails for the observed defect, make
  the smallest fix, and rerun relevant tests. Refactor only with the regression still passing.
- Use the validation matrix in `SKILLS.md`. Run targeted checks first, then broader checks
  when changing shared contracts, workflow, persistence, or provider behavior. Documentation-only
  changes need link/content and diff checks, not paid models or service restarts.
- Integration tests recreate a derived `_test` database; deterministic evaluations recreate
  a derived `_eval` database. Verify the target is a disposable local test database before
  running either. Do not run these against shared or production-like data.
- Confirm the tested container contains the edited source; Compose images do not automatically
  reflect host edits. Report exactly what ran, what passed, and what remains unverified.
- Distinguish unit/contract tests, scripted-agent evaluations, browser checks, and real-model
  quality evaluations. Inspect report status and exit code: `NOT_RUN` is not a passing evaluation.
- For real-model work, preserve dataset separation, thresholds, and all repetitions. Never
  send expected labels to the extractor or lower thresholds to manufacture a passing result.
  If tuning uses a holdout, reserve fresh cases for independent validation.
- The documented OpenAI holdout failure in `docs/auto_equity_TDD.md` §8.4
  remains a known limitation until superseded by comparable evidence. Do not claim it is resolved
  based on a happy path, structured JSON, or a historical result from another configuration.
- Update relevant operation/design documentation when behavior changes. Record material
  decisions and trade-offs in TDD §14, summarizing material changes in `docs/entrega.md`;
  keep the README brief and preserve dated evidence in TDD §8.4.

## Operational and privacy boundaries

- Do not print, commit, or embed `.env`, API keys, demo access tokens, full environment dumps,
  raw customer conversations, or personal document contents in logs or reports. Use synthetic
  inputs and the existing trace sanitizers; inspect only the minimum necessary configuration.
- Do not change provider/model settings, run paid model evaluations, send data to external
  telemetry, rotate credentials, or restart services unless authorized by the current task.
- Preserve database/object-store volumes. Destructive cleanup, data migration, and credential
  changes require an understood target and appropriate authorization.
- Do not commit or push unless requested. Before doing so, review the exact staged diff and
  exclude unrelated changes, secrets, and temporary verification artifacts.

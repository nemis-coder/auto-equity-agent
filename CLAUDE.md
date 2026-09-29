# Claude Code instructions

Use the same repository instructions as the other coding agents. The import below includes
the project's scope, architecture, invariants, TDD workflow, validation requirements,
privacy rules, and operational boundaries without maintaining a separate copy.

@AGENTS.md

## Task-specific workflows

Read the relevant section of [SKILLS.md](SKILLS.md) when choosing implementation or
verification steps. It is an on-demand workflow guide, not an installed Claude Code skill.
Do not load unrelated workflows or run their commands merely because they are documented.

## Maintaining this entry point

- Keep shared repository rules in `AGENTS.md` and detailed procedures in `SKILLS.md`.
  Put only Claude Code-specific guidance here; do not duplicate or contradict shared rules.
- Keep this file in English while preserving the application's Spanish customer responses.
- This file configures the coding assistant's project context, not the application's LLM
  provider. It does not authorize switching chat or extraction to Anthropic.

The import uses Claude Code's documented
[memory-file syntax](https://code.claude.com/docs/en/memory#import-additional-files).

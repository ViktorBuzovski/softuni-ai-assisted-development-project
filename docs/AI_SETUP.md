# AI tooling and setup

What was used to build this project. It records only what was actually run.

## Tools

| | Claude Code | Codex |
| --- | --- | --- |
| Interface | VS Code extension | Command line (`codex exec`), installed with npm |
| Version | 2.1.314 (newest installed) | `codex-cli` 0.166.0 |
| Model | Opus 5.5 (`claude-opus-5-5`) | `gpt-6-astra` |
| Effort | `xhigh` | `high` |
| Used for | Planning, modules 1–3, fixes after each review, the exam document | Module 4, and the review of modules 2–3 |
| Sandbox | None | `read-only` for the review, `workspace-write` for module 4 |

Claude Code's model and effort come from `~/.claude/settings.json`. Codex's model comes from `~/.codex/config.toml`, and its effort is set on each run with `-c model_reasoning_effort="high"`.

## Invoice extraction at runtime

This proof of concept runs on a Claude Code subscription through the `claude` command (version 2.1.300), not on a paid API key. `ANTHROPIC_API_KEY` in `.env.example` is a dummy placeholder and isn't used, and the app removes it from the command's environment.

The running app uses Opus 5.5 (`claude-opus-5-5`) at `medium` effort. Both can be changed with `EXTRACTION_MODEL` and `EXTRACTION_EFFORT`.

## Environment

- macOS on Apple silicon
- VS Code
- Python 3.12, managed with uv 0.13.6
- Node 22.23.3, used to install Codex
- Google Chrome, driven by Playwright 1.64.1 for the browser tests and for rendering the sample invoices
- GitHub CLI 2.101.0

## Project instruction files

| File | Read by | What it holds |
| --- | --- | --- |
| `AGENTS.md` | Codex, and Claude Code through `CLAUDE.md` | Commands, layout, project decisions and working rules |
| `CLAUDE.md` | Claude Code | Imports `AGENTS.md`, so both tools share one set of instructions |

## Agents

No subagents were used.

## Skills

| Skill | Tool | Used for |
| --- | --- | --- |
| `docx` | Claude Code | Reading the assignment document |
| `claude-api` | Claude Code | Choosing the default extraction model |
| `invoice-extraction` | Claude Code and Codex | This project's own skill, for work on extraction and on showing extracted fields |
| `docs` | Claude Code | Writing the exam document |

`invoice-extraction` has one copy in `.claude/skills/` and one in `.agents/skills/`, and a test keeps them identical. Both tools were asked to confirm that they list it, using `claude -p` and `codex exec`.

## Usage by stage

| Stage | Tool | Model | Effort | Agents | Skills | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Planning | Claude Code | Opus 5.5 | `xhigh` | None | `docx` | Done |
| Module 1: Upload and storage | Claude Code | Opus 5.5 | `xhigh` | None | None | Done |
| Module 2: AI extraction | Claude Code | Opus 5.5 | `xhigh` | None | `claude-api`, `invoice-extraction` | Done |
| Module 3: Invoice records API | Claude Code | Opus 5.5 | `xhigh` | None | `invoice-extraction` | Done |
| Review of modules 2–3 | Codex | `gpt-6-astra` | `high` | None | None | Done |
| Module 4: Web UI | Codex | `gpt-6-astra` | `high` | None | `invoice-extraction` | Done |
| Browser check and fixes for module 4 | Claude Code | Opus 5.5 | `xhigh` | None | `invoice-extraction` | Done |
| Exam document | Claude Code | Opus 5.5 | `xhigh` | None | `docs` | Done |

## How the tools checked each other

| Check | State of the author's tests | Result |
| --- | --- | --- |
| Codex reviewed modules 2 and 3, written by Claude Code, in a read-only sandbox | 92 passed | 5 defects and 1 possible risk: 4 fixed, 2 left with the reasons in [prompt 3.2](PROMPTS.md#prompt-32-review-by-a-second-tool-codex) |
| Claude Code used module 4, written by Codex, in real Chrome with real extraction on all five samples | 108 passed, 4 skipped, because Codex's sandbox couldn't start Chrome | 1 failing browser test, an oversized status badge, quantities shown as money, no way forward for an invoice left in `uploaded`, and total and status off-screen at phone width, all fixed in [prompt 4.2](PROMPTS.md#prompt-42-check-the-page-in-a-real-browser-and-fix-what-codex-couldnt-see-claude-code) |

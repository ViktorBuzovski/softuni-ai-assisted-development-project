# AI tooling and setup

What was used to build this project, kept up to date as the work goes on. It records only what was actually run.

## Tools

| | Claude Code | Codex |
| --- | --- | --- |
| Interface | VS Code extension | Command line (`codex exec`), installed with npm |
| Version | 2.1.314 (newest installed) | `codex-cli` 0.166.0 |
| Model | Opus 5.5 (`claude-opus-5-5`) | `gpt-6-astra` |
| Effort | `xhigh` | `high` |
| Used for | Planning, modules 1–3 | Module 4 |
| Status | In use | Installed and tested, not used for a module yet |

Claude Code's model and effort come from `~/.claude/settings.json`. Codex's model comes from `~/.codex/config.toml`, and its effort is set on each run with `-c model_reasoning_effort="high"`.

## Invoice extraction at runtime

This proof of concept runs on a Claude Code subscription through the `claude` command (version 2.1.300), not on a paid API key. `ANTHROPIC_API_KEY` in `.env.example` is a dummy placeholder and isn't used, and the app removes it from the command's environment.

The running app uses Opus 5.5 (`claude-opus-5-5`) at `medium` effort. Both can be changed with `EXTRACTION_MODEL` and `EXTRACTION_EFFORT`.

## Environment

- macOS on Apple silicon
- VS Code
- Python 3.12, managed with uv 0.13.6
- Node 22.23.3, used to install Codex
- GitHub CLI 2.101.0

## Project instruction files

| File | Read by | What it holds |
| --- | --- | --- |
| `AGENTS.md` | Codex, and Claude Code through `CLAUDE.md` | Commands, layout, project decisions and working rules |
| `CLAUDE.md` | Claude Code | Imports `AGENTS.md`, so both tools share one set of instructions |

## Agents

No subagents used so far.

## Skills

| Skill | Tool | Used for |
| --- | --- | --- |
| `docx` | Claude Code | Reading the assignment document |
| `claude-api` | Claude Code | Choosing the default extraction model |
| `invoice-extraction` | Claude Code and Codex | This project's own skill, for work on extraction and on showing extracted fields |

`invoice-extraction` has one copy in `.claude/skills/` and one in `.agents/skills/`, and a test keeps them identical. Both tools were asked to confirm that they list it, using `claude -p` and `codex exec`.

## Usage by stage

| Stage | Tool | Model | Effort | Agents | Skills | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Planning | Claude Code | Opus 5.5 | `xhigh` | None | `docx` | Done |
| Module 1: Upload and storage | Claude Code | Opus 5.5 | `xhigh` | None | None | Done |
| Module 2: AI extraction | Claude Code | Opus 5.5 | `xhigh` | None | `claude-api`, `invoice-extraction` | Done |
| Module 3: Invoice records API | Claude Code | Opus 5.5 | `xhigh` | None | `invoice-extraction` | Done |
| Module 4: Web UI | Codex | – | – | – | – | Not started |

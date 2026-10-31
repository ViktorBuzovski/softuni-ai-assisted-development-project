# AI tooling and setup

What was used to build this project, kept up to date as the work goes on. It records only what was actually run.

## Tools

| | Claude Code | Codex |
| --- | --- | --- |
| Interface | VS Code extension | VS Code extension (`openai.chatgpt`) |
| Version | 2.1.314 (newest installed) | 26.1021.22027 (newest installed) |
| Model | Opus 5.5 (`claude-opus-5-5`) | `gpt-6-astra` |
| Effort | `xhigh` | `low` |
| Used for | Planning, modules 1–3 | Module 4 |
| Status | In use | Not used yet |

Model and effort come from each tool's settings file (`~/.claude/settings.json` and `~/.codex/config.toml`).

## Environment

- macOS on Apple silicon
- VS Code
- GitHub CLI 2.101.0
- uv 0.13.6

## Project instruction files

None yet.

## Agents

No subagents used so far.

## Skills

| Skill | Tool | Used for |
| --- | --- | --- |
| `docx` | Claude Code | Reading the assignment document |

## Usage by stage

| Stage | Tool | Model | Effort | Agents | Skills | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Planning | Claude Code | Opus 5.5 | `xhigh` | None | `docx` | In progress |
| Module 1: Upload and storage | Claude Code | – | – | – | – | Not started |
| Module 2: AI extraction | Claude Code | – | – | – | – | Not started |
| Module 3: Invoice records API | Claude Code | – | – | – | – | Not started |
| Module 4: Web UI | Codex | – | – | – | – | Not started |

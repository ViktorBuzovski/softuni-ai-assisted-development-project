from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_both_tools_get_the_same_skill():
    claude_copy = ROOT / ".claude/skills/invoice-extraction/SKILL.md"
    codex_copy = ROOT / ".agents/skills/invoice-extraction/SKILL.md"

    assert claude_copy.read_text() == codex_copy.read_text()

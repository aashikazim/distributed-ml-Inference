from pathlib import Path

from tools.check_docs import Block, lint, lint_block


def test_repository_docs_pass_static_render_checks() -> None:
    assert lint(Path(".")) == []


def test_semicolon_in_sequence_message_is_reported() -> None:
    bad = Block(Path("x.md"), 1, "sequenceDiagram\n    A->>B: XACK; XDEL\n")
    assert len(lint_block(bad)) == 1


def test_semicolons_outside_sequence_messages_are_allowed() -> None:
    flow = Block(Path("x.md"), 1, "flowchart LR\n    A[Validate; drop] --> B\n")
    note = Block(Path("x.md"), 1, "sequenceDiagram\n    A->>B: XACK, then XDEL\n")
    assert lint_block(flow) == []
    assert lint_block(note) == []

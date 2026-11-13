"""Static checks for Markdown docs so they keep rendering on GitHub.

Catches the cheap, known failure classes without a browser:

* Mermaid ``sequenceDiagram`` messages containing ``;`` (Mermaid treats ``;`` as a
  statement separator, so the rest of the line becomes a syntax error).
* Unbalanced code fences and ``<details>`` blocks.
* Relative links and images that point at files which do not exist.

Full rendering is checked separately by ``scripts/render-mermaid.sh`` (real
Mermaid CLI). ``--extract DIR`` writes every Mermaid block to ``DIR`` for it.
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MERMAID = re.compile(r"^```mermaid\n(.*?)^```", re.M | re.S)
FENCE = re.compile(r"^\s*```", re.M)
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
INLINE_CODE = re.compile(r"`[^`\n]*`")
# Participant -> participant arrows used in sequence diagrams.
SEQ_MESSAGE = re.compile(r"^\s*[\w ]+?\s*(?:-{1,2}(?:>>|>|x|\))[+-]?)\s*[\w ]+?\s*:(.*)$")


@dataclass(frozen=True)
class Block:
    path: Path
    line: int
    source: str


def markdown_files(root: Path) -> list[Path]:
    files = sorted((root / "docs").rglob("*.md"))
    readme = root / "README.md"
    return [readme, *files] if readme.exists() else files


def mermaid_blocks(path: Path) -> list[Block]:
    text = path.read_text()
    return [
        Block(path, text.count("\n", 0, match.start()) + 1, match.group(1))
        for match in MERMAID.finditer(text)
    ]


def lint_block(block: Block) -> list[str]:
    lines = block.source.splitlines()
    if not lines or not lines[0].strip().startswith("sequenceDiagram"):
        return []
    problems = []
    for offset, line in enumerate(lines[1:], start=1):
        message = SEQ_MESSAGE.match(line)
        if message and ";" in message.group(1):
            problems.append(
                f"{block.path}:{block.line + offset + 1}: ';' in a sequence message "
                f"ends the statement early: {line.strip()!r}"
            )
    return problems


def lint_file(path: Path) -> list[str]:
    text = path.read_text()
    problems = []
    if len(FENCE.findall(text)) % 2:
        problems.append(f"{path}: unbalanced ``` code fences")
    prose = INLINE_CODE.sub("", re.sub(r"^```.*?^```", "", text, flags=re.M | re.S))
    if prose.count("<details>") != prose.count("</details>"):
        problems.append(f"{path}: unbalanced <details> blocks")
    for match in LINK.finditer(prose):
        target = match.group(1).split("#", 1)[0]
        if not target or re.match(r"^[a-z]+:", target):
            continue
        if not (path.parent / target).exists():
            problems.append(f"{path}: broken relative link {match.group(1)!r}")
    for block in mermaid_blocks(path):
        problems.extend(lint_block(block))
    return problems


def lint(root: Path) -> list[str]:
    return [problem for path in markdown_files(root) for problem in lint_file(path)]


def extract(root: Path, target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in markdown_files(root):
        for block in mermaid_blocks(path):
            count += 1
            name = f"{count:03d}__{str(path.relative_to(root)).replace('/', '__')}__L{block.line}"
            (target / f"{name}.mmd").write_text(block.source)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--extract", type=Path, help="write Mermaid blocks to this directory")
    args = parser.parse_args()
    if args.extract:
        sys.stdout.write(f"extracted {extract(args.root, args.extract)} Mermaid blocks\n")
        return
    problems = lint(args.root)
    for problem in problems:
        sys.stderr.write(problem + "\n")
    if problems:
        sys.exit(1)
    sys.stdout.write("docs lint passed\n")


if __name__ == "__main__":
    main()

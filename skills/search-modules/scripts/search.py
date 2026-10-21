#!/usr/bin/env python3
"""Rank portable_skill modules by frontmatter keyword hits. Python stdlib only."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def catalog_root() -> Path:
    env = os.environ.get("PORTABLE_SKILL_DIR", "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser())
    script = Path(__file__).resolve()
    # skills/search-modules/scripts/search.py -> catalog root
    if len(script.parents) >= 4:
        candidates.append(script.parents[3])
    candidates.append(Path.home() / "portable_skill")
    for candidate in candidates:
        if (candidate / "skills" / "search-modules" / "SKILL.md").is_file():
            return candidate
    return candidates[-1]


def parse_scalar(raw: str):
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        parts = []
        for piece in inner.split(","):
            piece = piece.strip().strip("\"'")
            if piece:
                parts.append(piece)
        return parts
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict = {}
    index = 1
    while index < len(lines):
        line = lines[index]
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if ":" not in line:
            index += 1
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        if rest in {">", "|", ">-", "|-"}:
            index += 1
            chunks: list[str] = []
            while index < len(lines):
                nxt = lines[index]
                if nxt.strip() == "---":
                    break
                if nxt and not nxt[0].isspace():
                    break
                chunks.append(nxt.strip())
                index += 1
            data[key] = " ".join(chunk for chunk in chunks if chunk)
            continue
        data[key] = parse_scalar(rest)
        index += 1
    return data


def as_text(value) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def score_module(frontmatter: dict, terms: list[str]) -> int:
    fields = {
        "name": as_text(frontmatter.get("name", "")).lower(),
        "title": as_text(frontmatter.get("title", "")).lower(),
        "summary": as_text(frontmatter.get("summary", "")).lower(),
        "stack": as_text(frontmatter.get("stack", "")).lower(),
        "tags": as_text(frontmatter.get("tags", "")).lower(),
    }
    weights = {"name": 5, "title": 3, "tags": 3, "stack": 2, "summary": 1}
    score = 0
    for term in terms:
        for field, weight in weights.items():
            if term in fields[field]:
                score += weight
    phrase = " ".join(terms)
    if phrase and phrase in fields["summary"]:
        score += 2
    return score


def main(argv: list[str]) -> int:
    terms = [arg.lower() for arg in argv if arg.strip()]
    if not terms:
        print("Usage: search.py <keyword> [keyword ...]", file=sys.stderr)
        return 2
    root = catalog_root()
    modules = root / "modules"
    hits: list[tuple[int, str, dict, Path]] = []
    if modules.is_dir():
        for path in sorted(modules.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            frontmatter = parse_frontmatter(text)
            score = score_module(frontmatter, terms)
            if score <= 0:
                continue
            name = as_text(frontmatter.get("name", path.stem))
            hits.append((score, name, frontmatter, path))
    hits.sort(key=lambda item: (-item[0], item[1]))
    if not hits:
        print("No matching modules.")
        return 0
    for score, name, frontmatter, path in hits:
        title = as_text(frontmatter.get("title", ""))
        summary = as_text(frontmatter.get("summary", ""))
        print(f"{score}\t{name}\t{title}")
        if summary:
            print(summary)
        print(path)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

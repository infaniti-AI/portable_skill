---
name: search-modules
description: Locate reusable capability modules in the portable_skill catalog and load them before implementing a feature. Use this whenever work might already be covered by a module — auth, billing, storage, queues, search, analytics, email, or deploy — and before writing that capability from scratch.
---

# Search modules

This skill is the entry point for the catalog. Do the steps in order. Do not skip the `requires` closure.

## 1. Find the clone

Resolve the catalog root:

1. If `PORTABLE_SKILL_DIR` is set and that directory exists, use it.
2. Otherwise, if `~/portable_skill` exists, use it.
3. Otherwise clone it:

```bash
git clone https://github.com/infaniti-AI/portable_skill ~/portable_skill
```

Optional update, only when the worktree is clean and a newer catalog is wanted:

```bash
git -C "$ROOT" pull --ff-only
```

`$ROOT` is the directory chosen above. Do not clone into a source application repo.

## 2. Search frontmatter

Prefer the bundled script (Python 3 standard library, no installs). From any working directory:

```bash
python3 "$ROOT/skills/search-modules/scripts/search.py" auth billing
```

Arguments are keywords. Matching is case-insensitive against each module's `name`, `title`, `summary`, `stack`, and `tags`. The script prints matches with the highest score first. Exit 0 means the search ran, including when nothing matched.

If `rg` is available, a manual pass over the same fields is:

```bash
rg -n --glob '*.md' '^(name|title|summary|stack|tags):' "$ROOT/modules"
```

Use the script's ranking when both are available. Read-only: do not rewrite modules during a search.

## 3. Read matches and apply them

1. Open the top matches in full. A score lead of one or two keywords is enough to read two or three files; read more when the request spans several tags.
2. Collect every name in those files' `requires` lists. Open those modules too, then repeat until no new names appear.
3. Follow the module's architecture, env var names, schema, setup order, and checklist. Where a module says `> NOTE: unverified`, confirm that point in current vendor docs before relying on it.
4. Implement in the target project with that project's names. Keep catalog examples generic; do not copy them in as product identifiers.
5. If nothing matches, say so and implement from scratch. Do not invent a module that is not in `modules/`.

## Script output

`search.py` prints one block per hit: score, module name, title, summary, and the file path. A line `No matching modules.` means the catalog has no hit for those keywords.

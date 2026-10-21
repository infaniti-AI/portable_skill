# portable_skill

Product-agnostic modules and agent skills. Each module is one reusable capability. Skills are workflows that point at those modules.

## Install

1. Put the catalog on disk:
   - Missing: `git clone https://github.com/infaniti-AI/portable_skill ~/portable_skill`
   - Already cloned: `git -C ~/portable_skill pull --ff-only`
   - Or set `PORTABLE_SKILL_DIR` to a clone and pull that path. Skip the pull when the worktree is dirty.
2. Symlink each directory under `skills/` into the user-level skills directory this agent already loads. Link the whole folder so `scripts/` stays next to `SKILL.md`.
3. Requirements: Python 3 standard library. `rg` is optional and recommended. No other packages.

## Layout

- `modules/<name>.md` — capability: architecture, env names, schema, setup, failure prevention, tests, checklist.
- `skills/search-modules/` — start here. Search, then read the best modules and everything they `requires`.
- `skills/<name>/` — a workflow that combines modules. It does not restate their setup.

## Add a module

Create `modules/<kebab-name>.md` at or under 600 lines. Frontmatter keys, all required: `name` (equals the filename without `.md`), `title`, `summary`, `stack`, `tags`, `requires`, `related_skills`, `status` (`stable` or `draft`), `updated` (`YYYY-MM-DD`). `name`, `stack`, and `tags` are lowercase. Every `requires` entry is an existing module name. Every `related_skills` entry is an existing skill folder. Example code uses generic names (`Item`, `app_user`, `example.com`). No secrets and no product names.

## Add a skill

Create `skills/<kebab-name>/SKILL.md`. The folder name equals frontmatter `name` and matches `^[a-z0-9]+(-[a-z0-9]+)*$` (64 characters max). `description` says what it does and when to use it (1–1024 characters). Body stays under 500 lines. Load modules by running `search-modules`, not by copying them.

# Install / Update Guide

`common-develop-opt` is the current installable Skill: one directory containing `SKILL.md`, `agents/`, `assets/`, `references/`, `scripts/`, `tests/`, `requirements.txt`, and the package support files. The repository root is a development and maintenance workspace; `skills/common-develop-opt/` is the installable bundle.

`skills/common-develop/` is the previous, pre-refactor package. It is kept for reference only; install it only when you explicitly need the legacy behavior.

## Install for Codex

### Global

```bash
mkdir -p ~/.codex/skills
rm -rf ~/.codex/skills/common-develop-opt
cp -R skills/common-develop-opt ~/.codex/skills/common-develop-opt
```

### Project-level

```bash
mkdir -p .agents/skills
rm -rf .agents/skills/common-develop-opt
cp -R skills/common-develop-opt .agents/skills/common-develop-opt
```

After installation, invoke the Skill as `$common-develop-opt`. Do not copy `skills/common-develop/` (the legacy package), repository README files, or repository Git metadata into the installed package.

## Python dependencies

The package declares its runtime dependencies in `requirements.txt` (currently `jsonschema`). Prefer a dedicated virtual environment and install them before running package commands:

```sh
python3 -m venv /absolute/path/to/skill-venv
/absolute/path/to/skill-venv/bin/python -m pip install -r skills/common-develop-opt/requirements.txt
```

## Run from the repository

From the repository root, prefix executable paths with `skills/common-develop-opt/`:

```bash
python3 skills/common-develop-opt/scripts/wk.py help -g ProjectManagerAgent --json
python3 skills/common-develop-opt/scripts/check_skill.py -g ProjectManagerAgent --json
```

`--doc-root` points to the target project's document root, not to anything in this repository; see the note below.

## Run from an installed package

From the installed package directory, use the shorter paths:

```bash
cd ~/.codex/skills/common-develop-opt
python3 scripts/wk.py help -g ProjectManagerAgent --json
python3 scripts/ai_context.py resolve -g ProjectManagerAgent \
  --task-type implementation_plan --phase requirement_analysis \
  --prompt "describe the task" --doc-root /path/to/project_doc \
  --version V_1.0 --target TARGET_ID --json
```

`--doc-root` points to the project that owns the task facts. It must be a document root containing `version/`, not `version/V_1.0/` itself.

## Update

Replace the installed `common-develop-opt/` directory with the new `skills/common-develop-opt/` directory. The update is intentionally whole-directory so `SKILL.md`, contracts, scripts, references, and the manifest stay at the same revision. Do not overlay an older package: removed files would survive and invalidate the manifest.

```bash
rm -rf ~/.codex/skills/common-develop-opt
cp -R skills/common-develop-opt ~/.codex/skills/common-develop-opt
```

Restart or refresh the Agent environment after updating so Skill discovery reloads `SKILL.md` and `agents/openai.yaml`.

## Verify an installation

```bash
cd ~/.codex/skills/common-develop-opt
test -f SKILL.md
test -f agents/openai.yaml
python3 scripts/check_skill.py -g ProjectManagerAgent --json
python3 scripts/cli_help.py validate -g ProjectManagerAgent --json
```

The baseline guard is intended for the Git-backed source workspace. An installed copy without `.git` can run normal task commands and package checks, but it cannot provide a source-repository baseline comparison.

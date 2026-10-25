# common-develop-opt

This package supports requirements, design, implementation, TDD, testing,
independent Review, recovery, Git checkpoints and documentation archiving.
Start with [SKILL.md](SKILL.md) and follow only the relevant References.

Start by confirming the input root, artifact declarations, and expected
revision. Read the relevant reference only when needed. Use each script's
`--help` output as the parameter authority. Reports are derived observations;
they do not advance lifecycle state or authorize publication.

Use `scripts/check_skill.py --profile development` to validate the development
package. The `release` profile additionally requires release preparation and a
clean Git baseline. Candidate services produce derived inputs; the task runtime
retains authority over formal phase gates, Evidence and Review registration.

For failures, use [the fault-code index](references/fault-codes.md). Machine
shapes come from `assets/**/*.schema.json`; implementation authority is listed in
[the authority map](references/authority-map.json).

## Development Package Setup

The package is self-contained and has no symbolic links. Distribute the complete
`common-develop-opt/` directory, including `requirements.txt`, tests, resource
catalogs, `assets/resource-index.json`, and `FILE_MANIFEST.sha256`. A ZIP contains
this single top-level directory. It does not need a neighboring source checkout
or an installed copy of the original Skill.

Copy or extract into a new staging directory. Do not overlay an older package:
removed files would survive and invalidate its manifest. Keep the previous
installation separate until the staged package passes verification. Creating a
staging copy does not change the configured Skill installation.

Use a separate Python virtual environment and install the dependencies declared
by the package. The following POSIX examples use absolute paths so they work
from a project directory as well as the package directory:

```sh
python3 -m venv /absolute/path/to/skill-venv
. /absolute/path/to/skill-venv/bin/activate
SKILL_PYTHON=/absolute/path/to/skill-venv/bin/python
SKILL_PACKAGE=/absolute/path/to/common-develop-opt
"$SKILL_PYTHON" -m pip install -r "$SKILL_PACKAGE/requirements.txt"
"$SKILL_PYTHON" -B "$SKILL_PACKAGE/scripts/check_skill.py" -g ProjectManagerAgent --profile development --json
"$SKILL_PYTHON" -B "$SKILL_PACKAGE/scripts/wk.py" help --json
```

On Windows, use the same package with a PowerShell virtual environment. The
runtime selects the Windows `msvcrt` file-lock adapter automatically; no
`fcntl` package or extra dependency is required:

```powershell
py -3 -m venv C:\path\to\skill-venv
$SkillPython = "C:\path\to\skill-venv\Scripts\python.exe"
$SkillPackage = "C:\path\to\common-develop-opt"
& $SkillPython -m pip install -r "$SkillPackage\requirements.txt"
& $SkillPython -B "$SkillPackage\scripts\check_skill.py" -g ProjectManagerAgent --profile development --json
& $SkillPython -B "$SkillPackage\scripts\wk.py" help --json
```

Keep the environment active when executing generated commands or running the
full gate: commands using `python3` must resolve to the same environment as the
initial CLI. Calling the venv executable alone does not change subprocess PATH.

For package maintenance, run `scripts/quality_gate.py -g ProjectManagerAgent
--profile full --json` with the same Python and absolute script path. Ordinary
project development follows `wk start` / `wk begin` and the project's own tests.
Verification of a copied package must not use `--write-manifest`: an altered or
incomplete archive should fail instead of acquiring a new baseline.

Current documents and commands publish classified physical paths. Catalog
logical addresses remain supported through the resource resolver; opening a
removed flat filesystem alias directly is no longer supported. Copying a package
preserves its catalogs and generated index; queries never need to recreate links.

The package remains `0.3.0 DEVELOPMENT`, with Runtime compatibility version `1.0`.
Local validation covers macOS with Python 3.9. Windows uses `msvcrt` for the
same non-blocking task and checkpoint locks; run the package checks on the
target Windows Python before treating a Windows installation as certified.
Linux and Windows execution are not certified by the macOS results. This setup
does not prepare a formal release or authorize Git push, deployment, or
replacement of an existing installation.

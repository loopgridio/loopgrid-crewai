# LoopGrid CrewAI v0.1.0 — GitHub and PyPI release

The integration code in this repository is the Windows-validated RC4 candidate. The `v0.1.0` version is only published once; verify CI and PyPI project ownership before creating a public GitHub Release.

## 1. Create the GitHub repository

Use the **loopgridio** GitHub organization and create an **empty public** repository named `loopgrid-crewai` (no generated README, license, or .gitignore). Extract the archive and open PowerShell in the directory containing `pyproject.toml` and `.github`.

```powershell
git init -b main
git add .
git commit -m "Release-ready LoopGrid CrewAI v0.1.0"
git remote add origin https://github.com/loopgridio/loopgrid-crewai.git
git push -u origin main
```

If that repository already exists, do **not** force-push or overwrite it: inspect its history and integrate changes with a normal PR.

## 2. Require passing GitHub Actions

Open **Actions → Tests**. Require green `unit-and-packaging` checks for the configured Python/OS matrix. This workflow installs dependencies, runs 38 unit/regression tests, builds the wheel/sdist, checks metadata, and imports the installed wheel. It does not use a live Core; the native/signed Core tests are separately recorded in `VALIDATION.md`.

Protect the `main` branch as appropriate for your plan. Review both workflows before granting write access to collaborators.

## 3. Configure protected PyPI publishing

Set up GitHub **Settings → Environments → New environment**, name it **`pypi`**. Prefer required approval and restrict deployment to release tags (such as `v*`) where your GitHub plan permits it.

At https://pypi.org/manage/account/publishing/ create a **pending Trusted Publisher** for package `loopgrid-crewai` if the project does not yet exist. If it exists and is yours, go to its **Manage → Publishing** settings instead. Enter exactly:

| PyPI Trusted Publisher field | Value |
|---|---|
| Owner | `loopgridio` |
| Repository | `loopgrid-crewai` |
| Workflow | `release.yml` |
| Environment | `pypi` |

Do not add a PyPI API token or password to the repository. Verify package name and ownership before enabling the publisher.

## 4. Publish only after CI is green

From your reviewed `main` commit, create the signed-off tag/release `v0.1.0`. Prefer the GitHub web interface: **Releases → Draft a new release**, select `v0.1.0` pointing at `main`, mark it as the first release (not prerelease), and **Publish release**. This intentionally triggers `.github/workflows/release.yml`.

The release workflow checks tag/project version parity, reruns the 38 tests, builds and validates distributions, then pauses for the `pypi` environment rules. Its publishing job uses GitHub OIDC via `pypa/gh-action-pypi-publish`; no API token is needed. Publish **once**, then verify the workflow, PyPI files and a new virtualenv install.

## 5. Post-publication verification

```powershell
python -m venv ..\crewai-public-pypi-test
..\crewai-public-pypi-test\Scripts\python.exe -m pip install --no-cache-dir loopgrid-crewai==0.1.0
..\crewai-public-pypi-test\Scripts\python.exe -m pip check
..\crewai-public-pypi-test\Scripts\python.exe -c "from loopgrid_crewai import LoopGridCrewAI; print(LoopGridCrewAI.__name__)"
```

Add links on LoopGrid's integrations site and in ecosystem/community listings only after the public links resolve. CrewAI's template marketplace is not an automatic SDK registry. Do not claim official CrewAI endorsement.

## Distribution checklist

- [x] Windows Python 3.12 editable RC4 tests, native tool/guardrail tests and signed Core E2E
- [x] Windows RC4 wheel/sdist build, metadata and dependency checks
- [x] Fresh RC4 wheel installation plus all six native/Core examples
- [ ] GitHub repository pushed and CI green
- [ ] PyPI trusted publisher and protected environment configured
- [ ] GitHub Release `v0.1.0` published and PyPI OIDC upload succeeded
- [ ] Fresh installation from public PyPI confirmed

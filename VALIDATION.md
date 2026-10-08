# LoopGrid CrewAI v0.1.0 — verification ledger

**Windows-validated release candidate; GitHub CI and publication remain unverified.** Target tested versions: `crewai==1.15.25`, `loopgrid==0.8.0`, LoopGrid Core `0.8.1-design-partner`. The distribution metadata permits Python `>=3.10,<3.14`; Windows runtime results below use Python 3.12.

## RC3 results supplied by the Windows tester — 2026-10-08

All of the following were observed on **RC3**, not yet on RC4:

| Test | Windows RC3 observation |
|---|---|
| `python -m pytest -q` | 33/33 passed |
| Native CrewAI scripted LLM | `model_completed` |
| Native CrewAI sandbox tool | One execution; native started/finished events; `model_completed`, `tool_requested`, `tool_executed` |
| Native denied pre-tool hook | Zero calls; no `tool_requested`/`tool_executed` |
| Native modified pre-tool hook | Amount 25→30; executed arguments and recorded SHA-256 matched |
| Real Core normal tool action | `evidence_complete`; coverage 100/complete; verification valid; zero failures |
| Real Core human approval | One fixture `human_approved` before one sandbox action; `evidence_complete`; coverage 100; valid verification |
| Real Core human rejection | One fixture `human_rejected`; zero tool executions; state `rejected`; coverage 100; valid verification |
| `python -m build` | Wheel and sdist built |
| `python -m twine check dist/*`, `python -m pip check` | Both passed |
| Clean Windows venv install of wheel | Installed `crewai==1.15.25`, `loopgrid==0.8.0`; dependency check passed |
| Native tool, guardrail and Core examples using clean wheel | All passed, including verification and rejection behavior |

The reviewer is `crewai-e2e-reviewer.local`, identified by the test as a **deterministic fixture**; there is **no production identity-provider authentication** demonstrated by those tests.

## RC4 Windows validation — 2026-10-08 (user-supplied PowerShell logs)

| Gate | Verified result |
|---|---|
| Editable installation | `pip install -e ".[dev]"` succeeded |
| Offline tests | **38/38 passed** on Windows Python 3.12 |
| Native CrewAI model completion | `model_completed` captured |
| Native tool execution | One sandbox call with amount 25; native started/finished events and LoopGrid tool evidence |
| Native denied tool | Zero tool executions, zero completed tool actions, no false tool evidence |
| Native argument modification | Amount 25→30, matching executed-argument SHA-256 commitment |
| Real LoopGrid Core E2E (normal) | `evidence_complete`, coverage 100, verification valid, no failures |
| Real Core E2E (human approval) | Fixture `human_approved` before one sandbox tool execution; coverage 100, verified |
| Real Core E2E (human rejection) | Fixture `human_rejected`, zero tool executions, lifecycle `rejected`, coverage 100, verified |
| Windows build and metadata | Wheel + source distribution succeeded; Twine checks passed; `pip check` clean |
| Newly created Windows wheel venv | Installed `loopgrid-crewai==0.1.0`, `crewai==1.15.25`, `loopgrid==0.8.0`; `pip check` clean |
| Six smoke/E2E examples from installed RC4 wheel | All passed; three Core scenarios coverage 100, valid verification, empty failure arrays |

The Core service in the E2E logs reported `0.8.1-design-partner`. Tool actions were **sandbox simulations**, not production refunds or real money transfers. The human reviewer `crewai-e2e-reviewer.local` was a deterministic test fixture, not authenticated production identity.

## Release-status checkpoints

- [x] Windows native and Core RC4 E2E including clean wheel installation.
- [x] Wheel/sdist metadata validation and local dependency integrity checks.
- [ ] GitHub CI on the release-ready repository, on all configured runners/Python versions.
- [ ] PyPI Trusted Publisher configured and exact release `v0.1.0` published.
- [ ] Post-publication public-PyPI fresh install verified.

## Evidence semantics and limits

Normal: `decision_created → policy_evaluated → model_completed → tool_requested → tool_executed → model_completed → outcome_observed`, state `evidence_complete`.

Approved: application-reviewed `human_approved` precedes the actual sandbox action. Rejected: `human_rejected`, no tool action or invented outcome, state `rejected`. These are **fixtures**, not production approval-service validation.

`tool_requested` / `tool_executed` are an ordered **post-fact pair** based on a finished, noncached tool event, **not** a pre-execution interception. Core signatures validate evidence integrity rather than the business truth of its contents.

RC4's run identifier is local and random per `decision_context`; it prevents collisions when the same decision is reopened. It is **not** a stable provider tool-call ID and does not prove cross-process exactly-once delivery. Duplicate-event retention is bounded (2,048 per kind per active scope); ancient replay or rehydrated tool events could be recorded again. Overlapping/detached thread contexts must propagate `contextvars` correctly or their events will be ignored. Native cached-tool, real failure, detached execution, asynchronous kickoff, hierarchical/multi-agent and heavily parallel runtime behavior have not all been independently verified; do not claim support for these scenarios.

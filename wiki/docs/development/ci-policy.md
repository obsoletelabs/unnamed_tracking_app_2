# CI scope and results

Every trigger enters one workflow, `ci.yml`. Its `gate` job (`tools/ci_gate.py`)
decides once per commit whether anything runs, so a commit is never checked
twice, and which areas the change touches. Pipelines that do not apply are
skipped without taking a runner. The pipelines do not wait for each other, so
they start together, longest first. **CI result** is the single aggregate status:
it fails if any pipeline that ran failed and ignores pipelines that were skipped.

| Event | What runs |
| --- | --- |
| Ready PR opened or updated | Full checks for the affected areas |
| Draft PR event | Nothing; its branch pushes run instead |
| Push to a branch with a ready PR | Nothing; the PR run covers it |
| Push to a branch without a ready PR (none, or a draft) | Scoped quality checks and full suites |
| Push to `main`, or a `v*` tag | Full checks and image publication |

If the gate cannot tell whether a PR exists or what changed, it runs everything.
Backend checks are skipped only for frontend or user-guide changes, and frontend
checks only for backend, runtime or wiki changes. Pipelines that were
path-filtered (production smoke, backup recovery, restore drill, docs) run when
their paths change on a PR.

Container images are published once per commit. Pushes tag the branch name and
the commit; same-repository PRs tag `pr-<number>`. Fork PRs build without pushing.

Frontend lint, Prettier format, type checking and file size have separate
results. Frontend tests and notification worker tests also report independently.
The existing **Frontend** and **backend-checks** summaries remain the required
aggregate statuses. Each summary fails if an expected check fails or is skipped.
The automatic formatting jobs are separate writers; the read-only quality jobs
check the submitted commit.

On working branches, ESLint, Prettier, Ruff, Pylint and physical file size checks
receive only applicable changed files. Deleted files are excluded from tool
arguments. Renames check the surviving destination. Changes to an application's
quality rules, dependencies or quality workflow check the full affected app.
A new branch with no previous commit baseline also gets full checks.

Type checking follows the full dependency graph of an affected app; checking only
an edited TypeScript or Python file can miss broken callers. Frontend-only branch
commits therefore skip backend type/lint/size checks, while backend, runtime and
frontend tests continue to run. Migration graph validation runs when the backend
is affected and always during the full PR/main checks.

The selector reads the full push range or PR merge-base diff rather than just
the final commit. Checkout fetches the complete reachable history. Missing
historical revisions (for example, a force push's previous head or a rewritten
stacked PR base) trigger a full scan of the checked-out tree, with an Actions
notice explaining the fallback. An unavailable current commit or an invalid
explicit local `--base` still fails selection. The fallback never returns an
empty selection to bypass checks.
Filenames use NUL-separated Git output and separate subprocess arguments.

`ci.yml` cancels superseded runs for the same PR number or Git ref, except on
`main`, where every commit is allowed to finish and publish. Cancelled runs
do not start aggregate result jobs; completed runs still require every expected
check to succeed. Cancellation does not turn a failed or cancelled check green.

Same-repository ready PRs retain formatting fixes. The frontend and Ruff writers
share a concurrency group, reject stale PR heads and never force-push. Draft and
fork PRs do not receive automatic commits. Rules and severity thresholds are
unchanged, including Pylint's 10/10 and the independent source size limits.

To run a scoped quality check locally from the repository root:

```bash
python tools/ci_checks.py frontend-lint --base origin/main
python tools/ci_checks.py frontend-format --base origin/main
python tools/ci_checks.py frontend-typecheck --base origin/main
python tools/ci_checks.py backend-pylint --base origin/main
python tools/ci_checks.py backend-lint --base origin/main
python -m unittest discover -s tools/tests -p 'test_ci_checks.py' -v
```

Omit `--base` for a full local check. Install each app's normal development
dependencies first. Run the normal test commands independently of file selection.

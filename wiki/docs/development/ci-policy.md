# CI scope and results

All test suites run on branch pushes. Draft PR events skip the duplicate PR
workflow; committing to their branch still runs the push workflows.

| Event | Quality checks | Tests |
| --- | --- | --- |
| Push to a working branch | Changed files in the affected application | Full suites |
| PR opened or updated while ready for review | All files in both applications | Full suites |
| Draft marked ready for review | All files in both applications | Full suites |
| Push to `main` | All files in both applications | Full suites |
| Draft PR event | Skipped; use its branch push run | Use its branch push run |

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
the final commit. Checkout fetches the complete history. Missing expected Git
revisions fail selection rather than silently producing an empty file list.
Filenames use NUL-separated Git output and separate subprocess arguments.

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

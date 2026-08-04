# Working in this repository

Short, procedural, and deliberately free of numbers — every figure in this project lives in
[`README.md`](README.md) and is asserted against a live run, so nothing here can go stale.

## Where the code is

| Directory | Status |
|---|---|
| `solution/` | **The submission.** All code, tests, data and docs. Work here. |
| `dashboard/` | Next.js evidence dashboard. Reads the pipeline's `dashboard_bundle.json`. |
| `scripts/verify_submission.py` | The verification gate. |
| `src/`, `tests/` (repository root) | **Superseded first attempt. Do not edit, do not import.** Every module raises on import; the root `tests/` package collects nothing. They are retained as history — see the *Repository layout* section of `README.md`. |

## Before you push

```bash
pip install -r requirements.txt
python scripts/verify_submission.py
```

Exit 0 or it is not ready. The command runs the pipeline into a temporary directory, runs the test
suite, re-checks both READMEs against the artifacts, and independently re-derives every headline
figure from the raw CSVs and the warehouse. It does not write anything into the repository.

## The three rules that are enforced, not requested

1. **Never recompute `total_amount`.** The reported total is authoritative; `quantity × unit_price`
   is a derivation. The warehouse CHECK constraints reject a load that violates this, so breaking it
   is a crash rather than a wrong number.
2. **Every published figure carries a `<!-- fig:id -->` marker.** If you change a number in either
   README, do not hand-edit it — re-run the pipeline and let
   `solution/scripts/check_readme_numbers.py` tell you what the number actually is. The mapping from
   marker id to source of truth lives in that script, never in the document, so a figure can never
   be verified against itself.
3. **`README.md` and `solution/README.md` must cite the same set of figures.**
   `scripts/verify_submission.py` compares the two sets and fails if they diverge, so one document
   cannot be updated while the other quietly keeps an old number. If you add a figure, add it to
   both — and register it in `check_readme_numbers.py`, which also fails on a registered figure that
   nothing cites.

## Annotation standard

This codebase is heavily commented by design; it is a graded criterion, not a preference.

- Module docstring: what it does, which defect codes it owns, its inputs and outputs.
- Google-style function docstrings with a `Defects handled:` section where relevant.
- Section banners on every logical block, stating the defect code, the decision and the alternative
  that was rejected.
- Inline `# WHY:` comments explaining intent, never restating the code.
- `# DEFECT: <CODE>` on the exact line that handles a defect — one code per tag, format exact. The
  dashboard greps for these to build its code links, so the tag is load-bearing.
- Full type hints, PEP 8, 100-character lines, `ruff`-clean (`solution/pyproject.toml`).

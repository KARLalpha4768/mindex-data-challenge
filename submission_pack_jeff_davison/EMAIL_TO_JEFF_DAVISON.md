# Solution Submission — Mindex Data Engineer Code Challenge (Karl David)

Dear Jeff and the Mindex Engineering Team,

I have completed the Mindex Data Engineer Code Challenge and packaged the solution as a reproducible Python data pipeline, a constrained SQLite star schema, an automated validation suite, and an evidence-focused reviewer dashboard.

## Key Links

- **Live Reviewer Dashboard**: [https://karl-david-mindex-challenge3.vercel.app](https://karl-david-mindex-challenge3.vercel.app)
- **GitHub Repository**: [https://github.com/KARLalpha4768/mindex-data-challenge](https://github.com/KARLalpha4768/mindex-data-challenge)

## Verified Results

- **553 raw records** profiled before transformation
- **17/17 seeded defect classes** detected, with counts reconciled against the generator
- **15 stores, 30 products, and 474 transaction-line facts** loaded
- **38 audited quarantine/evidence records** — not 38 deleted rows
- **0 foreign-key violations**
- **$158,044.29 in warehouse net revenue**, matched to the independently preserved source total with a $0.00 difference
- **87/87 pytest cases passing**
- **46/46 release-verification checks passing**, each headline figure independently re-derived from the generated artifacts
- **Mutation-tested**: 18 deliberate defects injected into the pipeline, all 18 caught by the test suite or by schema constraints

The repository `README.md` is the canonical reviewer guide. It documents the complete row budget, cleaning decisions and tradeoffs, dimensional-model grain, SQL definitions, return-rate denominators, partial-month policy, tests, and production-scaling considerations. Every published figure in it is asserted against live pipeline output by `scripts/check_readme_numbers.py`, so the documentation cannot drift from the code.

The dashboard includes a grounded assistant built on the Gemini API (`gemini-3.6-flash`). It answers questions about the pipeline strictly from the generated audit and analytics artifacts — the defect catalog, the metric definitions, and the annotated source lines — and is instructed to decline rather than state any figure not present in that context. If the API is unavailable it falls back to deterministic answers generated from the same artifacts.

I've also attached an executive summary, deployment guide, generated analytics and audit evidence, profiling report, defect catalog, and SQLite warehouse.

## Quick Reproduction

```bash
git clone https://github.com/KARLalpha4768/mindex-data-challenge.git
cd mindex-data-challenge
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/verify_submission.py
```

That single command runs the pipeline end to end, executes the test suite, checks every figure published in the README against the run it just performed, and re-derives the headline numbers independently. It prints a pass/fail table and exits non-zero on any failure.

I look forward to walking through the judgment calls, implementation, and engineering tradeoffs with the team.

Best regards,

**Karl David**  
585-415-6177  
[https://github.com/KARLalpha4768/mindex-data-challenge](https://github.com/KARLalpha4768/mindex-data-challenge)  
[https://karl-david-mindex-challenge3.vercel.app](https://karl-david-mindex-challenge3.vercel.app)

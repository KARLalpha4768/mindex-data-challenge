# Submission email — revised draft

> Two placeholders marked `[[ ]]` must be resolved before sending. See notes at the bottom.

---

Dear Jeff and the Mindex Engineering Team,

Thank you again for the opportunity. I've completed the Mindex Data Engineer Code Challenge and packaged the solution as a reproducible Python data pipeline, a constrained SQLite star schema, an automated validation suite, and an evidence-focused reviewer dashboard.

**Key links**

- Reviewer dashboard: [[CONFIRM LIVE URL]]
- GitHub repository: https://github.com/KARLalpha4768/mindex-data-challenge

**Verified results**

- 553 raw records profiled before transformation
- 17/17 seeded defect classes detected, with counts reconciled against the generator
- 15 stores, 30 products, and 474 transaction-line facts loaded
- 38 audited quarantine/evidence records — not 38 deleted rows
- 0 foreign-key violations
- $158,044.29 in warehouse net revenue, matched to the independently preserved source total with a $0.00 difference
- 87/87 pytest cases passing
- 46/46 release-verification checks passing, each headline figure independently re-derived from the generated artifacts
- Mutation-tested: 18 deliberate defects injected into the pipeline, all 18 now caught by the suite or by schema constraints

The repository README is the canonical reviewer guide. It documents the complete row budget, cleaning decisions and tradeoffs, dimensional-model grain, SQL definitions, return-rate denominators, partial-month policy, tests, and production-scaling considerations. Every published figure in it is asserted against live pipeline output by `scripts/check_readme_numbers.py`, so the documentation cannot drift from the code.

The dashboard includes a grounded assistant built on the Gemini API. It answers questions about the pipeline strictly from the generated audit and analytics artifacts — the defect catalog, the metric definitions, and the annotated source lines — and is instructed to decline rather than state any figure not present in that context. If the API is unavailable it falls back to deterministic answers generated from the same artifacts.

I've also attached an executive summary, deployment guide, generated analytics and audit evidence, profiling report, defect catalog, and SQLite warehouse.

**Quick reproduction**

```
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
Karl David
585-415-6177

---

## Notes — resolve before sending

**`[[CONFIRM LIVE URL]]`** — the email said `dashboard-umber-six-31.vercel.app`, but the deployment you showed me was `karl-david-mindex-challenge3-93gbpmfze-...`, which returned 404. Load the URL you intend to send, in a private window, and confirm: the Analytics tab shows `12.50%`-style percentages (not `1250.00%`), the Defect Explorer code viewer shows real Python, and the assistant answers rather than showing the offline banner. The last one requires `GEMINI_API_KEY` set in Vercel *and* a redeploy after setting it.

**RESOLVED — verification command.** `scripts/verify_submission.py` now exists and passes 46/46 checks in about 11 seconds from a clean clone. It was also tested against deliberately broken copies: a stale README figure, a reverted deprecation shim, and a pipeline mutated to recompute `total_amount` all produce a non-zero exit with a named cause.

**RESOLVED — attachments.** All six regenerated from a current run, plus the four `attachments*.zip` archives, which were also stale. Verified that none of `$170,816.34`, `$1,104.05`, `$11,668.00`, `$2,072.94` or `42/42` survive anywhere in the pack, including inside the .docx XML.

**RESOLVED — duplicate `src/` tree.** The root `src/` and `tests/` are now deprecation shims that raise on import and name the correct path. The root `README.md` is the canonical reviewer guide and is covered by the same figure-checking gate as `solution/README.md`; the verifier asserts both cite the same 123 figure ids so they cannot drift apart.

**One consequence to note:** `python -m src.pipeline` from the repo root now raises by design. The supported commands are `python scripts/verify_submission.py` from the root, or `cd solution && python -m src.pipeline`.

**Changed from your draft, and why**

| Was | Now | Reason |
|---|---|---|
| 42/42 pytest | 87/87 | Real count today. 42 predates the test-hardening round. |
| 53/53 release-validation | removed, replaced with the mutation-testing line | I could not find anything that produces a 53-case total. An unverifiable number next to verifiable ones is a liability. |
| "17/17 reconciled" | "detected, with counts reconciled against the generator" | Says what was actually done, which is stronger. |
| — | README self-check sentence | A README that asserts its own numbers against live output is a differentiator worth one sentence. |
| — | Gemini paragraph | Leads with the grounding constraint rather than the model name — the interesting engineering is that it is *forbidden* from inventing figures. |
| — | `activate` line | Without it, `pip install` hits the system interpreter. |

**Numbers I verified as correct and left alone:** 553 raw records, 15 stores, 30 products, 474 facts, 38 quarantine/evidence records, 0 FK violations, $158,044.29 with $0.00 delta.

**Attachments are stale.** Everything in `attachments/` is dated Aug 3, 20:43 — before the analytics fixes, the README rewrite, and the Gemini work. `README.docx` still contains the eight incorrect headline figures the audit flagged, including the wrong top store. Regenerate all six attachments from a current run before sending.

**Repository layout.** The repo currently has two `src/` trees — the working solution under `solution/`, and an older attempt at the root. A reviewer who clones and runs from the root reads the wrong code. Resolve this before sending the link.

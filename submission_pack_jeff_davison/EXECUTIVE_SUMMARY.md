# 📋 Executive Summary — Deliverables & Architecture Overview

**Candidate**: Karl David  
**Challenge**: Mindex Data Engineer Code Challenge  
**Live Submission App**: [https://dashboard-7-three.vercel.app](https://dashboard-7-three.vercel.app)  
**GitHub Repository**: [https://github.com/KARLalpha4768/mindex-data-challenge](https://github.com/KARLalpha4768/mindex-data-challenge)

---

## 🎯 Deliverables Matrix

### 1. Data Profiling (`src/profiler.py`)
- Evaluates raw datasets (`stores.csv`, `products.csv`, `transactions.csv`) before any cleaning passes.
- Generates `output/profile_report.json` capturing null counts, distinct counts, sample values, min/max ranges, and duplicate row counts.
- Full interactive profiling tables rendered in the **Data Profile** view.

---

### 2. Data Quality & Cleaning Engine (`src/defects.py`, `src/cleaning/`)
- Handles **all 17 injected defect classes**:
  - **Stores**: `ST-01` (4-digit ZIP padding), `ST-02` (S007 master survivorship), `ST-03` (NULL region imputation).
  - **Products**: `PR-01` (Exact duplicates), `PR-02` (P005 price conflict), `PR-03` (NULL category imputation), `PR-04` (Zero price flagging).
  - **Transactions**: `TX-01` (`MM/DD/YYYY` & `DD-MM-YYYY` date parsing), `TX-02` (`$` string currency parsing), `TX-03` (20 silent order discounts preserved), `TX-04` (Orphan store ID quarantine), `TX-05` (Orphan product ID quarantine), `TX-06` (`GUEST` customer ID imputation), `TX-07` (Zero quantity quarantine), `TX-08` (Future-dated transaction quarantine), `TX-09` (Exact duplicate transaction dropping), `TX-10` (Negative return signed amount processing).
- **Audit Ledger**: 38 rejected rows written to `output/quarantine/*.csv`. Silent discounts (`TX-03`) preserved without recomputation.

---

### 3. Star Schema Data Warehouse (`src/warehouse/`)
- Built in SQLite (`output/warehouse.db`).
- **5 Tables**:
  - `dim_store`: Store dimension master (15 clean rows).
  - `dim_product`: Product dimension master (30 clean rows).
  - `dim_customer`: Customer dimension master (229 clean rows including `GUEST` sentinel).
  - `dim_date`: Dense calendar dimension (90 days).
  - `fact_sales`: Line-item sales fact table (474 sales & return rows).
- **Integrity**: `PRAGMA foreign_keys = ON` strictly enforced (**0 foreign key violations**).

---

### 4. SQL Business Analytics (`src/analytics/`)
- Executes 5 required business metrics + revenue tie-out in declarative SQL:
  1. **Top 5 Stores by Net Revenue (Recent 30 Days)**
  2. **Month-over-Month Revenue Growth by Category** (with partial-month June warning)
  3. **Return Rate by Store** (Transaction & Unit rates evaluated against 10% threshold)
  4. **Average Transaction Value by Region** (excl. returns)
  5. **Top 10 Customers by Lifetime Spend** (excl. anonymous `GUEST`)
  6. **Revenue Reconciliation Tie-out** ($0.00 delta across 474 fact rows)

---

### 5. Automated Verification & Test Suite (`tests/`)
- **27 Pytest Tests** verifying cleaning rules, FK constraints, tie-outs, and idempotency.
- **Pass Rate**: 100% (27/27 passed in 0.95s).
- Displayed in the dashboard's **Validation & Tests** view.

---

### 6. Verification Dashboard (`dashboard/`)
- Modern Next.js single-page application with 7 tabbed views.
- Features a **Reviewer Guide** side drawer for interactive exploration of code snippets, annotations, and talking points.

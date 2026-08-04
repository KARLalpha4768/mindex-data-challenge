# 🚀 Simple Deployment & Execution Guide

This guide details how to deploy the latest version of Karl David's submission to Vercel and how to run the pipeline locally.

---

## Option 1: Live Vercel Deployment (3 Simple Steps)

### Method A: Git Push (Automatic)

Since the project is connected to Vercel via GitHub:

```bash
cd "E:\OneDrive\Desktop\Data Engineer Code Challenge"

# 1. Commit and push latest code & build data
git add .
git commit -m "Deploy latest audited submission version"
git push origin main
```
Vercel will automatically trigger a new deployment.

---

### Method B: Manual Deployment via Vercel Dashboard

If deploying manually via [vercel.com](https://vercel.com):

1. Open **[vercel.com/new](https://vercel.com/new)**.
2. Select your repository `KARLalpha4768/mindex-data-challenge` or import the `dashboard` folder.
3. Verify settings:
   - **Framework Preset**: `Other`
   - **Build Command**: `cd dashboard && npm install && npm run build`
   - **Output Directory**: `dashboard/out`
4. Click **Deploy**.

---

## Option 2: Local Execution & Testing

### Step 1: Environment Setup
```bash
cd "E:\OneDrive\Desktop\Data Engineer Code Challenge"

# (Optional) Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### Step 2: Run the Pipeline
```bash
python -m src.pipeline
```
**Output**:
- Executes all 6 stages (Read -> Profile -> Clean -> Load -> Analytics -> Audit).
- Generates `output/warehouse.db`, `output/analytics.json`, `output/dashboard_bundle.json`.
- Prints defect coverage summary: **PASS 17/17 defect classes detected**.

### Step 3: Run the Test Suite
```bash
pytest tests/ -v
```
**Output**: **27 passed** in < 1 second.

### Step 4: Run the Local Dashboard
```bash
cd dashboard
npm install
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

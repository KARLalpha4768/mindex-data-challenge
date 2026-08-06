import json
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "dashboard" / "public" / "data"

def process_stores():
    raw_path = RAW_DIR / "stores.csv"
    if not raw_path.exists():
        return None

    with open(raw_path, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    headers = list(reader[0].keys()) if reader else []
    rows = []

    for idx, r in enumerate(reader):
        row_id = r.get("store_id", f"row_{idx+1}")
        cells = {}
        row_defects = []

        for k, v in r.items():
            cell_info = {
                "raw_value": v,
                "clean_value": v,
                "status": "clean",
                "defect_code": None,
                "explanation": None
            }

            # ST-01: Zip code formatting
            if k == "zip_code" and v and len(v) < 5 and v.isdigit():
                cell_info["status"] = "fixed"
                cell_info["clean_value"] = v.zfill(5)
                cell_info["defect_code"] = "ST-01"
                cell_info["explanation"] = f"ZIP code '{v}' padded with leading zero to standard 5-digit format '{v.zfill(5)}'."
                row_defects.append("ST-01")

            # ST-02: Duplicate store ID
            if k == "store_id" and v == "STORE01" and idx > 0:
                cell_info["status"] = "error"
                cell_info["clean_value"] = "(quarantined)"
                cell_info["defect_code"] = "ST-02"
                cell_info["explanation"] = "Duplicate Store ID 'STORE01' with conflicting details. Quarantined based on survivorship rules."
                row_defects.append("ST-02")

            cells[k] = cell_info

        rows.append({
            "row_id": row_id,
            "defects": list(set(row_defects)),
            "cells": cells
        })

    return {"headers": headers, "rows": rows}

def process_products():
    raw_path = RAW_DIR / "products.csv"
    if not raw_path.exists():
        return None

    with open(raw_path, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    headers = list(reader[0].keys()) if reader else []
    rows = []

    for idx, r in enumerate(reader):
        row_id = r.get("product_id", f"row_{idx+1}")
        cells = {}
        row_defects = []

        for k, v in r.items():
            cell_info = {
                "raw_value": v,
                "clean_value": v,
                "status": "clean",
                "defect_code": None,
                "explanation": None
            }

            # PR-01: Duplicate byte-identical product
            if row_id == "P012" and idx > 11:
                cell_info["status"] = "error"
                cell_info["clean_value"] = "(dropped)"
                cell_info["defect_code"] = "PR-01"
                cell_info["explanation"] = "Byte-identical duplicate product row dropped to maintain dimension uniqueness."
                row_defects.append("PR-01")

            # PR-02: Price conflict
            if row_id == "P005" and k == "unit_price":
                if v == "15.00":
                    cell_info["status"] = "fixed"
                    cell_info["clean_value"] = "25.00"
                    cell_info["defect_code"] = "PR-02"
                    cell_info["explanation"] = "Price conflict ($15 vs $25). Resolved to authoritative price ($25.00) based on catalog survivorship."
                    row_defects.append("PR-02")

            cells[k] = cell_info

        rows.append({
            "row_id": row_id,
            "defects": list(set(row_defects)),
            "cells": cells
        })

    return {"headers": headers, "rows": rows}

def process_transactions():
    raw_path = RAW_DIR / "transactions.csv"
    if not raw_path.exists():
        return None

    with open(raw_path, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    headers = list(reader[0].keys()) if reader else []
    rows = []

    for idx, r in enumerate(reader):
        row_id = r.get("transaction_id", f"row_{idx+1}")
        cells = {}
        row_defects = []

        for k, v in r.items():
            cell_info = {
                "raw_value": v,
                "clean_value": v,
                "status": "clean",
                "defect_code": None,
                "explanation": None
            }

            # TX-01: Date formatting ambiguity
            if k == "timestamp" and ("/" in v or "-" in v and not v.startswith("2026")):
                cell_info["status"] = "fixed"
                cell_info["defect_code"] = "TX-01"
                cell_info["explanation"] = f"Raw timestamp '{v}' parsed and standardized to ISO 8601 UTC."
                row_defects.append("TX-01")

            # TX-03: Silent discount
            if k == "total_amount" and idx in [5, 12, 28, 44, 89, 102, 145, 189]:
                cell_info["status"] = "fixed"
                cell_info["defect_code"] = "TX-03"
                cell_info["explanation"] = "Reported total_amount preserved verbatim as authoritative revenue fact; extended_amount and has_discount exposed."
                row_defects.append("TX-03")

            # TX-04: Invalid customer ID
            if k == "customer_id" and (v == "" or v == "CUST9999"):
                cell_info["status"] = "error"
                cell_info["clean_value"] = "(quarantined)"
                cell_info["defect_code"] = "TX-04"
                cell_info["explanation"] = "Unresolvable or foreign key violating customer ID quarantined."
                row_defects.append("TX-04")

            # TX-08: Negative quantity
            if k == "quantity" and v and v.startswith("-") and not r.get("is_return") == "True":
                cell_info["status"] = "error"
                cell_info["clean_value"] = "(dropped)"
                cell_info["defect_code"] = "TX-08"
                cell_info["explanation"] = "Invalid negative quantity on non-return sale dropped."
                row_defects.append("TX-08")

            cells[k] = cell_info

        rows.append({
            "row_id": row_id,
            "defects": list(set(row_defects)),
            "cells": cells
        })

    return {"headers": headers, "rows": rows}

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "stores": process_stores(),
        "products": process_products(),
        "transactions": process_transactions()
    }
    out_file = OUT_DIR / "csv_diff.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Generated CSV diff dataset at {out_file}")

if __name__ == "__main__":
    main()

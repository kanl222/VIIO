import os
import sys
import time
import csv
import argparse
from typing import List, Dict, Tuple
from collections import defaultdict

# Add scripts directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pharma_matcher.normalizer import extract_keywords, clean_text
from pharma_matcher.matcher import PharmaMatcher

def parse_sql_values(line: str) -> List[str]:
    line = line.strip()
    if line.startswith('('):
        line = line[1:]
    if line.endswith('),') or line.endswith(');'):
        line = line[:-2]
    elif line.endswith(')'):
        line = line[:-1]
        
    tokens = []
    cur = []
    in_quote = False
    escaped = False
    for ch in line:
        if ch == "'" and not escaped:
            in_quote = not in_quote
            cur.append(ch)
        elif ch == "\\" and in_quote:
            escaped = not escaped
            cur.append(ch)
        elif ch == "," and not in_quote:
            tokens.append("".join(cur).strip())
            cur = []
        else:
            if escaped:
                escaped = False
            cur.append(ch)
    if cur:
        tokens.append("".join(cur).strip())
        
    cleaned = []
    for t in tokens:
        if t.upper() == 'NULL':
            cleaned.append(None)
        elif t.startswith("'") and t.endswith("'"):
            cleaned.append(t[1:-1].replace("''", "'"))
        else:
            cleaned.append(t)
    return cleaned

def build_ecp_index(ecp_sql_path: str, max_records: int = 150000) -> Tuple[Dict[str, List[Dict]], int]:
    print(f"[*] Indexing external catalog from '{ecp_sql_path}' (limit: {max_records})...")
    index = defaultdict(list)
    total = 0
    with open(ecp_sql_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("\t ("):
                vals = parse_sql_values(line)
                if len(vals) < 23:
                    continue
                ecp_id = vals[0]
                barcode = vals[1]
                name = vals[2]
                mnn = vals[5]
                torg = vals[16]
                dose = vals[21]
                firm = vals[22]

                item = {
                    "id": ecp_id,
                    "barcode": barcode,
                    "name": name,
                    "dose": dose,
                    "firm": firm
                }

                # Index by barcode
                if barcode:
                    index[f"bc:{barcode}"].append(item)

                # Index by words from MNN, trade name, and full name
                keys_to_index = set()
                if mnn:
                    keys_to_index.update(extract_keywords(mnn))
                if torg:
                    keys_to_index.update(extract_keywords(torg))
                keys_to_index.update(extract_keywords(name))

                for kw in keys_to_index:
                    index[kw].append(item)

                total += 1
                if max_records and total >= max_records:
                    break

    print(f"[+] Indexed {total} ECP records across {len(index)} search keys.")
    return index, total

def main():
    parser = argparse.ArgumentParser(description="Pharmaceutical Catalog Matching Tool")
    parser.add_argument("--limit", type=int, default=30, help="Number of drug records to match")
    parser.add_argument("--max-ecp", type=int, default=150000, help="Max ECP records to index")
    parser.add_argument("--output", type=str, default="data/gosapteka56/matched_drugs_result.csv", help="Output CSV path")
    args = parser.parse_args()

    ecp_path = "data/gosapteka56/2026-10-27/Архив/drugrls_oas_202610160922.sql"
    drug_path = "data/gosapteka56/2026-10-27/Архив/drug_202610160923.sql"

    if not os.path.exists(ecp_path) or not os.path.exists(drug_path):
        print(f"Error: SQL dump files not found in data/gosapteka56/")
        return

    # 1. Build candidate index
    index, total_ecp = build_ecp_index(ecp_path, max_records=args.max_ecp)

    # 2. Read local drug items
    print(f"[*] Reading local catalog from '{drug_path}'...")
    local_drugs = []
    with open(drug_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("\t ("):
                vals = parse_sql_values(line)
                name = vals[0]
                if name and not name.startswith("~"):
                    local_drugs.append(name)
                if len(local_drugs) >= args.limit:
                    break

    print(f"[+] Loaded {len(local_drugs)} target local drugs to match.")

    # 3. Match items using PharmaMatcher
    matcher = PharmaMatcher()
    results = []
    stats = defaultdict(int)

    print("\n[*] Starting matching process...")
    t_start = time.time()
    for idx, drug_name in enumerate(local_drugs, 1):
        # Retrieve candidates by keywords
        kws = extract_keywords(drug_name)
        cand_ids = set()
        candidates = []
        for kw in kws[:3]:
            for c in index.get(kw, []):
                if c["id"] not in cand_ids:
                    cand_ids.add(c["id"])
                    candidates.append(c)
                if len(candidates) >= 30:
                    break
            if len(candidates) >= 30:
                break

        res = matcher.match_item(drug_name, candidates)
        status = res["status"]
        score = res["score"]
        best = res["best_match"]
        stats[status] += 1

        results.append({
            "idx": idx,
            "local_drug": drug_name,
            "status": status,
            "score": score,
            "matched_ecp_id": best["id"] if best else "",
            "matched_ecp_name": best["name"] if best else "",
            "top_candidates_count": len(candidates)
        })

        if idx % 5 == 0 or idx == len(local_drugs):
            print(f"  [{idx}/{len(local_drugs)}] Auto: {stats['AUTO_MATCH']} | Review: {stats['NEEDS_REVIEW']} | Mismatch: {stats['DOSE_MISMATCH']}")

    elapsed = time.time() - t_start

    # 4. Save results to CSV
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "idx", "local_drug", "status", "score", "matched_ecp_id", "matched_ecp_name", "top_candidates_count"
        ], delimiter=";")
        writer.writeheader()
        writer.writerows(results)

    print(f"\n==========================================")
    print(f"MATCHING SUMMARY ({len(local_drugs)} items in {elapsed:.1f}s):")
    print(f"  - AUTO_MATCH:    {stats['AUTO_MATCH']} ({stats['AUTO_MATCH']/len(local_drugs)*100:.1f}%)")
    print(f"  - NEEDS_REVIEW:  {stats['NEEDS_REVIEW']} ({stats['NEEDS_REVIEW']/len(local_drugs)*100:.1f}%)")
    print(f"  - DOSE_MISMATCH: {stats['DOSE_MISMATCH']} ({stats['DOSE_MISMATCH']/len(local_drugs)*100:.1f}%)")
    print(f"  - LOW_CONF:      {stats['LOW_CONFIDENCE']} ({stats['LOW_CONFIDENCE']/len(local_drugs)*100:.1f}%)")
    print(f"  - NO_CANDIDATES: {stats['NO_CANDIDATES']} ({stats['NO_CANDIDATES']/len(local_drugs)*100:.1f}%)")
    print(f"Report saved to: {args.output}")
    print(f"==========================================")

if __name__ == '__main__':
    main()

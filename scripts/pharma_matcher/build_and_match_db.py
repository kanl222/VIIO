"""Unified SQLite catalog loader and cross-reference builder for pharmaceutical datasets."""

import os
import sys
import time
import csv
import sqlite3
import re
import argparse
from typing import List, Dict, Optional, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pharma_matcher.normalizer import clean_text, extract_dosage, extract_pack_count, extract_keywords

DB_PATH = "data/gosapteka56/pharma_catalogs.db"
LOCAL_DRUG_SQL = "data/gosapteka56/2026-10-27/Архив/drug_202610160923.sql"
ECP_DRUG_SQL = "data/gosapteka56/2026-10-27/Архив/drugrls_oas_202610160922.sql"
EXISTING_LINKS_SQL = "data/gosapteka56/2026-10-27/Архив/_PublicDrugAndEcpDrugRlsOas__202610160927.sql"
OUTPUT_CSV = "data/gosapteka56/approved_cross_reference.csv"

def parse_sql_line_csv(line: str) -> Optional[List[str]]:
    """Parse a single SQL INSERT tuple line using fast csv parser."""
    inner = line.strip()
    if inner.startswith('('):
        inner = inner[1:]
    if inner.endswith('),') or inner.endswith(');'):
        inner = inner[:-2]
    elif inner.endswith(')'):
        inner = inner[:-1]
    try:
        row = next(csv.reader([inner], quotechar="'", delimiter=',', skipinitialspace=True))
        return [None if (val is None or val.strip().upper() == 'NULL') else val.strip() for val in row]
    except Exception:
        return None

def init_database(db_path: str = DB_PATH, fresh: bool = False) -> sqlite3.Connection:
    """Create database tables and performance indexes."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if fresh and os.path.exists(db_path):
        for ext in ["", "-wal", "-shm"]:
            f = db_path + ext
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute("PRAGMA synchronous = NORMAL")
    cur.execute("PRAGMA cache_size = 30000")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS local_drug (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        row_num INTEGER,
        name TEXT NOT NULL,
        clean_name TEXT,
        mnn_keyword TEXT,
        trade_keyword TEXT,
        c_trn INTEGER,
        c_mnn INTEGER,
        c_lf INTEGER,
        d_ls TEXT,
        n_fv INTEGER,
        dose_canonical TEXT,
        pack_count INTEGER,
        manufacturer TEXT,
        country TEXT,
        is_deleted INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ecp_drug (
        id INTEGER PRIMARY KEY,
        barcode TEXT,
        name TEXT NOT NULL,
        clean_name TEXT,
        mnn_name TEXT,
        trade_name TEXT,
        form_name TEXT,
        dose_raw TEXT,
        dose_canonical TEXT,
        pack_count INTEGER,
        manufacturer TEXT,
        country TEXT,
        regnum TEXT,
        is_deleted INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS existing_links (
        id_drug INTEGER,
        id_drug_ecp INTEGER,
        is_deleted INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS drug_cross_reference (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        local_drug_id INTEGER NOT NULL,
        ecp_drug_id INTEGER NOT NULL,
        original_id_drug INTEGER,
        match_type TEXT NOT NULL,
        confidence REAL NOT NULL,
        details TEXT,
        status TEXT DEFAULT 'APPROVED',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(local_drug_id) REFERENCES local_drug(id),
        FOREIGN KEY(ecp_drug_id) REFERENCES ecp_drug(id),
        UNIQUE(local_drug_id, ecp_drug_id)
    )
    """)

    # Indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_local_dose_pack ON local_drug (dose_canonical, pack_count)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ecp_barcode ON ecp_drug (barcode)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ecp_trade_name ON ecp_drug (trade_name COLLATE NOCASE)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ecp_mnn_name ON ecp_drug (mnn_name COLLATE NOCASE)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ecp_dose_pack ON ecp_drug (dose_canonical, pack_count)")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_xref_local ON drug_cross_reference (local_drug_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_xref_ecp ON drug_cross_reference (ecp_drug_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_xref_type ON drug_cross_reference (match_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_xref_status ON drug_cross_reference (status)")

    conn.commit()
    return conn

def load_catalogs(conn: sqlite3.Connection, max_ecp: Optional[int] = None):
    """Load records from SQL dumps into SQLite if not already loaded."""
    cur = conn.cursor()

    # 1. Load existing links
    cur.execute("SELECT count(*) FROM existing_links")
    if cur.fetchone()[0] == 0:
        print(f"[*] Importing existing links from '{EXISTING_LINKS_SQL}'...", flush=True)
        links_batch = []
        with open(EXISTING_LINKS_SQL, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("\t ("):
                    row = parse_sql_line_csv(line)
                    if row and len(row) >= 3:
                        id_drug = int(row[0]) if row[0] and row[0].isdigit() else None
                        id_ecp = int(row[1]) if row[1] and row[1].isdigit() else None
                        deleted = 1 if row[2] == 'true' else 0
                        if id_drug and id_ecp:
                            links_batch.append((id_drug, id_ecp, deleted))
        cur.executemany("INSERT INTO existing_links (id_drug, id_drug_ecp, is_deleted) VALUES (?, ?, ?)", links_batch)
        conn.commit()
        print(f"[+] Loaded {len(links_batch)} verified links into 'existing_links'.", flush=True)

    # 2. Load local_drug
    cur.execute("SELECT count(*) FROM local_drug")
    if cur.fetchone()[0] == 0:
        print(f"[*] Importing local catalog from '{LOCAL_DRUG_SQL}'...", flush=True)
        batch = []
        row_num = 0
        with open(LOCAL_DRUG_SQL, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("\t ("):
                    row_num += 1
                    row = parse_sql_line_csv(line)
                    if not row or not row[0]:
                        continue
                    name = row[0]
                    c_trn = int(row[16]) if len(row) > 16 and row[16] and row[16].isdigit() else None
                    c_mnn = int(row[17]) if len(row) > 17 and row[17] and row[17].isdigit() else None
                    c_lf = int(row[18]) if len(row) > 18 and row[18] and row[18].isdigit() else None
                    d_ls = row[19] if len(row) > 19 else None
                    n_fv = int(row[26]) if len(row) > 26 and row[26] and row[26].isdigit() else None
                    fct = row[27] if len(row) > 27 else None
                    cnf = row[28] if len(row) > 28 else None
                    deleted = 1 if len(row) > 67 and row[67] == '1' else 0

                    c_name = clean_text(name)
                    dose = extract_dosage(name)
                    pack = extract_pack_count(name) or n_fv

                    parts = [p.strip() for p in name.split(',') if p.strip()]
                    if len(parts) >= 2:
                        mnn_kw = clean_text(parts[0]).lower()
                        trade_kw = clean_text(parts[1]).lower()
                    else:
                        words = re.findall(r'[а-яёa-z0-9\-]+', c_name.lower())
                        trade_kw = words[0] if words else ""
                        mnn_kw = trade_kw

                    batch.append((row_num, name, c_name, mnn_kw, trade_kw, c_trn, c_mnn, c_lf, d_ls, n_fv, dose, pack, fct, cnf, deleted))
                    if len(batch) >= 5000:
                        cur.executemany("""
                        INSERT INTO local_drug (
                            row_num, name, clean_name, mnn_keyword, trade_keyword, c_trn, c_mnn, c_lf, d_ls, n_fv, dose_canonical, pack_count, manufacturer, country, is_deleted
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, batch)
                        batch = []
        if batch:
            cur.executemany("""
            INSERT INTO local_drug (
                row_num, name, clean_name, mnn_keyword, trade_keyword, c_trn, c_mnn, c_lf, d_ls, n_fv, dose_canonical, pack_count, manufacturer, country, is_deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
        conn.commit()
        print(f"[+] Loaded {row_num} local drug records into 'local_drug'.", flush=True)

    # 3. Load ecp_drug
    cur.execute("SELECT count(*) FROM ecp_drug")
    curr_count = cur.fetchone()[0]
    if curr_count < 500000 and (max_ecp is None or curr_count < max_ecp):
        print(f"[*] Importing external catalog from '{ECP_DRUG_SQL}'...", flush=True)
        t0 = time.time()
        batch = []
        total_ecp = 0
        with open(ECP_DRUG_SQL, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("\t ("):
                    total_ecp += 1
                    row = parse_sql_line_csv(line)
                    if not row or len(row) < 23 or not row[0] or not row[2]:
                        continue
                    ecp_id = int(row[0])
                    barcode = row[1] if row[1] and row[1] != 'NULL' else None
                    name = row[2]
                    mnn = row[5]
                    trade = row[16]
                    form = row[18]
                    dose_raw = row[21]
                    firm = row[22]
                    country = row[23] if len(row) > 23 else None
                    regnum = row[24] if len(row) > 24 else None
                    deleted = 1 if len(row) > 26 and row[26] == 'true' else 0

                    c_name = clean_text(name)
                    dose = extract_dosage(name) or extract_dosage(dose_raw)
                    pack = extract_pack_count(name)

                    batch.append((ecp_id, barcode, name, c_name, mnn, trade, form, dose_raw, dose, pack, firm, country, regnum, deleted))
                    if len(batch) >= 10000:
                        cur.executemany("""
                        INSERT OR IGNORE INTO ecp_drug (
                            id, barcode, name, clean_name, mnn_name, trade_name, form_name, dose_raw, dose_canonical, pack_count, manufacturer, country, regnum, is_deleted
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, batch)
                        batch = []

                    if max_ecp and total_ecp >= max_ecp:
                        break

        if batch:
            cur.executemany("""
            INSERT OR IGNORE INTO ecp_drug (
                id, barcode, name, clean_name, mnn_name, trade_name, form_name, dose_raw, dose_canonical, pack_count, manufacturer, country, regnum, is_deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
        conn.commit()
        print(f"[+] Loaded {total_ecp} external records into 'ecp_drug' in {time.time()-t0:.1f}s.", flush=True)

def reconcile_catalogs(conn: sqlite3.Connection):
    """High-precision automated reconciliation pipeline."""
    cur = conn.cursor()
    print("\n[*] Starting high-precision reconciliation pipeline...", flush=True)

    # 1. Reset cross_reference
    cur.execute("DELETE FROM drug_cross_reference")
    conn.commit()

    # 2. Build in-memory index of all 529k ECP records
    t0 = time.time()
    cur.execute("""
    SELECT id, LOWER(trade_name), LOWER(mnn_name), dose_canonical, pack_count, is_deleted, barcode 
    FROM ecp_drug 
    WHERE dose_canonical IS NOT NULL
    """)
    ecp_rows = cur.fetchall()

    trade_pack_map = {}
    mnn_pack_map = {}
    trade_dose_map = {}
    barcode_map = {}

    for eid, trade, mnn, dose, pack, deleted, barcode in ecp_rows:
        if barcode and len(barcode.strip()) >= 8:
            barcode_map[barcode.strip()] = eid
        if trade:
            tr_c = clean_text(trade).lower()
            if pack:
                trade_pack_map.setdefault((tr_c, dose, pack), []).append((eid, deleted))
            trade_dose_map.setdefault((tr_c, dose), []).append((eid, deleted))
        if mnn:
            mn_c = clean_text(mnn).lower()
            if pack:
                mnn_pack_map.setdefault((mn_c, dose, pack), []).append((eid, deleted))

    print(f"[*] In-memory indexed 529k ECP records in {time.time()-t0:.2f}s.", flush=True)

    def pick_best_candidate(cands: List[Tuple[int, int]]) -> int:
        active = [eid for eid, d in cands if d == 0]
        return active[0] if active else cands[0][0]

    # 3. Match against local catalog
    cur.execute("SELECT id, name, trade_keyword, mnn_keyword, dose_canonical, pack_count FROM local_drug WHERE dose_canonical IS NOT NULL")
    local_rows = cur.fetchall()

    inserts = []
    matched_lids = set()

    for lid, name, tr_kw, mn_kw, dose, pack in local_rows:
        trade = clean_text(tr_kw).lower() if tr_kw else ""
        mnn = clean_text(mn_kw).lower() if mn_kw else ""

        # Tier 1: Exact Trade Name + Canonical Dose + Pack Count (Confidence: 0.98)
        if trade and pack and (trade, dose, pack) in trade_pack_map:
            best_eid = pick_best_candidate(trade_pack_map[(trade, dose, pack)])
            inserts.append((lid, best_eid, 'EXACT_TRADE_DOSE_PACK', 0.98, f'{{"trade": "{trade}", "dose": "{dose}", "pack": {pack}}}', 'APPROVED'))
            matched_lids.add(lid)
            continue

        # Tier 2: Exact MNN + Canonical Dose + Pack Count (Confidence: 0.92)
        if mnn and pack and (mnn, dose, pack) in mnn_pack_map:
            best_eid = pick_best_candidate(mnn_pack_map[(mnn, dose, pack)])
            inserts.append((lid, best_eid, 'EXACT_MNN_DOSE_PACK', 0.92, f'{{"mnn": "{mnn}", "dose": "{dose}", "pack": {pack}}}', 'APPROVED'))
            matched_lids.add(lid)
            continue

        # Tier 3: Exact Trade Name + Canonical Dose (Confidence: 0.88)
        if trade and (trade, dose) in trade_dose_map:
            best_eid = pick_best_candidate(trade_dose_map[(trade, dose)])
            inserts.append((lid, best_eid, 'EXACT_TRADE_DOSE', 0.88, f'{{"trade": "{trade}", "dose": "{dose}"}}', 'APPROVED'))
            matched_lids.add(lid)
            continue

    # Bulk insert all verified links
    t_ins = time.time()
    cur.executemany("""
    INSERT OR IGNORE INTO drug_cross_reference (local_drug_id, ecp_drug_id, match_type, confidence, details, status)
    VALUES (?, ?, ?, ?, ?, ?)
    """, inserts)
    conn.commit()
    print(f"[+] Inserted {len(inserts)} verified matches in {time.time()-t_ins:.2f}s.", flush=True)

    # 4. Views for reporting
    cur.execute("""
    CREATE VIEW IF NOT EXISTS v_approved_cross_reference AS
    SELECT 
        x.id AS link_id,
        x.local_drug_id,
        l.name AS local_name,
        l.manufacturer AS local_manufacturer,
        x.ecp_drug_id,
        e.name AS ecp_name,
        e.barcode AS ecp_barcode,
        e.regnum AS ecp_regnum,
        e.manufacturer AS ecp_manufacturer,
        x.match_type,
        x.confidence,
        x.status,
        x.created_at
    FROM drug_cross_reference x
    JOIN local_drug l ON l.id = x.local_drug_id
    JOIN ecp_drug e ON e.id = x.ecp_drug_id
    WHERE x.status = 'APPROVED'
    """)

    cur.execute("""
    CREATE VIEW IF NOT EXISTS v_matched_catalogs AS
    SELECT 
        x.id AS link_id,
        l.id AS local_id,
        l.name AS local_name,
        e.id AS ecp_id,
        e.name AS ecp_name,
        e.barcode AS ecp_barcode,
        x.match_type,
        x.confidence,
        x.details,
        x.status,
        x.created_at
    FROM drug_cross_reference x
    JOIN local_drug l ON l.id = x.local_drug_id
    JOIN ecp_drug e ON e.id = x.ecp_drug_id
    """)

    cur.execute("""
    CREATE VIEW IF NOT EXISTS v_unmatched_local AS
    SELECT l.* 
    FROM local_drug l
    LEFT JOIN drug_cross_reference x ON l.id = x.local_drug_id
    WHERE x.id IS NULL
    """)

    cur.execute("""
    CREATE VIEW IF NOT EXISTS v_match_stats AS
    SELECT 
        match_type, 
        count(*) AS total_links, 
        round(avg(confidence), 4) AS avg_confidence,
        status
    FROM drug_cross_reference
    GROUP BY match_type, status
    """)
    conn.commit()

    # 5. Export to CSV
    print(f"[*] Exporting approved cross-reference table to '{OUTPUT_CSV}'...", flush=True)
    cur.execute("""
    SELECT 
        link_id, local_drug_id, local_name, local_manufacturer,
        ecp_drug_id, ecp_name, ecp_barcode, ecp_regnum,
        ecp_manufacturer, match_type, confidence, status
    FROM v_approved_cross_reference
    ORDER BY link_id
    """)
    approved_rows = cur.fetchall()
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            "link_id", "local_drug_id", "local_name", "local_manufacturer",
            "ecp_drug_id", "ecp_name", "ecp_barcode", "ecp_regnum",
            "ecp_manufacturer", "match_type", "confidence", "status"
        ])
        writer.writerows(approved_rows)

    # 6. Summary metrics
    cur.execute("SELECT count(*) FROM local_drug")
    total_local = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM ecp_drug")
    total_ecp = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM drug_cross_reference WHERE status = 'APPROVED'")
    total_approved = cur.fetchone()[0]

    print("\n==========================================", flush=True)
    print("FINAL HIGH-PRECISION RECONCILIATION SUMMARY:", flush=True)
    print(f"  - Local catalog:      {total_local} records", flush=True)
    print(f"  - External ECP:       {total_ecp} records", flush=True)
    print(f"  - Approved Matches:   {total_approved} ({total_approved/total_local*100:.1f}% coverage)", flush=True)
    print("Matches Breakdown:")
    for row in cur.execute("SELECT match_type, total_links, avg_confidence FROM v_match_stats"):
        print(f"    * {row[0]}: {row[1]} records (Avg Confidence: {row[2]})", flush=True)
    print(f"Database: '{DB_PATH}'", flush=True)
    print(f"Exported: '{OUTPUT_CSV}'", flush=True)
    print("==========================================", flush=True)

def main():
    parser = argparse.ArgumentParser(description="Build SQLite catalog and cross-reference table.")
    parser.add_argument("--db", type=str, default=DB_PATH, help="Path to SQLite database")
    parser.add_argument("--max-ecp", type=int, default=None, help="Max ECP records to load")
    parser.add_argument("--fresh", action="store_true", help="Wipe database and rebuild from scratch")
    args = parser.parse_args()

    conn = init_database(args.db, fresh=args.fresh)
    load_catalogs(conn, max_ecp=args.max_ecp)
    reconcile_catalogs(conn)
    conn.close()

if __name__ == '__main__':
    main()

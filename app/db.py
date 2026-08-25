import sqlite3, openpyxl
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "parcelpilot.db"
XLSX_PATH = Path(__file__).parent.parent / "data" / "ParcelPilot_Assessment_Data.xlsx"

SNAPSHOT_TIME = "2026-08-16 11:00:00"  # from README sheet, Asia/Kolkata

def build_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)

    for sheet_name in ["accounts", "orders", "tickets"]:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        headers = rows[0]
        cols_sql = ", ".join(f'"{h}" TEXT' for h in headers)
        conn.execute(f'CREATE TABLE {sheet_name} ({cols_sql})')
        placeholders = ", ".join("?" for _ in headers)
        for row in rows[1:]:
            conn.execute(
                f'INSERT INTO {sheet_name} VALUES ({placeholders})',
                [str(v) if v is not None else None for v in row]
            )
    conn.commit()
    conn.close()

def get_conn():
    if not DB_PATH.exists():
        build_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

if __name__ == "__main__":
    build_db()
    print("DB built at", DB_PATH)
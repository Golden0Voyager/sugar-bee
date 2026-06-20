"""Migration script: local SQLite → Cloud SQL PostgreSQL.

Usage:
    uv run python scripts/migrate_to_cloudsql.py

Requires:
    - cloud-sql-proxy (brew install cloud-sql-proxy)
    - SUGAR_BEE_DATABASE_URL env var or --url argument
"""

import argparse
import atexit
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


TABLES_ORDER = [
    'app_users',
    'user_profiles',
    'records',
    'medication_plans',
    'dosage_history',
    'medication_logs',
    'health_analyses',
    'chat_messages',
    'user_auth_providers',
]

BOOLEAN_COLS = {
    'app_users': ['is_active'],
    'records': ['is_predicted'],
    'medication_plans': ['is_active'],
    'medication_logs': ['taken'],
    'health_analyses': ['is_auto_generated'],
    'user_auth_providers': ['verified'],
}


def get_sqlite_conn():
    import sqlite3
    db_path = os.environ.get('SUGAR_BEE_DB_PATH', os.path.join(BASE_DIR, 'glucose.db'))
    if not os.path.exists(db_path):
        print(f"[ERROR] SQLite DB not found: {db_path}")
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def start_proxy(instance_name, port=5433):
    proc = subprocess.Popen(
        ['cloud-sql-proxy', f'--port={port}', instance_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(lambda: proc.terminate())
    print(f"[proxy] Starting cloud-sql-proxy on port {port}...")
    time.sleep(2)
    if proc.poll() is not None:
        print("[ERROR] cloud-sql-proxy failed to start")
        sys.exit(1)
    print("[proxy] Ready")
    return proc


def get_pg_conn(host, port, user, password, dbname):
    import psycopg2
    conn = psycopg2.connect(
        host=host, port=port, user=user, password=password, dbname=dbname,
    )
    conn.autocommit = True
    return conn


def parse_db_url(url):
    from urllib.parse import parse_qs, urlparse
    # Handle postgresql+psycopg2://user:pass@/dbname?host=/cloudsql/...
    clean = url.replace('+psycopg2', '').replace('+psycopg', '')
    parsed = urlparse(clean)
    qs = parse_qs(parsed.query)
    return {
        'host': 'localhost',
        'port': 5433,
        'user': parsed.username or 'postgres',
        'password': parsed.password or '',
        'dbname': parsed.path.lstrip('/') or qs.get('dbname', ['sugar_bee'])[0],
    }


def fetch_sqlite_data(sqlite_conn, table):
    cur = sqlite_conn.execute(f"SELECT * FROM {table} ORDER BY id")
    rows = cur.fetchall()
    if not rows:
        print(f"  [skip] {table}: 0 rows")
        return [], []
    columns = [desc[0] for desc in cur.description]
    return columns, [dict(row) for row in rows]


def convert_row(row, table):
    """Convert SQLite row values for PostgreSQL."""
    converted = {}
    for col, val in row.items():
        if val is None:
            converted[col] = None
            continue
        bool_cols = BOOLEAN_COLS.get(table, [])
        if col in bool_cols:
            converted[col] = 1 if val else 0
        elif isinstance(val, float) and str(val) == '0':
            converted[col] = 0.0
        elif col == 'is_predicted':
            converted[col] = 1 if val else 0
        else:
            converted[col] = val
    return converted


def build_insert_sql(table, columns):
    cols = ', '.join(columns)
    placeholders = ', '.join(['%s'] * len(columns))
    return f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"


def migrate_table(pg_conn, sqlite_conn, table, batch_size=500):
    print(f"\n[{table}] Reading from SQLite...")
    columns, rows = fetch_sqlite_data(sqlite_conn, table)
    if not rows:
        return 0

    insert_sql = build_insert_sql(table, columns)
    total = len(rows)
    print(f"  -> Inserting {total} rows to PostgreSQL (batch={batch_size})...")

    cur = pg_conn.cursor()
    inserted = 0
    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        values = [tuple(convert_row(r, table).values()) for r in batch]
        try:
            cur.executemany(insert_sql, values)
            pg_conn.commit()
            inserted += len(batch)
            print(f"  ... {inserted}/{total}")
        except Exception as e:
            pg_conn.rollback()
            print(f"  [ERROR] batch failed at row {i}: {e}")
            raise

    # Update sequence for SERIAL primary key
    try:
        cur.execute(f"SELECT setval('{table}_id_seq', (SELECT MAX(id) FROM {table}))")
        pg_conn.commit()
    except Exception as e:
        pg_conn.rollback()
        print(f"  [warn] sequence update failed: {e}")

    cur.close()
    return inserted


def main():
    parser = argparse.ArgumentParser(description='Migrate SQLite → Cloud SQL PostgreSQL')
    parser.add_argument('--url', help='PostgreSQL connection URL (default: SUGAR_BEE_DATABASE_URL env)')
    parser.add_argument('--proxy-port', type=int, default=5433, help='cloud-sql-proxy port')
    parser.add_argument('--skip-proxy', action='store_true', help='Skip starting proxy (already running)')
    parser.add_argument('--tables', nargs='+', help='Specific tables to migrate (default: all)')
    args = parser.parse_args()

    db_url = args.url or os.environ.get('SUGAR_BEE_DATABASE_URL')
    if not db_url:
        print("[ERROR] Provide --url or set SUGAR_BEE_DATABASE_URL")
        sys.exit(1)

    pg_info = parse_db_url(db_url)
    tables_to_migrate = args.tables or TABLES_ORDER

    # Connect to SQLite
    sqlite_conn = get_sqlite_conn()
    print(f"[sqlite] Connected: {os.environ.get('SUGAR_BEE_DB_PATH', os.path.join(BASE_DIR, 'glucose.db'))}")

    # Start proxy if needed
    instance_name = 'project-c0560c79-7c6a-4f31-a11:asia-east2:sugar-bee-db-hk'
    proxy_proc = None
    if not args.skip_proxy:
        proxy_proc = start_proxy(instance_name, args.proxy_port)
    else:
        print("[proxy] Skipped (--skip-proxy)")

    try:
        pg_conn = get_pg_conn(
            host='localhost',
            port=args.proxy_port,
            user=pg_info['user'],
            password=pg_info['password'],
            dbname=pg_info['dbname'],
        )
        print(f"[postgres] Connected: {pg_info['user']}@{pg_info['dbname']}")

        total = 0
        for table in tables_to_migrate:
            n = migrate_table(pg_conn, sqlite_conn, table)
            total += n

        print(f"\n{'='*50}")
        print(f"Migration complete! {total} total rows inserted.")
        print(f"{'='*50}")

        # Check counts
        print("\n[verify] Row counts:")
        cur = pg_conn.cursor()
        for table in TABLES_ORDER:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            sqlite_cur = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}")
            sqlite_count = sqlite_cur.fetchone()[0]
            status = "✓" if count == sqlite_count else "✗"
            print(f"  {status} {table}: PostgreSQL={count}, SQLite={sqlite_count}")
        cur.close()

    finally:
        pg_conn.close()
        sqlite_conn.close()
        if proxy_proc:
            proxy_proc.terminate()

    print("\nDone. Restart the Cloud Run service to see migrated data.")


if __name__ == '__main__':
    main()

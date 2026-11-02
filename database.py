"""SQLite storage for accounts, customers, transfers and audit logs."""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

def _load_dotenv() -> None:
    """Load .env next to this file (no dependency); real env vars win.

    Lives here because database.py is imported first by every module, so
    .env values are present before anyone reads process env at import time.
    """
    try:
        path = Path(__file__).parent / ".env"
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except OSError:
        pass


_load_dotenv()

DB_PATH = os.environ.get("HDFC_BANK_DB", str(Path(__file__).parent / "hdfc_bank.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    account_type TEXT NOT NULL,
    balance REAL NOT NULL,
    status TEXT NOT NULL,
    opened_date TEXT NOT NULL,
    kyc_status TEXT NOT NULL,
    notes TEXT DEFAULT '',
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    request_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    action TEXT NOT NULL,
    args_json TEXT NOT NULL,
    eligibility TEXT DEFAULT '',
    governance_decision TEXT DEFAULT '',
    execution_status TEXT DEFAULT '',
    reason TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS review_requests (
    ticket_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    reason TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_blocks (
    customer_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT '',
    blocked_until TEXT NOT NULL,
    strikes INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    user_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_events_customer_time
    ON chat_events (customer_id, created_at);
"""


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> str:
    path = db_path or DB_PATH
    conn = get_conn(path)
    conn.executescript(SCHEMA)
    conn.commit()
    # Seed only on first launch (empty customers table)
    count = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]
    if count == 0:
        _seed(conn)
    conn.close()
    return path


def _seed(conn: sqlite3.Connection) -> None:
    customers = [
        ("ACC-01", "Rajesh Kumar", "rajesh@example.in"),
        ("ACC-02", "Anita Desai", "anita@example.in"),
        ("ACC-03", "Vikram Singh", "vikram@example.in"),
        ("ACC-04", "Meera Joshi", "meera@example.in"),
        ("ACC-05", "Arun Pillai", "arun@example.in"),
        ("ACC-06", "Kavita Nair", "kavita@example.in"),
    ]
    conn.executemany(
        "INSERT INTO customers (customer_id, name, email) VALUES (?, ?, ?)", customers
    )

    today = date.today()

    def d(days_ago: int) -> str:
        return (today - timedelta(days=days_ago)).isoformat()

    # account_id, customer, type, balance, status, days_ago, kyc, notes
    accounts = [
        ("ACC-01", "ACC-01", "Savings", 75000.0, "active", 365, "verified", "Salary account."),
        ("ACC-02", "ACC-02", "Savings", 45000.0, "active", 200, "verified", "Savings account."),
        ("ACC-03", "ACC-03", "Current", 150000.0, "active", 500, "verified", "Business current account."),
        ("ACC-04", "ACC-04", "Savings", 12000.0, "active", 100, "verified", "Student account."),
        ("ACC-05", "ACC-05", "Savings", 300000.0, "active", 150, "verified", "High balance savings."),
        ("ACC-06", "ACC-06", "Savings", 5000.0, "active", 50, "pending", "KYC pending verification."),
    ]

    for aid, cid, atype, bal, status, ago, kyc, notes in accounts:
        conn.execute(
            """INSERT INTO accounts
               (account_id, customer_id, account_type, balance, status,
                opened_date, kyc_status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (aid, cid, atype, bal, status, d(ago), kyc, notes),
        )
    conn.commit()


def reset_db(db_path: str | None = None) -> str:
    """Delete and reseed (used by tests / demo reset)."""
    path = db_path or DB_PATH
    if os.path.exists(path):
        os.remove(path)
    return init_db(path)


def reset_demo_data(db_path: str | None = None) -> None:
    """Wipe business + audit data and reseed fresh demo state."""
    path = db_path or DB_PATH
    conn = get_conn(path)
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM audit_log")
    conn.execute("DELETE FROM chat_blocks")
    conn.execute("DELETE FROM chat_events")
    conn.execute("DELETE FROM accounts")
    conn.execute("DELETE FROM customers")
    _seed(conn)
    conn.close()

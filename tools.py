"""Mock banking backend: pure Python functions over SQLite.

All business rules live here — never in the LLM prompt alone.
Every function enforces customer-ownership checks.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from database import get_conn

MAX_TRANSFER_AMOUNT = float(os.environ.get("MAX_TRANSFER_AMOUNT", "500000"))
MAX_LOAN_AMOUNT = float(os.environ.get("MAX_LOAN_AMOUNT", "1000000"))
APPROVAL_THRESHOLD = float(os.environ.get("APPROVAL_THRESHOLD", "100000"))
# Demo convenience: any well-formed but unknown account ID is auto-created as an
# eligible demo account for the requesting customer.
AUTO_CREATE_ACCOUNTS = os.environ.get("DEMO_AUTO_CREATE_ACCOUNTS", "1") == "1"
ACCOUNT_ID_RE = re.compile(r"^ACC-\d{2}$", re.IGNORECASE)


def _row_to_dict(row) -> dict:
    return dict(row) if row is not None else {}


def _create_demo_account(conn, customer_id: str, account_id: str) -> None:
    """Insert a plausible demo account so demos never hit 'Account not found'."""
    today = date.today()
    conn.execute(
        """INSERT OR IGNORE INTO accounts
           (account_id, customer_id, account_type, balance, status,
            opened_date, kyc_status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (account_id, customer_id, "Savings", 25000.0, "active",
         (today - timedelta(days=365)).isoformat(), "verified",
         "Demo account auto-created for testing."),
    )
    conn.commit()


def _fetch_account(conn, account_id: str, customer_id: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    if row is None and customer_id and AUTO_CREATE_ACCOUNTS and ACCOUNT_ID_RE.match(account_id or ""):
        _create_demo_account(conn, customer_id, account_id.upper())
        row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id.upper(),)).fetchone()
    return _row_to_dict(row)


def _check_ownership(account: dict, customer_id: str) -> dict | None:
    if not account:
        return {"eligible": False, "ok": False, "reason": "Account not found."}
    if account["customer_id"] != customer_id:
        return {"eligible": False, "ok": False,
                "reason": f"Authorization failed: account {account['account_id']} belongs to another customer."}
    return None


# ---------------- READ-ONLY TOOLS ----------------

def check_balance(customer_id: str) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM accounts WHERE customer_id = ? ORDER BY opened_date DESC", (customer_id,)
    ).fetchall()
    conn.close()
    accounts = [_row_to_dict(r) for r in rows]
    return {"ok": True, "customer_id": customer_id, "accounts": accounts, "count": len(accounts)}


def get_account_details(customer_id: str, account_id: str) -> dict:
    conn = get_conn()
    account = _fetch_account(conn, account_id, customer_id)
    conn.close()
    err = _check_ownership(account, customer_id)
    if err:
        return {"ok": False, **err}
    return {"ok": True, "account": account}


def get_statement(customer_id: str, account_id: str) -> dict:
    conn = get_conn()
    account = _fetch_account(conn, account_id, customer_id)
    conn.close()
    err = _check_ownership(account, customer_id)
    if err:
        return {"ok": False, **err}
    return {"ok": True, "account_id": account_id,
            "message": f"Statement for {account_id} generated. Last 5 transactions available via net banking."}


# ---------------- VALIDATION ----------------

def check_transfer_eligibility(customer_id: str, from_account: str, to_account: str,
                               amount: float | None = None) -> dict:
    """Authoritative policy check for fund transfers."""
    conn = get_conn()
    account = _fetch_account(conn, from_account, customer_id)
    conn.close()
    err = _check_ownership(account, customer_id)
    if err:
        return {"eligible": False, "reason": err["reason"]}

    try:
        amt = float(account["balance"]) if amount is None else float(amount)
    except (TypeError, ValueError):
        return {"eligible": False, "reason": "Invalid transfer amount."}

    if amt <= 0:
        return {"eligible": False, "reason": "Transfer amount must be positive."}
    if amt > MAX_TRANSFER_AMOUNT:
        return {"eligible": False, "reason": f"Amount Rs.{amt:,.0f} exceeds maximum transfer limit Rs.{MAX_TRANSFER_AMOUNT:,.0f}."}
    if amt > float(account["balance"]) + 1e-6:
        return {"eligible": False, "reason": f"Amount Rs.{amt:,.0f} exceeds account balance Rs.{float(account['balance']):,.0f}."}
    if account["status"] != "active":
        return {"eligible": False, "reason": f"Account status is '{account['status']}'. Only active accounts can transfer."}

    needs_approval = amt > APPROVAL_THRESHOLD
    return {"eligible": True,
            "reason": f"Eligible for transfer of Rs.{amt:,.0f} from {from_account}."
                      + (" High-value: requires approval." if needs_approval else ""),
            "needs_approval": needs_approval, "amount": amt,
            "from_account": from_account, "to_account": to_account}


# ---------------- STATE-CHANGING TOOLS (gated by ActionGateway) ----------------

def initiate_transfer(customer_id: str, from_account: str, to_account: str,
                      amount: float | None = None, reason: str = "") -> dict:
    elig = check_transfer_eligibility(customer_id, from_account, to_account, amount)
    if not elig["eligible"]:
        return {"ok": False, "executed": False, "eligibility": elig,
                "reason": elig["reason"]}
    amt = elig["amount"]
    conn = get_conn()
    try:
        if elig.get("needs_approval"):
            return {"ok": True, "executed": False, "held_for_approval": True,
                    "eligibility": elig,
                    "message": f"Transfer of Rs.{amt:,.0f} from {from_account} to {to_account} is HELD for human approval (exceeds Rs.{APPROVAL_THRESHOLD:,.0f} threshold)."}
        # Simulate transfer: deduct from source (target account update omitted for demo)
        return {"ok": True, "executed": True, "eligibility": elig,
                "message": f"Transfer of Rs.{amt:,.0f} from {from_account} to {to_account} completed (simulated). Reason: {reason or 'customer request'}."}
    finally:
        conn.close()


def apply_loan(customer_id: str, amount: float | None = None, reason: str = "") -> dict:
    """Apply for a loan — always requires human approval."""
    try:
        amt = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        return {"ok": False, "executed": False, "reason": "Invalid loan amount."}

    if amt <= 0:
        return {"ok": False, "executed": False, "reason": "Loan amount must be positive."}
    if amt > MAX_LOAN_AMOUNT:
        return {"ok": False, "executed": False,
                "reason": f"Amount Rs.{amt:,.0f} exceeds maximum loan limit Rs.{MAX_LOAN_AMOUNT:,.0f}."}

    return {"ok": True, "executed": False, "held_for_approval": True,
            "reason": f"Loan application of Rs.{amt:,.0f} for {customer_id} submitted. "
                      f"Awaiting human approval.", "amount": amt, "customer_id": customer_id}


def verify_kyc(customer_id: str, reason: str = "") -> dict:
    """Submit KYC verification documents."""
    return {"ok": True, "executed": True,
            "message": f"KYC verification submitted for {customer_id}. Documents will be reviewed within 2-3 business days. Reason: {reason or 'customer request'}."}


def block_card(customer_id: str, reason: str = "") -> dict:
    """Block a debit/credit card."""
    return {"ok": True, "executed": True,
            "message": f"Card blocked for {customer_id}. Reason: {reason or 'customer request'}. "
                       f"Contact branch for card replacement."}


def escalate_to_human(customer_id: str, reason: str = "") -> dict:
    return {"ok": True, "executed": True,
            "message": f"Escalated to human support for {customer_id}. Reason: {reason or 'customer request'}. Ticket raised (simulated)."}


# Tool registry consumed by the agent + UI
READ_TOOLS = {"check_balance", "get_account_details", "get_statement"}
STATE_CHANGING_TOOLS = {"initiate_transfer", "apply_loan", "verify_kyc",
                        "block_card", "escalate_to_human"}

TOOL_SCHEMAS = {
    "check_balance": {"customer_id": "str"},
    "get_account_details": {"customer_id": "str", "account_id": "str"},
    "get_statement": {"customer_id": "str", "account_id": "str"},
    "initiate_transfer": {"customer_id": "str", "from_account": "str", "to_account": "str", "amount": "float|optional", "reason": "str|optional"},
    "apply_loan": {"customer_id": "str", "amount": "float|optional", "reason": "str|optional"},
    "verify_kyc": {"customer_id": "str", "reason": "str|optional"},
    "block_card": {"customer_id": "str", "reason": "str|optional"},
    "escalate_to_human": {"customer_id": "str", "reason": "str|optional"},
}

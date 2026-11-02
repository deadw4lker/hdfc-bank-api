"""Local policy simulator used in Demo Mode.

Clearly labelled: this is NOT WARDEN. It mimics allow/block/hold
decisions so the demo works without a governance backend.
"""
from __future__ import annotations

import re

from tools import APPROVAL_THRESHOLD, MAX_TRANSFER_AMOUNT

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"system\s+override",
    r"transfer\s+every\s+account",
    r"ignore\s+the\s+banking\s+policy",
    r"override\s+(the\s+)?policy",
    r"approve\s+my\s+transfer\s+(without|regardless)",
    r"do\s+not\s+ask",
]


def _has_injection(text: str) -> bool:
    t = (text or "").lower()
    return any(re.search(p, t) for p in INJECTION_PATTERNS)


def simulate_decision(action: str, args: dict, context: dict) -> dict:
    """Return {decision: ALLOW|BLOCK|REQUIRE_APPROVAL, reason}."""
    reason_text = str(args.get("reason", "")) + " " + str(context.get("user_text", ""))

    # 1. Malformed / invalid arguments -> BLOCK
    amt = args.get("amount", None)
    if amt is not None:
        try:
            amt_f = float(amt)
            if amt_f <= 0:
                return {"decision": "BLOCK", "reason": "Invalid amount: must be positive."}
            if amt_f > MAX_TRANSFER_AMOUNT:
                return {"decision": "BLOCK", "reason": f"Amount exceeds maximum transfer limit Rs.{MAX_TRANSFER_AMOUNT:,.0f}."}
        except (TypeError, ValueError):
            return {"decision": "BLOCK", "reason": "Invalid amount: not a number."}
    if action in ("initiate_transfer", "block_card", "verify_kyc"):
        if not args.get("from_account") and not args.get("account_id"):
            # transfer needs from_account; block_card/verify_kyc need account context
            if action != "escalate_to_human" and action != "apply_loan":
                return {"decision": "BLOCK", "reason": "Missing account_id."}

    # 2. Ownership check at gateway layer (defence in depth)
    account_owner = context.get("account_owner")
    actor = context.get("customer_id")
    if account_owner and actor and account_owner != actor:
        return {"decision": "BLOCK",
                "reason": f"Cross-customer action blocked: {actor} cannot act on {account_owner}'s account."}

    # 3. Bulk / injection-driven destructive scope -> BLOCK
    if _has_injection(reason_text) or context.get("bulk_action"):
        if action in ("initiate_transfer", "block_card"):
            return {"decision": "BLOCK",
                    "reason": "Blocked: instruction-override / bulk-action pattern detected. Governance does not allow policy override via chat."}

    # 4. High-value hold
    if action == "initiate_transfer" and amt is not None:
        try:
            if float(amt) > APPROVAL_THRESHOLD:
                return {"decision": "REQUIRE_APPROVAL",
                        "reason": f"Amount Rs.{float(amt):,.0f} exceeds approval threshold Rs.{APPROVAL_THRESHOLD:,.0f}; held for human review."}
        except (TypeError, ValueError):
            pass

    # 5. Loan always requires human approval
    if action == "apply_loan":
        try:
            amt_f = float(args.get("amount") or 0)
            if amt_f > 0:
                return {"decision": "REQUIRE_APPROVAL",
                        "reason": f"Loan application of Rs.{amt_f:,.0f} requires human approval."}
        except (TypeError, ValueError):
            pass

    return {"decision": "ALLOW", "reason": "Local demo policy: checks passed."}

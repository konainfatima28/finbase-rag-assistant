"""Soft domain router (PROMPT.md §6.3): keyword/regex -> likely doc_ids -> score boost. Never a filter."""

from __future__ import annotations

import re

ROUTES: dict[str, re.Pattern[str]] = {
    "personal_loans": re.compile(
        r"\b(personal )?loans?\b|foreclos|pre-?pay|part[- ]pay|\bemi\b|\bnoc\b|cibil|bureau|foir|bounce|processing fee|"
        r"disburs|tenure|borrow|penal|mandate|e-?sign|cancellation|cooling",
        re.I,
    ),
    "credit_cards": re.compile(
        r"credit card|\bcards?\b|\bluxe\b|\bneo\b|\bmetal\b|lounge|\bmad\b|minimum amount due|forex|markup|cashback|"
        r"reward|annual fee|grace period|finance charge|cash advance|over-?limit|late payment",
        re.I,
    ),
    "payments_upi": re.compile(
        r"\bupi\b|refund|chargeback|\bu\d{2}\b|reversal|failed (transaction|payment|debit)|p2p|p2m|imps|npci|"
        r"dispute|provisional credit|compensation|\bpin reset|merchant|autopay|recurring",
        re.I,
    ),
    "fd_wealth": re.compile(
        r"\bfd\b|fixed deposit|\btds\b|15g|15h|\bsip\b|mutual fund|gold|silver|senior citizen|premature|dicgc|"
        r"wealth|crypto|bitcoin|ethereum|intraday|f&o|chit|ponzi|farmland|agricultur|deposit insurance",
        re.I,
    ),
    "kyc_security": re.compile(
        r"\bkyc\b|v-?kyc|video kyc|\bovd\b|aadhaar|\bpan\b|passport|dormant|inactive|reactivat|fraud|helpline|"
        r"zero liability|freeze|block(ed)? card|2fa|two[- ]factor|unauthori[sz]ed|stolen|lost",
        re.I,
    ),
    "savings_account": re.compile(
        r"savings|\batm\b|interest slab|interest rate on (my )?(savings|balance)|debit card|minimum balance|\bmab\b|"
        r"zero balance|closure|close (my )?account|neft|rtgs|contactless|cash deposit|balance inquiry",
        re.I,
    ),
}


def route(query: str) -> list[str]:
    """doc_ids whose domain keywords match the query (empty = no preference). Used only as a soft boost."""
    return [doc_id for doc_id, pattern in ROUTES.items() if pattern.search(query)]

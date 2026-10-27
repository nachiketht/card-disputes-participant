"""A duplicate stands only when two fetched charges match.

Merchant is the merchant id on the transaction payload. Amount and currency
must match exactly. One shared check serves every approach.
"""

from dispute_copilot.fields import FACT_AMOUNT, FACT_CURRENCY, Classification
from dispute_copilot.rules.classify import Decision


def duplicate_pair(payload: dict | None) -> bool:
    """True when two rows share merchant id, amount, and currency."""
    if not isinstance(payload, dict):
        return False
    rows = payload.get("transactions")
    if not isinstance(rows, list):
        return False
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _charge_key(row)
        if key is None:
            continue
        if key in seen:
            return True
        seen.add(key)
    return False


def apply_duplicate_guard(
    decision: Decision,
    payload: dict | None,
    transactions_unavailable: bool,
) -> tuple[Decision, bool]:
    """Keep a duplicate, or withdraw it.

    Unavailable transactions stay on the existing path: the class is unchanged.
    A fetched payload with no exact pair withdraws the class. The caller routes
    that result to manual review.
    """
    if decision.classification != Classification.DUPLICATE_CHARGE:
        return decision, False
    if transactions_unavailable:
        return decision, False
    if duplicate_pair(payload):
        return decision, False
    return Decision(Classification.AMBIGUOUS, (), False), True


def _charge_key(row: dict) -> tuple[str, str, str] | None:
    merchant = row.get("merchant_id")
    amount = row.get(FACT_AMOUNT)
    currency = row.get(FACT_CURRENCY)
    if not isinstance(merchant, str) or not merchant.strip():
        return None
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    if not isinstance(currency, str) or not currency.strip():
        return None
    return (merchant.strip(), _amount_text(amount), currency.strip())


def _amount_text(amount: int | float) -> str:
    if isinstance(amount, float) and amount.is_integer():
        return str(int(amount))
    return str(amount)

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from catalog import ACTION_LABELS, CATEGORY_LABELS, NAVIGATION_ACTIONS, WRITE_ACTIONS


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Operation:
    action: str
    amount: Decimal | None
    currency: str
    category_id: str | None
    occurred_at: str

    def to_dict(self):
        return {**self.__dict__, "amount": str(self.amount) if self.amount is not None else None}

    @property
    def is_write(self):
        return self.action in WRITE_ACTIONS

    def describe(self):
        category = f" — {CATEGORY_LABELS[self.category_id]}" if self.category_id else ""
        if not self.is_write:
            period = f" ({self.occurred_at[:7]})" if self.action == "report" else ""
            return f"{ACTION_LABELS[self.action]}{category}{period}"
        return f"{ACTION_LABELS[self.action]}{category} — {self.amount:,.2f} {self.currency} ({self.occurred_at})"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("Duplicate JSON key")
        result[key] = value
    return result


def strict_json(text):
    def reject(_):
        raise ValidationError("Non-finite JSON number")

    try:
        return json.loads(
            text, parse_float=Decimal, parse_constant=reject, object_pairs_hook=_unique_object
        )
    except (ValueError, TypeError) as error:
        raise ValidationError("Invalid JSON") from error


def validate_operations(items, settings, now=None, stored=False):
    if not isinstance(items, list) or not 1 <= len(items) <= settings.max_operations:
        raise ValidationError("Invalid operation count")
    today = (
        (now or datetime.now(ZoneInfo(settings.timezone)))
        .astimezone(ZoneInfo(settings.timezone))
        .date()
    )
    operations = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "action",
            "amount",
            "currency",
            "category_id",
            "occurred_at",
        }:
            raise ValidationError("Invalid operation fields")
        action, category = item["action"], item["category_id"]
        if not isinstance(action, str) or action not in ACTION_LABELS:
            raise ValidationError("Unknown action")
        if action in ("expense_add", "expense_sub"):
            if not isinstance(category, str) or category not in CATEGORY_LABELS:
                raise ValidationError("Unknown category")
        elif action in ("expense_add_prompt", "expense_sub_prompt"):
            if category is not None and (
                not isinstance(category, str) or category not in CATEGORY_LABELS
            ):
                raise ValidationError("Unknown category")
        elif category is not None:
            raise ValidationError("This action does not have a category")
        amount = item["amount"]
        if action in WRITE_ACTIONS:
            if isinstance(amount, bool) or not isinstance(
                amount, (int, Decimal, str) if stored else (int, Decimal)
            ):
                raise ValidationError("Amount must be numeric")
            try:
                amount = Decimal(amount)
                if (
                    not amount.is_finite()
                    or not 0 < amount <= settings.max_amount
                    or amount != amount.quantize(Decimal("0.01"))
                ):
                    raise ValidationError("Invalid amount")
            except (InvalidOperation, ValueError) as error:
                raise ValidationError("Invalid amount") from error
        elif amount is not None:
            raise ValidationError("Read and menu commands must have amount=null")
        if item["currency"] != settings.currency:
            raise ValidationError("Unsupported currency")
        raw_date = item["occurred_at"]
        if raw_date is None:
            date = today
        else:
            try:
                if not isinstance(raw_date, str):
                    raise ValueError("Invalid date type")
                dt = datetime.fromisoformat(raw_date)
                date = dt.astimezone(ZoneInfo(settings.timezone)).date() if dt.tzinfo else dt.date()
            except ValueError as error:
                raise ValidationError("Invalid date") from error
        if not today - timedelta(days=366) <= date <= today:
            raise ValidationError("Date outside supported range")
        # Personal budgets store a current balance, not a historical ledger.
        if action not in ("expense_add", "expense_sub", "report") and date != today:
            raise ValidationError("Historical dates are supported only for expenses and reports")
        operations.append(Operation(action, amount, settings.currency, category, date.isoformat()))
    if len(operations) > 1 and any(op.action in NAVIGATION_ACTIONS for op in operations):
        raise ValidationError("A menu/prompt command must be sent separately")
    return operations


def parse_result(text, settings, now=None):
    if not isinstance(text, str) or len(text) > 20000:
        raise ValidationError("Response too large")
    data = strict_json(text)
    if not isinstance(data, dict) or set(data) != {"operations", "clarification"}:
        raise ValidationError("Invalid result fields")
    clarification = data["clarification"]
    if clarification is not None:
        if (
            not isinstance(clarification, str)
            or not clarification.strip()
            or len(clarification) > 400
            or data["operations"] != []
        ):
            raise ValidationError("Invalid clarification")
        return [], clarification
    return validate_operations(data["operations"], settings, now), None

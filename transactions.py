"""Map validated operations to the SAME functions used by the button handlers."""

from datetime import datetime

from catalog import CATEGORY_LABELS
import google_api


def execute_operation(operation):
    if operation.action.startswith("expense_"):
        amount = operation.amount if operation.action == "expense_add" else -operation.amount
        google_api.add_expense_matrix(
            CATEGORY_LABELS[operation.category_id],
            amount,
            datetime.fromisoformat(operation.occurred_at),
        )
    else:
        handlers = {
            "andrei_add": (google_api.andrei_mb, "add_amb"),
            "andrei_sub": (google_api.andrei_mb, "sub_amb"),
            "max_add": (google_api.max_mb, "add_mmb"),
            "max_sub": (google_api.max_mb, "sub_mmb"),
        }
        handler, action = handlers[operation.action]
        if handler(action, operation.amount) is not True:
            raise RuntimeError("Operation rejected")

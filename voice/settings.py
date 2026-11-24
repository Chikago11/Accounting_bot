import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = False
    currency: str = "THB"
    timezone: str = "Asia/Bangkok"
    max_duration: int = 120
    max_bytes: int = 10_000_000
    max_operations: int = 10
    max_amount: Decimal = Decimal("10000000")
    ttl: int = 900
    timeout: int = 180
    data_dir: Path = ROOT / "data"
    aliases_file: Path = ROOT / "category_aliases.json"
    bridge_command: tuple[str, ...] = ()

    @classmethod
    def from_env(cls):
        if os.getenv("VOICE_INPUT_ENABLED", "false").lower() not in ("true", "1", "yes"):
            return cls()
        command = json.loads(os.environ["VOICE_BRIDGE_COMMAND"])
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(x, str) and x for x in command)
        ):
            raise ValueError("VOICE_BRIDGE_COMMAND must be a JSON argument array")
        value = cls(
            enabled=True,
            currency=os.getenv("VOICE_DEFAULT_CURRENCY", "THB"),
            timezone=os.getenv("VOICE_TIMEZONE", "Asia/Bangkok"),
            max_duration=int(os.getenv("VOICE_MAX_DURATION_SECONDS", "120")),
            max_bytes=int(os.getenv("VOICE_MAX_FILE_BYTES", "10000000")),
            max_operations=int(os.getenv("VOICE_MAX_OPERATIONS", "10")),
            max_amount=Decimal(os.getenv("VOICE_MAX_AMOUNT", "10000000")),
            ttl=int(os.getenv("VOICE_PENDING_TTL_SECONDS", "900")),
            timeout=int(os.getenv("HERMES_TIMEOUT_SECONDS", "180")),
            data_dir=Path(os.getenv("VOICE_DATA_DIR", str(ROOT / "data"))),
            aliases_file=Path(
                os.getenv("VOICE_CATEGORY_ALIASES_FILE", str(ROOT / "category_aliases.json"))
            ),
            bridge_command=tuple(command),
        )
        ZoneInfo(value.timezone)
        if value.currency != "THB":
            raise ValueError("This project's budget currency is THB; conversion is not supported")
        if not (
            1 <= value.max_duration <= 120
            and 1 <= value.max_bytes <= 10_000_000
            and 1 <= value.max_operations <= 10
            and 1 <= value.ttl <= 86400
            and 5 <= value.timeout <= 300
            and value.max_amount.is_finite()
            and 0 < value.max_amount <= Decimal("10000000")
        ):
            raise ValueError("Invalid voice limits")
        return value

from datetime import UTC, datetime


def utcnow():
    return datetime.now(UTC)


def stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def parse(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    stamp(result)
    return result

"""Conservative interpretation of device timestamps."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def capture_time(raw: str | None, zone: str) -> dict:
    result = {"raw": raw, "utc": None, "basis": "unknown", "timezone": None}
    if not raw:
        return result
    try:
        value = raw.strip().replace("Z", "+00:00")
        if len(value) >= 19 and value[4] == ":" and value[7] == ":":
            value = value[:10].replace(":", "-") + "T" + value[11:]
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            tz = ZoneInfo(zone)
            first, second = parsed.replace(tzinfo=tz, fold=0), parsed.replace(tzinfo=tz, fold=1)
            if first.utcoffset() != second.utcoffset():
                result["basis"] = "ambiguous_local_time"
                result["timezone"] = zone
                return result
            parsed = first
            result.update(basis="assumed_timezone", timezone=zone)
        else:
            result["basis"] = "embedded_offset"
        result["utc"] = parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        result["basis"] = "invalid_metadata"
    return result

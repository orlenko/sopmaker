"""Installed inspection locations. Values stay IANA IDs; browsers add readable labels."""

from functools import lru_cache
from zoneinfo import available_timezones

REGIONS = {"Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic",
           "Australia", "Europe", "Indian", "Pacific"}


@lru_cache(maxsize=1)
def timezone_options():
    return tuple(sorted({"UTC"} | {zone for zone in available_timezones()
                                  if zone.split("/", 1)[0] in REGIONS}))

"""Settings from environment variables (all optional)."""
import os
from dataclasses import dataclass

HOST = "127.0.0.1"  # deliberately not configurable: put a reverse proxy in front


def _int(name, default, lo, hi):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    v = int(raw)
    if not lo <= v <= hi:
        raise ValueError("%s must be between %d and %d" % (name, lo, hi))
    return v


@dataclass
class Config:
    port: int
    data_dir: str
    tz: str
    home_years: int
    search_years: int
    search_ttl: int
    fetches_per_minute: int
    summaries_per_minute: int
    static_dir: str

    @classmethod
    def from_env(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return cls(
            port=_int("FM_PORT", 7510, 1024, 65535),
            data_dir=os.path.abspath(os.environ.get("FM_DATA_DIR") or os.path.join(root, "data")),
            tz=os.environ.get("FM_TZ") or "America/New_York",
            home_years=_int("FM_HOME_YEARS", 4, 1, 10),
            search_years=_int("FM_SEARCH_YEARS", 3, 1, 10),
            search_ttl=_int("FM_SEARCH_TTL", 3600, 60, 86400),
            fetches_per_minute=_int("FM_FETCHES_PER_MINUTE", 30, 1, 120),
            summaries_per_minute=_int("FM_SUMMARIES_PER_MINUTE", 6, 0, 30),
            static_dir=os.path.join(root, "static"),
        )

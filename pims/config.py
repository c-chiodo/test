"""Runtime configuration for PIMS.

Everything an operator can change without a code deploy lives here, sourced
from environment variables with defaults that make the app run out of the box.
The legacy client read `FECore.INI` for the SQL instance and resolved the rest
through `Connection_Select_ConnectionString_byLabel`; the same two ideas —
an environment label and a resolvable backing store — survive, in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

#: Analytes the system knows how to record, limit-check and report on.
ANALYTES: tuple[str, ...] = (
    "moisture",
    "temp",
    "ph",
    "ffa",
    "tfa",
    "spintest",
    "flash",
    "impurities",
    "unsaponifiables",
    "linoleic",
    "stearic",
)

ANALYTE_LABELS: dict[str, str] = {
    "moisture": "Moisture",
    "temp": "Temperature",
    "ph": "pH",
    "ffa": "FFA",
    "tfa": "TFA",
    "spintest": "Spintest fallout",
    "flash": "Flash",
    "impurities": "Impurities",
    "unsaponifiables": "Unsaponifiables",
    "linoleic": "Linoleic acid",
    "stearic": "Stearic acid",
}

ANALYTE_UNITS: dict[str, str] = {
    "moisture": "%",
    "temp": "°F",
    "ph": "",
    "ffa": "%",
    "tfa": "%",
    "spintest": "mils",
    "flash": "p/f",
    "impurities": "%",
    "unsaponifiables": "%",
    "linoleic": "%",
    "stearic": "%",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Process-wide settings. Build with :func:`get_settings`."""

    # Which environment this process is serving. The UI paints a banner for
    # anything that is not "PRODUCTION" — the legacy app's red "You are in the
    # TEST environment" line, kept because it did its job.
    environment: str = "DEMO"

    # Backing store. sqlite:///<path> today; the repository layer is written
    # against a narrow interface so a SQL Server DSN can replace it (see
    # docs/PIMS_MIGRATION.md).
    database_url: str = "sqlite:///" + str(REPO_ROOT / "data" / "pims.db")

    # Seed the demo dataset when the database is empty.
    auto_seed: bool = True

    # Where the built web UI lives; served at / when present.
    web_dist: Path = field(default_factory=lambda: REPO_ROOT / "pimsweb" / "dist")

    # LIMS (LabWare) integration. "local" reads the projected lims_result
    # table; "sqlserver" would read the live linked server.
    lims_mode: str = "local"
    lims_source_label: str = "XLIMSFEEDGROUP"

    # Support thresholds: how stale the LIMS projection may get before
    # /api/support/diagnostics reports degraded/failed. FE-2026-001 went
    # unnoticed for ~7 months because nothing measured this.
    lims_warn_hours: int = 24
    lims_fail_hours: int = 72

    session_hours: int = 12

    @property
    def sqlite_path(self) -> Path:
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError(
                f"Only sqlite:/// URLs are supported by the bundled repository "
                f"(got {self.database_url!r}); see docs/PIMS_MIGRATION.md."
            )
        return Path(self.database_url[len("sqlite:///") :])

    @property
    def is_production(self) -> bool:
        return self.environment.strip().upper() == "PRODUCTION"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process settings, reading the environment on first call."""

    global _settings
    if _settings is None:
        _settings = Settings(
            environment=os.environ.get("PIMS_ENV", "DEMO"),
            database_url=os.environ.get(
                "PIMS_DATABASE_URL", Settings.database_url
            ),
            auto_seed=_env_bool("PIMS_AUTO_SEED", True),
            web_dist=Path(
                os.environ.get("PIMS_WEB_DIST", str(REPO_ROOT / "pimsweb" / "dist"))
            ),
            lims_mode=os.environ.get("PIMS_LIMS_MODE", "local"),
            lims_source_label=os.environ.get("PIMS_LIMS_SOURCE", "XLIMSFEEDGROUP"),
            lims_warn_hours=int(os.environ.get("PIMS_LIMS_WARN_HOURS", "24")),
            lims_fail_hours=int(os.environ.get("PIMS_LIMS_FAIL_HOURS", "72")),
            session_hours=int(os.environ.get("PIMS_SESSION_HOURS", "12")),
        )
    return _settings


def reset_settings(settings: Settings | None = None) -> None:
    """Replace the cached settings — used by tests."""

    global _settings
    _settings = settings

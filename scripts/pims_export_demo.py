"""Export the seeded PIMS database as JSON for the browser-only demo build.

`pimsweb` normally talks to the FastAPI app. The demo build ships the same UI
with this dataset baked in and the rules re-implemented in the page
(`pimsweb/src/demo/`), so it can be opened as a single HTML file with no
server. It is a sandbox for looking around — the real system is `pims/`.

    python scripts/pims_export_demo.py            # writes pimsweb/src/demo/dataset.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pims import db  # noqa: E402
from pims.config import get_settings  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "pimsweb" / "src" / "demo" / "dataset.json"

# Everything the UI reads. Sessions and password hashes are deliberately absent:
# the demo build accepts any seeded username and does no authentication.
TABLES = [
    "company",
    "plant",
    "department",
    "order_type",
    "status",
    "material_type",
    "material",
    "material_plant",
    "material_test",
    "material_spec",
    "blend_recipe",
    "blend_recipe_component",
    "location_type",
    "location",
    "location_default",
    "customer",
    "vendor",
    "partner_requirement",
    "transaction_type",
    "order",
    "inventory_transaction",
    "pending_shipment",
    "qc",
    "test_point",
    "qc_in_process",
    "qa_question",
    "qa_header",
    "qa_response",
    "lims_result",
    "saved_query",
    "system_setting",
]

USER_COLUMNS = "user_id, username, full_name, email, role"


def main() -> int:
    settings = get_settings()
    db.init_db(settings)
    data: dict[str, list[dict]] = {}
    for table in TABLES:
        data[table] = db.query(f'SELECT * FROM "{table}"')
    data["app_user"] = db.query(f"SELECT {USER_COLUMNS} FROM app_user WHERE active = 1")
    data["user_plant_access"] = db.query("SELECT * FROM user_plant_access")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    total = sum(len(rows) for rows in data.values())
    size_mb = OUT.stat().st_size / 1_048_576
    print(f"{OUT.relative_to(Path.cwd())}: {total:,} rows, {size_mb:.2f} MB")
    for table, rows in sorted(data.items(), key=lambda kv: -len(kv[1]))[:8]:
        print(f"  {table:24} {len(rows):>6,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

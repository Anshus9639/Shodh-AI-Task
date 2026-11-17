"""Regenerate schemas/*.schema.json from the Pydantic data contracts."""

from __future__ import annotations

import sys
from pathlib import Path

from verityflow.schemas import export_schemas

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    paths = export_schemas(ROOT / "schemas")
    for path in paths:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())

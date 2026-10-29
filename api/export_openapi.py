"""Write the API's OpenAPI (Swagger) schema to api/openapi.json.

    python -m api.export_openapi

The live schema is also served at /openapi.json, with Swagger UI at /docs and ReDoc at /redoc.
Generate the committed file with the pinned api/requirements.txt, so it matches the Docker image.
"""
from __future__ import annotations

import json
from pathlib import Path

from api.app.main import app

OPENAPI_PATH = Path(__file__).resolve().parent / "openapi.json"


def main() -> None:
    OPENAPI_PATH.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OPENAPI_PATH}")


if __name__ == "__main__":
    main()

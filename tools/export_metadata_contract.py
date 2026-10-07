"""Export the public Plugin API 1.1.1 metadata JSON schemas from host models.

Run with backend dependencies installed and PYTHONPATH=src/backend.
"""

import argparse
import json
from pathlib import Path

from src.plugin_api.metadata_contracts import (
    MetadataProviderRegistration,
    MetadataProviderRequest,
    ProviderResponse,
)


def export(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name, model in (("metadata-registration-v1", MetadataProviderRegistration),
                        ("metadata-request-v1", MetadataProviderRequest),
                        ("metadata-response-v1", ProviderResponse)):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["x-api-contract-version"] = "1.1.1"
        (destination / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    export(parser.parse_args().destination)

"""Export the public host-mediated authentication contribution schema."""

import argparse
import json
from pathlib import Path

from src.plugin_api.auth_contracts import PasswordResetFlowDefinition


def export(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    schema = PasswordResetFlowDefinition.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["x-api-contract-version"] = "1.1.7"
    (destination / "auth-password-reset-v1.schema.json").write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    export(parser.parse_args().destination)

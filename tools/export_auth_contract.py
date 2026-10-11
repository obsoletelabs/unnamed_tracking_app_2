"""Export the public host-mediated authentication contribution schema."""

import argparse
import json
from pathlib import Path

from src.plugin_api.auth_contracts import (
    PasswordResetFlowDefinition, UserInviteFlowDefinition, UserInviteIssue, UserInviteIdentity,
)


def export(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name, model, version in (
        ("auth-password-reset-v1", PasswordResetFlowDefinition, "1.1.7"),
        ("auth-user-invite-v1", UserInviteFlowDefinition, "1.1.8"),
        ("auth-user-invite-issue-v1", UserInviteIssue, "1.1.8"),
        ("auth-user-invite-identity-v1", UserInviteIdentity, "1.1.8"),
    ):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["x-api-contract-version"] = version
        (destination / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    export(parser.parse_args().destination)

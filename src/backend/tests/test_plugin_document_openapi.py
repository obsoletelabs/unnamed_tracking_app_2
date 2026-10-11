"""Generated clients must distinguish the two original-document HTTP methods."""

import warnings

from fastapi.openapi.utils import get_openapi

from src.main import app


def test_document_download_operations_are_unique_and_stable():
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    path = "/api/plugins/{plugin_id}/capabilities/documents/{document_id}/download"
    methods = schema["paths"][path]
    assert methods["get"]["operationId"].endswith("_get")
    assert methods["head"]["operationId"].endswith("_head")
    operation_ids = [
        operation["operationId"]
        for operations in schema["paths"].values()
        for method, operation in operations.items()
        if method in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert not any("Duplicate Operation ID" in str(warning.message) for warning in emitted)

"""Generated backend-client schema parity."""

import json
from pathlib import Path

from server.api.schema import ProtocolDocument


def test_committed_protocol_schema_matches_python_contract() -> None:
    schema_path = Path("clients/backend-client/src/generated/protocol.schema.json")
    assert json.loads(schema_path.read_text()) == ProtocolDocument.model_json_schema()

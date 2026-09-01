from __future__ import annotations

import importlib.util
import json
import subprocess
import sys


def _loaded_application_packages(module: str) -> set[str]:
    probe = f"""
import importlib
import json
import sys

importlib.import_module({module!r})
print(json.dumps(sorted(
    name for name in sys.modules
    if name == 'entrypoints' or name.startswith('entrypoints.')
    or name == 'server' or name.startswith('server.')
)))
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
    return set(json.loads(result.stdout))


def test_core_context_does_not_load_application_packages() -> None:
    assert _loaded_application_packages("vibesys.context") == set()


def test_old_nested_server_package_is_absent() -> None:
    legacy_package = ".".join(("vibesys", "server"))  # noqa: FLY002
    assert importlib.util.find_spec(legacy_package) is None


def test_server_event_contract_does_not_load_entrypoints() -> None:
    loaded = _loaded_application_packages("server.events")
    assert not any(name == "entrypoints" or name.startswith("entrypoints.") for name in loaded)

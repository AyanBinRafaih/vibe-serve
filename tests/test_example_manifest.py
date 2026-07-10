import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from vibe_serve.example_manifest import ExampleManifest


def _write_manifest(tmp_path: Path, body: str) -> Path:
    example_dir = tmp_path / "examples" / "fake-example"
    example_dir.mkdir(parents=True)
    (example_dir / "vibeserve.example.toml").write_text(textwrap.dedent(body))
    return example_dir


def test_detect_returns_none_when_manifest_absent(tmp_path):
    ref = tmp_path / "examples" / "no-manifest" / "reference"
    ref.mkdir(parents=True)
    assert ExampleManifest.detect(ref) is None


def test_detect_loads_manifest_sibling_to_ref(tmp_path):
    example_dir = _write_manifest(
        tmp_path,
        """
        [service]
        base_url = "http://localhost:8080"
        health_check = "GET /wrk2-api/user-timeline/read?user_id=1&start=0&stop=1"

        [benchmark]
        primary_metric = "p50_ms"
        direction = "minimize"
        metrics_format = "jsonl"
        """,
    )
    ref = example_dir / "reference"
    ref.mkdir()
    manifest = ExampleManifest.detect(ref)
    assert manifest is not None
    assert manifest.service.base_url == "http://localhost:8080"
    assert manifest.benchmark.primary_metric == "p50_ms"
    assert manifest.benchmark.direction == "minimize"


def test_invalid_health_check_format_rejected(tmp_path):
    example_dir = _write_manifest(
        tmp_path,
        """
        [service]
        health_check = "not-a-valid-probe"

        [benchmark]
        primary_metric = "p50_ms"
        direction = "minimize"
        """,
    )
    with pytest.raises(ValidationError):
        ExampleManifest.load(example_dir)


def test_direction_must_be_minimize_or_maximize(tmp_path):
    example_dir = _write_manifest(
        tmp_path,
        """
        [benchmark]
        primary_metric = "p50_ms"
        direction = "down"
        """,
    )
    with pytest.raises(ValidationError):
        ExampleManifest.load(example_dir)


def test_script_path_and_has_script(tmp_path):
    example_dir = _write_manifest(
        tmp_path,
        """
        [benchmark]
        primary_metric = "p50_ms"
        direction = "minimize"
        """,
    )
    (example_dir / "check.sh").write_text("#!/bin/bash\nexit 0\n")
    manifest = ExampleManifest.load(example_dir)
    assert manifest.has_script("check.sh")
    assert not manifest.has_script("build.sh")
    assert manifest.require_script("check.sh") == example_dir / "check.sh"
    with pytest.raises(FileNotFoundError):
        manifest.require_script("build.sh")


def test_env_vars_match_issue_contract(tmp_path):
    example_dir = _write_manifest(
        tmp_path,
        """
        [service]
        base_url = "http://localhost:8080"

        [benchmark]
        primary_metric = "p50_ms"
        direction = "minimize"
        """,
    )
    manifest = ExampleManifest.load(example_dir)
    env = manifest.env(output_dir="/tmp/vibeserve-run-1", load_level="medium")
    assert env["VIBESERVE_BASE_URL"] == "http://localhost:8080"
    assert env["VIBESERVE_OUTPUT_DIR"] == "/tmp/vibeserve-run-1"
    assert env["VIBESERVE_LOAD_LEVEL"] == "medium"


def test_to_objective_maps_direction_vocabulary(tmp_path):
    example_dir = _write_manifest(
        tmp_path,
        """
        [benchmark]
        primary_metric = "p50_ms"
        direction = "minimize"
        """,
    )
    manifest = ExampleManifest.load(example_dir)
    objective = manifest.to_objective()
    assert objective.name == "p50_ms"
    assert objective.direction == "min"


def test_unknown_lifecycle_script_name_rejected(tmp_path):
    example_dir = _write_manifest(
        tmp_path,
        """
        [benchmark]
        primary_metric = "p50_ms"
        direction = "minimize"
        """,
    )
    manifest = ExampleManifest.load(example_dir)
    with pytest.raises(ValueError):
        manifest.script_path("not_a_real_script.sh")

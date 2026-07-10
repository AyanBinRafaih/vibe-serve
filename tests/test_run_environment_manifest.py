from pathlib import Path

import pytest

from vibe_serve.example_manifest import ExampleManifest
from vibe_serve.sandbox import run_environment as re_mod


def _manifest(tmp_path: Path, *, needs_socket: bool, with_setup_sh: bool) -> ExampleManifest:
    example_dir = tmp_path / "examples" / "fake"
    example_dir.mkdir(parents=True)
    (example_dir / "vibeserve.example.toml").write_text(
        f"""
        [setup]
        needs_docker_socket = {"true" if needs_socket else "false"}

        [benchmark]
        primary_metric = "p50_ms"
        direction = "minimize"
        """
    )
    if with_setup_sh:
        (example_dir / "setup.sh").write_text("#!/bin/bash\nexit 0\n")
    return ExampleManifest.load(example_dir)


def test_docker_socket_not_mounted_by_default(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path, needs_socket=False, with_setup_sh=False)
    logs = []

    class FakeBackend:
        def make_sandbox(self, kind, **kwargs):
            self.captured_bind_mounts = kwargs["bind_mounts"]
            self.captured_setup_fns = kwargs.get("setup_fns", [])

            class FakeSandbox:
                def start(self_inner):
                    pass

                def stop(self_inner):
                    pass

            return FakeSandbox()

    backend = FakeBackend()
    request = re_mod.RunEnvironmentRequest(
        log_dir=tmp_path / "logs",
        workspace=tmp_path / "workspace",
        ref_dir=None,
        backend=backend,
        agent_backend="cli",
        cli_provider=None,
        log=logs.append,
        example_manifest=manifest,
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "workspace").mkdir()

    env = re_mod.DockerEnvironment.from_options({})
    env.open(request)

    assert (
        "/var/run/docker.sock",
        "/var/run/docker.sock",
        False,
    ) not in backend.captured_bind_mounts
    assert not any("docker.sock" in line for line in logs)


def test_docker_socket_mounted_when_requested(tmp_path):
    manifest = _manifest(tmp_path, needs_socket=True, with_setup_sh=False)
    logs = []

    class FakeBackend:
        def make_sandbox(self, kind, **kwargs):
            self.captured_bind_mounts = kwargs["bind_mounts"]

            class FakeSandbox:
                def start(self_inner):
                    pass

                def stop(self_inner):
                    pass

            return FakeSandbox()

    backend = FakeBackend()
    request = re_mod.RunEnvironmentRequest(
        log_dir=tmp_path / "logs",
        workspace=tmp_path / "workspace",
        ref_dir=None,
        backend=backend,
        agent_backend="cli",
        cli_provider=None,
        log=logs.append,
        example_manifest=manifest,
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "workspace").mkdir()

    env = re_mod.DockerEnvironment.from_options({})
    env.open(request)

    assert ("/var/run/docker.sock", "/var/run/docker.sock", False) in backend.captured_bind_mounts
    assert any("Docker daemon" in line for line in logs)


def test_setup_sh_runs_and_raises_on_nonzero_exit(tmp_path):
    manifest = _manifest(tmp_path, needs_socket=False, with_setup_sh=True)

    executed = {}

    class FakeSandbox:
        def start(self):
            pass

        def stop(self):
            pass

        def execute(self, cmd, timeout=None):
            executed["cmd"] = cmd
            executed["timeout"] = timeout

            class R:
                exit_code = 1
                output = "boom"

            return R()

    class FakeBackend:
        def make_sandbox(self, kind, **kwargs):
            self.setup_fns = kwargs.get("setup_fns", [])
            return FakeSandbox()

    backend = FakeBackend()
    request = re_mod.RunEnvironmentRequest(
        log_dir=tmp_path / "logs",
        workspace=tmp_path / "workspace",
        ref_dir=None,
        backend=backend,
        agent_backend="cli",
        cli_provider=None,
        example_manifest=manifest,
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "workspace").mkdir()

    env = re_mod.DockerEnvironment.from_options({})
    with pytest.raises(RuntimeError, match="setup.sh failed"):
        env.open(request)
        for fn in backend.setup_fns:
            fn(FakeSandbox())

    # If setup_fns weren't invoked automatically by open() in this fake
    # harness, invoke the one we captured directly to prove it raises.
    if "cmd" not in executed:
        with pytest.raises(RuntimeError, match="setup.sh failed"):
            for fn in backend.setup_fns:
                fn(FakeSandbox())


def _manifest_with_scripts(tmp_path: Path, scripts) -> ExampleManifest:
    example_dir = tmp_path / "examples" / "scripted"
    example_dir.mkdir(parents=True)
    (example_dir / "vibeserve.example.toml").write_text(
        '[benchmark]\nprimary_metric = "p50_ms"\ndirection = "minimize"\n'
    )
    for name in scripts:
        (example_dir / name).write_text("#!/bin/bash\nexit 0\n")
    return ExampleManifest.load(example_dir)


def _request(tmp_path: Path, backend, manifest, logs=None):
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "workspace").mkdir(exist_ok=True)
    return re_mod.RunEnvironmentRequest(
        log_dir=tmp_path / "logs",
        workspace=tmp_path / "workspace",
        ref_dir=None,
        backend=backend,
        agent_backend="cli",
        cli_provider=None,
        log=(logs.append if logs is not None else None),
        example_manifest=manifest,
    )


def test_manifest_env_vars_injected_into_container_env(tmp_path):
    manifest = _manifest_with_scripts(tmp_path, [])
    captured = {}

    class FakeSandbox:
        def start(self):
            pass

        def stop(self):
            pass

    class FakeBackend:
        def make_sandbox(self, kind, **kwargs):
            captured.update(kwargs)
            return FakeSandbox()

    re_mod.DockerEnvironment.from_options({}).open(_request(tmp_path, FakeBackend(), manifest))
    env = captured["extra_env"]
    assert env["VIBESERVE_LOAD_LEVEL"] == "medium"
    assert env["VIBESERVE_OUTPUT_DIR"] == "/workspace/example_output"
    assert "VIBESERVE_BASE_URL" in env


def test_build_sh_runs_after_setup_sh(tmp_path):
    manifest = _manifest_with_scripts(tmp_path, ["setup.sh", "build.sh"])
    executed = []

    class FakeSandbox:
        def start(self):
            pass

        def stop(self):
            pass

        def execute(self, cmd, timeout=None):
            executed.append(cmd)

            class R:
                exit_code = 0
                output = ""

            return R()

    captured = {}

    class FakeBackend:
        def make_sandbox(self, kind, **kwargs):
            captured.update(kwargs)
            return FakeSandbox()

    re_mod.DockerEnvironment.from_options({}).open(_request(tmp_path, FakeBackend(), manifest))
    for fn in captured["setup_fns"]:
        fn(FakeSandbox())
    setup_i = next(i for i, c in enumerate(executed) if "setup.sh" in c)
    build_i = next(i for i, c in enumerate(executed) if "build.sh" in c)
    assert setup_i < build_i


def test_teardown_sh_runs_on_close_before_stop(tmp_path):
    manifest = _manifest_with_scripts(tmp_path, ["teardown.sh"])
    events = []

    class FakeSandbox:
        def start(self):
            pass

        def stop(self):
            events.append("stop")

        def execute(self, cmd, timeout=None):
            events.append(f"exec:{cmd}")

            class R:
                exit_code = 0
                output = ""

            return R()

    class FakeBackend:
        def make_sandbox(self, kind, **kwargs):
            return FakeSandbox()

    session = re_mod.DockerEnvironment.from_options({}).open(
        _request(tmp_path, FakeBackend(), manifest)
    )
    assert not any(e.startswith("exec:") for e in events)
    session.close()
    exec_i = next(i for i, e in enumerate(events) if "teardown.sh" in e)
    assert exec_i < events.index("stop")


def test_teardown_sh_failure_does_not_raise(tmp_path):
    manifest = _manifest_with_scripts(tmp_path, ["teardown.sh"])

    class FakeSandbox:
        def start(self):
            pass

        def stop(self):
            pass

        def execute(self, cmd, timeout=None):
            raise RuntimeError("boom")

    class FakeBackend:
        def make_sandbox(self, kind, **kwargs):
            return FakeSandbox()

    session = re_mod.DockerEnvironment.from_options({}).open(
        _request(tmp_path, FakeBackend(), manifest)
    )
    session.close()

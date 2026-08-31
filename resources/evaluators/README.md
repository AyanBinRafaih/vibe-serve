# Evaluator packages

This directory contains local development builds of reusable VibeSys evaluator
packages. Task definitions depend on package names and exact versions, not on
these source paths. The runtime resolves a package to immutable contents and
records its `sha256:` digest with the run.

Each immediate child is a self-contained package with a
`vibesys.evaluator.toml` file:

```toml
schema_version = 1
name = "vibesys-evaluator-example"
version = "0.1.0"
protocol_version = 1

[entrypoints]
example-check = ["example-check"]
```

Entry points are logical public names mapped to argv prefixes. Local source
packages may use the literal `${PACKAGE_ROOT}` token in an argv element. The
resolver expands it to the absolute package directory, allowing commands to run
from a candidate repository. Arguments declared by a task are appended to the
resolved prefix without invoking a shell. A task argument may use
`${PROJECT_ROOT}` when the evaluator needs an absolute path to the candidate.
The selected run environment expands it to the candidate repository root before
running the resolved command. This is necessary for tools such as `go -C`,
which change cwd.

The local collection is the initial package source. Published packages can
later provide the same metadata and entry-point names through a registry-backed
resolver. Repository-specific checks and workloads belong with their task, not
in this directory.

## Cargo tools from Git

An evaluator package may declare a Rust CLI installed from an immutable Git
revision:

```toml
[tools.request-factory]
kind = "cargo-git"
git = "https://github.com/uw-syfi/request-factory"
rev = "118da6137275fda3a290e9012853214dc437c6c0"
package = "req-frontend"
bins = ["session_runner"]

[entrypoints]
run = ["${TOOL:request-factory/session_runner}"]
```

The revision must be a full lowercase commit SHA. VibeSys always passes
`--locked` to Cargo and installs only the declared binaries. A tool token must
occupy one complete entrypoint argument and may reference only a binary from
the corresponding declaration.

Local runs install tools in a content-addressed, operator-owned cache outside
the candidate workspace. The cache records the normalized tool specification
and each installed binary's SHA256 digest. Only the selected content-addressed
installation roots are imported read-only into the agent's host confinement;
the writable cache parent remains operator-only. Tool-bearing evaluator
packages are not yet supported by the Docker, Modal, or SkyPilot run
environments; those environments reject the package before starting external
resources.

The bundled Request Factory evaluator exposes two entrypoints. The low-level
`request-factory-engine` entrypoint forwards arguments directly to the pinned
binary. Experiments normally use `request-factory-adapter` and pass a
task-owned Python script as the first argument; the evaluator invokes that
script with `--request-factory-engine <trusted-path>` before the task's
remaining arguments.

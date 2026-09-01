"""Versioned evaluator package contracts and local resolution."""

from vibesys.evaluators.packages import (
    PACKAGE_ROOT_TOKEN,
    PROJECT_ROOT_TOKEN,
    TOOL_TOKEN_PREFIX,
    CargoGitToolSpec,
    EvaluatorPackageError,
    EvaluatorPackageMetadata,
    EvaluatorPackageNotFoundError,
    EvaluatorPackageRegistry,
    EvaluatorPackageRequirement,
    ResolvedEvaluatorPackage,
    load_evaluator_package,
    resolve_evaluator_package,
    tool_token,
)
from vibesys.evaluators.tools import (
    EvaluatorToolError,
    EvaluatorToolLifecycleHandler,
    cargo_install_argv,
    prepare_evaluator_tools,
    tool_install_root,
    tool_path_replacements,
    tool_spec_digest,
)

__all__ = [
    "PACKAGE_ROOT_TOKEN",
    "PROJECT_ROOT_TOKEN",
    "TOOL_TOKEN_PREFIX",
    "CargoGitToolSpec",
    "EvaluatorPackageError",
    "EvaluatorPackageMetadata",
    "EvaluatorPackageNotFoundError",
    "EvaluatorPackageRegistry",
    "EvaluatorPackageRequirement",
    "EvaluatorToolError",
    "EvaluatorToolLifecycleHandler",
    "ResolvedEvaluatorPackage",
    "cargo_install_argv",
    "load_evaluator_package",
    "prepare_evaluator_tools",
    "resolve_evaluator_package",
    "tool_install_root",
    "tool_path_replacements",
    "tool_spec_digest",
    "tool_token",
]

#!/usr/bin/env python3
"""Enforce dependency direction between the TypeScript client packages."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PACKAGE_RULES: dict[str, frozenset[str]] = {
    "@vibesys/backend-client": frozenset(),
    "@vibesys/core-state": frozenset({"@vibesys/backend-client"}),
    "@vibesys/tui": frozenset({"@vibesys/backend-client", "@vibesys/core-state"}),
}
PACKAGE_DIRS = {
    "@vibesys/backend-client": Path("clients/backend-client"),
    "@vibesys/core-state": Path("clients/core-state"),
    "@vibesys/tui": Path("clients/tui"),
}
IMPORT_PATTERN = re.compile(r"(?:\bfrom\s+|\bimport\s*(?:\(\s*)?)[\"']([^\"']+)[\"']")
CORE_RECONSTRUCTION_PATTERN = re.compile(r"\bcore\s*:\s*\{\s*\.\.\.")


def architecture_errors(root: Path) -> list[str]:
    """Return every package-boundary violation below ``root``."""
    package_roots = {name: (root / relative).resolve() for name, relative in PACKAGE_DIRS.items()}
    errors: list[str] = []
    for package_name, package_root in package_roots.items():
        errors.extend(package_errors(root, package_name, package_root))
    return errors


def package_errors(
    root: Path,
    package_name: str,
    package_root: Path,
) -> list[str]:
    """Check one package manifest and its source imports."""
    manifest_path = package_root / "package.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{manifest_path.relative_to(root)}: cannot read package manifest: {exc}"]
    all_declared = {
        dependency
        for section in ("dependencies", "devDependencies", "peerDependencies")
        for dependency in manifest.get(section, {})
        if dependency in PACKAGE_RULES
    }
    runtime_dependencies = set(manifest.get("dependencies", {}))
    errors = [
        f"{manifest_path.relative_to(root)}: {package_name} must not depend on {dependency}"
        for dependency in sorted(all_declared - PACKAGE_RULES[package_name])
    ]
    if package_name == "@vibesys/core-state":
        errors.extend(
            f"{manifest_path.relative_to(root)}: core-state must not depend on {dependency}"
            for section in ("dependencies", "devDependencies", "peerDependencies")
            for dependency in manifest.get(section, {})
            if dependency.startswith("@opentui/")
        )
    source_root = package_root / "src"
    sources = sorted({*source_root.rglob("*.ts"), *source_root.rglob("*.tsx")})
    for source in sources:
        source_text = source.read_text()
        for specifier in IMPORT_PATTERN.findall(source_text):
            errors.extend(
                import_errors(
                    root,
                    package_name,
                    source,
                    specifier,
                    runtime_dependencies,
                )
            )
        if (
            package_name == "@vibesys/tui"
            and ".test." not in source.name
            and CORE_RECONSTRUCTION_PATTERN.search(source_text)
        ):
            errors.append(
                f"{source.relative_to(root)}: TUI production code must not reconstruct CoreState"
            )
    return errors


def import_errors(
    root: Path,
    package_name: str,
    source: Path,
    specifier: str,
    declared: set[str],
) -> list[str]:
    """Check one source import against package rules."""
    location = source.relative_to(root)
    errors = internal_import_errors(location, package_name, specifier, declared)
    if package_name == "@vibesys/core-state" and specifier.startswith(("node:", "@opentui/")):
        errors.append(f"{location}: core-state must stay runtime- and UI-independent")
    if specifier.startswith("."):
        resolved = (source.parent / specifier).resolve()
        errors.extend(
            f"{location}: cross-package relative import must use {other_name}'s public API"
            for other_name, relative in PACKAGE_DIRS.items()
            for other_root in [(root / relative).resolve()]
            if other_name != package_name and resolved.is_relative_to(other_root)
        )
    return errors


def internal_import_errors(
    location: Path,
    package_name: str,
    specifier: str,
    declared: set[str],
) -> list[str]:
    """Check a workspace-package import."""
    if not specifier.startswith("@vibesys/"):
        return []
    imported_package = "/".join(specifier.split("/")[:2])
    if imported_package not in PACKAGE_RULES:
        return []
    if specifier != imported_package:
        return [f"{location}: import {specifier!r} bypasses {imported_package}'s public API"]
    if imported_package not in PACKAGE_RULES[package_name]:
        return [f"{location}: {package_name} must not import {imported_package}"]
    if imported_package not in declared:
        return [f"{location}: {imported_package} must be declared in {package_name} dependencies"]
    return []


def main() -> int:
    """Run the repository architecture check."""
    root = Path(__file__).resolve().parents[1]
    errors = architecture_errors(root)
    if not errors:
        print("TypeScript package boundaries are valid.")
        return 0
    print("TypeScript package boundary violations:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

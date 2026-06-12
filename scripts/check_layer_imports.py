from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = ROOT / "unshuffle"
LAYER_ORDER = ["core", "audio", "persistence", "logic", "runtime", "bridge"]
LAYER_NAMES = set(LAYER_ORDER)
CHECKED_DIRS = [PACKAGE_ROOT / layer for layer in LAYER_ORDER]


@dataclass(frozen=True)
class ImportIssue:
    kind: str
    source: str
    target: str
    line: int
    detail: str


def _module_name_for_file(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    return ".".join(rel.parts)


def _layer_for_module(module_name: str) -> str | None:
    parts = module_name.split(".")
    if len(parts) >= 2 and parts[0] == "unshuffle" and parts[1] in LAYER_NAMES:
        return parts[1]
    return None


def _resolve_relative_module(source_module: str, module: str | None, level: int) -> str:
    source_parts = source_module.split(".")
    anchor = source_parts[:-level]
    if module:
        return ".".join(anchor + module.split("."))
    return ".".join(anchor)


def _iter_import_targets(source_module: str, tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            module_name = _resolve_relative_module(source_module, node.module, node.level) if node.level else (node.module or "")
            if module_name:
                yield module_name, node.lineno


def _classify_issue(source_module: str, target_module: str, line: int) -> ImportIssue | None:
    source_layer = _layer_for_module(source_module)
    if source_layer is None or not target_module.startswith("unshuffle."):
        return None

    target_layer = _layer_for_module(target_module)
    if target_layer is None:
        return ImportIssue(
            kind="warning",
            source=source_module,
            target=target_module,
            line=line,
            detail="imports a root compatibility/helper module outside the layered graph",
        )

    source_index = LAYER_ORDER.index(source_layer)
    target_index = LAYER_ORDER.index(target_layer)

    if source_index < target_index:
        return ImportIssue(
            kind="error",
            source=source_module,
            target=target_module,
            line=line,
            detail="imports a higher layer",
        )

    if source_layer in {"audio", "persistence"} and target_layer in {"audio", "persistence"} and source_layer != target_layer:
        return ImportIssue(
            kind="warning",
            source=source_module,
            target=target_module,
            line=line,
            detail="crosses between sibling infrastructure layers",
        )

    return None


def collect_issues() -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for directory in CHECKED_DIRS:
        for path in directory.rglob("*.py"):
            source_module = _module_name_for_file(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for target_module, line in _iter_import_targets(source_module, tree):
                issue = _classify_issue(source_module, target_module, line)
                if issue is not None:
                    issues.append(issue)
    return sorted(issues, key=lambda item: (item.kind, item.source, item.line, item.target))


def main() -> int:
    issues = collect_issues()
    errors = [issue for issue in issues if issue.kind == "error"]
    warnings = [issue for issue in issues if issue.kind == "warning"]

    if errors:
        print("Layer import check failed:", file=sys.stderr)
        for issue in errors:
            print(
                f"  ERROR {issue.source}:{issue.line} -> {issue.target} ({issue.detail})",
                file=sys.stderr,
            )
    else:
        print("No layer-graph violations found.")

    if warnings:
        print("Compatibility warnings:")
        for issue in warnings:
            print(f"  WARN  {issue.source}:{issue.line} -> {issue.target} ({issue.detail})")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

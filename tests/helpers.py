"""Shared helpers for the tests: build small manifests and lint them."""

from __future__ import annotations

import textwrap
from pathlib import Path

from kube_llm_lint.checks import Finding, LintResult, Options, lint_documents
from kube_llm_lint.loader import parse_text

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent


def lint_text(text: str, **options: object) -> LintResult:
    """Lint a YAML string (dedented)."""
    docs = parse_text(textwrap.dedent(text), "test.yaml")
    return lint_documents(docs, Options(**options))  # type: ignore[arg-type]


def rules_of(findings: list[Finding]) -> list[str]:
    return sorted(f.rule for f in findings)


def pod(container_yaml: str, pod_yaml: str = "", kind: str = "Pod") -> str:
    """A manifest with one container; ``pod_yaml`` adds pod-level keys (volumes, hostIPC ...)."""
    # Tests append unindented extra lines to indented blocks, so strip the first
    # line's indentation from every line that has it instead of using dedent().
    rows = [r for r in container_yaml.split("\n") if r.strip()]
    indent = rows[0][: len(rows[0]) - len(rows[0].lstrip())]
    body = [r[len(indent):] if r.startswith(indent) else r.lstrip() for r in rows]
    lines = ["apiVersion: v1", f"kind: {kind}", "metadata:", "  name: t", "spec:"]
    if pod_yaml:
        lines.extend("  " + row for row in textwrap.dedent(pod_yaml).strip("\n").split("\n"))
    lines.append("  containers:")
    lines.append("  - " + body[0])
    lines.extend("    " + row for row in body[1:])
    return "\n".join(lines) + "\n"

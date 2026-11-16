"""Render findings as text or JSON."""

from __future__ import annotations

import json
from collections import Counter

from . import __version__, rules
from .checks import LintResult
from .i18n import t

_COLORS = {rules.ERROR: "31", rules.WARNING: "33", rules.INFO: "36"}


def _paint(text: str, severity: str, color: bool) -> str:
    return f"\033[{_COLORS[severity]}m{text}\033[0m" if color else text


def counts(result: LintResult) -> Counter[str]:
    return Counter(f.severity for f in result.findings)


def render_text(result: LintResult, lang: str, color: bool = False) -> str:
    """Human-readable report: one block per finding, then a summary line."""
    lines: list[str] = []
    for f in result.findings:
        label = _paint(f"{t(lang, 'sev.' + f.severity):<7}", f.severity, color)
        owner = f"{f.kind}/{f.name}" if f.name else f.kind
        lines.append(f"{f.file}:{f.line}: {label} {f.rule}  [{owner} / {f.container} / {f.server}]")
        lines.append(f"    {f.message}")
        if f.fix:
            lines.append(f"    {t(lang, 'report.fix')}")
            lines.extend(f"      {row}" if row else "" for row in f.fix.rstrip("\n").split("\n"))
        lines.append("")
    c = counts(result)
    lines.append(t(
        lang, "report.summary",
        errors=c[rules.ERROR], warnings=c[rules.WARNING], infos=c[rules.INFO],
        containers=result.containers, files=result.files,
    ))
    return "\n".join(lines) + "\n"


def render_json(result: LintResult, lang: str) -> str:
    """Machine-readable report for CI."""
    c = counts(result)
    payload = {
        "findings": [f.as_dict() for f in result.findings],
        "summary": {
            "errors": c[rules.ERROR],
            "warnings": c[rules.WARNING],
            "info": c[rules.INFO],
            "containers": result.containers,
            "files": result.files,
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/LeeGyeongPil/kube-llm-lint"
_SARIF_LEVELS = {rules.ERROR: "error", rules.WARNING: "warning", rules.INFO: "note"}


def _sarif_uri(name: str) -> str:
    """Return a URI-safe artifact name ('<stdin>' has characters a URI cannot hold)."""
    return "stdin" if name == "<stdin>" else name


def render_sarif(result: LintResult, lang: str) -> str:
    """SARIF 2.1.0 log (for example for GitHub code scanning)."""
    index = {rule.id: i for i, rule in enumerate(rules.RULES)}
    driver_rules = [
        {
            "id": rule.id,
            "shortDescription": {"text": t(lang, "rule." + rule.id)},
            "helpUri": rule.source,
            "defaultConfiguration": {"level": _SARIF_LEVELS[rule.severity]},
            "properties": {"sources": list(rule.sources)},
        }
        for rule in rules.RULES
    ]
    results = []
    for f in result.findings:
        text = f.message
        if f.fix:
            text += "\n\n" + t(lang, "report.fix") + "\n" + f.fix.rstrip("\n")
        results.append({
            "ruleId": f.rule,
            "ruleIndex": index[f.rule],
            "level": _SARIF_LEVELS[f.severity],
            "message": {"text": text},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": _sarif_uri(f.file)},
                    "region": {"startLine": max(f.line, 1)},
                },
            }],
        })
    payload = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "kube-llm-lint",
                "version": __version__,
                "informationUri": INFORMATION_URI,
                "rules": driver_rules,
            }},
            "results": results,
        }],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_rules(lang: str) -> str:
    """The ``--list-rules`` table."""
    lines = []
    for rule in rules.RULES:
        lines.append(f"{rule.id}  [{t(lang, 'sev.' + rule.severity)}]")
        lines.append(f"    {t(lang, 'rule.' + rule.id)}")
        for url in rule.sources:
            lines.append(f"    {t(lang, 'report.source')}: {url}")
        lines.append(f"    {t(lang, 'report.checked')}: {rule.checked}")
        lines.append("")
    return "\n".join(lines)

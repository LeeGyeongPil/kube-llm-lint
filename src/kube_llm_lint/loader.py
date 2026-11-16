"""Read manifests from files, directories or stdin, keeping line numbers."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

STDIN_NAME = "<stdin>"


class InputError(Exception):
    """An input problem the user can fix. ``key`` is a message-catalog key."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


class TemplateInputError(InputError):
    """The input looks like an unrendered Helm template."""


@dataclass
class Document:
    """One YAML document (a Kubernetes object) and where it came from."""

    file: str
    data: Any  # ruamel CommentedMap, so ``data.lc`` has line numbers


def key_line(mapping: Any, key: str, default: int = 1) -> int:
    """1-based line of ``key`` inside a parsed mapping."""
    try:
        return int(mapping.lc.key(key)[0]) + 1
    except (AttributeError, KeyError):
        return default


def item_line(sequence: Any, index: int, default: int = 1) -> int:
    """1-based line of item ``index`` inside a parsed sequence."""
    try:
        return int(sequence.lc.item(index)[0]) + 1
    except (AttributeError, KeyError, IndexError):
        return default


def node_line(node: Any, default: int = 1) -> int:
    """1-based line where a parsed mapping or sequence starts."""
    try:
        return int(node.lc.line) + 1
    except AttributeError:
        return default


def parse_text(text: str, filename: str) -> list[Document]:
    """Parse a (multi-document) YAML text into Kubernetes object documents."""
    yaml = YAML(typ="rt")
    docs: list[Document] = []
    try:
        for data in yaml.load_all(text):
            _collect(data, filename, docs)
    except Exception as exc:  # ruamel raises several error types
        if "{{" in text:
            raise TemplateInputError("err.template", path=filename, marker="{{") from exc
        reason = " ".join(str(exc).split())[:300]
        raise InputError("err.parse", path=filename, reason=reason) from exc
    return docs


def _collect(data: Any, filename: str, out: list[Document]) -> None:
    if not hasattr(data, "get"):  # empty documents and bare scalars
        return
    if data.get("kind") == "List" and isinstance(data.get("items"), list):
        for item in data["items"]:
            _collect(item, filename, out)
        return
    out.append(Document(file=filename, data=data))


def expand_paths(paths: Iterable[str]) -> list[str]:
    """Turn files, directories and ``-`` into a list of inputs (``-`` is kept)."""
    result: list[str] = []
    for raw in paths:
        if raw == "-":
            result.append(raw)
            continue
        path = Path(raw)
        if path.is_dir():
            found = sorted(
                str(p)
                for p in path.rglob("*")
                if p.is_file() and p.suffix in (".yaml", ".yml")
            )
            if not found:
                raise InputError("err.no_yaml", path=raw)
            result.extend(found)
        else:
            result.append(raw)
    return result


def load_paths(paths: Iterable[str]) -> list[Document]:
    """Load every input. Raises :class:`InputError` on the first unreadable input."""
    return load_inputs(paths)[0]


def load_inputs(paths: Iterable[str]) -> tuple[list[Document], int]:
    """Like :func:`load_paths`, also returning how many inputs were read."""
    docs: list[Document] = []
    inputs = expand_paths(paths)
    for raw in inputs:
        if raw == "-":
            text, name = sys.stdin.read(), STDIN_NAME
        else:
            name = raw
            try:
                text = Path(raw).read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise InputError("err.not_found", path=raw) from exc
            except UnicodeDecodeError as exc:
                raise InputError("err.encoding", path=raw) from exc
            except OSError as exc:
                raise InputError("err.read", path=raw, reason=str(exc)) from exc
        docs.extend(parse_text(text, name))
    return docs, len(inputs)

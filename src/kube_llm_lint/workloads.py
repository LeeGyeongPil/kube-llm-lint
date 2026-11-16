"""Find the pod specs inside Kubernetes workload objects."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .loader import Document, item_line, key_line

IGNORE_ANNOTATION = "kube-llm-lint/ignore"

# kind -> path from the object to the pod spec
_POD_PATHS: dict[str, tuple[str, ...]] = {
    "Pod": ("spec",),
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "ReplicaSet": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}
_LWS_TEMPLATES = ("leaderTemplate", "workerTemplate")


@dataclass
class PodTarget:
    """A pod spec found in a workload, with the context the checks need."""

    file: str
    kind: str
    name: str
    namespace: str
    pod_spec: Any
    ignored: frozenset[str]
    lws: bool = False  # LeaderWorkerSet: pipeline parallelism spans pods
    fallback_line: int = 1


def _dig(node: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not hasattr(node, "get"):
            return None
        node = node.get(key)
    return node


def _ignored(*annotation_maps: Any) -> frozenset[str]:
    rules: set[str] = set()
    for annotations in annotation_maps:
        if not hasattr(annotations, "get"):
            continue
        value = annotations.get(IGNORE_ANNOTATION)
        if value:
            rules.update(part.strip() for part in str(value).split(",") if part.strip())
    return frozenset(rules)


def pod_targets(doc: Document) -> Iterator[PodTarget]:
    """Yield every pod spec in ``doc`` (nothing for unrelated kinds)."""
    data = doc.data
    kind = str(data.get("kind", ""))
    meta = data.get("metadata") or {}
    name = str(meta.get("name", "")) if hasattr(meta, "get") else ""
    namespace = str(meta.get("namespace", "")) if hasattr(meta, "get") else ""
    object_annotations = meta.get("annotations") if hasattr(meta, "get") else None
    fallback = key_line(data, "kind")

    if kind in _POD_PATHS:
        path = _POD_PATHS[kind]
        spec = _dig(data, path)
        if hasattr(spec, "get"):
            template_meta = _dig(data, path[:-1] + ("metadata",)) if len(path) > 1 else None
            template_annotations = (
                template_meta.get("annotations") if hasattr(template_meta, "get") else None
            )
            yield PodTarget(
                doc.file, kind, name, namespace, spec,
                _ignored(object_annotations, template_annotations), False, fallback,
            )
    elif kind == "LeaderWorkerSet":
        base = _dig(data, ("spec", "leaderWorkerTemplate"))
        for template_name in _LWS_TEMPLATES:
            template = _dig(base, (template_name,))
            spec = _dig(template, ("spec",))
            if hasattr(spec, "get"):
                template_meta = template.get("metadata")
                template_annotations = (
                    template_meta.get("annotations") if hasattr(template_meta, "get") else None
                )
                yield PodTarget(
                    doc.file, kind, name, namespace, spec,
                    _ignored(object_annotations, template_annotations), True, fallback,
                )


def iter_containers(target: PodTarget) -> Iterator[tuple[Any, int]]:
    """Yield ``(container, line)`` for the regular containers of a pod."""
    containers = target.pod_spec.get("containers")
    if not isinstance(containers, list):
        return
    for index, container in enumerate(containers):
        if hasattr(container, "get"):
            yield container, item_line(containers, index, target.fallback_line)

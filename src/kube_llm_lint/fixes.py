"""YAML snippets the user can paste to fix a finding."""

from __future__ import annotations

import io
import math
from typing import Any

from ruamel.yaml import YAML

from .i18n import t

_HANDLERS = ("httpGet", "tcpSocket", "exec", "grpc")
DEFAULT_SHM_SIZE = "2Gi"


def _plain(node: Any) -> Any:
    """Convert ruamel containers and scalars to plain Python values."""
    if hasattr(node, "items"):
        return {str(k): _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    if isinstance(node, bool):
        return bool(node)
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    return str(node) if node is not None else None


def dump(data: Any) -> str:
    """Block-style YAML text for plain data."""
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def failure_threshold(budget: int, initial_delay: int, period: int) -> int:
    """Smallest ``failureThreshold`` so that delay + period x threshold >= budget."""
    return max(1, math.ceil((budget - initial_delay) / max(1, period)))


def startup_probe(
    lang: str,
    source_probe: Any,
    budget: int,
    *,
    initial_delay: int = 0,
    period: int = 10,
) -> str:
    """A ``startupProbe`` block that reuses the handler (path/port) of ``source_probe``."""
    handler: dict[str, Any] = {}
    if hasattr(source_probe, "get"):
        for name in _HANDLERS:
            if source_probe.get(name) is not None:
                handler = {name: _plain(source_probe[name])}
                break
    if not handler:
        handler = {"httpGet": {"path": "/health", "port": 8000}}
    probe: dict[str, Any] = dict(handler)
    if initial_delay:
        probe["initialDelaySeconds"] = initial_delay
    probe["periodSeconds"] = period
    probe["failureThreshold"] = failure_threshold(budget, initial_delay, period)
    comment = t(lang, "fix.startup_probe", budget=budget)
    return f"# {comment}\n" + dump({"startupProbe": probe})


def shm_volume(lang: str, container_name: str, volume: str = "shm", size: str = DEFAULT_SHM_SIZE) -> str:
    """A memory-backed ``/dev/shm`` volume plus its mount."""
    body = {
        "volumes": [{"name": volume, "emptyDir": {"medium": "Memory", "sizeLimit": size}}],
        "containers": [
            {
                "name": container_name,
                "volumeMounts": [{"name": volume, "mountPath": "/dev/shm"}],
            }
        ],
    }
    return f"# {t(lang, 'fix.shm')}\n" + dump(body)


def shm_volume_only(lang: str, volume: str, size: str = DEFAULT_SHM_SIZE) -> str:
    """Replace an existing ``/dev/shm`` volume with a large enough memory-backed one."""
    body = {"volumes": [{"name": volume, "emptyDir": {"medium": "Memory", "sizeLimit": size}}]}
    return f"# {t(lang, 'fix.shm_volume')}\n" + dump(body)


def gpu_resources(lang: str, key: str, count: int) -> str:
    """``resources`` with the right GPU count."""
    value = str(count)
    body = {"resources": {"limits": {key: value}, "requests": {key: value}}}
    return f"# {t(lang, 'fix.gpu')}\n" + dump(body)


def token_secret(lang: str, env_name: str) -> str:
    """Read the Hugging Face token from a Secret instead of a literal."""
    body = {
        "env": [
            {
                "name": env_name,
                "valueFrom": {"secretKeyRef": {"name": "hf-token-secret", "key": "token"}},
            }
        ]
    }
    return f"# {t(lang, 'fix.token')}\n" + dump(body)


def cache_volume(lang: str, container_name: str, mount_path: str, volume: str = "hf-cache") -> str:
    """Mount a PersistentVolumeClaim at the model cache path."""
    body = {
        "volumes": [{"name": volume, "persistentVolumeClaim": {"claimName": "hf-cache"}}],
        "containers": [
            {"name": container_name, "volumeMounts": [{"name": volume, "mountPath": mount_path}]}
        ],
    }
    return f"# {t(lang, 'fix.cache')}\n" + dump(body)


def mem_fraction(lang: str, flag: str, value: str) -> str:
    """A valid memory fraction argument."""
    return f"# {t(lang, 'fix.mem')}\n{flag}={value}\n"

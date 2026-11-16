"""Cross-checks between serving-server arguments and the pod spec."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import fixes, rules, servers
from .i18n import t
from .loader import Document, item_line, load_paths
from .quantity import parse_quantity
from .workloads import PodTarget, iter_containers, pod_targets

GPU_KEYS = ("nvidia.com/gpu", "amd.com/gpu")
DEFAULT_STARTUP_BUDGET = 600
MIN_SHM_BYTES = 1024**3  # 1Gi, the size the TGI README recommends
HF_TOKEN_ENVS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
MASKED_TOKEN = "hf_****"


@dataclass
class Options:
    """Settings for one lint run."""

    startup_budget: int = DEFAULT_STARTUP_BUDGET
    disable: frozenset[str] = frozenset()
    lang: str = "en"


@dataclass
class Finding:
    """One problem found in one container."""

    file: str
    line: int
    kind: str
    name: str
    namespace: str
    container: str
    server: str
    rule: str
    severity: str
    message: str
    fix: str | None = None
    data: dict[str, Any] = field(default_factory=dict)  # numbers behind the message

    def as_dict(self) -> dict[str, Any]:
        """The public JSON shape (``data`` is internal)."""
        return {
            "file": self.file,
            "line": self.line,
            "kind": self.kind,
            "name": self.name,
            "namespace": self.namespace,
            "container": self.container,
            "server": self.server,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "fix": self.fix,
        }


@dataclass
class LintResult:
    findings: list[Finding]
    containers: int  # serving containers that were checked
    files: int


# ---------------------------------------------------------------- helpers


PROBE_DEFAULTS = (("initialDelaySeconds", 0), ("periodSeconds", 10), ("failureThreshold", 3))


def probe_value(probe: Any, key: str, default: int) -> int:
    """Read a probe number; a missing key or a null value means the Kubernetes default.

    Raises ``ValueError``/``TypeError`` when the value is not a number.
    """
    value = probe.get(key)
    return default if value is None else int(value)


def probe_budget(probe: Any) -> int | None:
    """Seconds the kubelet waits before acting: delay + period x failureThreshold."""
    if not hasattr(probe, "get"):
        return None
    try:
        delay, period, threshold = (probe_value(probe, k, d) for k, d in PROBE_DEFAULTS)
    except (TypeError, ValueError):
        return None
    return delay + period * threshold


def _probe_numbers(ctx: "_Context", probe: Any, name: str) -> tuple[int, int, int] | None:
    """Delay, period and threshold of a probe, or ``None`` after reporting a bad value."""
    if not hasattr(probe, "get"):
        return None
    numbers = []
    for key, default in PROBE_DEFAULTS:
        try:
            numbers.append(probe_value(probe, key, default))
        except (TypeError, ValueError):
            ctx.emit("arg-unresolved", "msg.arg-unresolved",
                     arg=f"{name}.{key}", value=str(probe.get(key)))
            return None
    return numbers[0], numbers[1], numbers[2]


def gpu_resources(container: Any) -> tuple[int | None, str | None, str | None]:
    """Return ``(gpu_count, resource_key, mig_resource)`` of a container.

    ``limits`` win over ``requests``. ``gpu_count`` is ``None`` when no GPU
    resource is set or its value is not a whole number.
    """
    resources = container.get("resources")
    if not hasattr(resources, "get"):
        return None, None, None
    total, used_key, mig = 0, None, None
    for section in ("limits", "requests"):
        values = resources.get(section)
        if not hasattr(values, "get"):
            continue
        for key in values:
            if str(key).startswith("nvidia.com/mig-") and mig is None:
                mig = str(key)
    for key in GPU_KEYS:
        for section in ("limits", "requests"):
            values = resources.get(section)
            if hasattr(values, "get") and values.get(key) is not None:
                try:
                    total += int(str(values[key]))
                except ValueError:
                    return None, key, mig
                used_key = key
                break
    return (total if used_key else None), used_key, mig


def _normalize(path: str) -> str:
    return path.rstrip("/") or "/"


def _mounts(container: Any) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    mounts = container.get("volumeMounts")
    if isinstance(mounts, list):
        for mount in mounts:
            if hasattr(mount, "get") and mount.get("mountPath") and mount.get("name"):
                result.append((_normalize(str(mount["mountPath"])), str(mount["name"])))
    return result


def _volumes(pod_spec: Any) -> dict[str, Any]:
    volumes = pod_spec.get("volumes")
    result: dict[str, Any] = {}
    if isinstance(volumes, list):
        for volume in volumes:
            if hasattr(volume, "get") and volume.get("name"):
                result[str(volume["name"])] = volume
    return result


def _covering_mount(mounts: list[tuple[str, str]], path: str) -> tuple[str, str] | None:
    best: tuple[str, str] | None = None
    for mount_path, name in mounts:
        covers = mount_path == "/" or path == mount_path or path.startswith(mount_path + "/")
        if covers and (best is None or len(mount_path) > len(best[0])):
            best = (mount_path, name)
    return best


def _formula(spec: servers.ServerSpec, lws: bool) -> str:
    if spec.server == servers.TGI:
        return f"num-shard {spec.required_gpus}"
    if lws:
        parts = [f"tp {spec.tp}"]
        if spec.dp_local:
            parts.append(f"dp-local {spec.dp_local}")
        return " x ".join(parts)
    if spec.dp_local:
        return f"tp {spec.tp} x pp {spec.pp} x dp-local {spec.dp_local}"
    return f"tp {spec.tp} x pp {spec.pp} x dp {spec.dp}"


# ---------------------------------------------------------------- the checks


class _Context:
    """Collects findings for one container and applies ``--disable``/annotations."""

    def __init__(self, target: PodTarget, container: Any, line: int,
                 spec: servers.ServerSpec, options: Options) -> None:
        self.target, self.container, self.line = target, container, line
        self.spec, self.options = spec, options
        self.name = str(container.get("name", ""))
        self.findings: list[Finding] = []

    def emit(self, rule_id: str, key: str, fix: str | None = None, *,
             line: int | None = None, data: dict[str, Any] | None = None,
             **params: object) -> None:
        if rule_id in self.options.disable or rule_id in self.target.ignored:
            return
        rule = rules.BY_ID[rule_id]
        message = t(self.options.lang, key, **params)
        self.findings.append(Finding(
            file=self.target.file, line=line or self.line, kind=self.target.kind,
            name=self.target.name, namespace=self.target.namespace, container=self.name,
            server=self.spec.server, rule=rule_id, severity=rule.severity,
            message=message, fix=fix, data=data or {},
        ))


def _check_gpu(ctx: _Context, gpus: int | None, key: str | None, mig: str | None) -> None:
    spec, lang = ctx.spec, ctx.options.lang
    image = str(ctx.container.get("image", "")).lower()
    if mig:
        ctx.emit("gpu-mig-unchecked", "msg.gpu-mig-unchecked", resource=mig)
        return
    if gpus is None and key is None:
        if "cpu" not in image:
            ctx.emit("gpu-missing", "msg.gpu-missing",
                     fixes.gpu_resources(lang, "nvidia.com/gpu", spec.required_gpus or 1),
                     server=spec.server)
        return
    if gpus == 0 and "cpu" not in image:
        ctx.emit("gpu-missing", "msg.gpu-missing",
                 fixes.gpu_resources(lang, key or "nvidia.com/gpu", spec.required_gpus or 1),
                 server=spec.server)
        return
    required = spec.required_gpus
    if gpus is not None and required is not None and required != gpus:
        ctx.emit(
            "gpu-parallel-mismatch", "msg.gpu-parallel-mismatch",
            fixes.gpu_resources(lang, key or "nvidia.com/gpu", required),
            data={"required": required, "gpus": gpus},
            required=required, formula=_formula(spec, ctx.target.lws), gpus=gpus,
            resource=key,
        )


def _check_shm(ctx: _Context, gpus: int | None, mounts: list[tuple[str, str]],
               volumes: dict[str, Any]) -> None:
    spec, lang = ctx.spec, ctx.options.lang
    env = servers.literal_env(ctx.container)
    shm = next((name for path, name in mounts if path == "/dev/shm"), None)

    needed = spec.required_gpus
    if needed is None and spec.server == servers.TGI and "shard" not in spec.unresolved:
        needed = gpus  # TGI shards over every visible GPU when --num-shard is not set
    host_ipc = ctx.target.pod_spec.get("hostIPC") is True
    nccl_off = env.get("NCCL_SHM_DISABLE") == "1"
    if shm is None:
        if needed is not None and needed > 1 and not host_ipc and not nccl_off:
            ctx.emit("shm-missing", "msg.shm-missing",
                     fixes.shm_volume(lang, ctx.name), required=needed)
        return
    volume = volumes.get(shm)
    empty_dir = volume.get("emptyDir") if volume is not None else None
    if volume is None or empty_dir is None:
        return  # hostPath, claim templates and so on: cannot judge
    if not hasattr(empty_dir, "get"):
        empty_dir = {}
    if empty_dir.get("medium") != "Memory":
        ctx.emit("shm-not-memory", "msg.shm-not-memory",
                 fixes.shm_volume_only(lang, shm), volume=shm)
        return
    limit = empty_dir.get("sizeLimit")
    if limit is not None:
        try:
            size = parse_quantity(limit)
        except ValueError:
            return
        if size < MIN_SHM_BYTES:
            ctx.emit("shm-too-small", "msg.shm-too-small",
                     fixes.shm_volume_only(lang, shm), data={"bytes": size},
                     volume=shm, size=str(limit))


def _check_probes(ctx: _Context) -> None:
    liveness = ctx.container.get("livenessProbe")
    startup = ctx.container.get("startupProbe")
    target = ctx.options.startup_budget
    lang = ctx.options.lang
    if startup is None:
        numbers = _probe_numbers(ctx, liveness, "livenessProbe")
        if numbers is None:
            return
        budget = numbers[0] + numbers[1] * numbers[2]
        if budget < target:
            ctx.emit("no-startup-probe", "msg.no-startup-probe",
                     fixes.startup_probe(lang, liveness, target),
                     data={"budget": budget, "target": target},
                     budget=budget, target=target)
        return
    numbers = _probe_numbers(ctx, startup, "startupProbe")
    if numbers is None:
        return
    delay, period, threshold = numbers
    budget = delay + period * threshold
    if budget < target:
        ctx.emit("probe-budget-short", "msg.probe-budget-short",
                 fixes.startup_probe(lang, startup, target, initial_delay=delay, period=period),
                 data={"budget": budget, "target": target}, budget=budget, target=target)


def _check_cache(ctx: _Context, mounts: list[tuple[str, str]], volumes: dict[str, Any]) -> None:
    spec = ctx.spec
    model = spec.model
    if not model or model.startswith(("/", "./", "../", "~")):
        return  # unknown, or weights already on disk
    if spec.unresolved.get("cache"):
        return
    if spec.cache_paths:
        path = spec.cache_paths[0]
        mount = _covering_mount(mounts, path)
    else:
        if spec.server == servers.TGI:
            path = "/data"
            mount = _covering_mount(mounts, path)
        else:
            path = "~/.cache/huggingface"
            mount = next(
                ((p, n) for p, n in mounts
                 if p.endswith("/.cache/huggingface") or p.endswith("/.cache/huggingface/hub")),
                None,
            )
    mount_path = mount[0] if mount else path
    if mount and not spec.cache_paths:
        path = mount[0]  # show the mount that was found instead of the default guess
    lang = ctx.options.lang
    fix = fixes.cache_volume(lang, ctx.name, mount_path if mount else (
        "/data" if spec.server == servers.TGI else "/root/.cache/huggingface"))
    if mount is None:
        ctx.emit("hf-cache-ephemeral", "msg.hf-cache-ephemeral.nomount", fix,
                 model=model, path=path)
        return
    volume = volumes.get(mount[1])
    if volume is not None and volume.get("emptyDir") is not None:
        ctx.emit("hf-cache-ephemeral", "msg.hf-cache-ephemeral.emptydir", fix,
                 model=model, path=path, volume=mount[1])


def _check_token(ctx: _Context) -> None:
    env = ctx.container.get("env")
    if not isinstance(env, list):
        return
    for index, entry in enumerate(env):
        if not hasattr(entry, "get") or str(entry.get("name")) not in HF_TOKEN_ENVS:
            continue
        value = entry.get("value")
        if value is not None and str(value) != "":
            name = str(entry["name"])
            ctx.emit("hf-token-plaintext", "msg.hf-token-plaintext",
                     fixes.token_secret(ctx.options.lang, name),
                     line=item_line(env, index, ctx.line),
                     env=name, masked=MASKED_TOKEN)  # the value itself is never read into output


def _check_mem(ctx: _Context) -> None:
    spec = ctx.spec
    if spec.mem is None or spec.server not in servers.MEM_FLAG:
        return
    flag = servers.MEM_FLAG[spec.server]
    inclusive = spec.server == servers.VLLM
    try:
        number = float(spec.mem)
    except ValueError:
        number = float("nan")
    valid = 0 < number <= 1 if inclusive else 0 < number < 1
    if not valid:
        ctx.emit("mem-fraction-range", "msg.mem-fraction-range",
                 fixes.mem_fraction(ctx.options.lang, flag, "0.9"),
                 flag=flag, value=spec.mem, range="(0, 1]" if inclusive else "(0, 1)")


def _check_info(ctx: _Context) -> None:
    spec = ctx.spec
    if spec.config:
        ctx.emit("config-file-unchecked", "msg.config-file-unchecked", file=spec.config)
    for arg, value in spec.unresolved.items():
        ctx.emit("arg-unresolved", "msg.arg-unresolved", arg=arg, value=value)


def check_container(target: PodTarget, container: Any, line: int,
                    options: Options) -> list[Finding] | None:
    """Findings for one container, or ``None`` when it is not a serving container."""
    spec = servers.inspect_container(container, lws=target.lws)
    if spec is None:
        return None
    ctx = _Context(target, container, line, spec, options)
    gpus, key, mig = gpu_resources(container)
    mounts, volumes = _mounts(container), _volumes(target.pod_spec)
    _check_gpu(ctx, gpus, key, mig)
    _check_shm(ctx, gpus, mounts, volumes)
    _check_probes(ctx)
    _check_cache(ctx, mounts, volumes)
    _check_token(ctx)
    _check_mem(ctx)
    _check_info(ctx)
    return ctx.findings


# ---------------------------------------------------------------- entry points


def lint_documents(docs: Iterable[Document], options: Options | None = None,
                   files_read: int | None = None) -> LintResult:
    """Lint already parsed documents (``files_read`` counts inputs without documents too)."""
    options = options or Options()
    findings: list[Finding] = []
    containers = 0
    files: set[str] = set()
    for doc in docs:
        files.add(doc.file)
        for target in pod_targets(doc):
            for container, line in iter_containers(target):
                result = check_container(target, container, line, options)
                if result is None:
                    continue
                containers += 1
                findings.extend(result)
    findings.sort(key=lambda f: (f.file, f.line, f.rule))
    return LintResult(findings, containers, len(files) if files_read is None else files_read)


def lint(paths: Iterable[str], options: Options | None = None) -> list[Finding]:
    """Lint files, directories or ``-`` (stdin) and return the findings.

    Raises :class:`kube_llm_lint.loader.InputError` for unreadable input.
    """
    return lint_documents(load_paths(paths), options).findings

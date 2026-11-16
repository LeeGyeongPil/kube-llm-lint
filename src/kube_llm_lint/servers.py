"""Recognise LLM serving containers and read their arguments.

Argument tables follow the vLLM engine/serve arguments, the SGLang server
arguments and the TGI launcher options (see ``rules.py`` for source URLs).
Unknown flags are ignored; only the flags listed here are interpreted.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

VLLM, SGLANG, TGI = "vllm", "sglang", "tgi"

# flag -> canonical name, per server
ALIASES: dict[str, dict[str, str]] = {
    VLLM: {
        "--tensor-parallel-size": "tp", "-tp": "tp",
        "--pipeline-parallel-size": "pp", "-pp": "pp",
        "--data-parallel-size": "dp", "-dp": "dp",
        "--data-parallel-size-local": "dp_local",
        "--gpu-memory-utilization": "mem", "--device-memory-utilization": "mem",
        "--download-dir": "cache",
        "--config": "config",
        "--model": "model",
    },
    SGLANG: {
        "--tensor-parallel-size": "tp", "--tp-size": "tp", "--tp": "tp",
        "--pipeline-parallel-size": "pp", "--pp-size": "pp",
        "--data-parallel-size": "dp", "--dp-size": "dp", "--dp": "dp",
        "--mem-fraction-static": "mem",
        "--download-dir": "cache",
        "--model-path": "model", "--model": "model",
    },
    TGI: {
        "--num-shard": "shard",
        "--model-id": "model",
        "--huggingface-hub-cache": "cache",
    },
}
# flag name shown to the user for the memory fraction check
MEM_FLAG = {VLLM: "--gpu-memory-utilization", SGLANG: "--mem-fraction-static"}
# environment variables that are documented for each server (nothing else is read)
TGI_ENV = {"NUM_SHARD": "shard", "MODEL_ID": "model", "HUGGINGFACE_HUB_CACHE": "cache"}
HF_CACHE_ENV = ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE")

_SHELLS = {"sh", "bash", "ash", "dash", "zsh"}
_K8S_VAR = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")
_SHELL_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")
_SHELL_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_ANY_VAR = re.compile(r"\$[({]?[A-Za-z_]")
_PUNCT = set("();<>|&")


@dataclass
class ServerSpec:
    """What a serving container asks for, as far as it can be read statically."""

    server: str
    argv: list[str]
    tp: int | None = 1
    pp: int | None = 1
    dp: int | None = 1
    dp_local: int | None = None
    required_gpus: int | None = None  # None: unknown (TGI without shard count, or unresolved)
    shard_given: bool = False
    mem: str | None = None
    model: str | None = None
    cache_paths: list[str] = field(default_factory=list)
    config: str | None = None
    unresolved: dict[str, str] = field(default_factory=dict)  # canonical arg -> raw value


def literal_env(container: Any) -> dict[str, str | None]:
    """Container env as ``name -> literal value`` (``None`` for valueFrom entries)."""
    result: dict[str, str | None] = {}
    env = container.get("env")
    if not isinstance(env, list):
        return result
    for entry in env:
        if hasattr(entry, "get") and entry.get("name") is not None:
            value = entry.get("value")
            result[str(entry["name"])] = None if value is None else str(value)
    return result


def _expand_k8s(text: str, env: dict[str, str | None]) -> str:
    def sub(match: re.Match[str]) -> str:
        value = env.get(match.group(1))
        return match.group(0) if value is None else value

    return _K8S_VAR.sub(sub, text)


def _expand_shell(text: str, env: dict[str, str | None]) -> str:
    def default(match: re.Match[str]) -> str:
        value = env.get(match.group(1))
        return match.group(2) if value in (None, "") else value  # type: ignore[return-value]

    def plain(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        value = env.get(name)
        return match.group(0) if value is None else value

    return _SHELL_VAR.sub(plain, _SHELL_DEFAULT.sub(default, text))


def _split_script(script: str) -> list[list[str]]:
    """Split a shell string into commands (separated by ``&&``, ``;``, ``|`` ...)."""
    script = script.replace("\\\n", " ").replace("\n", " ; ")
    script = _K8S_VAR.sub(lambda m: "${" + m.group(1) + "}", script)
    try:
        lexer = shlex.shlex(script, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = script.split()
    commands: list[list[str]] = [[]]
    for token in tokens:
        if token and set(token) <= _PUNCT:
            commands.append([])
        else:
            commands[-1].append(token)
    return [c for c in commands if c]


def _base(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _find_marker(tokens: list[str]) -> tuple[str, int, bool] | None:
    """Return ``(server, index_after_marker, has_positional_model)`` or ``None``."""
    for i, token in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        base = _base(token)
        if base == "vllm" and nxt == "serve":
            return VLLM, i + 2, True
        if token.startswith("vllm.entrypoints"):
            return VLLM, i + 1, False
        if token == "sglang.launch_server" or token.startswith("sglang.launch_server"):
            return SGLANG, i + 1, False
        if base == "sglang" and nxt == "serve":
            return SGLANG, i + 2, False
        if base == "text-generation-launcher":
            return TGI, i + 1, False
    return None


def _image_server(image: str) -> str | None:
    name = re.sub(r"[:@][^/]*$", "", image.lower())
    if "sglang" in name:
        return SGLANG
    if "text-generation-inference" in name:
        return TGI
    if "vllm" in name:
        return VLLM
    return None


def _candidate_commands(container: Any, env: dict[str, str | None]) -> list[list[str]]:
    raw = [
        _expand_k8s(str(x), env)
        for x in list(container.get("command") or []) + list(container.get("args") or [])
    ]
    if len(raw) >= 3 and _base(raw[0]) in _SHELLS and raw[1].startswith("-") and raw[1].endswith("c"):
        return [
            [_expand_shell(tok, env) for tok in command] for command in _split_script(raw[2])
        ]
    return [raw]


def _int_or_none(value: str | None, default: int | None) -> tuple[int | None, bool]:
    """Return ``(number, ok)``; a missing value gives ``default``."""
    if value is None:
        return default, True
    if _ANY_VAR.search(value):
        return None, False
    try:
        return int(value.strip()), True
    except ValueError:
        return None, False


def inspect_container(container: Any, *, lws: bool = False) -> ServerSpec | None:
    """Return the :class:`ServerSpec` of a serving container, or ``None``."""
    env = literal_env(container)
    commands = _candidate_commands(container, env)
    image = str(container.get("image", ""))

    server: str | None = None
    tokens: list[str] = []
    start = 0
    positional_model = False
    for command in commands:
        found = _find_marker(command)
        if found:
            server, start, positional_model = found
            tokens = command
            break
    if server is None:
        # Only an image whose entrypoint runs the server is judged by its name.
        # A custom ``command`` (Ray worker, start script ...) may run anything.
        if container.get("command"):
            return None
        server = _image_server(image)
        if server is None:
            return None
        tokens = [t for command in commands for t in command]
        start = 0

    table = ALIASES[server]
    args: dict[str, str] = {}
    model_positional: str | None = None
    if positional_model and start < len(tokens) and not tokens[start].startswith("-"):
        model_positional = tokens[start]
        start += 1
    i = start
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok.startswith("-"):
            continue
        name, eq, value = tok.partition("=")
        if server == VLLM and name.startswith("--"):
            # vLLM's FlexibleArgumentParser accepts '_' and '-' in long flag names
            name = "--" + name[2:].replace("_", "-")
        canon = table.get(name)
        if canon is None:
            continue
        if not eq:
            if i < len(tokens) and (
                not tokens[i].startswith("-") or re.fullmatch(r"-\d+(\.\d+)?", tokens[i])
            ):
                value = tokens[i]
                i += 1
            else:
                continue  # flag without a value: ignore
        args[canon] = value

    if server == TGI:
        for env_name, canon in TGI_ENV.items():
            if canon not in args and env.get(env_name) not in (None, ""):
                args[canon] = env[env_name]  # type: ignore[assignment]
    if model_positional and "model" not in args:
        args["model"] = model_positional

    spec = ServerSpec(server=server, argv=tokens)
    # parallel sizes
    if server == TGI:
        shard, ok = _int_or_none(args.get("shard"), None)
        spec.shard_given = "shard" in args
        spec.required_gpus = shard
        if not ok:
            spec.unresolved["shard"] = args["shard"]
    else:
        values: dict[str, int | None] = {}
        for canon, default in (("tp", 1), ("pp", 1), ("dp", 1), ("dp_local", None)):
            number, ok = _int_or_none(args.get(canon), default)
            values[canon] = number
            if not ok:
                spec.unresolved[canon] = args[canon]
        spec.tp, spec.pp, spec.dp, spec.dp_local = (
            values["tp"], values["pp"], values["dp"], values["dp_local"],
        )
        if "dp_local" in spec.unresolved or not all(
            v is not None for v in (spec.tp, spec.pp, spec.dp)
        ):
            spec.required_gpus = None
        elif lws:
            spec.required_gpus = spec.tp * (spec.dp_local or 1)  # type: ignore[operator]
        else:
            spec.required_gpus = spec.tp * spec.pp * (spec.dp_local or spec.dp)  # type: ignore[operator]

    # memory fraction, model, config file
    mem = args.get("mem")
    if mem is not None:
        if _ANY_VAR.search(mem):
            spec.unresolved["mem"] = mem
        else:
            spec.mem = mem
    model = args.get("model")
    if model is not None:
        if _ANY_VAR.search(model):
            spec.unresolved["model"] = model
        else:
            spec.model = model
    config = args.get("config")
    if config is not None:
        spec.config = config

    # explicit cache locations: flag first, then the documented environment variables
    cache = args.get("cache")
    if cache is None and server != TGI:
        for name in HF_CACHE_ENV:
            if env.get(name):
                cache = env[name]
                break
        if cache is None and env.get("HF_HOME"):
            cache = env["HF_HOME"].rstrip("/") + "/hub"  # type: ignore[union-attr]
    if cache is not None:
        if _ANY_VAR.search(cache):
            spec.unresolved["cache"] = cache
        else:
            spec.cache_paths.append(os.path.normpath(cache))
    return spec

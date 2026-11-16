"""Language selection and message lookup (English and Korean)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources

LANGUAGES = ("en", "ko")
ENV_VAR = "KUBE_LLM_LINT_LANG"
# Order matters: the first variable that is set and non-empty decides.
LOCALE_VARS = (ENV_VAR, "LC_ALL", "LC_MESSAGES", "LANG")


def resolve_lang(cli_value: str | None, environ: Mapping[str, str]) -> str:
    """Pick the output language.

    ``--lang`` beats ``KUBE_LLM_LINT_LANG``, which beats ``LC_ALL``,
    ``LC_MESSAGES`` and ``LANG``. Anything that does not start with ``ko`` is English.
    """
    if cli_value:
        return "ko" if cli_value.lower().startswith("ko") else "en"
    for name in LOCALE_VARS:
        value = environ.get(name, "")
        if value:
            return "ko" if value.lower().startswith("ko") else "en"
    return "en"


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    """Load the message table for one language."""
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language {lang!r}; use one of {LANGUAGES}")
    text = resources.files("kube_llm_lint").joinpath("locales", f"{lang}.json").read_text("utf-8")
    return json.loads(text)


def t(lang: str, key: str, **params: object) -> str:
    """Look up ``key`` in ``lang`` and fill ``{placeholders}``."""
    table = catalog(lang)
    template = table[key]
    return template.format(**params) if params else template

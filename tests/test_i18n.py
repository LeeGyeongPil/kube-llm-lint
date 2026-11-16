"""AC-10: English and Korean output and language precedence."""

from __future__ import annotations

import json
import re

import pytest

from kube_llm_lint import rules
from kube_llm_lint.cli import main
from kube_llm_lint.i18n import LANGUAGES, catalog, resolve_lang

from helpers import ROOT

BROKEN = str(ROOT / "examples" / "broken.yaml")
HANGUL = re.compile("[가-힣]")


def test_catalog_keys_equal():
    en, ko = set(catalog("en")), set(catalog("ko"))
    assert en - ko == set()
    assert ko - en == set()
    # every rule has a description and a message in both languages
    for rule in rules.RULES:
        assert f"rule.{rule.id}" in en


def test_catalog_placeholders_equal():
    placeholder = re.compile(r"\{(\w+)\}")
    en, ko = catalog("en"), catalog("ko")
    for key in en:
        assert set(placeholder.findall(en[key])) == set(placeholder.findall(ko[key])), key


def test_lang_precedence():
    assert resolve_lang("ko", {"LANG": "en_US.UTF-8"}) == "ko"
    assert resolve_lang("en", {"KUBE_LLM_LINT_LANG": "ko", "LANG": "ko_KR.UTF-8"}) == "en"
    assert resolve_lang(None, {"KUBE_LLM_LINT_LANG": "en", "LANG": "ko_KR.UTF-8"}) == "en"
    assert resolve_lang(None, {"KUBE_LLM_LINT_LANG": "ko", "LC_ALL": "en_US"}) == "ko"
    assert resolve_lang(None, {"LC_ALL": "ko_KR.UTF-8", "LC_MESSAGES": "en_US", "LANG": "en_US"}) == "ko"
    assert resolve_lang(None, {"LC_MESSAGES": "ko_KR", "LANG": "en_US"}) == "ko"
    assert resolve_lang(None, {"LANG": "ko_KR.UTF-8"}) == "ko"
    assert resolve_lang(None, {"LANG": "fr_FR.UTF-8"}) == "en"
    assert resolve_lang(None, {"LC_ALL": "", "LANG": "ko_KR.UTF-8"}) == "ko"  # empty counts as unset
    assert resolve_lang(None, {}) == "en"


def run(capsys, *argv, env=None):
    code = main(list(argv), environ=env or {})
    return code, capsys.readouterr()


def test_help_and_findings_localized(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help", "--lang", "ko"], environ={})
    assert exc.value.code == 0
    ko_help = capsys.readouterr().out
    assert "사용법" in ko_help and HANGUL.search(ko_help)

    with pytest.raises(SystemExit):
        main(["--help", "--lang", "en"], environ={"LANG": "ko_KR.UTF-8"})
    en_help = capsys.readouterr().out
    assert "usage:" in en_help and not HANGUL.search(en_help)

    _, ko = run(capsys, BROKEN, "--lang", "ko")
    _, en = run(capsys, BROKEN, "--lang", "en")
    assert HANGUL.search(ko.out) and "요약" in ko.out
    assert not HANGUL.search(en.out) and "Summary" in en.out
    # rule ids and JSON field names do not change with the language
    for rule in ("gpu-parallel-mismatch", "shm-missing", "no-startup-probe"):
        assert rule in ko.out and rule in en.out


def test_env_selects_language(capsys):
    _, out = run(capsys, BROKEN, env={"KUBE_LLM_LINT_LANG": "ko"})
    assert "요약" in out.out
    _, out = run(capsys, BROKEN, env={"LANG": "ko_KR.UTF-8", "KUBE_LLM_LINT_LANG": "en"})
    assert "Summary" in out.out


def test_json_ids_are_language_independent(capsys):
    _, en = run(capsys, BROKEN, "--format", "json", "--lang", "en")
    _, ko = run(capsys, BROKEN, "--format", "json", "--lang", "ko")
    strip = lambda text: [(f["rule"], f["line"], f["severity"]) for f in json.loads(text)["findings"]]  # noqa: E731
    assert strip(en.out) == strip(ko.out)


def test_errors_are_localized(capsys):
    _, ko = run(capsys, "nope.yaml", "--lang", "ko")
    assert HANGUL.search(ko.err)
    _, en = run(capsys, "nope.yaml", "--lang", "en")
    assert not HANGUL.search(en.err)


def test_every_rule_message_renders_in_both_languages():
    for lang in LANGUAGES:
        table = catalog(lang)
        for rule in rules.RULES:
            assert any(k == f"msg.{rule.id}" or k.startswith(f"msg.{rule.id}.") for k in table)

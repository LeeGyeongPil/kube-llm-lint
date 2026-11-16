"""Command line entry point: ``kube-llm-lint``."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from typing import NoReturn

from . import __version__, report, rules
from .checks import DEFAULT_STARTUP_BUDGET, Options, lint_documents
from .i18n import LANGUAGES, resolve_lang, t
from .loader import InputError, load_inputs

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2
FAIL_LEVELS = ("error", "warning", "info", "none")


_ARGPARSE_PATTERNS = (
    (re.compile(r"argument (.+?): invalid choice: (.+?) \(choose from (.*)\)$"), "err.arg_choice",
     ("option", "value", "choices")),
    (re.compile(r"argument (.+?): invalid \w+ value: (.+)$"), "err.arg_value", ("option", "value")),
    (re.compile(r"argument (.+?): expected one argument$"), "err.arg_missing", ("option",)),
    (re.compile(r"unrecognized arguments: (.*)$"), "err.arg_unknown", ("args",)),
)


def translate_argparse(lang: str, message: str) -> str:
    """Translate the argparse errors users actually hit; others are shown as they are."""
    for pattern, key, names in _ARGPARSE_PATTERNS:
        match = pattern.search(message)
        if match:
            return t(lang, key, **dict(zip(names, match.groups())))
    return message


class _Formatter(argparse.HelpFormatter):
    """Help formatter with a translated ``usage:`` prefix."""

    lang = "en"

    def add_usage(self, usage, actions, groups, prefix=None):  # type: ignore[no-untyped-def]
        return super().add_usage(usage, actions, groups, prefix or t(self.lang, "help.usage_prefix"))


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose errors are translated and exit with code 2."""

    lang = "en"

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        text = translate_argparse(self.lang, message)
        sys.stderr.write(f"{self.prog}: {t(self.lang, 'err.prefix')}: {text}\n")
        raise SystemExit(EXIT_USAGE)


def build_parser(lang: str) -> argparse.ArgumentParser:
    """The argument parser with help text in ``lang``."""
    formatter = type("Formatter", (_Formatter,), {"lang": lang})
    parser = _Parser(
        prog="kube-llm-lint",
        description=t(lang, "help.description"),
        epilog=t(lang, "help.epilog"),
        formatter_class=formatter,
        add_help=False,
    )
    parser.lang = lang
    parser._positionals.title = t(lang, "help.positional")  # noqa: SLF001
    parser._optionals.title = t(lang, "help.options")  # noqa: SLF001
    parser.add_argument("paths", nargs="*", metavar="PATH", help=t(lang, "help.paths"))
    parser.add_argument("-h", "--help", action="help", help=t(lang, "help.help"))
    parser.add_argument("--version", action="version", version=f"kube-llm-lint {__version__}",
                        help=t(lang, "help.version"))
    parser.add_argument("--lang", choices=LANGUAGES, help=t(lang, "help.lang"))
    parser.add_argument("--format", choices=("text", "json", "sarif"), default="text",
                        help=t(lang, "help.format"))
    parser.add_argument("--fail-on", choices=FAIL_LEVELS, default="error",
                        help=t(lang, "help.fail_on"))
    parser.add_argument("--startup-budget", type=int, default=DEFAULT_STARTUP_BUDGET,
                        metavar="SECONDS",
                        help=t(lang, "help.startup_budget", default=DEFAULT_STARTUP_BUDGET))
    parser.add_argument("--disable", default="", metavar="RULES", help=t(lang, "help.disable"))
    parser.add_argument("--list-rules", action="store_true", help=t(lang, "help.list_rules"))
    return parser


def _prescan_lang(argv: Sequence[str]) -> str | None:
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--lang")
    known, _ = pre.parse_known_args(list(argv))
    return known.lang


def _fail(lang: str, key: str, **params: object) -> int:
    sys.stderr.write(t(lang, "err.prefix") + ": " + t(lang, key, **params) + "\n")
    return EXIT_USAGE


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run the linter and return the process exit code (0, 1 or 2)."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    lang = resolve_lang(_prescan_lang(args_list), env)
    parser = build_parser(lang)
    args = parser.parse_args(args_list)

    if args.list_rules:
        sys.stdout.write(report.render_rules(lang))
        return EXIT_OK
    if not args.paths:
        return _fail(lang, "err.no_input")
    if args.startup_budget <= 0:
        return _fail(lang, "err.bad_budget", value=args.startup_budget)
    disabled = frozenset(p.strip() for p in args.disable.split(",") if p.strip())
    unknown = sorted(disabled - set(rules.BY_ID))
    if unknown:
        return _fail(lang, "err.unknown_rule", rules=", ".join(unknown),
                     valid=", ".join(rules.BY_ID))

    try:
        docs, files_read = load_inputs(args.paths)
    except InputError as exc:
        return _fail(lang, exc.key, **exc.params)

    options = Options(startup_budget=args.startup_budget, disable=disabled, lang=lang)
    result = lint_documents(docs, options, files_read)
    if args.format == "json":
        sys.stdout.write(report.render_json(result, lang))
    elif args.format == "sarif":
        sys.stdout.write(report.render_sarif(result, lang))
    else:
        color = sys.stdout.isatty() and "NO_COLOR" not in env
        sys.stdout.write(report.render_text(result, lang, color))

    if args.fail_on == "none":
        return EXIT_OK
    threshold = rules.SEVERITY_RANK[args.fail_on]
    failing = any(rules.SEVERITY_RANK[f.severity] >= threshold for f in result.findings)
    return EXIT_FINDINGS if failing else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

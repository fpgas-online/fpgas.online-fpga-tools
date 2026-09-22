"""`fpgatools` command line: one subcommand per module, registered here.

Each module exposes `add_parser(subparsers)` and gives its parser a `func`
default taking the parsed namespace and returning an exit code.
"""

from __future__ import annotations

import argparse
import importlib
import sys

# Modules that provide subcommands, in help order.
_SUBCOMMAND_MODULES = (
    "fpgatools.version",
    "fpgatools.patchset",
    "fpgatools.debianize",
    "fpgatools.bump",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fpgatools",
        description="Patch series, versions and packaging for the fpgas.online "
        "openFPGALoader/OpenOCD builds.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name in _SUBCOMMAND_MODULES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:  # a subcommand not implemented yet
            if exc.name != name:
                raise
            continue
        module.add_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except FpgatoolsError as exc:
        print(f"fpgatools: error: {exc}", file=sys.stderr)
        return 1


class FpgatoolsError(Exception):
    """A user-facing failure: printed without a traceback, exit status 1."""

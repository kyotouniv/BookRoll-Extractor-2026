from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .core import (
    DEFAULT_BASE_URL,
    build_plan,
    combine_collection,
    extract_collection,
    format_plan,
    plan_payload,
)


def _add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home-html", required=True, type=Path, help="saved BookRoll home HTML")
    parser.add_argument("--batch-dir", required=True, type=Path, help="new output directory")
    parser.add_argument("--known-index", type=Path, help="optional collection_index.json for known page counts")
    parser.add_argument("--select", help="material numbers, e.g. 1,3-5; default: all")


def _make_plan(args: argparse.Namespace):
    return build_plan(
        args.home_html,
        args.batch_dir,
        selection=args.select,
        known_page_counts=args.known_index,
    )


def _print_plan(args: argparse.Namespace) -> int:
    plans = _make_plan(args)
    payload = plan_payload(plans, args.home_html, args.batch_dir)
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_plan(payload))
    return 0


def _extract(args: argparse.Namespace) -> int:
    plans = _make_plan(args)
    payload = plan_payload(plans, args.home_html, args.batch_dir)
    if args.dry_run:
        print(format_plan(payload) if not args.json else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    cookie = os.environ.get(args.cookie_env, "")
    if not cookie.strip():
        raise ValueError(
            f"{args.cookie_env} is empty; set it in the process environment. "
            "The CLI never writes the cookie to disk."
        )

    def progress(message: str) -> None:
        print(message, flush=True)

    result = extract_collection(
        plans,
        args.batch_dir,
        cookie=cookie,
        base_url=args.base_url,
        delay=args.delay,
        retries=args.retries,
        progress=progress,
        combine=args.combine,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if result["summary"]["failed"] == 0 else 1


def _combine(args: argparse.Namespace) -> int:
    manifest = combine_collection(args.collection_index, args.output_pdf)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bookroll",
        description="Authorized BookRoll material extraction, PDF merge, and local WebUI.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    dry_run = subparsers.add_parser("dry-run", help="show the plan without network access or file writes")
    _add_plan_args(dry_run)
    dry_run.add_argument("--json", action="store_true", help="print machine-readable JSON")
    dry_run.set_defaults(handler=_print_plan)

    plan = subparsers.add_parser("plan", help="same as dry-run")
    _add_plan_args(plan)
    plan.add_argument("--json", action="store_true", help="print machine-readable JSON")
    plan.set_defaults(handler=_print_plan)

    extract = subparsers.add_parser("extract", help="download and decrypt selected authorized materials")
    _add_plan_args(extract)
    extract.add_argument("--base-url", default=os.environ.get("BOOKROLL_BASE_URL", DEFAULT_BASE_URL))
    extract.add_argument("--cookie-env", default="BOOKROLL_SESSION_COOKIE", help="environment variable name")
    extract.add_argument("--delay", type=float, default=0.15, help="seconds between image requests")
    extract.add_argument("--retries", type=int, default=3)
    extract.add_argument("--combine", action="store_true", help="also create batch/output/pdf/bookroll_all.pdf")
    extract.add_argument("--dry-run", action="store_true", help="plan only; never reads cookie or contacts network")
    extract.add_argument("--json", action="store_true", help="print dry-run plan as JSON")
    extract.set_defaults(handler=_extract)

    combine = subparsers.add_parser("combine", help="merge PDFs from an existing collection index")
    combine.add_argument("--collection-index", required=True, type=Path)
    combine.add_argument("--output-pdf", required=True, type=Path)
    combine.set_defaults(handler=_combine)

    webui = subparsers.add_parser("webui", help="start the local WebUI")
    webui.add_argument("--host", default="127.0.0.1")
    webui.add_argument("--port", type=int, default=51837, help="five-digit local port")
    webui.set_defaults(handler=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "webui":
            from .webui import run_server

            run_server(args.host, args.port)
            return 0
        return args.handler(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

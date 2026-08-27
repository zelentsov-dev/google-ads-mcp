from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import NoReturn

from google_ads_mcp.adapter import GoogleAdsReadAdapter
from google_ads_mcp.config import initialize_config, load_config
from google_ads_mcp.constants import API_VERSION, VERSION
from google_ads_mcp.errors import PublicError
from google_ads_mcp.server import serve_stdio
from google_ads_mcp.service import GoogleAdsService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="google-ads-mcp")
    parser.add_argument("--config", help="Explicit accounts.json path")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("version", help="Print version and API baseline")

    config = subcommands.add_parser("config", help="Manage local configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("init", help="Create an owner-only config template")
    config_commands.add_parser("validate", help="Validate config and credential file permissions")

    auth = subcommands.add_parser("auth", help="Diagnose Google Ads authentication")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    doctor = auth_commands.add_parser("doctor", help="Run a read-only authentication probe")
    doctor.add_argument("--profile", required=True)

    accounts = subcommands.add_parser("accounts", help="Discover accessible customers")
    accounts_commands = accounts.add_subparsers(dest="accounts_command", required=True)
    discover = accounts_commands.add_parser("discover", help="List accessible customers")
    discover.add_argument("--profile", required=True)

    serve = subcommands.add_parser("serve", help="Run the MCP server")
    serve.add_argument("--stdio", action="store_true", required=True)
    return parser


def _fail(exc: PublicError) -> NoReturn:
    payload = exc.as_dict()
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)
    raise SystemExit(2)


def _service(config_path: str | None) -> GoogleAdsService:
    return GoogleAdsService(load_config(config_path), GoogleAdsReadAdapter())


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        if args.command == "version":
            print(f"google-ads-mcp {VERSION} (Google Ads API {API_VERSION}, read-only)")
            return
        if args.command == "config" and args.config_command == "init":
            path = initialize_config(args.config)
            print(f"Created owner-only config: {path}")
            return
        if args.command == "config" and args.config_command == "validate":
            config = load_config(args.config)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "path": str(config.path),
                        "profiles": [profile.name for profile in config.profiles],
                        "warnings": list(config.warnings),
                        "readOnly": True,
                    },
                    sort_keys=True,
                )
            )
            return
        if args.command == "auth" and args.auth_command == "doctor":
            config = load_config(args.config)
            profile = config.profile(args.profile)
            if profile.default_customer_id is None:
                raise PublicError(
                    "default_customer_missing",
                    "The selected profile has no defaultCustomerId for auth doctor.",
                )
            service = GoogleAdsService(config, GoogleAdsReadAdapter())
            response = asyncio.run(
                service.auth_check(args.profile, profile.default_customer_id)
            )
            print(json.dumps(response))
            return
        if args.command == "accounts" and args.accounts_command == "discover":
            print(json.dumps(asyncio.run(_service(args.config).customers_list(args.profile))))
            return
        if args.command == "serve":
            serve_stdio(args.config)
            return
        raise PublicError("invalid_command", "Unsupported command")
    except PublicError as exc:
        _fail(exc)

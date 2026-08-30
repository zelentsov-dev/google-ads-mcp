from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Sequence
from typing import NoReturn

from google_ads_mcp.adapter import GoogleAdsReadAdapter
from google_ads_mcp.config import initialize_config, load_config
from google_ads_mcp.constants import API_VERSION, VERSION
from google_ads_mcp.errors import PublicError
from google_ads_mcp.journal import OperationJournal, resolve_state_dir
from google_ads_mcp.operator import OperatorService
from google_ads_mcp.policies import (
    approve_policy,
    initialize_policies,
    load_policies,
    revoke_policy,
)
from google_ads_mcp.secrets import SystemSecretStore
from google_ads_mcp.server import serve_stdio
from google_ads_mcp.service import GoogleAdsService
from google_ads_mcp.write_adapter import GoogleAdsWriteAdapter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="google-ads-mcp")
    parser.add_argument("--config", help="Explicit accounts.json path")
    parser.add_argument("--policies", help="Explicit write-policies.json path")
    parser.add_argument("--state-dir", help="Explicit operation state directory")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("version", help="Print version and API baseline")
    onboard = subcommands.add_parser("onboard", help="Validate a profile and store its token")
    onboard.add_argument("--profile")

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

    secrets = subcommands.add_parser("secrets", help="Manage system secure-storage references")
    secret_commands = secrets.add_subparsers(dest="vault_action", required=True)
    set_token = secret_commands.add_parser(
        "set-developer-token", help="Read a developer token from hidden input"
    )
    set_token.add_argument("--profile", required=True)

    profiles = subcommands.add_parser("profiles", help="Inspect configured profiles")
    profile_commands = profiles.add_subparsers(dest="profiles_command", required=True)
    profile_commands.add_parser("list", help="List profiles without secrets")

    policies = subcommands.add_parser("policies", help="Manage local write policies")
    policy_commands = policies.add_subparsers(dest="policy_command", required=True)
    policy_commands.add_parser("init", help="Create an owner-only empty policy document")
    policy_commands.add_parser("validate", help="Validate policy schema and file permissions")
    approve = policy_commands.add_parser("approve", help="Approve an unchanged policy for 30 days")
    approve.add_argument("--policy-id", required=True)
    revoke = policy_commands.add_parser("revoke", help="Revoke one policy immediately")
    revoke.add_argument("--policy-id", required=True)
    policy_commands.add_parser("status", help="List policy approval and expiry state")

    operations = subcommands.add_parser("operations", help="Inspect durable operation state")
    operation_commands = operations.add_subparsers(dest="operation_command", required=True)
    operation_list = operation_commands.add_parser("list", help="List recent operations")
    operation_list.add_argument("--profile")
    operation_list.add_argument("--limit", type=int, default=50)
    operation_inspect = operation_commands.add_parser("inspect", help="Inspect one operation")
    operation_inspect.add_argument("--operation-id", required=True)
    operation_verify = operation_commands.add_parser("verify", help="Reconcile one operation")
    operation_verify.add_argument("--operation-id", required=True)

    client = subcommands.add_parser("client", help="Generate a local MCP client registration")
    client_commands = client.add_subparsers(dest="client_command", required=True)
    install = client_commands.add_parser("install", help="Print a secret-free client command")
    install.add_argument("--target", required=True, choices=("codex", "claude"))

    serve = subcommands.add_parser("serve", help="Run the MCP server")
    serve.add_argument("--stdio", action="store_true", required=True)
    serve.add_argument("--allow-writes", action="store_true")
    return parser


def _fail(exc: PublicError) -> NoReturn:
    payload = exc.as_dict()
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)
    raise SystemExit(2)


def _service(config_path: str | None) -> GoogleAdsService:
    return GoogleAdsService(load_config(config_path), GoogleAdsReadAdapter())


def _operator(
    config_path: str | None, policy_path: str | None, state_dir: str | None
) -> OperatorService:
    config = load_config(config_path)
    read_adapter = GoogleAdsReadAdapter()
    read_service = GoogleAdsService(config, read_adapter)
    resolved_state = resolve_state_dir(state_dir)
    return OperatorService(
        config,
        read_service,
        read_adapter,
        GoogleAdsWriteAdapter(read_adapter=read_adapter),
        allow_writes=False,
        policy_loader=lambda: load_policies(policy_path),
        state_dir=resolved_state,
        journal=OperationJournal(resolved_state),
    )


def _profile_items(config_path: str | None) -> list[dict[str, object]]:
    config = load_config(config_path)
    return [
        {
            "name": profile.name,
            "loginCustomerId": profile.login_customer_id,
            "defaultCustomerId": profile.default_customer_id,
            "authType": profile.auth.type,
            "allowWrites": profile.allow_writes,
        }
        for profile in config.profiles
    ]


def _set_developer_token(config_path: str | None, profile_name: str) -> None:
    config = load_config(config_path)
    profile = config.profile(profile_name)
    if not profile.auth.developer_token_keyring:
        raise PublicError(
            "keyring_not_configured",
            "The selected profile must set auth.developerTokenKeyring=true.",
        )
    token = getpass.getpass("Google Ads developer token: ")
    SystemSecretStore().set_developer_token(profile_name, token)
    print(json.dumps({"stored": True, "profile": profile_name, "secretValueReturned": False}))


def _client_install(target: str, config_path: str | None) -> None:
    resolved = str(load_config(config_path).path)
    command = ["google-ads-mcp", "--config", resolved, "serve", "--stdio"]
    if target == "codex":
        output = {
            "target": "codex",
            "command": ["codex", "mcp", "add", "google-ads", "--", *command],
        }
    else:
        output = {
            "target": "claude",
            "command": ["claude", "mcp", "add", "google-ads", "--", *command],
        }
    print(json.dumps(output))


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        if args.command == "version":
            print(f"google-ads-mcp {VERSION} (Google Ads API {API_VERSION}, safe operator)")
            return
        if args.command == "onboard":
            config = load_config(args.config)
            profile_name = args.profile or (
                config.profiles[0].name if len(config.profiles) == 1 else None
            )
            if profile_name is None:
                raise PublicError(
                    "profile_required",
                    "--profile is required when more than one profile is configured.",
                )
            _set_developer_token(args.config, profile_name)
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
                        "writeProfiles": [
                            profile.name for profile in config.profiles if profile.allow_writes
                        ],
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
            response = asyncio.run(service.auth_check(args.profile, profile.default_customer_id))
            print(json.dumps(response))
            return
        if args.command == "accounts" and args.accounts_command == "discover":
            print(json.dumps(asyncio.run(_service(args.config).customers_list(args.profile))))
            return
        if args.command == "secrets" and args.vault_action == "set-developer-token":
            _set_developer_token(args.config, args.profile)
            return
        if args.command == "profiles" and args.profiles_command == "list":
            print(json.dumps({"profiles": _profile_items(args.config)}, sort_keys=True))
            return
        if args.command == "policies" and args.policy_command == "init":
            print(f"Created owner-only policy file: {initialize_policies(args.policies)}")
            return
        if args.command == "policies" and args.policy_command == "validate":
            policies = load_policies(args.policies)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "path": str(policies.path),
                        "policyIds": [item.policy_id for item in policies.policies],
                    },
                    sort_keys=True,
                )
            )
            return
        if args.command == "policies" and args.policy_command == "approve":
            policy = approve_policy(load_policies(args.policies), args.policy_id)
            print(json.dumps({"policyId": policy.policy_id, "approval": policy.approval.as_dict()}))
            return
        if args.command == "policies" and args.policy_command == "revoke":
            policy = revoke_policy(load_policies(args.policies), args.policy_id)
            print(json.dumps({"policyId": policy.policy_id, "approval": policy.approval.as_dict()}))
            return
        if args.command == "policies" and args.policy_command == "status":
            policies = load_policies(args.policies)
            print(
                json.dumps(
                    {
                        "path": str(policies.path),
                        "policies": [
                            {"policyId": item.policy_id, "approval": item.approval.as_dict()}
                            for item in policies.policies
                        ],
                    }
                )
            )
            return
        if args.command == "operations":
            operator = _operator(args.config, args.policies, args.state_dir)
            if args.operation_command == "list":
                response = asyncio.run(operator.list(args.profile, args.limit))
            elif args.operation_command == "inspect":
                response = asyncio.run(operator.inspect(args.operation_id))
            else:
                response = asyncio.run(operator.verify(args.operation_id))
            print(json.dumps(response))
            return
        if args.command == "client" and args.client_command == "install":
            _client_install(args.target, args.config)
            return
        if args.command == "serve":
            serve_stdio(
                args.config,
                allow_writes=args.allow_writes,
                policy_path=args.policies,
                state_dir_path=args.state_dir,
            )
            return
        raise PublicError("invalid_command", "Unsupported command")
    except PublicError as exc:
        _fail(exc)

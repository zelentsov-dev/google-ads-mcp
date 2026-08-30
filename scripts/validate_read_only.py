from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "google_ads_mcp"
ALLOWED_SERVICES = {
    "CustomerService",
    "GoogleAdsFieldService",
    "GoogleAdsService",
    "KeywordPlanIdeaService",
}
ALLOWED_OPERATION_TYPES = {
    "ad_group_ad_operation",
    "ad_group_asset_operation",
    "ad_group_criterion_operation",
    "ad_group_operation",
    "asset_group_asset_operation",
    "asset_group_operation",
    "asset_operation",
    "campaign_asset_operation",
    "campaign_budget_operation",
    "campaign_criterion_operation",
    "campaign_operation",
}
REQUIRED_FORBIDDEN_WRITE_SERVICES = {
    "AccountBudgetProposalService",
    "AccountLinkService",
    "BatchJobService",
    "BillingSetupService",
    "ConversionAdjustmentUploadService",
    "ConversionUploadService",
    "CustomerClientLinkService",
    "CustomerManagerLinkService",
    "CustomerUserAccessInvitationService",
    "CustomerUserAccessService",
    "DataLinkService",
    "OfflineUserDataJobService",
    "UserListService",
}
EXPECTED_TOOLS = set(json.loads((ROOT / "api-contract" / "tools-v0.2.json").read_text())["tools"])
FORBIDDEN_PARAMETER_PARTS = {
    "protobuf",
    "rawmutate",
    "rawpayload",
    "servicename",
    "mutateoperations",
}


def _registered_tools(tree: ast.AST) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
            for decorator in node.decorator_list
        )
    }


def validate() -> None:
    registered_tools: dict[str, ast.AsyncFunctionDef | ast.FunctionDef] = {}
    service_calls: set[str] = set()
    violations: list[str] = []
    for path in SOURCE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        registered_tools.update(_registered_tools(tree))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "get_service" or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                violations.append(f"{path.name}:{node.lineno}: dynamic service name")
            else:
                service_calls.add(first.value)
                if first.value not in ALLOWED_SERVICES:
                    violations.append(f"{path.name}:{node.lineno}: forbidden service {first.value}")
    if set(registered_tools) != EXPECTED_TOOLS:
        violations.append(
            f"tool contract mismatch: expected={sorted(EXPECTED_TOOLS)} "
            f"actual={sorted(registered_tools)}"
        )
    apply_tools = {name for name in registered_tools if name.endswith("_apply")}
    if apply_tools != {"operations_apply"}:
        violations.append(f"unexpected apply interfaces: {sorted(apply_tools)}")
    for name, function in registered_tools.items():
        parameters = {
            argument.arg.lower()
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
        }
        forbidden = parameters & FORBIDDEN_PARAMETER_PARTS
        if forbidden:
            violations.append(f"{name}: forbidden raw parameter {sorted(forbidden)[0]}")
    write_adapter = ast.parse(
        (SOURCE / "write_adapter.py").read_text(encoding="utf-8"),
        filename="write_adapter.py",
    )
    operation_types: set[str] = set()
    forbidden_write_services: set[str] = set()
    for node in ast.walk(write_adapter):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_OPERATIONS"
            and isinstance(node.value, ast.Dict)
        ):
            operation_types = {
                str(key.value)
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "FORBIDDEN_WRITE_SERVICES"
            and isinstance(node.value, ast.Call)
            and node.value.args
            and isinstance(node.value.args[0], ast.Set)
        ):
            forbidden_write_services = {
                str(item.value)
                for item in node.value.args[0].elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
    if operation_types != ALLOWED_OPERATION_TYPES:
        violations.append(f"write operation allowlist mismatch: {sorted(operation_types)}")
    if forbidden_write_services != REQUIRED_FORBIDDEN_WRITE_SERVICES:
        violations.append(
            "forbidden write service list mismatch: "
            f"{sorted(forbidden_write_services)}"
        )
    if violations:
        raise SystemExit("\n".join(violations))
    print(
        f"safe operator boundary valid: {len(registered_tools)} tools, "
        f"{len(service_calls)} services, {len(operation_types)} operation types, "
        f"{len(forbidden_write_services)} forbidden services"
    )


if __name__ == "__main__":
    validate()

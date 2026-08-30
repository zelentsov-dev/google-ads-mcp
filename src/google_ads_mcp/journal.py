from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from google_ads_mcp.constants import DEFAULT_STATE_ENV, DEFAULT_STATE_PATH
from google_ads_mcp.errors import SecurityError, ValidationError
from google_ads_mcp.receipts import canonical_json, value_fingerprint
from google_ads_mcp.write_models import OperationState, WritePlan

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    receipt_hash TEXT NOT NULL UNIQUE,
    profile TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    related_key TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    applied_at TEXT,
    updated_at TEXT NOT NULL,
    receipt_used INTEGER NOT NULL DEFAULT 0,
    current_fingerprint TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    validation_digest TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    monetary_delta_json TEXT NOT NULL,
    risk_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    verification_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS operations_scope_state
ON operations(profile, customer_id, related_key, state);
CREATE INDEX IF NOT EXISTS operations_customer_state
ON operations(profile, customer_id, state);
CREATE INDEX IF NOT EXISTS operations_created
ON operations(created_at DESC);
"""


def resolve_state_dir(cli_path: str | None = None) -> Path:
    raw = cli_path or os.environ.get(DEFAULT_STATE_ENV) or DEFAULT_STATE_PATH
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path).absolute()


def _time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _decode(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise SecurityError("The operation journal contains invalid structured data")
    return cast(dict[str, Any], decoded)


def _open_journal(path: Path, *, create: bool) -> tuple[int, os.stat_result]:
    if os.name == "nt":
        raise SecurityError(
            "Windows ACL verification is unavailable; write operations are disabled",
            code="writes_not_secure",
        )
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise SecurityError("Secure journal opening is unavailable")
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | nofollow
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise SecurityError("The operation journal could not be opened securely") from exc
    file_stat = os.fstat(descriptor)
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != os.getuid()
        or stat.S_IMODE(file_stat.st_mode) & 0o077
    ):
        os.close(descriptor)
        raise SecurityError("The operation journal must be an owner-only regular file")
    try:
        path_stat = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        os.close(descriptor)
        raise SecurityError("The operation journal identity could not be verified") from exc
    if (path_stat.st_dev, path_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino):
        os.close(descriptor)
        raise SecurityError("The operation journal changed while it was opened")
    return descriptor, file_stat


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: str
    receipt_hash: str
    profile: str
    customer_id: str
    policy_id: str
    kind: str
    related_key: str
    state: OperationState
    created_at: str
    expires_at: str
    applied_at: str | None
    updated_at: str
    receipt_used: bool
    current_fingerprint: str
    plan_digest: str
    validation_digest: str
    policy_fingerprint: str
    before: dict[str, Any]
    after: dict[str, Any]
    monetary_delta: dict[str, Any]
    risk: dict[str, Any]
    validation: dict[str, Any]
    verification: dict[str, Any]
    plan: WritePlan
    result: dict[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "operationId": self.operation_id,
            "policyId": self.policy_id,
            "kind": self.kind,
            "state": self.state,
            "receiptExpiresAt": self.expires_at,
            "before": self.before,
            "after": self.after,
            "monetaryDelta": self.monetary_delta,
            "risk": self.risk,
            "validation": self.validation,
            "verification": self.verification,
            "receiptUsed": self.receipt_used,
            "createdAt": self.created_at,
            "appliedAt": self.applied_at,
            "result": self.result,
        }


class OperationJournal:
    path: Path
    _identity: tuple[int, int]

    def __init__(self, state_dir: Path) -> None:
        if os.name == "nt":
            raise SecurityError(
                "Windows ACL verification is unavailable; write operations are disabled",
                code="writes_not_secure",
            )
        if not state_dir.is_absolute():
            raise SecurityError("The operation state directory must be absolute")
        if state_dir.is_symlink():
            raise SecurityError("The operation state directory must not be a symbolic link")
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_stat = state_dir.stat()
        if directory_stat.st_uid != os.getuid() or stat.S_IMODE(directory_stat.st_mode) & 0o077:
            raise SecurityError("The operation state directory must be owner-only")
        self.path = state_dir / "operations.sqlite3"
        try:
            descriptor, file_stat = _open_journal(self.path, create=True)
        except SecurityError:
            descriptor, file_stat = _open_journal(self.path, create=False)
        self._identity = (file_stat.st_dev, file_stat.st_ino)
        os.close(descriptor)
        with self._connection() as connection:
            connection.executescript(_SCHEMA)
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(operations)").fetchall()
            }
            for name in ("plan_digest", "validation_digest", "policy_fingerprint"):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE operations ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                    )

    def _verify_identity(self, descriptor_stat: os.stat_result) -> None:
        try:
            path_stat = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise SecurityError("The operation journal identity could not be verified") from exc
        current = (descriptor_stat.st_dev, descriptor_stat.st_ino)
        if current != self._identity or (path_stat.st_dev, path_stat.st_ino) != self._identity:
            raise SecurityError("The operation journal changed after initialization")

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        descriptor, descriptor_stat = _open_journal(self.path, create=False)
        self._verify_identity(descriptor_stat)
        try:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            self._verify_identity(descriptor_stat)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA journal_mode = DELETE")
                connection.execute("PRAGMA synchronous = FULL")
                yield connection
                self._verify_identity(descriptor_stat)
            finally:
                connection.close()
        finally:
            os.close(descriptor)

    @staticmethod
    def _record(row: sqlite3.Row) -> OperationRecord:
        plan = WritePlan.from_dict(_decode(str(row["plan_json"])))
        before = _decode(str(row["before_json"]))
        after = _decode(str(row["after_json"]))
        monetary_delta = _decode(str(row["monetary_delta_json"]))
        risk = _decode(str(row["risk_json"]))
        validation = _decode(str(row["validation_json"]))
        plan_digest = str(row["plan_digest"])
        validation_digest = str(row["validation_digest"])
        if (
            str(row["profile"]) != plan.profile
            or str(row["customer_id"]) != plan.customer_id
            or str(row["policy_id"]) != plan.policy_id
            or str(row["kind"]) != plan.kind
            or str(row["related_key"]) != plan.related_key
            or before != plan.before
            or after != plan.after
            or monetary_delta != plan.monetary_delta
            or risk != plan.risk
            or str(row["current_fingerprint"]) != value_fingerprint(plan.before)
            or not plan_digest
            or plan_digest != value_fingerprint(plan.as_dict())
            or not validation_digest
            or validation_digest != value_fingerprint(validation)
            or not str(row["policy_fingerprint"])
        ):
            raise SecurityError(
                "The operation journal integrity check failed",
                code="operation_journal_tampered",
            )
        return OperationRecord(
            operation_id=str(row["operation_id"]),
            receipt_hash=str(row["receipt_hash"]),
            profile=str(row["profile"]),
            customer_id=str(row["customer_id"]),
            policy_id=str(row["policy_id"]),
            kind=str(row["kind"]),
            related_key=str(row["related_key"]),
            state=cast(OperationState, row["state"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            applied_at=str(row["applied_at"]) if row["applied_at"] is not None else None,
            updated_at=str(row["updated_at"]),
            receipt_used=bool(row["receipt_used"]),
            current_fingerprint=str(row["current_fingerprint"]),
            plan_digest=plan_digest,
            validation_digest=validation_digest,
            policy_fingerprint=str(row["policy_fingerprint"]),
            before=before,
            after=after,
            monetary_delta=monetary_delta,
            risk=risk,
            validation=validation,
            verification=_decode(str(row["verification_json"])),
            plan=plan,
            result=_decode(str(row["result_json"])),
        )

    def create_preview(
        self,
        *,
        operation_id: str,
        receipt_hash_value: str,
        plan: WritePlan,
        current_fingerprint: str,
        plan_digest: str,
        validation_digest: str,
        policy_fingerprint: str,
        validation: dict[str, Any],
        created_at: datetime,
        expires_at: datetime,
    ) -> OperationRecord:
        now = _time(created_at)
        values = (
            operation_id,
            receipt_hash_value,
            plan.profile,
            plan.customer_id,
            plan.policy_id,
            plan.kind,
            plan.related_key,
            "previewed",
            now,
            _time(expires_at),
            now,
            current_fingerprint,
            plan_digest,
            validation_digest,
            policy_fingerprint,
            canonical_json(plan.before),
            canonical_json(plan.after),
            canonical_json(plan.monetary_delta),
            canonical_json(plan.risk),
            canonical_json(validation),
            "{}",
            canonical_json(plan.as_dict()),
            "{}",
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                unresolved = connection.execute(
                    """
                    SELECT operation_id FROM operations
                    WHERE profile = ? AND customer_id = ?
                      AND state IN ('applying', 'partial', 'committed_unverified')
                    LIMIT 1
                    """,
                    (plan.profile, plan.customer_id),
                ).fetchone()
                if unresolved is not None:
                    raise ValidationError(
                        "An unresolved customer write requires operations_verify "
                        "before another preview",
                        code="operation_unresolved",
                    )
                connection.execute(
                    """
                    INSERT INTO operations (
                        operation_id, receipt_hash, profile, customer_id, policy_id, kind,
                        related_key, state, created_at, expires_at, updated_at,
                        current_fingerprint, plan_digest, validation_digest, policy_fingerprint,
                        before_json, after_json, monetary_delta_json,
                        risk_json, validation_json, verification_json, plan_json, result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return self.inspect(operation_id)

    def inspect(self, operation_id: str) -> OperationRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
        if row is None:
            raise ValidationError(
                "The requested operation does not exist", code="operation_not_found"
            )
        return self._record(row)

    def by_receipt_hash(self, value: str) -> OperationRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE receipt_hash = ?", (value,)
            ).fetchone()
        if row is None:
            raise ValidationError("The operation receipt is unknown", code="invalid_receipt")
        return self._record(row)

    def begin_apply(
        self, operation_id: str, receipt_hash_value: str, now: datetime
    ) -> OperationRecord:
        now_text = _time(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
                ).fetchone()
                if row is None or str(row["receipt_hash"]) != receipt_hash_value:
                    raise ValidationError(
                        "The operation receipt is unknown", code="invalid_receipt"
                    )
                if bool(row["receipt_used"]) or str(row["state"]) != "previewed":
                    raise ValidationError(
                        "The operation receipt has already been used", code="receipt_replayed"
                    )
                if now_text >= str(row["expires_at"]):
                    connection.execute(
                        """
                        UPDATE operations SET state = 'expired', updated_at = ?
                        WHERE operation_id = ?
                        """,
                        (now_text, operation_id),
                    )
                    connection.execute("COMMIT")
                    raise ValidationError(
                        "The operation receipt has expired", code="receipt_expired"
                    )
                unresolved = connection.execute(
                    """
                    SELECT operation_id FROM operations
                    WHERE profile = ? AND customer_id = ?
                      AND operation_id != ?
                      AND state IN ('applying', 'partial', 'committed_unverified')
                    LIMIT 1
                    """,
                    (
                        str(row["profile"]),
                        str(row["customer_id"]),
                        operation_id,
                    ),
                ).fetchone()
                if unresolved is not None:
                    raise ValidationError(
                        "An unresolved customer write requires operations_verify before apply",
                        code="operation_unresolved",
                    )
                connection.execute(
                    """
                    UPDATE operations
                    SET state = 'applying', receipt_used = 1, applied_at = ?, updated_at = ?
                    WHERE operation_id = ?
                    """,
                    (now_text, now_text, operation_id),
                )
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return self.inspect(operation_id)

    def invalidate(self, operation_id: str, reason: str, now: datetime) -> OperationRecord:
        verification = canonical_json({"state": "invalidated", "reason": reason})
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE operations
                SET state = 'expired', receipt_used = 1, verification_json = ?, updated_at = ?
                WHERE operation_id = ? AND state = 'previewed'
                """,
                (verification, _time(now), operation_id),
            )
            connection.execute("COMMIT")
        return self.inspect(operation_id)

    def transition(
        self,
        operation_id: str,
        state: OperationState,
        *,
        verification: dict[str, Any],
        result: dict[str, Any],
        now: datetime,
    ) -> OperationRecord:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE operations
                SET state = ?, verification_json = ?, result_json = ?, updated_at = ?
                WHERE operation_id = ? AND state IN ('applying', 'partial', 'committed_unverified')
                """,
                (
                    state,
                    canonical_json(verification),
                    canonical_json(result),
                    _time(now),
                    operation_id,
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ValidationError(
                    "The operation cannot transition from its current state",
                    code="invalid_operation_state",
                )
            connection.execute("COMMIT")
        return self.inspect(operation_id)

    def list(self, *, limit: int = 50, profile: str | None = None) -> tuple[OperationRecord, ...]:
        if limit < 1 or limit > 200:
            raise ValidationError("limit must be between 1 and 200")
        query = "SELECT * FROM operations"
        parameters: tuple[Any, ...]
        if profile is None:
            query += " ORDER BY created_at DESC LIMIT ?"
            parameters = (limit,)
        else:
            query += " WHERE profile = ? ORDER BY created_at DESC LIMIT ?"
            parameters = (profile, limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self._record(row) for row in rows)

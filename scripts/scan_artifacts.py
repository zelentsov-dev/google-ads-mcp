from __future__ import annotations

import argparse
import math
import re
import tarfile
import zipfile
from collections import Counter
from pathlib import Path

DENIED_PATH = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\..*)?|accounts\.json|google-ads\.ya?ml|"
    r"application_default_credentials\.json|service-account.*\.json|credentials.*\.json)$"
)
DENIED_CONTENT = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        rb"(?i)[\"'](?:api[_-]?key|access[_-]?token|authorization|refresh[_-]?token|"
        rb"client[_-]?secret|developer[_-]?token|private[_-]?key)[\"']\s*[:=]\s*"
        rb"[\"'][A-Za-z0-9_./+=-]{12,}[\"']"
    ),
    re.compile(
        rb"(?im)^\s*(?:api[_-]?key|access[_-]?token|authorization|refresh[_-]?token|"
        rb"client[_-]?secret|developer[_-]?token|private[_-]?key)\s*:\s*"
        rb"[A-Za-z0-9_./+=-]{20,}\s*$"
    ),
    re.compile(rb"(?i)\bBearer\s+[A-Za-z0-9_./+=-]{20,}"),
    re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(rb"(?<![0-9A-Za-z_-])GOCSPX-[0-9A-Za-z_-]{20,}(?![0-9A-Za-z_-])"),
    re.compile(rb"\b1//[0-9A-Za-z._-]{20,}\b"),
    re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)
GENERIC_TOKEN = re.compile(rb"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/=-]{40,}(?![A-Za-z0-9_+/=-])")
INTEGRITY_LABELS = (b"checksum", b"digest", b"hash", b"sha256", b"sha384", b"sha512")


def _entropy(value: bytes) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _contains_generic_secret(content: bytes) -> bool:
    for match in GENERIC_TOKEN.finditer(content):
        candidate = match.group().rstrip(b"=")
        lowered_candidate = candidate.lower()
        context = content[max(0, match.start() - 40) : match.start()].lower()
        if any(
            label in context or lowered_candidate.startswith(label + b"=")
            for label in INTEGRITY_LABELS
        ):
            continue
        categories = sum(
            (
                any(65 <= value <= 90 for value in candidate),
                any(97 <= value <= 122 for value in candidate),
                any(48 <= value <= 57 for value in candidate),
                any(value in b"_+/=-" for value in candidate),
            )
        )
        entropy = _entropy(candidate)
        if (categories >= 4 and entropy >= 4.5) or (categories >= 3 and entropy >= 5.0):
            return True
    return False


def _check(name: str, content: bytes) -> None:
    normalized = name.replace("\\", "/")
    if DENIED_PATH.search(normalized):
        raise SystemExit(f"Denied artifact path: {normalized}")
    for pattern in DENIED_CONTENT:
        if pattern.search(content):
            raise SystemExit(f"Credential-like content in artifact member: {normalized}")
    if _contains_generic_secret(content):
        raise SystemExit(f"High-entropy credential-like content in artifact member: {normalized}")


def scan(path: Path) -> None:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not member.is_dir():
                    _check(member.filename, archive.read(member))
        return
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if member.isfile():
                    extracted = archive.extractfile(member)
                    assert extracted is not None
                    _check(member.name, extracted.read())
        return
    _check(path.name, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    artifacts = tuple(
        candidate
        for path in args.artifacts
        for candidate in (
            tuple(item for item in path.rglob("*") if item.is_file())
            if path.is_dir()
            else (path,)
        )
    )
    if not artifacts:
        raise SystemExit("No artifact files were provided")
    for artifact in artifacts:
        scan(artifact)
    print(f"artifact scan valid: {len(artifacts)} files")


if __name__ == "__main__":
    main()

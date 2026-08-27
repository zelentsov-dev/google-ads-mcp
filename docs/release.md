# Release procedure

Releases are owner-authorized external mutations. Do not create a commit, tag, GitHub release, PyPI upload, marketplace entry, or attestation without explicit authorization.

For `v0.1.0`, finish test-account, production-read-only, and plugin acceptance first. Freeze one exact commit, review its complete diff, and require all CI/security workflows green. Build wheel, sdist, plugin zip, SPDX SBOM, SHA-256 checksums, and GitHub provenance from that exact commit. Publish the Python distribution as `google-ads-operator-mcp` through PyPI Trusted Publishing.

OCI images, hosted transports, Cloud Run, writes, optimizer, scheduler, deletes, and conversion uploads remain out of scope.

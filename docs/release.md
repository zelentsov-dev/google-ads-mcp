# Release procedure

Releases are owner-authorized external mutations. Do not create a commit, tag, GitHub release, PyPI upload, marketplace entry, or attestation without explicit authorization.

For `v0.2.0`, finish the documented production-only risk-accepted mutation matrix, production read-only regression, paused Performance Max/App acceptance, the bounded seven-day Search gate, and clean Codex/Claude plugin acceptance first. Freeze one exact commit, review its complete diff, and require successful CI, Security, and CodeQL push workflows on that exact current `main` SHA. The tag workflow verifies both conditions through GitHub Actions before any release build. Build wheel, sdist, plugin zip, SPDX SBOM, SHA-256 checksums, and GitHub provenance from that exact commit. Create the GitHub release before publishing `google-ads-operator-mcp` through PyPI Trusted Publishing.

OCI images, hosted transports, Cloud Run, optimizer cycles, internal schedulers, irreversible deletes, external attribution, conversion uploads, Performance Max/App activation, and raw image ingestion remain out of scope. The temporary Codex heartbeat is an external acceptance monitor, not a server capability.

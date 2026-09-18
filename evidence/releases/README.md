# Release evidence

**Empty: no release has been published.** CommitGuard has no tags and no GitHub
releases. It is published to PyPI as `commitguardian` (see
[ADR-009](../../docs/adr/009-published-to-pypi-as-commitguardian.md)).

When the first release happens, this directory holds, per release: the tag, the
built sdist and wheel names, `SHA256SUMS`, the SBOM, the cross-platform install
results, and a link to the draft-release workflow run that produced them.

The process: [docs/maintainers/release-process.md](../../docs/maintainers/release-process.md).
The workflow: `.github/workflows/release.yml` (written, **never yet executed**).

"""Policy sources: where the policy that evaluates a change comes from.

The source of a policy is a security decision. A pull request that edits
``.commitguard.yaml`` to ``action: allow`` must not be evaluated with the policy
it introduces, otherwise it could approve itself.

=================  =======================================  ======================
Kind               Configuration read from                  Used by
=================  =======================================  ======================
``working_tree``   global config + work tree + ``--config``  local scan/check/hooks
``revision``       the tree of a *trusted* commit (never     CI (PR base, push
                   the work tree); no global config          ``before``, default
                                                             branch)
``builtin``        built-in secure defaults only             CI when no trusted
                                                             commit exists
=================  =======================================  ======================

On top of any source, a central service can apply a :class:`MandatoryPolicy`
(e.g. the operator of the GitHub App, later an organisation). It is a floor,
not a layer: repository configuration cannot weaken it (see
:mod:`commitguard.policies.mandatory`). The intended future hierarchy is::

    global mandatory policy -> organisation mandatory policy
        -> trusted repository configuration -> (local developer configuration)

where every mandatory level can only tighten what the levels below produce.
"""

from enum import StrEnum
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, field_validator

from commitguard.config.defaults import CONFIG_FILENAMES, MAX_CONFIG_BYTES
from commitguard.config.loader import (
    ConfigLayer,
    ConfigSource,
    LoadedConfig,
    load_config,
    load_effective_config,
    parse_config,
)
from commitguard.config.schema import CommitGuardConfig
from commitguard.exceptions.base import UnsafeInputError
from commitguard.exceptions.configuration import ConfigurationError
from commitguard.git.repository import Repository
from commitguard.policies.mandatory import validate_mandatory_config
from commitguard.security.hashing import sha256_hex
from commitguard.security.validation import validate_git_sha, validate_repository_path


class PolicySourceKind(StrEnum):
    WORKING_TREE = "working_tree"
    REVISION = "revision"
    BUILTIN = "builtin"


class PolicySource(BaseModel):
    """A description of where policy is loaded from, shown in every report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PolicySourceKind
    revision: str | None = None
    description: str
    config_path: str | None = None  # repository-relative; None = .commitguard.yaml/.yml

    @field_validator("revision")
    @classmethod
    def _valid_revision(cls, value: str | None) -> str | None:
        return None if value is None else validate_git_sha(value)

    @field_validator("config_path")
    @classmethod
    def _valid_path(cls, value: str | None) -> str | None:
        return None if value is None else validate_repository_path(value)

    def __str__(self) -> str:
        if self.kind is PolicySourceKind.REVISION and self.revision:
            return f"{self.description} ({self.revision[:12]})"
        return self.description


class MandatoryPolicy(BaseModel):
    """A policy floor applied after all configuration layers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config: CommitGuardConfig
    description: str
    fingerprint: str


def load_mandatory_policy(path: Path, *, description: str | None = None) -> MandatoryPolicy:
    """Load and validate a mandatory policy file (same schema as ``.commitguard.yaml``)."""
    config = load_config(path)
    try:
        validate_mandatory_config(config)
    except ValueError as exc:
        raise ConfigurationError(str(exc), path=path) from None
    canonical = config.model_dump_json(exclude_unset=True)
    return MandatoryPolicy(
        config=config,
        description=description or path.name,
        fingerprint=sha256_hex(canonical.encode("utf-8")),
    )


def load_config_at_revision(
    repository: Repository, revision: str, *, config_path: str | None = None
) -> LoadedConfig:
    """Built-in defaults + the repository configuration stored in ``revision``.

    Nothing is read from the work tree or the user's global configuration. With
    ``config_path`` the file must exist at that revision; otherwise the usual
    names are looked up and a missing file means built-in defaults.
    """
    layers: list[tuple[ConfigSource, CommitGuardConfig]] = [
        (ConfigSource(layer=ConfigLayer.BUILTIN), CommitGuardConfig(version=1))
    ]
    candidates = [config_path] if config_path else list(CONFIG_FILENAMES)
    found: list[tuple[str, bytes]] = []
    for name in candidates:
        try:
            data = repository.read_blob_at(revision, name, max_bytes=MAX_CONFIG_BYTES)
        except UnsafeInputError as exc:
            raise ConfigurationError(f"{exc} (trusted revision {revision[:12]})") from exc
        if data is not None:
            found.append((name, data))
    if config_path and not found:
        raise ConfigurationError(
            f"configuration file {config_path} does not exist at trusted revision {revision[:12]}"
        )
    if len(found) > 1:
        raise ConfigurationError(
            f"multiple configuration files at {revision[:12]}: "
            + ", ".join(name for name, _ in found)
        )
    if found:
        name, data = found[0]
        display = Path(PurePosixPath(name))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigurationError(
                f"not valid UTF-8 text (trusted revision {revision[:12]})", path=display
            ) from exc
        config = parse_config(text, path=display)
        source = ConfigSource(layer=ConfigLayer.REPOSITORY, path=display, revision=revision)
        layers.append((source, config))
    return LoadedConfig(layers=tuple(layers))


def load_policy_source(repository: Repository, source: PolicySource) -> LoadedConfig:
    if source.kind is PolicySourceKind.REVISION:
        if source.revision is None:
            raise ConfigurationError("revision policy source without a revision")
        return load_config_at_revision(repository, source.revision, config_path=source.config_path)
    if source.kind is PolicySourceKind.BUILTIN:
        if source.config_path:
            raise ConfigurationError(
                f"configuration file {source.config_path} requested but no trusted revision exists"
            )
        return LoadedConfig(
            layers=((ConfigSource(layer=ConfigLayer.BUILTIN), CommitGuardConfig(version=1)),)
        )
    explicit = repository.root / source.config_path if source.config_path else None
    return load_effective_config(repository.root, explicit_path=explicit)


def config_differs(repository: Repository, trusted: str, head: str) -> list[str]:
    """Configuration file names whose content differs between two commits."""
    changed = []
    for name in CONFIG_FILENAMES:
        try:
            before = repository.read_blob_at(trusted, name, max_bytes=MAX_CONFIG_BYTES)
            after = repository.read_blob_at(head, name, max_bytes=MAX_CONFIG_BYTES)
        except UnsafeInputError:
            changed.append(name)  # e.g. replaced by a symlink or oversized: report it
            continue
        if before != after:
            changed.append(name)
    return changed

"""Locate, parse, validate and layer configuration files.

Precedence (later layers override earlier ones, field by field)::

    1. built-in defaults           commitguard.policies.defaults
    2. global configuration        $XDG_CONFIG_HOME/commitguard/config.yaml
                                   (default ~/.config/commitguard/config.yaml)
    3. repository configuration    <repo root>/.commitguard.yaml (or .yml)
    4. explicit configuration      --config PATH

A layer only overrides the policy fields it sets; omitted policies and fields
keep the value from the layer below, ultimately the secure built-in default.

Security properties:

* strict safe YAML (no object construction, no duplicate keys, no aliases);
* files are size-limited and must be regular files;
* repository configuration is read only from the repository root - never from
  parent directories, which may be controlled by someone else.
"""

import os
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from commitguard.config.defaults import (
    CONFIG_FILENAMES,
    GLOBAL_CONFIG_DIRNAME,
    GLOBAL_CONFIG_FILENAME,
    MAX_CONFIG_BYTES,
)
from commitguard.config.schema import CommitGuardConfig
from commitguard.exceptions.base import UnsafeInputError
from commitguard.exceptions.configuration import ConfigurationError
from commitguard.security.safe_yaml import load_yaml
from commitguard.utils.filesystem import read_text_limited


class ConfigLayer(StrEnum):
    BUILTIN = "builtin"
    GLOBAL = "global"
    REPOSITORY = "repository"
    EXPLICIT = "explicit"


class ConfigSource(BaseModel):
    """Where one configuration layer came from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: ConfigLayer
    path: Path | None = None

    def __str__(self) -> str:
        return f"{self.layer.value}: {self.path}" if self.path else self.layer.value


class LoadedConfig(BaseModel):
    """All configuration layers in precedence order (lowest first)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layers: tuple[tuple[ConfigSource, CommitGuardConfig], ...]

    @property
    def configs(self) -> tuple[CommitGuardConfig, ...]:
        return tuple(config for _, config in self.layers)

    @property
    def sources(self) -> tuple[ConfigSource, ...]:
        return tuple(source for source, _ in self.layers)


def find_config(repository_root: Path) -> Path | None:
    """Return the configuration file at ``repository_root``, if any.

    Having both ``.commitguard.yaml`` and ``.commitguard.yml`` is ambiguous and
    rejected.
    """
    candidates = [repository_root / name for name in CONFIG_FILENAMES]
    present = [path for path in candidates if path.exists() or path.is_symlink()]
    if len(present) > 1:
        raise ConfigurationError(
            "multiple configuration files found: " + ", ".join(p.name for p in present)
        )
    return present[0] if present else None


def global_config_path() -> Path:
    """Path of the per-user global configuration file (may not exist)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".config"
    return root / GLOBAL_CONFIG_DIRNAME / GLOBAL_CONFIG_FILENAME


def parse_config(text: str, *, path: Path | None = None) -> CommitGuardConfig:
    """Parse and validate configuration from YAML text."""
    try:
        document = load_yaml(text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML: {exc}", path=path) from exc

    if not isinstance(document, dict):
        raise ConfigurationError("configuration must be a YAML mapping", path=path)

    try:
        return CommitGuardConfig.model_validate(document)
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_error(exc), path=path) from exc


def load_config(path: Path) -> CommitGuardConfig:
    """Read, parse and validate a configuration file."""
    try:
        text = read_text_limited(path, max_bytes=MAX_CONFIG_BYTES)
    except FileNotFoundError as exc:
        raise ConfigurationError("configuration file not found", path=path) from exc
    except (OSError, UnsafeInputError) as exc:
        raise ConfigurationError(str(exc), path=path) from exc
    return parse_config(text, path=path)


def load_repository_config(repository_root: Path) -> tuple[CommitGuardConfig, Path | None]:
    """Load only the repository layer, or an empty config if it has none."""
    path = find_config(repository_root)
    if path is None:
        return CommitGuardConfig(version=1), None
    return load_config(path), path


def load_effective_config(
    repository_root: Path | None,
    *,
    explicit_path: Path | None = None,
    include_global: bool = True,
) -> LoadedConfig:
    """Load every applicable configuration layer in precedence order."""
    layers: list[tuple[ConfigSource, CommitGuardConfig]] = [
        (ConfigSource(layer=ConfigLayer.BUILTIN), CommitGuardConfig(version=1))
    ]
    if include_global:
        path = global_config_path()
        if path.exists() or path.is_symlink():
            layers.append((ConfigSource(layer=ConfigLayer.GLOBAL, path=path), load_config(path)))
    if repository_root is not None:
        repo_path = find_config(repository_root)
        if repo_path is not None:
            layers.append(
                (ConfigSource(layer=ConfigLayer.REPOSITORY, path=repo_path), load_config(repo_path))
            )
    if explicit_path is not None:
        layers.append(
            (
                ConfigSource(layer=ConfigLayer.EXPLICIT, path=explicit_path),
                load_config(explicit_path),
            )
        )
    return LoadedConfig(layers=tuple(layers))


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["invalid configuration:"]
    for error in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)

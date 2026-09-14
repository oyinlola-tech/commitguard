"""Locate, parse and validate configuration files.

Security properties:

* only ``yaml.SafeLoader`` is used (no Python object construction);
* duplicate mapping keys are rejected (otherwise a later ``action: allow``
  could silently override an earlier ``action: block``);
* files are size-limited and must be regular files;
* configuration is looked up only at the repository root - never in parent
  directories, which may be controlled by someone else.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from commitguard.config.defaults import CONFIG_FILENAMES, MAX_CONFIG_BYTES
from commitguard.config.schema import CommitGuardConfig
from commitguard.exceptions.base import UnsafeInputError
from commitguard.exceptions.configuration import ConfigurationError
from commitguard.utils.filesystem import read_text_limited


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys and anchors/aliases.

    Configuration has no use for aliases, and forbidding them removes the
    "billion laughs" style of resource exhaustion entirely.
    """

    def compose_node(self, parent: yaml.Node | None, index: int) -> yaml.Node | None:
        if self.check_event(yaml.events.AliasEvent):
            event = self.peek_event()  # type: ignore[no-untyped-call]
            raise yaml.composer.ComposerError(
                None, None, "YAML aliases are not allowed in configuration", event.start_mark
            )
        return super().compose_node(parent, index)


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


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


def parse_config(text: str, *, path: Path | None = None) -> CommitGuardConfig:
    """Parse and validate configuration from YAML text."""
    try:
        document = yaml.load(text, Loader=_UniqueKeySafeLoader)  # noqa: S506 - SafeLoader subclass
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
    """Load the repository's configuration, or built-in defaults if it has none.

    Returns the configuration and the path it came from (``None`` = defaults).
    """
    path = find_config(repository_root)
    if path is None:
        return CommitGuardConfig(version=1), None
    return load_config(path), path


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["invalid configuration:"]
    for error in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)

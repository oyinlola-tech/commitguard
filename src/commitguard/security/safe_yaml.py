"""Hardened YAML loading for configuration and rule files.

* ``yaml.SafeLoader`` only: no Python object construction;
* duplicate mapping keys are rejected (a later ``action: allow`` must not
  silently override an earlier ``action: block``);
* anchors/aliases are rejected, removing "billion laughs" style expansion.
"""

from typing import Any

import yaml


class UnsafeYAMLError(yaml.YAMLError):
    """Raised for YAML constructs CommitGuard refuses to load."""


class _StrictSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent: yaml.Node | None, index: int) -> yaml.Node | None:
        if self.check_event(yaml.events.AliasEvent):
            event = self.peek_event()  # type: ignore[no-untyped-call]
            raise yaml.composer.ComposerError(
                None, None, "YAML aliases are not allowed", event.start_mark
            )
        return super().compose_node(parent, index)


def _construct_unique_mapping(
    loader: _StrictSafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in seen
        except TypeError as exc:  # unhashable key such as a list
            raise yaml.constructor.ConstructorError(
                None, None, "mapping keys must be scalars", key_node.start_mark
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_yaml(text: str) -> object:
    """Parse one YAML document with the strict safe loader.

    Raises :class:`yaml.YAMLError` (including for duplicate keys and aliases).
    """
    # SafeLoader subclass: no object construction (see tests for !!python tags).
    return yaml.load(text, Loader=_StrictSafeLoader)  # noqa: S506  # nosec B506


def load_yaml_for_inspection(text: str) -> object:
    """Parse third-party YAML (e.g. GitHub workflow files) for read-only inspection.

    Uses ``yaml.SafeLoader`` (no object construction) but, unlike
    :func:`load_yaml`, accepts anchors and duplicate keys, which GitHub accepts
    in workflows. Never use this for CommitGuard configuration or rules.
    """
    return yaml.safe_load(text)

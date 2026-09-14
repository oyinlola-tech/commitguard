"""Load and validate rule files.

Rule files are data, parsed with the strict safe YAML loader and validated
against :mod:`commitguard.rules.models`. Built-in rules ship inside the wheel
(``commitguard/rules/data``); in a source checkout they are read from the
repository's top-level ``rules/`` directory.
"""

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from commitguard.exceptions.base import UnsafeInputError
from commitguard.exceptions.configuration import RulesError
from commitguard.rules.matcher import CompiledRules
from commitguard.rules.models import (
    AI_DOMAINS_FILE,
    AI_IDENTITIES_FILE,
    BOT_IDENTITIES_FILE,
    PATTERNS_FILE,
    AIDomainRules,
    AIIdentityRules,
    BotIdentityRules,
    PatternRules,
    RuleSet,
)
from commitguard.security.safe_yaml import load_yaml
from commitguard.utils.filesystem import read_text_limited

MAX_RULE_FILE_BYTES = 1024 * 1024


def builtin_rules_dir() -> Path:
    """Directory containing the built-in rule files."""
    packaged = Path(__file__).resolve().parent / "data"
    if packaged.is_dir():
        return packaged
    source_checkout = Path(__file__).resolve().parents[3] / "rules"
    if (source_checkout / AI_IDENTITIES_FILE).is_file():
        return source_checkout
    raise RulesError("built-in rule files not found (broken installation?)")


def _load_file[M: BaseModel](directory: Path, filename: str, model: type[M]) -> M:
    path = directory / filename
    try:
        text = read_text_limited(path, max_bytes=MAX_RULE_FILE_BYTES)
        document = load_yaml(text)
        return model.model_validate(document)
    except FileNotFoundError as exc:
        raise RulesError("rule file not found", path=path) from exc
    except (OSError, UnsafeInputError) as exc:
        raise RulesError(str(exc), path=path) from exc
    except yaml.YAMLError as exc:
        raise RulesError(f"invalid YAML: {exc}", path=path) from exc
    except ValidationError as exc:
        raise RulesError(_format(exc), path=path) from exc


def load_rules(directory: Path) -> CompiledRules:
    """Load, validate and cross-check every rule file in ``directory``."""
    try:
        rules = RuleSet(
            ai_identities=_load_file(directory, AI_IDENTITIES_FILE, AIIdentityRules),
            ai_domains=_load_file(directory, AI_DOMAINS_FILE, AIDomainRules),
            bots=_load_file(directory, BOT_IDENTITIES_FILE, BotIdentityRules),
            patterns=_load_file(directory, PATTERNS_FILE, PatternRules),
        )
        return CompiledRules(rules)
    except ValidationError as exc:
        raise RulesError(_format(exc), path=directory) from exc
    except ValueError as exc:  # conflicting aliases detected while compiling
        raise RulesError(str(exc), path=directory) from exc


@cache
def load_builtin_rules() -> CompiledRules:
    """Load the built-in rules once per process."""
    return load_rules(builtin_rules_dir())


def _format(exc: ValidationError) -> str:
    lines = ["invalid rules:"]
    for error in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)

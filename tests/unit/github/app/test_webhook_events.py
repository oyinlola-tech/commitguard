"""Normalisation of GitHub App webhooks into internal events."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from commitguard.ci.context import CIEventKind
from commitguard.github.errors import WebhookValidationError
from commitguard.github.events import (
    IgnoredEvent,
    InstallationAction,
    InstallationEvent,
    InstallationRepositoriesEvent,
    PullRequestEvent,
    PushEvent,
    RepositoriesAction,
    normalize_webhook,
)
from commitguard.github.identifiers import AccountType
from commitguard.github.pull_requests import PullRequestDisposition, disposition

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "webhooks"
A, B, C = "1" * 40, "2" * 40, "3" * 40


def load(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return document


def test_every_fixture_normalises() -> None:
    names = sorted(p.name for p in FIXTURES.glob("*.json"))
    assert names == [
        "installation.created.json",
        "installation.deleted.json",
        "installation_repositories.added.json",
        "installation_repositories.removed.json",
        "pull_request.closed.json",
        "pull_request.opened.json",
        "pull_request.reopened.json",
        "pull_request.synchronize.json",
        "push.json",
    ]
    for name in names:
        event = normalize_webhook(name.split(".")[0], load(name))
        assert not isinstance(event, IgnoredEvent), name


def test_push() -> None:
    event = normalize_webhook("push", load("push.json"))
    assert isinstance(event, PushEvent)
    assert event.installation_id == 42
    assert (event.repository.id, event.repository.full_name) == (5001, "octo-org/project")
    assert event.context.event is CIEventKind.PUSH
    assert (event.context.before_sha, event.context.after_sha) == (A, B)


@pytest.mark.parametrize(
    ("name", "action", "expected"),
    [
        ("pull_request.opened.json", "opened", PullRequestDisposition.SCAN),
        ("pull_request.synchronize.json", "synchronize", PullRequestDisposition.SCAN),
        ("pull_request.reopened.json", "reopened", PullRequestDisposition.SCAN),
        ("pull_request.closed.json", "closed", PullRequestDisposition.RECORD_MERGE),
    ],
)
def test_pull_request_actions(name: str, action: str, expected: PullRequestDisposition) -> None:
    event = normalize_webhook("pull_request", load(name))
    assert isinstance(event, PullRequestEvent)
    assert event.action == action
    assert event.number == 7
    assert event.context.base_sha == A
    assert disposition(event) is expected


def test_synchronize_scans_whole_pull_request_not_only_new_commits() -> None:
    event = normalize_webhook("pull_request", load("pull_request.synchronize.json"))
    assert isinstance(event, PullRequestEvent)
    # The push's before/after are not used: the range is base..new head.
    assert (event.context.base_sha, event.context.head_sha) == (A, C)


def test_closed_without_merge_and_irrelevant_actions() -> None:
    payload = load("pull_request.closed.json")
    payload["pull_request"]["merged"] = False
    event = normalize_webhook("pull_request", payload)
    assert isinstance(event, PullRequestEvent)
    assert disposition(event) is PullRequestDisposition.CLOSE
    for action in ("labeled", "assigned", "review_requested", "edited"):
        event = normalize_webhook(
            "pull_request", load("pull_request.opened.json") | {"action": action}
        )
        assert isinstance(event, PullRequestEvent)
        assert disposition(event) is PullRequestDisposition.IGNORE
    base_change = load("pull_request.opened.json") | {
        "action": "edited",
        "changes": {"base": {"ref": {"from": "develop"}}},
    }
    event = normalize_webhook("pull_request", base_change)
    assert isinstance(event, PullRequestEvent)
    assert disposition(event) is PullRequestDisposition.SCAN


def test_installation_events() -> None:
    created = normalize_webhook("installation", load("installation.created.json"))
    assert isinstance(created, InstallationEvent)
    assert created.action is InstallationAction.CREATED
    assert created.account.type is AccountType.ORGANIZATION
    assert created.account.login == "octo-org"
    assert [r.full_name for r in created.repositories] == ["octo-org/project"]
    assert created.permissions["checks"] == "write"
    deleted = normalize_webhook("installation", load("installation.deleted.json"))
    assert isinstance(deleted, InstallationEvent)
    assert deleted.action is InstallationAction.DELETED


def test_user_installation_is_not_confused_with_organization() -> None:
    payload = load("installation.created.json")
    payload["installation"]["account"] = {"login": "octocat", "id": 7, "type": "User"}
    event = normalize_webhook("installation", payload)
    assert isinstance(event, InstallationEvent)
    assert event.account.type is AccountType.USER


def test_installation_repositories_events() -> None:
    added = normalize_webhook(
        "installation_repositories", load("installation_repositories.added.json")
    )
    assert isinstance(added, InstallationRepositoriesEvent)
    assert added.action is RepositoriesAction.ADDED
    assert [r.id for r in added.added] == [5002]
    removed = normalize_webhook(
        "installation_repositories", load("installation_repositories.removed.json")
    )
    assert isinstance(removed, InstallationRepositoriesEvent)
    assert [r.id for r in removed.removed] == [5001]


@pytest.mark.parametrize(
    "event_name", ["ping", "issues", "workflow_run", "check_suite", "pull_request_target"]
)
def test_unused_events_are_ignored_not_errors(event_name: str) -> None:
    assert isinstance(normalize_webhook(event_name, load("pull_request.opened.json")), IgnoredEvent)


def _without(payload: dict[str, Any], *path: str) -> dict[str, Any]:
    target = payload
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    return payload


@pytest.mark.parametrize(
    ("event_name", "fixture", "path"),
    [
        ("push", "push.json", ("installation",)),
        ("push", "push.json", ("installation", "id")),
        ("push", "push.json", ("repository",)),
        ("push", "push.json", ("repository", "id")),
        ("push", "push.json", ("after",)),
        ("pull_request", "pull_request.opened.json", ("action",)),
        ("pull_request", "pull_request.opened.json", ("pull_request", "head", "sha")),
        ("pull_request", "pull_request.opened.json", ("pull_request", "number")),
        ("installation", "installation.created.json", ("installation", "account")),
        (
            "installation_repositories",
            "installation_repositories.added.json",
            ("repository_selection",),
        ),
    ],
)
def test_missing_mandatory_fields_are_rejected(
    event_name: str, fixture: str, path: tuple[str, ...]
) -> None:
    with pytest.raises(WebhookValidationError) as info:
        normalize_webhook(event_name, _without(load(fixture), *path))
    assert info.value.status == 400


@pytest.mark.parametrize(
    "full_name",
    [
        "$(touch /tmp/pwned)/project",
        "../../something",
        "octo-org/repo; malicious-command",
        "octo-org/..",
        "a/b/c",
        "octo-org/project.git",
        "-rf/x",
        "octo-org/`id`",
    ],
)
def test_malicious_repository_names_are_rejected(full_name: str) -> None:
    payload = load("push.json")
    payload["repository"]["full_name"] = full_name
    payload["repository"].pop("name")
    payload["repository"].pop("owner")
    with pytest.raises(WebhookValidationError):
        normalize_webhook("push", payload)


def _set(key: str, value: object, section: str | None = None) -> Callable[[dict[str, Any]], None]:
    def mutate(payload: dict[str, Any]) -> None:
        (payload[section] if section else payload)[key] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (_set("id", True, "installation"), "installation.id"),
        (_set("id", -1, "installation"), "installation.id"),
        (_set("id", "42", "installation"), "installation.id"),
        (_set("name", "other", "repository"), "inconsistent"),
        (_set("after", "not-a-sha"), "malformed"),
        (_set("ref", "refs/heads/x" + chr(0) + "y"), "control characters"),
    ],
)
def test_type_confusion_and_inconsistent_fields(
    mutate: Callable[[dict[str, Any]], None], expected: str
) -> None:
    payload = load("push.json")
    mutate(payload)
    with pytest.raises(WebhookValidationError) as info:
        normalize_webhook("push", payload)
    assert expected in str(info.value)


def test_untrusted_pull_request_text_is_not_carried_into_events() -> None:
    payload = load("pull_request.opened.json")
    payload["pull_request"]["title"] = "$(touch /tmp/pwned) `id` && rm -rf / ; | nc"
    payload["pull_request"]["body"] = "secret-looking text"
    event = normalize_webhook("pull_request", payload)
    assert isinstance(event, PullRequestEvent)
    dumped = event.model_dump_json()
    assert "pwned" not in dumped
    assert "secret-looking" not in dumped

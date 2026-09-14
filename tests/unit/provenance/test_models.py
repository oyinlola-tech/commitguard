import pytest
from pydantic import ValidationError

from commitguard.git.commit import Commit
from commitguard.provenance.author import Identity
from commitguard.provenance.signatures import SignatureInfo, SignatureStatus

IDENT = Identity(name="Ada", email="ada@example.com")


def test_identity_str() -> None:
    assert str(Identity(name="Claude", email="noreply@anthropic.com")) == (
        "Claude <noreply@anthropic.com>"
    )


def test_identity_is_immutable_and_strict() -> None:
    identity = Identity(name="a", email="b")
    with pytest.raises(ValidationError):
        identity.name = "c"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Identity(name="a", email="b", extra="x")  # type: ignore[call-arg]


def test_signature_model() -> None:
    info = SignatureInfo(status=SignatureStatus.UNSIGNED)
    assert info.key_id is None


def test_commit_derives_trailers_from_message() -> None:
    commit = Commit(
        author=IDENT,
        committer=IDENT,
        message="feat: x\n\nCo-authored-by: John Doe <john@example.com>\n",
    )
    (trailer,) = commit.trailers
    assert trailer.name == "John Doe"
    assert commit.trailers_with_key("co-authored-by") == (trailer,)
    assert commit.signature is None
    assert commit.short_sha == "pending"
    assert commit.is_pending


def test_commit_trailers_cannot_disagree_with_message() -> None:
    commit = Commit(author=IDENT, committer=IDENT, message="feat: x\n")
    data = commit.model_dump()
    data["message"] = "feat: y\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    assert len(Commit.model_validate(data).trailers) == 1


def test_commit_round_trip() -> None:
    commit = Commit(sha="a" * 40, author=IDENT, committer=IDENT, message="x\n\nA-by: B <b@c.d>\n")
    assert Commit.model_validate(commit.model_dump()) == commit
    assert commit.short_sha == "aaaaaaa"

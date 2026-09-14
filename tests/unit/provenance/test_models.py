import pytest
from pydantic import ValidationError

from commitguard.provenance.author import Identity
from commitguard.provenance.signatures import SignatureInfo, SignatureStatus
from commitguard.provenance.trailers import Trailer


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


def test_trailer_key_normalisation() -> None:
    trailer = Trailer(key=" CO-AUTHORED-BY ", value="Claude <noreply@anthropic.com>", line_number=3)
    assert trailer.normalized_key == "co-authored-by"


def test_trailer_line_numbers_are_one_based() -> None:
    with pytest.raises(ValidationError):
        Trailer(key="k", value="v", line_number=0)


def test_signature_model() -> None:
    info = SignatureInfo(status=SignatureStatus.UNSIGNED)
    assert info.key_id is None

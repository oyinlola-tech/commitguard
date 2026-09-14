"""Parsing of the ``pre-push`` hook's standard input.

Git writes one line per ref update::

    <local ref> SP <local object name> SP <remote ref> SP <remote object name> LF

* a deletion has local ref ``(delete)`` and an all-zero local object name;
* a new remote ref has an all-zero remote object name;
* annotated tags arrive as *tag object* names, not commit names.

Ref names are untrusted: they may legally contain quotes, ``;``, ``$()``,
backticks, ``|`` and non-ASCII characters. They are only ever passed to Git as
single argument-vector elements or displayed after sanitisation. Git ref names
cannot contain spaces, which makes the four-field split unambiguous.
"""

from pydantic import BaseModel, ConfigDict

from commitguard.exceptions.base import UnsafeInputError
from commitguard.security.validation import is_git_sha

MAX_UPDATES = 10_000
MAX_REF_LENGTH = 4096
DELETE_LOCAL_REF = "(delete)"


def is_zero_oid(oid: str) -> bool:
    return bool(oid) and set(oid) == {"0"}


class PushUpdate(BaseModel):
    """One ref update announced to the pre-push hook."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    local_ref: str
    local_oid: str
    remote_ref: str
    remote_oid: str

    @property
    def is_delete(self) -> bool:
        return is_zero_oid(self.local_oid)

    @property
    def is_new_ref(self) -> bool:
        return is_zero_oid(self.remote_oid)

    @property
    def is_tag(self) -> bool:
        return self.remote_ref.startswith("refs/tags/")


def _valid_oid(oid: str) -> bool:
    return is_git_sha(oid) or (len(oid) in (40, 64) and is_zero_oid(oid))


def _valid_ref(ref: str) -> bool:
    return 0 < len(ref) <= MAX_REF_LENGTH and not any(
        ord(ch) < 0x20 or ord(ch) == 0x7F for ch in ref
    )


def parse_pre_push_input(text: str) -> tuple[PushUpdate, ...]:
    """Parse and validate pre-push stdin. Malformed input raises (fail closed)."""
    updates: list[PushUpdate] = []
    for number, line in enumerate(text.split("\n"), start=1):
        line = line.removesuffix("\r")
        if not line:
            continue
        fields = line.split(" ")
        if len(fields) != 4:
            raise UnsafeInputError(f"pre-push input line {number}: expected 4 fields")
        local_ref, local_oid, remote_ref, remote_oid = fields
        if not (_valid_oid(local_oid) and _valid_oid(remote_oid)):
            raise UnsafeInputError(f"pre-push input line {number}: invalid object name")
        if len(local_oid) != len(remote_oid):
            raise UnsafeInputError(f"pre-push input line {number}: mixed object formats")
        if not (_valid_ref(local_ref) and _valid_ref(remote_ref)):
            raise UnsafeInputError(f"pre-push input line {number}: invalid ref name")
        if is_zero_oid(local_oid) != (local_ref == DELETE_LOCAL_REF):
            raise UnsafeInputError(f"pre-push input line {number}: inconsistent deletion")
        if len(updates) >= MAX_UPDATES:
            raise UnsafeInputError("too many ref updates in one push")
        updates.append(
            PushUpdate(
                local_ref=local_ref,
                local_oid=local_oid,
                remote_ref=remote_ref,
                remote_oid=remote_oid,
            )
        )
    return tuple(updates)

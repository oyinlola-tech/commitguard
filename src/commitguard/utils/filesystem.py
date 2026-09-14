"""Filesystem helpers with conservative defaults.

* Reads are size-limited and only follow regular files.
* Writes are atomic and never overwrite an existing file unless asked to.
"""

import os
import stat
import tempfile
from pathlib import Path

from commitguard.exceptions.base import UnsafeInputError


def read_bytes_limited(path: Path, *, max_bytes: int) -> bytes:
    """Read a regular file, refusing files larger than ``max_bytes``.

    Raises :class:`FileNotFoundError` if the file is missing and
    :class:`UnsafeInputError` if it is not a regular file or is too large.
    """
    st = path.stat()  # follows symlinks deliberately; the target must be regular
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeInputError(f"{path} is not a regular file")
    if st.st_size > max_bytes:
        raise UnsafeInputError(f"{path} is larger than {max_bytes} bytes")
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:  # file grew between stat() and read()
        raise UnsafeInputError(f"{path} is larger than {max_bytes} bytes")
    return data


def read_text_limited(path: Path, *, max_bytes: int, encoding: str = "utf-8") -> str:
    """Like :func:`read_bytes_limited`, decoding strictly as ``encoding``."""
    data = read_bytes_limited(path, max_bytes=max_bytes)
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise UnsafeInputError(f"{path} is not valid {encoding} text") from exc


def atomic_write_text(
    path: Path,
    content: str,
    *,
    overwrite: bool = False,
    mode: int = 0o644,
) -> None:
    """Atomically write ``content`` to ``path``.

    The data is written to a temporary file in the same directory and then
    moved into place. With ``overwrite=False`` (the default) an existing file
    is never replaced and :class:`FileExistsError` is raised.
    """
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.chmod(mode)
        if overwrite:
            tmp_path.replace(path)
        else:
            # os.link fails atomically if the destination exists.
            os.link(tmp_path, path)
            tmp_path.unlink()
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

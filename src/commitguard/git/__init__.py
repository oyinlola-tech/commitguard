"""Git integration layer.

* :mod:`commitguard.git.commit` - the normalised :class:`Commit` data model
  (pure data, no I/O; safe for detectors to import).
* :mod:`commitguard.git.commands` - the single choke point that executes Git.
* :mod:`commitguard.git.repository` - repository discovery and read-only queries.
* :mod:`commitguard.git.diff` - staged change inspection (planned).
* :mod:`commitguard.git.hooks` - hook installation and removal (planned).

The detection engine and detectors must never import the I/O modules; this is
enforced by ``tests/unit/test_architecture.py``.
"""

# Repository fixtures

Test repositories are created on the fly in a temporary directory by the
`git_repo` fixture in `tests/conftest.py`, instead of being committed here:
a nested `.git` directory cannot be committed, and generated repositories keep
tests independent of the developer's Git configuration.

Use `GitRepo.commit(message, author=..., committer=...)` to build the history a
test needs.

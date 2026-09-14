"""Command-line interface.

The CLI is the only layer that reads process state (CWD, argv) and writes to
the terminal. It composes the lower layers; it contains no detection or policy
logic of its own.
"""

"""Audit logging (planned, opt-in).

The goal is a local, append-only record of scans and decisions (initially
SQLite, PostgreSQL later) so blocked findings keep their evidence. Nothing is
recorded today, and audit storage will never be enabled implicitly.
"""

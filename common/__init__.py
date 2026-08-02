"""Marks this directory as a Python *package*.

If you come from another language, this file is the surprising part: Python
treats a directory as an importable package when it contains an `__init__.py`.
Its presence is what makes `from common.config import ...` work.

The file is intentionally almost empty. Any code written here would run on
every single `import common.…`, which makes imports slow and side-effecty.
Shared code goes in sibling files (`config.py`, `metrics.py`) instead.
"""

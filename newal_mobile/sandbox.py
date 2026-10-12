"""Path sandbox: every path a tool touches must resolve inside the workspace."""

from __future__ import annotations

import os
from pathlib import Path


class SandboxError(ValueError):
    """A path is outside the workspace or not allowed at all."""


def resolve_in_workspace(root: str | os.PathLike, rel: str) -> Path:
    if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
        raise SandboxError("path must be a non-empty relative path")
    if os.path.isabs(rel) or rel.startswith("~"):
        raise SandboxError("absolute paths are not allowed")
    real_root = Path(os.path.realpath(root))
    # realpath resolves "..", and symlinks, so an escape through either is caught below.
    target = Path(os.path.realpath(real_root / rel))
    if target != real_root and real_root not in target.parents:
        raise SandboxError("path escapes the workspace")
    return target

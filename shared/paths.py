"""Locate the repository root without depending on the process working directory."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Directory that contains ``config/defaults.yaml``.

    ``LACTOGRID_ROOT`` wins. Otherwise walk parents of this file so the
    code works from a source checkout. Installed wheels should set the
    environment variable.
    """
    env = os.environ.get("LACTOGRID_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "defaults.yaml").is_file():
            return parent
    raise FileNotFoundError(
        "Cannot find config/defaults.yaml. Set LACTOGRID_ROOT to the repository root."
    )


def defaults_path() -> Path:
    return repo_root() / "config" / "defaults.yaml"


def scenarios_dir() -> Path:
    return repo_root() / "config" / "scenarios"

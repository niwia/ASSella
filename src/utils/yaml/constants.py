"""Shared constants with no dependencies.

SHARED_REDISTS is imported directly by 13 other modules across the
codebase, so it lives in its own leaf rather than being re-exported
through a higher layer - that keeps the dependency arrow pointing one
way and removes the only reason the package could ever form a cycle.

Extracted from ``utils/yaml_config_manager.py``. That module is now a thin
facade that re-exports this package's public names, so every existing
``from utils.yaml_config_manager import ...`` keeps working unchanged.

Do not import across sibling modules in this package except from ``core`` -
dependencies must point in one direction (leaf -> core) to keep the package
import-cycle free.
"""

import logging

# --- imports ---------------------------------------------------------
import re
from typing import Set


logger = logging.getLogger(__name__)


SHARED_REDISTS: Set[str] = {
    "228980", "1034630", "228981", "228982", "228983", "228984", "228985",
    "228986", "228987", "228988", "228989", "228990", "229000", "229001",
    "229002", "229003", "229004", "229005", "229006", "229007", "229010",
    "229011", "229012", "229020", "229030", "229031", "229032",
}

BACKUP_SUFFIX = ".bak"

HEX_64_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

NUMERIC_ID_PATTERN = re.compile(r"^\d+$")

TOP_LEVEL_KEY_PATTERN = re.compile(r"^['\"]?[A-Za-z0-9_]+['\"]?[ \t]*:", re.MULTILINE)

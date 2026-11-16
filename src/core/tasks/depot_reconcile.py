"""Reconcile a game's saved depot manifests against what is actually installed.

The update checker compares the depot file ACCELA wrote against Steam. That file
can be wrong in two ways that both read as a permanent pending update:

* it lists a depot the current install was not downloaded with (a depot left
  behind by an earlier selection, e.g. an inherited Windows depot on a Linux
  install), so a 2021-era manifest is compared against today's Steam build;
* it hints a manifest id from before the last update, when ``.DepotDownloader``
  already holds the manifest that was written.

``.DepotDownloader/metadata.json`` records the depot list the install was made
with and the directory holds the manifests that were written, so both are better
evidence than the depot file. This module reconciles the two and reports whether
the file needs rewriting.

Qt-free on purpose: every rule is driven against plain dicts and a temp dir.
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def installed_manifest_id(install_path: Optional[str], depot_id: str) -> Optional[str]:
    """Return the manifest id of the newest ``{depot_id}_*.manifest`` on disk."""
    if not install_path:
        return None
    ddm_dir = os.path.join(install_path, ".DepotDownloader")
    if not os.path.isdir(ddm_dir):
        return None
    try:
        candidates = [
            fname for fname in os.listdir(ddm_dir)
            if fname.startswith(f"{depot_id}_") and fname.endswith(".manifest")
        ]
        candidates.sort(
            key=lambda f: os.path.getmtime(os.path.join(ddm_dir, f)),
            reverse=True,
        )
        for fname in candidates:
            base = fname[:-9]  # strip ".manifest"
            parts = base.split("_", 1)
            if len(parts) == 2:
                return parts[1]
    except OSError as e:
        logger.debug(f"[DepotReconcile] Could not read manifest candidates from {ddm_dir}: {e}")
    return None


def _selected_depots(install_path: str) -> Optional[set]:
    """Depot ids recorded in metadata.json, or None when it cannot narrow us down."""
    meta_json = Path(install_path) / ".DepotDownloader" / "metadata.json"
    if not meta_json.exists():
        return None
    try:
        data = json.loads(meta_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug(f"[DepotReconcile] Could not read metadata.json: {e}")
        return None
    selected = data.get("selected_depots_list")
    if not isinstance(selected, list) or not selected:
        return None
    return {str(d) for d in selected}


def reconcile_saved_depots(
    saved_depots: Dict[str, str], install_path: Optional[str]
) -> Tuple[Dict[str, str], bool]:
    """Narrow *saved_depots* to the active selection and the installed manifests.

    Returns the reconciled mapping and whether it differs from ``saved_depots``.
    A selection that would empty the mapping is ignored: a depot file is only
    better replaced by evidence, never deleted by it.
    """
    if not saved_depots or not install_path or not os.path.isdir(install_path):
        return saved_depots, False

    reconciled = dict(saved_depots)
    changed = False

    selected = _selected_depots(install_path)
    if selected:
        kept = {did: mid for did, mid in reconciled.items() if did in selected}
        if kept and kept != reconciled:
            reconciled = kept
            changed = True

    for did in list(reconciled.keys()):
        installed = installed_manifest_id(install_path, did)
        if installed and installed != reconciled[did]:
            reconciled[did] = installed
            changed = True

    return reconciled, changed


def write_depot_file(depot_file: Path, saved_depots: Dict[str, str]) -> None:
    """Rewrite a depot file in its canonical ``depot_id: manifest_id`` form."""
    depot_file.parent.mkdir(parents=True, exist_ok=True)
    with open(depot_file, "w", encoding="utf-8") as f:
        for did, mid in sorted(
            saved_depots.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]
        ):
            f.write(f"{did}: {mid}\n")

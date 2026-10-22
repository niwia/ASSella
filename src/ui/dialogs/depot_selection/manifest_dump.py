"""Prepare a depot's manifest so that individual files can be picked out of it.

Customising files starts from a manifest: the app's newest Hubcap bundle is
extracted, the depot key comes from the bundle's LUA file (falling back to the
local key database), and DepotDownloader is pointed at the ``.manifest`` inside
it to write a text listing of the depot's contents.

None of that needs a widget, so it lives here rather than in the dialog. Every
failure is a :class:`ManifestDumpError` whose message is what the dialog shows,
which is why the messages read like user-facing text.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from utils.lua_parsing import find_live

logger = logging.getLogger(__name__)

AppId = Union[int, str]


class ManifestDumpError(Exception):
    """A depot's manifest could not be prepared for file-level selection."""


@dataclass(frozen=True)
class ManifestDump:
    """A prepared dump: where its files are, and how to turn them into a list."""

    app_id: AppId
    depot_id: str
    manifest_id: str
    manifest_file: str
    keys_path: str
    temp_dir: str
    txt_path: str

    def command(self, dotnet_path: str, dll_path: str) -> list:
        """The DepotDownloader invocation that writes the file listing."""
        return [
            dotnet_path,
            dll_path,
            "-app", str(self.app_id),
            "-depot", str(self.depot_id),
            "-manifest", str(self.manifest_id),
            "-manifestfile", self.manifest_file,
            "-depotkeys", self.keys_path,
            "-manifest-only",
            "-dir", self.temp_dir,
        ]


def find_manifest_bundle(app_id: AppId, manifests_dir: Optional[Path] = None) -> Path:
    """The newest Hubcap bundle for ``app_id``.

    Two names are in circulation - ``accela_fetch_<app>.zip`` for the plain
    fetch and ``accela_fetch_<app>_<something>.zip`` for the variants - and the
    most recently written one wins.
    """
    if manifests_dir is None:
        from utils.helpers import get_base_path

        manifests_dir = get_base_path() / "hubcap_manifests"

    zips = list(manifests_dir.glob(f"accela_fetch_{app_id}.zip")) + list(
        manifests_dir.glob(f"accela_fetch_{app_id}_*.zip")
    )
    if not zips:
        raise ManifestDumpError(
            f"No manifest zip file found for AppID {app_id} in {manifests_dir}."
        )
    zips.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return zips[0]


def extract_bundle(zip_path: str, temp_dir: str) -> None:
    """Unpack a Hubcap bundle into ``temp_dir``."""
    try:
        from utils.steam_manifest import safe_extract_zip

        safe_extract_zip(zip_path, temp_dir)
    except Exception as e:
        raise ManifestDumpError(f"Failed to extract manifest zip: {e}") from e


def read_depot_key(
    temp_dir: str,
    depot_id: str,
    app_id: AppId,
    key_lookup: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[str]:
    """The depot key, from the bundle's LUA file or the local key database.

    Bundles generated from a depot key request carry the key in their LUA file,
    either as an ``addappid`` line or as a ``["depot"] = "key"`` entry; a
    smart-generated bundle carries none, so the local database is tried too.

    ``key_lookup`` replaces the database for one call; it is given the depot id
    and returns the key or None.
    """
    try:
        lua_files = list(Path(temp_dir).glob("*.lua"))
        if lua_files:
            lua_content = Path(lua_files[0]).read_text(encoding="utf-8", errors="ignore")
            match = find_live(
                lua_content,
                r"addappid\(\s*"
                + re.escape(depot_id)
                + r"\s*,\s*\d+\s*,\s*\"([a-fA-F0-9]+)\"\)",
            )
            if not match:
                match = re.search(
                    rf'\["?{re.escape(depot_id)}"?\]\s*=\s*"(.*?)"', lua_content
                )
            if match:
                return match.group(1).strip()
    except Exception as e:
        logger.warning(f"Failed to parse LUA for depot keys: {e}")

    try:
        if key_lookup is not None:
            cached = key_lookup(depot_id)
        else:
            from managers.depot_key_manager import DepotKeyManager

            cached = DepotKeyManager().get_depot_keys(app_id).get(depot_id)
        if cached:
            return cached
    except Exception as dkm_e:
        logger.warning(
            f"Failed to load key from depot_keys.db for depot {depot_id}: {dkm_e}"
        )

    return None


def find_manifest_file(temp_dir: str, depot_id: str) -> Path:
    """The bundle's ``.manifest`` for ``depot_id``, or the only one there is."""
    manifest_files = list(Path(temp_dir).glob(f"{depot_id}_*.manifest"))
    if not manifest_files:
        manifest_files = list(Path(temp_dir).glob("*.manifest"))
    if not manifest_files:
        raise ManifestDumpError(
            f"No manifest file (*.manifest) found for depot {depot_id} in the extracted manifest bundle."
        )
    return manifest_files[0]


def manifest_id_from_name(name: str) -> str:
    """``123_456789.manifest`` -> ``456789``; a bare stem is its own id."""
    stem = name.replace(".manifest", "")
    return stem.split("_", 1)[1] if "_" in stem else stem


def write_keys_file(keys_path: str, depot_id: str, depot_key: str) -> None:
    """Write the single-line ``depot.keys`` DepotDownloader reads."""
    try:
        with open(keys_path, "w") as kf:
            kf.write(f"{depot_id};{depot_key}\n")
    except OSError as e:
        raise ManifestDumpError(f"Failed to write keys file: {e}") from e


def discard_keys_file(keys_path: str) -> None:
    """Drop a ``depot.keys`` we wrote; the key itself is not ours to keep."""
    if os.path.exists(keys_path):
        try:
            os.remove(keys_path)
        except OSError:
            pass


def prepare_manifest_dump(
    app_id: AppId,
    depot_id: str,
    *,
    manifests_dir: Optional[Path] = None,
    temp_root: Optional[str] = None,
    key_lookup: Optional[Callable[[str], Optional[str]]] = None,
) -> ManifestDump:
    """Do everything up to running DepotDownloader for ``depot_id``.

    The optional arguments exist so this can be exercised against a scratch
    directory; the dialog passes none of them.
    """
    zip_path = find_manifest_bundle(app_id, manifests_dir)

    temp_dir = os.path.join(
        temp_root or tempfile.gettempdir(), f"selective_manifests_{app_id}"
    )
    os.makedirs(temp_dir, exist_ok=True)
    extract_bundle(str(zip_path), temp_dir)

    depot_key = read_depot_key(temp_dir, depot_id, app_id, key_lookup)
    if not depot_key:
        raise ManifestDumpError(
            f"Could not find depot key for depot {depot_id} in LUA config or local key database."
        )

    keys_path = os.path.join(temp_dir, "depot.keys")
    write_keys_file(keys_path, depot_id, depot_key)

    try:
        manifest_path = find_manifest_file(temp_dir, depot_id)
    except ManifestDumpError:
        # A key on disk outlives the dialog, so never leave one behind for a
        # dump that cannot be used.
        discard_keys_file(keys_path)
        raise

    manifest_id = manifest_id_from_name(manifest_path.name)

    return ManifestDump(
        app_id=app_id,
        depot_id=depot_id,
        manifest_id=manifest_id,
        manifest_file=str(manifest_path),
        keys_path=keys_path,
        temp_dir=temp_dir,
        txt_path=os.path.join(temp_dir, f"manifest_{depot_id}_{manifest_id}.txt"),
    )

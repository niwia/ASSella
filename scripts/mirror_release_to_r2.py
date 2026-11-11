#!/usr/bin/env python3
"""Mirror official ASSella GitHub release assets to Cloudflare R2.

Maintains a strict per-channel mirror layout under ``releases/`` so each
enabled channel keeps only its latest release tag on R2, with all older builds
for that channel pruned automatically after the new upload succeeds:

    releases/
      channels/<channel>.json
      <channel>/<tag>/<asset_name>

Publishing order guarantees atomicity for readers:
  1. Upload every asset under ``releases/<channel>/<tag>/``.
  2. Upload ``releases/channels/<channel>.json`` pointing at the new tag.
  3. Delete all keys under ``releases/<channel>/`` outside ``<tag>/``.

Required environment variables (unless ``--dry-run``):
  R2_RELEASES_ENDPOINT
  R2_RELEASES_BUCKET
  R2_RELEASES_ACCESS_KEY_ID
  R2_RELEASES_SECRET_ACCESS_KEY
  R2_RELEASES_PUBLIC_URL
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

ALLOWED_CHANNELS = ("stable", "beta")
DEFAULT_REPO = "niwia/ASSella"
SAFE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CANARY_TOKENS = ("canary", "testing", "devtest", "release_candidate", "releasecandidate")
BETA_TOKENS = ("beta", "dev", "rc", "candidate")
CANARY_TOKEN_RE = re.compile(
    r"(?<![a-z])(canary|testing|devtest|release[_-]?candidate)(?![a-z])",
    re.IGNORECASE,
)
BETA_TOKEN_RE = re.compile(
    r"(?<![a-z])(beta|dev|rc|candidate)(?![a-z])",
    re.IGNORECASE,
)

CONTENT_TYPES = {
    ".appimage": "application/octet-stream",
    ".zsync": "application/octet-stream",
    ".sha256": "text/plain; charset=utf-8",
    ".gz": "application/gzip",
    ".zip": "application/zip",
    ".json": "application/json; charset=utf-8",
}


@dataclass(frozen=True)
class LocalAsset:
    """Release artifact staged on local disk ready to upload to R2."""

    name: str
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class R2Settings:
    """Connection and bucket settings resolved from environment variables."""

    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    public_url: str

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "R2Settings":
        """Load R2 settings from environment variables."""
        endpoint = os.environ.get("R2_RELEASES_ENDPOINT", "").strip().rstrip("/")
        bucket = os.environ.get("R2_RELEASES_BUCKET", "").strip()
        access_key = os.environ.get("R2_RELEASES_ACCESS_KEY_ID", "").strip()
        secret_key = os.environ.get("R2_RELEASES_SECRET_ACCESS_KEY", "").strip()
        public_url = os.environ.get("R2_RELEASES_PUBLIC_URL", "").strip().rstrip("/")

        if dry_run:
            return cls(
                endpoint=endpoint or "https://dry-run.r2.cloudflarestorage.com",
                bucket=bucket or "dry-run-bucket",
                access_key_id=access_key or "dry-run-key",
                secret_access_key=secret_key or "dry-run-secret",
                public_url=public_url or "https://dry-run.r2.dev",
            )

        missing = [
            name
            for name, val in (
                ("R2_RELEASES_ENDPOINT", endpoint),
                ("R2_RELEASES_BUCKET", bucket),
                ("R2_RELEASES_ACCESS_KEY_ID", access_key),
                ("R2_RELEASES_SECRET_ACCESS_KEY", secret_key),
                ("R2_RELEASES_PUBLIC_URL", public_url),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

        return cls(
            endpoint=endpoint,
            bucket=bucket,
            access_key_id=access_key,
            secret_access_key=secret_key,
            public_url=public_url,
        )


def validate_tag(tag: str) -> str:
    """Validate that a release tag is non-empty and safe to use in R2 object keys."""
    clean = str(tag or "").strip()
    if not SAFE_TAG_RE.match(clean) or ".." in clean:
        raise ValueError(f"Unsafe or invalid release tag: {tag!r}")
    return clean


def _normalize_release_branch(target_commitish: Optional[str]) -> str:
    """Return normalized branch from ``target_commitish``, or ``''`` for unset/SHAs."""
    raw = str(target_commitish or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("refs/heads/"):
        raw = raw[len("refs/heads/"):]
    if raw in ("main", "master"):
        return "main"
    if len(raw) >= 7 and re.fullmatch(r"[0-9a-f]{7,40}", raw):
        return ""
    return raw


def classify_release_channel(
    tag: str,
    prerelease: bool = False,
    target_commitish: Optional[str] = None,
) -> Optional[str]:
    """Classify a release tag/branch/prerelease tuple into ``stable``, ``beta``, or ``canary``."""
    clean = str(tag or "").strip()
    if not clean:
        return None
    lower = clean.lower()
    branch = _normalize_release_branch(target_commitish)
    if CANARY_TOKEN_RE.search(lower) or branch == "canary":
        return "canary" if prerelease else None
    if BETA_TOKEN_RE.search(lower) or (prerelease and branch in ("", "beta", "main")):
        return "beta"
    if prerelease:
        return None
    if branch and branch != "main":
        return None
    stripped = clean.lstrip("vV")
    is_clean_semver = bool(re.match(r"^\d+\.\d+(\.\d+)?$", stripped))
    is_hotfix = bool(re.search(r"\bhotfix\b|[-._]hotfix\d*", lower))
    if is_clean_semver or is_hotfix:
        return "stable"
    return None


def is_appimage_asset(name: str) -> bool:
    """True when asset filename is an AppImage binary rather than metadata."""
    low = str(name or "").lower()
    return low.endswith(".appimage") and not low.endswith(".zsync") and not low.endswith(".sha256")


def compute_sha256(path: Path) -> str:
    """Compute lowercase hexadecimal SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256_sidecar(path: Path) -> Optional[str]:
    """Extract a 64-hex SHA-256 checksum from a ``.sha256`` sidecar file."""
    content = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\b[a-fA-F0-9]{64}\b", content)
    return match.group(0).lower() if match else None


def guess_content_type(filename: str) -> str:
    """Return an explicit MIME type for release artifacts."""
    low = filename.lower()
    for suffix, ctype in CONTENT_TYPES.items():
        if low.endswith(suffix):
            return ctype
    return "application/octet-stream"


def asset_key(channel: str, tag: str, asset_name: str) -> str:
    """Return the canonical R2 object key for a release asset."""
    return f"releases/{channel}/{tag}/{asset_name}"


def manifest_key(channel: str) -> str:
    """Return the canonical R2 object key for a channel pointer manifest."""
    return f"releases/channels/{channel}.json"


def _github_headers(token: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "User-Agent": "ASSella-R2-Mirror/1.0",
        "Accept": "application/vnd.github+json",
    }
    tok = (token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return headers


def _http_get_json(url: str, token: Optional[str] = None, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers=_github_headers(token))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download_with_retries(
    url: str,
    dest: Path,
    *,
    expected_size: Optional[int] = None,
    token: Optional[str] = None,
    attempts: int = 3,
    timeout: float = 120.0,
) -> None:
    """Stream an HTTPS asset to disk with retry/backoff and size verification."""
    last_err: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "ASSella-R2-Mirror/1.0",
                    **(
                        {"Authorization": f"Bearer {token.strip()}"}
                        if token and token.strip() and "api.github.com" in url
                        else {}
                    ),
                },
            )
            tmp_path = dest.with_name(f".{dest.name}.part")
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp_path, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            actual_size = tmp_path.stat().st_size
            if expected_size is not None and expected_size > 0 and actual_size != expected_size:
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Size mismatch for {dest.name}: expected {expected_size} bytes, got {actual_size}"
                )
            tmp_path.replace(dest)
            return
        except Exception as exc:
            last_err = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"Failed to download {url}: {last_err}")


class ChannelMismatchError(RuntimeError):
    """Raised when an explicit release belongs to a different channel than requested."""


class RollbackError(RuntimeError):
    """Raised when mirroring would replace a newer R2 channel release with an older one."""


def _parse_utc_epoch(raw: Any) -> float:
    """Parse an ISO-8601 timestamp into UTC epoch seconds, or 0.0 if missing/invalid."""
    text = str(raw or "").strip()
    if not text:
        return 0.0
    try:
        iso = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timestamp()
    except Exception:
        return 0.0


def _version_sort_key(tag: str) -> Tuple[int, int, int, int, int]:
    """Return a monotonic SemVer comparison key for release tags."""
    clean = re.sub(r"^[0-9]{8}\+[a-zA-Z0-9._-]+-", "", str(tag or "").strip()).lstrip("vV")
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", clean)
    if not m:
        return (0, 0, 0, -5, 0)
    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    suffix = (m.group(4) or "").strip().lower()
    if not suffix:
        return (major, minor, patch, 0, 0)
    if "hotfix" in suffix:
        hm = re.search(r"hotfix\D*(\d+)", suffix)
        return (major, minor, patch, 1, int(hm.group(1)) if hm else 1)
    nums = re.findall(r"\d+", suffix)
    sub = int(nums[-1]) if nums else 1
    if CANARY_TOKEN_RE.search(suffix):
        weight = -4
    elif re.search(r"(?<![a-z])dev(?![a-z])", suffix):
        weight = -3
    elif re.search(r"(?<![a-z])beta(?![a-z])", suffix):
        weight = -2
    elif re.search(r"(?<![a-z])(?:rc|release[_-]?candidate)(?![a-z])", suffix):
        weight = -1
    else:
        weight = -5
    return (major, minor, patch, weight, sub)


def _check_r2_rollback(
    *,
    channel: str,
    incoming_tag: str,
    incoming_published: str,
    remote_manifest: Optional[Dict[str, Any]],
) -> None:
    """Enforce per-channel monotonicity against the live R2 manifest."""
    if not remote_manifest:
        return
    remote_tag = str(remote_manifest.get("tag_name") or "").strip()
    if not remote_tag or remote_tag == incoming_tag:
        return

    if channel == "stable":
        if _version_sort_key(incoming_tag) < _version_sort_key(remote_tag):
            raise RollbackError(
                f"Refusing to roll back R2 'stable' manifest from {remote_tag} to older {incoming_tag}; "
                "pass --allow-rollback to override."
            )
        return

    incoming_ts = _parse_utc_epoch(incoming_published)
    remote_pub = str(remote_manifest.get("published_at") or remote_manifest.get("created_at") or "").strip()
    remote_ts = _parse_utc_epoch(remote_pub)
    if incoming_ts > 0 and remote_ts > 0 and incoming_ts < remote_ts:
        raise RollbackError(
            f"Refusing to roll back R2 '{channel}' manifest from {remote_tag} ({remote_pub}) "
            f"to older {incoming_tag} ({incoming_published}); pass --allow-rollback to override."
        )


def select_github_release(
    releases: Sequence[Dict[str, Any]],
    *,
    channel: str,
    tag: Optional[str] = None,
) -> Dict[str, Any]:
    """Select the target GitHub release for ``channel`` (latest or explicit tag)."""
    if tag:
        wanted = validate_tag(tag)
        for rel in releases:
            if str(rel.get("tag_name", "")).strip() == wanted:
                if rel.get("draft"):
                    raise RuntimeError(f"Release {wanted} is marked as draft.")
                detected = classify_release_channel(
                    wanted,
                    prerelease=bool(rel.get("prerelease")),
                    target_commitish=rel.get("target_commitish"),
                )
                if detected != channel:
                    raise ChannelMismatchError(
                        f"Release {wanted} belongs to channel {detected!r}, not {channel!r}."
                    )
                return rel
        raise RuntimeError(f"Release tag {wanted!r} was not found.")

    matching: List[Dict[str, Any]] = []
    for rel in releases:
        if rel.get("draft"):
            continue
        rtag = str(rel.get("tag_name", "")).strip()
        if not rtag or not SAFE_TAG_RE.match(rtag):
            continue
        if (
            classify_release_channel(
                rtag,
                prerelease=bool(rel.get("prerelease")),
                target_commitish=rel.get("target_commitish"),
            )
            != channel
        ):
            continue
        assets = [a for a in rel.get("assets", []) if isinstance(a, dict)]
        if not any(is_appimage_asset(str(a.get("name", ""))) for a in assets):
            continue
        matching.append(rel)

    if not matching:
        raise RuntimeError(f"No published {channel} release with AppImage assets found.")

    if channel == "stable":
        matching.sort(
            key=lambda r: (
                _version_sort_key(str(r.get("tag_name") or "")),
                _parse_utc_epoch(r.get("published_at") or r.get("created_at")),
            ),
            reverse=True,
        )
    else:
        matching.sort(
            key=lambda r: (
                _parse_utc_epoch(r.get("published_at") or r.get("created_at")),
                _version_sort_key(str(r.get("tag_name") or "")),
            ),
            reverse=True,
        )
    return matching[0]


def fetch_github_release(
    *,
    repo: str,
    channel: str,
    tag: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch release metadata from the GitHub Releases API."""
    if tag:
        clean_tag = validate_tag(tag)
        url = f"https://api.github.com/repos/{repo}/releases/tags/{clean_tag}"
        rel = _http_get_json(url, token=token)
        return select_github_release([rel], channel=channel, tag=clean_tag)

    url = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    releases = _http_get_json(url, token=token)
    if not isinstance(releases, list):
        raise RuntimeError("Unexpected response from GitHub Releases API.")
    return select_github_release(releases, channel=channel)


def collect_local_assets(directory: Path, tag: str) -> List[LocalAsset]:
    """Collect exact release artifacts built for ``tag`` in ``directory`` and verify checksums."""
    expected_names = [
        "ASSella.AppImage",
        "ASSella.AppImage.zsync",
        "ASSella.AppImage.sha256",
        "ASSella.AppImage.zsync.sha256",
        f"ASSella-{tag}-linux-source.tar.gz",
        f"ASSella-{tag}-linux-source.tar.gz.sha256",
        f"ASSella-{tag}-linux-source.zip",
        f"ASSella-{tag}-linux-source.zip.sha256",
    ]
    missing = [name for name in expected_names if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Missing expected release artifacts in {directory}: {', '.join(missing)}"
        )

    staged = [
        LocalAsset(
            name=name,
            path=directory / name,
            size=(directory / name).stat().st_size,
            sha256=compute_sha256(directory / name),
        )
        for name in expected_names
    ]
    verify_staged_sidecars(staged)
    return staged


def download_release_assets(
    release_data: Dict[str, Any],
    dest_dir: Path,
    *,
    token: Optional[str] = None,
) -> List[LocalAsset]:
    """Download every asset attached to ``release_data`` and verify hashes."""
    raw_assets = [a for a in release_data.get("assets", []) if isinstance(a, dict)]
    if not raw_assets:
        raise RuntimeError("GitHub release has zero assets.")

    staged: List[LocalAsset] = []
    for asset in raw_assets:
        name = Path(str(asset.get("name", "")).strip()).name
        dl_url = str(asset.get("browser_download_url", "")).strip()
        if not name or not dl_url:
            continue
        dest = dest_dir / name
        expected_size = int(asset["size"]) if asset.get("size") is not None else None
        _download_with_retries(dl_url, dest, expected_size=expected_size, token=token)
        actual_sha = compute_sha256(dest)
        digest_field = str(asset.get("digest") or "").strip().lower()
        if digest_field.startswith("sha256:"):
            expected_digest = digest_field.split(":", 1)[1].strip()
            if expected_digest and actual_sha != expected_digest:
                raise RuntimeError(
                    f"Digest mismatch for {name}: expected {expected_digest}, got {actual_sha}"
                )
        staged.append(LocalAsset(name=name, path=dest, size=dest.stat().st_size, sha256=actual_sha))

    if not any(is_appimage_asset(a.name) for a in staged):
        raise RuntimeError("Downloaded release contains no AppImage binary.")

    verify_staged_sidecars(staged)
    return staged


def verify_staged_sidecars(assets: Sequence[LocalAsset]) -> None:
    """Verify every ``<file>.sha256`` sidecar matches ``<file>``."""
    by_name = {a.name: a for a in assets}
    for asset in assets:
        if not asset.name.endswith(".sha256"):
            continue
        target_name = asset.name[: -len(".sha256")]
        target = by_name.get(target_name)
        if target is None:
            continue
        declared = parse_sha256_sidecar(asset.path)
        if not declared:
            raise RuntimeError(f"Malformed SHA-256 sidecar: {asset.name}")
        if declared != target.sha256:
            raise RuntimeError(
                f"Sidecar {asset.name} expects {declared}, but {target.name} has {target.sha256}"
            )


def build_channel_manifest(
    *,
    channel: str,
    tag: str,
    release_name: str,
    published_at: str,
    prerelease: bool,
    public_url: str,
    assets: Sequence[LocalAsset],
    target_commitish: Optional[str] = None,
    mirrored_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a GitHub-compatible release manifest for ``releases/channels/<channel>.json``."""
    base = public_url.rstrip("/")
    stamp = mirrored_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    clean_ver = tag.lstrip("vV")
    manifest: Dict[str, Any] = {
        "schema": 1,
        "channel": channel,
        "tag_name": tag,
        "version": clean_ver,
        "name": release_name or f"ASSella {tag}",
        "prerelease": bool(prerelease),
        "draft": False,
        "published_at": published_at,
        "mirrored_at": stamp,
        "public_base_url": base,
        "assets": [
            {
                "name": asset.name,
                "size": asset.size,
                "sha256": asset.sha256,
                "browser_download_url": f"{base}/{asset_key(channel, tag, asset.name)}",
            }
            for asset in sorted(assets, key=lambda a: (0 if a.name == "ASSella.AppImage" else 1, a.name))
        ],
    }
    if target_commitish:
        manifest["target_commitish"] = target_commitish
    return manifest


def create_r2_client(settings: R2Settings):
    """Create a boto3 S3 client configured cleanly for Cloudflare R2."""
    import boto3
    from botocore.config import Config

    cfg_kwargs: Dict[str, Any] = {
        "signature_version": "s3v4",
        "s3": {"addressing_style": "path"},
    }
    sig = inspect.signature(Config.__init__)
    options_attr = getattr(Config, "OPTION_DEFAULTS", {})
    if "request_checksum_calculation" in sig.parameters or "request_checksum_calculation" in options_attr:
        cfg_kwargs["request_checksum_calculation"] = "when_required"
        cfg_kwargs["response_checksum_validation"] = "when_required"

    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        region_name="auto",
        config=Config(**cfg_kwargs),
    )


def _fetch_remote_manifest(s3_client: Any, bucket: str, channel: str) -> Optional[Dict[str, Any]]:
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=manifest_key(channel))
        body = resp["Body"].read().decode("utf-8")
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _list_channel_keys(s3_client: Any, bucket: str, channel: str) -> List[str]:
    prefix = f"releases/{channel}/"
    keys: List[str] = []
    continuation: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        resp = s3_client.list_objects_v2(**kwargs)
        for item in resp.get("Contents", []) or []:
            key = item.get("Key")
            if key:
                keys.append(str(key))
        if not resp.get("IsTruncated"):
            break
        continuation = resp.get("NextContinuationToken")
    return keys


def _manifest_matches_assets(
    remote_manifest: Optional[Dict[str, Any]],
    *,
    tag: str,
    assets: Sequence[LocalAsset],
    existing_keys: Iterable[str],
    channel: str,
) -> bool:
    if not remote_manifest or str(remote_manifest.get("tag_name", "")).strip() != tag:
        return False
    remote_assets = {
        str(a.get("name", "")): (int(a.get("size") or -1), str(a.get("sha256") or "").lower())
        for a in remote_manifest.get("assets", [])
        if isinstance(a, dict)
    }
    local_assets = {a.name: (a.size, a.sha256.lower()) for a in assets}
    if remote_assets != local_assets:
        return False
    key_set = set(existing_keys)
    return all(asset_key(channel, tag, a.name) in key_set for a in assets)


def _make_transfer_config() -> Optional[Any]:
    try:
        from boto3.s3.transfer import TransferConfig
    except ImportError:
        return None
    return TransferConfig(
        multipart_threshold=64 * 1024 * 1024,
        multipart_chunksize=16 * 1024 * 1024,
        max_concurrency=4,
        use_threads=True,
    )


def sync_release_to_r2(
    *,
    s3_client: Any,
    settings: R2Settings,
    channel: str,
    tag: str,
    release_name: str,
    published_at: str,
    prerelease: bool,
    assets: Sequence[LocalAsset],
    target_commitish: Optional[str] = None,
    skip_if_unchanged: bool = False,
    allow_rollback: bool = False,
    retain_previous: int = 0,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> Dict[str, Any]:
    """Upload release assets for ``tag`` to R2, update channel manifest, and prune old tags."""
    if channel not in ALLOWED_CHANNELS:
        raise ValueError(f"Unsupported channel {channel!r}; allowed: {ALLOWED_CHANNELS}")
    clean_tag = validate_tag(tag)
    detected = classify_release_channel(
        clean_tag,
        prerelease=prerelease,
        target_commitish=target_commitish,
    )
    if detected != channel:
        raise ValueError(
            f"Tag {clean_tag!r} (prerelease={prerelease}) resolved to {detected!r}, not {channel!r}"
        )

    retain_count = max(0, int(retain_previous or 0))
    keep_prefix = f"releases/{channel}/{clean_tag}/"
    manifest = build_channel_manifest(
        channel=channel,
        tag=clean_tag,
        release_name=release_name,
        published_at=published_at,
        prerelease=prerelease,
        public_url=settings.public_url,
        assets=assets,
        target_commitish=target_commitish,
    )

    if dry_run:
        if s3_client is not None and not allow_rollback:
            remote_manifest = _fetch_remote_manifest(s3_client, settings.bucket, channel)
            _check_r2_rollback(
                channel=channel,
                incoming_tag=clean_tag,
                incoming_published=published_at,
                remote_manifest=remote_manifest,
            )
        log(f"[DRY-RUN] Would upload {len(assets)} assets to s3://{settings.bucket}/{keep_prefix}")
        for asset in assets:
            log(f"[DRY-RUN]   PUT {asset_key(channel, clean_tag, asset.name)} ({asset.size} bytes)")
        log(f"[DRY-RUN] Would upload manifest to s3://{settings.bucket}/{manifest_key(channel)}")
        log(f"[DRY-RUN] Would prune keys under releases/{channel}/ outside {keep_prefix} (retain_previous={retain_count})")
        return {"status": "dry_run", "manifest": manifest, "pruned": []}

    remote_manifest = _fetch_remote_manifest(s3_client, settings.bucket, channel)
    if not allow_rollback:
        _check_r2_rollback(
            channel=channel,
            incoming_tag=clean_tag,
            incoming_published=published_at,
            remote_manifest=remote_manifest,
        )

    retained_tags: List[str] = []
    if retain_count > 0 and remote_manifest:
        raw_candidates: List[Any] = [remote_manifest.get("tag_name")]
        prev_list = remote_manifest.get("retained_tags")
        if isinstance(prev_list, list):
            raw_candidates.extend(prev_list)
        for cand in raw_candidates:
            cand_tag = str(cand or "").strip()
            if (
                cand_tag
                and cand_tag != clean_tag
                and SAFE_TAG_RE.match(cand_tag)
                and ".." not in cand_tag
                and cand_tag not in retained_tags
            ):
                retained_tags.append(cand_tag)
        retained_tags = retained_tags[:retain_count]
    if retained_tags:
        manifest["retained_tags"] = retained_tags

    keep_prefixes = (keep_prefix, *(f"releases/{channel}/{t}/" for t in retained_tags))

    existing_keys = _list_channel_keys(s3_client, settings.bucket, channel)
    if skip_if_unchanged:
        if _manifest_matches_assets(
            remote_manifest,
            tag=clean_tag,
            assets=assets,
            existing_keys=existing_keys,
            channel=channel,
        ):
            stale_keys = [
                k
                for k in existing_keys
                if k.startswith(f"releases/{channel}/") and not any(k.startswith(p) for p in keep_prefixes)
            ]
            if not stale_keys:
                log(f"[SKIP] R2 already up to date for {channel} {clean_tag}.")
                return {"status": "unchanged", "manifest": remote_manifest, "pruned": []}

    transfer_cfg = _make_transfer_config()

    # 1. Upload every asset under releases/<channel>/<tag>/ before updating manifest.
    for asset in assets:
        key = asset_key(channel, clean_tag, asset.name)
        log(f"[UPLOAD] {key} ({asset.size} bytes)")
        upload_kwargs: Dict[str, Any] = {
            "ExtraArgs": {
                "ContentType": guess_content_type(asset.name),
                "CacheControl": "public, max-age=300",
            }
        }
        if transfer_cfg is not None:
            upload_kwargs["Config"] = transfer_cfg
        s3_client.upload_file(str(asset.path), settings.bucket, key, **upload_kwargs)

    # 2. Flip the channel manifest pointer last so readers never see partial uploads.
    mkey = manifest_key(channel)
    log(f"[MANIFEST] Updating {mkey} -> {clean_tag}")
    s3_client.put_object(
        Bucket=settings.bucket,
        Key=mkey,
        Body=(json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
        ContentType=CONTENT_TYPES[".json"],
        CacheControl="no-cache, max-age=60",
    )

    # 3. Prune older releases under releases/<channel>/ (keeping active + retained tags).
    all_channel_keys = _list_channel_keys(s3_client, settings.bucket, channel)
    stale = [
        k
        for k in all_channel_keys
        if k.startswith(f"releases/{channel}/") and not any(k.startswith(p) for p in keep_prefixes)
    ]
    pruned: List[str] = []
    for idx in range(0, len(stale), 1000):
        batch = stale[idx : idx + 1000]
        s3_client.delete_objects(
            Bucket=settings.bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": False},
        )
        pruned.extend(batch)
    if pruned:
        log(f"[PRUNE] Deleted {len(pruned)} stale object(s) from releases/{channel}/.")
    else:
        log(f"[PRUNE] No stale objects to remove in releases/{channel}/.")

    return {"status": "updated", "manifest": manifest, "pruned": pruned}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Mirror ASSella release assets to Cloudflare R2.")
    ap.add_argument("--channel", default="stable", choices=ALLOWED_CHANNELS, help="Release channel (default: stable)")
    ap.add_argument("--tag", default="", help="Release tag (required with --dir; optional with --from-github)")
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub repo slug (default: {DEFAULT_REPO})")

    src_group = ap.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--dir", type=Path, help="Local directory containing freshly built release artifacts")
    src_group.add_argument("--from-github", action="store_true", help="Download assets from GitHub Releases API")

    ap.add_argument("--published-at", default="", help="ISO-8601 release timestamp override for --dir mode")
    ap.add_argument("--release-name", default="", help="Human-readable release title override for --dir mode")
    ap.add_argument("--target-commitish", default="", help="Optional branch name override for classification/manifest")
    ap.add_argument(
        "--skip-if-unchanged",
        action="store_true",
        help="Skip upload when R2 manifest already matches tag and asset SHA-256 checksums",
    )
    ap.add_argument(
        "--skip-wrong-channel",
        action="store_true",
        help="Exit 0 instead of raising when an explicit --tag belongs to another channel",
    )
    ap.add_argument(
        "--allow-rollback",
        action="store_true",
        help="Allow overwriting a newer R2 channel release with an older release",
    )
    ap.add_argument(
        "--retain-previous",
        type=int,
        default=0,
        help="Number of prior release tag folders to retain under releases/<channel>/ before pruning",
    )
    ap.add_argument("--dry-run", action="store_true", help="Validate inputs and print planned R2 operations only")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    settings = R2Settings.from_env(dry_run=args.dry_run)
    s3_client = None if args.dry_run else create_r2_client(settings)

    if args.dir is not None:
        tag = validate_tag(args.tag)
        published_at = args.published_at.strip() or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        release_name = args.release_name.strip() or f"ASSella {tag}"
        target_branch = args.target_commitish.strip() or None
        is_prerelease = args.channel != "stable"
        assets = collect_local_assets(args.dir, tag)
        sync_release_to_r2(
            s3_client=s3_client,
            settings=settings,
            channel=args.channel,
            tag=tag,
            release_name=release_name,
            published_at=published_at,
            prerelease=is_prerelease,
            assets=assets,
            target_commitish=target_branch,
            skip_if_unchanged=args.skip_if_unchanged,
            allow_rollback=args.allow_rollback,
            retain_previous=args.retain_previous,
            dry_run=args.dry_run,
        )
        return 0

    gh_token = os.environ.get("GITHUB_TOKEN", "")
    explicit_tag = args.tag.strip() or None
    if explicit_tag and args.skip_wrong_channel:
        clean = validate_tag(explicit_tag)
        detected = classify_release_channel(clean, prerelease=False)
        if (
            detected is not None
            and detected != args.channel
            and not (args.channel == "beta" and detected == "stable")
        ):
            print(f"[SKIP] Release {clean} belongs to channel {detected!r}, not {args.channel!r}.")
            return 0

    try:
        rel = fetch_github_release(
            repo=args.repo,
            channel=args.channel,
            tag=explicit_tag,
            token=gh_token,
        )
    except ChannelMismatchError as exc:
        if args.skip_wrong_channel:
            print(f"[SKIP] {exc}")
            return 0
        raise
    tag = validate_tag(str(rel.get("tag_name", "")))
    published_at = str(rel.get("published_at") or rel.get("created_at") or "")
    release_name = str(rel.get("name") or f"ASSella {tag}")
    prerelease = bool(rel.get("prerelease"))
    target_commitish = str(rel.get("target_commitish") or "").strip() or None

    if (
        args.skip_wrong_channel
        and classify_release_channel(tag, prerelease=prerelease, target_commitish=target_commitish) != args.channel
    ):
        print(
            f"[SKIP] Release {tag} (prerelease={prerelease}, branch={target_commitish or 'unset'}) "
            f"is not {args.channel}."
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="assella-r2-mirror-") as tmp:
        assets = download_release_assets(rel, Path(tmp), token=gh_token)
        sync_release_to_r2(
            s3_client=s3_client,
            settings=settings,
            channel=args.channel,
            tag=tag,
            release_name=release_name,
            published_at=published_at,
            prerelease=prerelease,
            assets=assets,
            target_commitish=target_commitish,
            skip_if_unchanged=args.skip_if_unchanged,
            allow_rollback=args.allow_rollback,
            retain_previous=args.retain_previous,
            dry_run=args.dry_run,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

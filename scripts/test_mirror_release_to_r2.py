#!/usr/bin/env python3
"""Unit tests for scripts/mirror_release_to_r2.py.

Exercises channel classification, checksum verification, atomic upload order,
manifest generation, and stale-tag pruning using an in-memory S3 mock (so no
boto3 installation or network access is needed).
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import mirror_release_to_r2 as m2r  # noqa: E402


class FakeS3Client:
    """Minimal in-memory mock implementing the S3 calls used by mirror_release_to_r2."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}
        self.metadata: Dict[str, Dict[str, Any]] = {}
        self.events: List[str] = []

    def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs: Dict[str, Any] = None, **kwargs: Any) -> None:
        self.objects[key] = Path(filename).read_bytes()
        self.metadata[key] = dict(ExtraArgs or {})
        self.events.append(f"upload:{key}")

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str = "",
        CacheControl: str = "",
    ) -> None:
        self.objects[Key] = Body
        self.metadata[Key] = {"ContentType": ContentType, "CacheControl": CacheControl}
        self.events.append(f"put:{Key}")

    def get_object(self, *, Bucket: str, Key: str) -> Dict[str, Any]:
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", ContinuationToken: str = None) -> Dict[str, Any]:
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {
            "Contents": [{"Key": k} for k in keys],
            "IsTruncated": False,
        }

    def delete_objects(self, *, Bucket: str, Delete: Dict[str, Any]) -> Dict[str, Any]:
        deleted = []
        for obj in Delete.get("Objects", []):
            key = obj["Key"]
            self.objects.pop(key, None)
            self.metadata.pop(key, None)
            self.events.append(f"delete:{key}")
            deleted.append({"Key": key})
        return {"Deleted": deleted}


def _write_fake_release_dir(directory: Path, tag: str) -> None:
    files = {
        "ASSella.AppImage": b"fake-appimage-binary-bytes",
        "ASSella.AppImage.zsync": b"zsync-control-file",
        f"ASSella-{tag}-linux-source.tar.gz": b"fake-tar-gz",
        f"ASSella-{tag}-linux-source.zip": b"fake-zip",
    }
    for name, data in files.items():
        (directory / name).write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        (directory / f"{name}.sha256").write_text(f"{sha}  {name}\n", encoding="utf-8")


class MirrorReleaseToR2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = m2r.R2Settings(
            endpoint="https://acc.r2.cloudflarestorage.com",
            bucket="assella-releases",
            access_key_id="ak",
            secret_access_key="sk",
            public_url="https://pub-test.r2.dev",
        )

    def test_channel_classification_and_tag_validation(self) -> None:
        self.assertEqual(m2r.classify_release_channel("v3.1.0", prerelease=False), "stable")
        self.assertEqual(m2r.classify_release_channel("v3.1.0-hotfix1", prerelease=False), "stable")
        self.assertEqual(m2r.classify_release_channel("v3.1.0", prerelease=True), "beta")
        self.assertIsNone(
            m2r.classify_release_channel("v3.1.0", prerelease=True, target_commitish="arm64")
        )
        self.assertEqual(m2r.classify_release_channel("v3.1.0-beta.1", prerelease=True), "beta")
        self.assertEqual(m2r.classify_release_channel("v3.1.0-canary.2", prerelease=True), "canary")
        self.assertNotEqual(m2r.classify_release_channel("v3.1.0-alpha.1", prerelease=False), "canary")
        self.assertEqual(m2r.classify_release_channel("v3.1.0-alpha.1", prerelease=True), "beta")
        self.assertNotEqual(m2r.classify_release_channel("v3.1.0-latest", prerelease=True), "canary")
        self.assertIsNone(m2r.classify_release_channel("v3.1.0-latest", prerelease=False))
        self.assertEqual(
            m2r.classify_release_channel("v3.0Release_candidate111026001", prerelease=True),
            "canary",
        )
        self.assertEqual(
            m2r.classify_release_channel("v3.0.0testing051026003", prerelease=True),
            "canary",
        )
        with self.assertRaises(ValueError):
            m2r.validate_tag("../etc/passwd")
        with self.assertRaises(ValueError):
            m2r.validate_tag("releases/stable")

    def test_rejects_beta_tag_for_stable_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fake_release_dir(tmp_path, "v3.1.0-beta.1")
            assets = m2r.collect_local_assets(tmp_path, "v3.1.0-beta.1")
            s3 = FakeS3Client()
            with self.assertRaises(ValueError):
                m2r.sync_release_to_r2(
                    s3_client=s3,
                    settings=self.settings,
                    channel="stable",
                    tag="v3.1.0-beta.1",
                    release_name="ASSella beta",
                    published_at="2026-11-11T00:00:00Z",
                    prerelease=False,
                    assets=assets,
                )

    def test_select_latest_stable_release_ignores_prereleases_and_drafts(self) -> None:
        releases = [
            {
                "tag_name": "v3.2.0-canary.1",
                "prerelease": True,
                "draft": False,
                "published_at": "2026-11-11T10:00:00Z",
                "assets": [{"name": "ASSella.AppImage"}],
            },
            {
                "tag_name": "v3.1.1",
                "prerelease": False,
                "draft": True,
                "published_at": "2026-11-11T09:00:00Z",
                "assets": [{"name": "ASSella.AppImage"}],
            },
            {
                "tag_name": "v3.0.5",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-10-28T09:00:00Z",
                "assets": [{"name": "ASSella.AppImage"}],
            },
            {
                "tag_name": "v3.1.0",
                "prerelease": False,
                "draft": False,
                "published_at": "2026-11-09T22:45:12Z",
                "assets": [{"name": "ASSella.AppImage"}],
            },
        ]
        selected = m2r.select_github_release(releases, channel="stable")
        self.assertEqual(selected["tag_name"], "v3.1.0")

    def test_checksum_mismatch_raises_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fake_release_dir(tmp_path, "v3.1.0")
            (tmp_path / "ASSella.AppImage.sha256").write_text("0" * 64 + "  ASSella.AppImage\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                m2r.collect_local_assets(tmp_path, "v3.1.0")

    def test_upload_manifest_order_and_stale_pruning(self) -> None:
        s3 = FakeS3Client()
        # Seed older stable release and an unrelated manifest in R2.
        s3.objects["releases/stable/v3.0.5/ASSella.AppImage"] = b"old-appimage"
        s3.objects["releases/stable/v3.0.5/ASSella-v3.0.5-linux-source.tar.gz"] = b"old-tar"
        s3.objects["releases/beta/v3.0.9-beta.1/ASSella.AppImage"] = b"other-channel-untouched"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fake_release_dir(tmp_path, "v3.1.0")
            assets = m2r.collect_local_assets(tmp_path, "v3.1.0")

            res = m2r.sync_release_to_r2(
                s3_client=s3,
                settings=self.settings,
                channel="stable",
                tag="v3.1.0",
                release_name="ASSella v3.1.0",
                published_at="2026-11-09T22:45:12Z",
                prerelease=False,
                assets=assets,
                skip_if_unchanged=True,
                log=lambda _msg: None,
            )

            self.assertEqual(res["status"], "updated")
            self.assertEqual(len(res["pruned"]), 2)

            # Uploads happened strictly before manifest put, which happened before prune deletes.
            manifest_idx = s3.events.index("put:releases/channels/stable.json")
            upload_indices = [i for i, e in enumerate(s3.events) if e.startswith("upload:")]
            delete_indices = [i for i, e in enumerate(s3.events) if e.startswith("delete:")]
            self.assertTrue(all(i < manifest_idx for i in upload_indices))
            self.assertTrue(all(i > manifest_idx for i in delete_indices))

            # Old stable tag deleted, new stable tag intact, other channels untouched.
            self.assertNotIn("releases/stable/v3.0.5/ASSella.AppImage", s3.objects)
            self.assertIn("releases/stable/v3.1.0/ASSella.AppImage", s3.objects)
            self.assertIn("releases/stable/v3.1.0/ASSella-v3.1.0-linux-source.zip", s3.objects)
            self.assertIn("releases/beta/v3.0.9-beta.1/ASSella.AppImage", s3.objects)

            manifest = json.loads(s3.objects["releases/channels/stable.json"].decode("utf-8"))
            self.assertEqual(manifest["tag_name"], "v3.1.0")
            self.assertEqual(len(manifest["assets"]), 8)
            self.assertEqual(
                manifest["assets"][0]["browser_download_url"],
                "https://pub-test.r2.dev/releases/stable/v3.1.0/ASSella.AppImage",
            )

            # Running again with --skip-if-unchanged should no-op.
            res2 = m2r.sync_release_to_r2(
                s3_client=s3,
                settings=self.settings,
                channel="stable",
                tag="v3.1.0",
                release_name="ASSella v3.1.0",
                published_at="2026-11-09T22:45:12Z",
                prerelease=False,
                assets=assets,
                skip_if_unchanged=True,
                log=lambda _msg: None,
            )
            self.assertEqual(res2["status"], "unchanged")

    def test_main_skip_wrong_channel_exits_zero(self) -> None:
        rc = m2r.main(
            [
                "--channel",
                "stable",
                "--from-github",
                "--tag",
                "v3.1.0-beta.1",
                "--skip-wrong-channel",
                "--dry-run",
            ]
        )
        self.assertEqual(rc, 0)

    def test_branch_awareness_prevents_non_main_branch_on_stable(self) -> None:
        # Non-prerelease clean tags on beta/canary (such as historical v3.0.1) are excluded
        # from both stable and beta so v2.7.0beta remains Beta's latest until v2.9.9beta.
        self.assertIsNone(
            m2r.classify_release_channel("v3.0.1", prerelease=False, target_commitish="beta")
        )
        self.assertIsNone(
            m2r.classify_release_channel("v3.1.2", prerelease=False, target_commitish="beta")
        )
        self.assertEqual(
            m2r.classify_release_channel("v2.9.9beta", prerelease=True, target_commitish="beta"),
            "beta",
        )
        self.assertEqual(
            m2r.classify_release_channel("v3.1.2", prerelease=True, target_commitish="beta"),
            "beta",
        )
        self.assertIsNone(
            m2r.classify_release_channel("v3.1.2", prerelease=False, target_commitish="refs/heads/canary")
        )
        self.assertEqual(
            m2r.classify_release_channel("v3.1.2", prerelease=True, target_commitish="refs/heads/canary"),
            "canary",
        )
        self.assertIsNone(
            m2r.classify_release_channel("v3.1.2", prerelease=False, target_commitish="arm64")
        )
        self.assertEqual(
            m2r.classify_release_channel(
                "v3.1.2",
                prerelease=False,
                target_commitish="0123456789abcdef0123456789abcdef01234567",
            ),
            "stable",
        )

        releases = [
            {
                "tag_name": "v3.0.1",
                "prerelease": False,
                "draft": False,
                "target_commitish": "beta",
                "published_at": "2026-10-22T15:04:23Z",
                "assets": [{"name": "ASSella.AppImage"}],
            },
            {
                "tag_name": "v2.9.9beta",
                "prerelease": True,
                "draft": False,
                "target_commitish": "beta",
                "published_at": "2026-11-11T15:19:00Z",
                "assets": [{"name": "ASSella.AppImage"}],
            },
            {
                "tag_name": "v3.1.0",
                "prerelease": False,
                "draft": False,
                "target_commitish": "main",
                "published_at": "2026-11-09T22:45:12Z",
                "assets": [{"name": "ASSella.AppImage"}],
            },
        ]
        self.assertEqual(m2r.select_github_release(releases, channel="stable")["tag_name"], "v3.1.0")
        self.assertEqual(m2r.select_github_release(releases, channel="beta")["tag_name"], "v2.9.9beta")

    def test_beta_channel_mirror_generates_beta_manifest(self) -> None:
        s3 = FakeS3Client()
        s3.objects["releases/stable/v3.1.0/ASSella.AppImage"] = b"stable-untouched"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_fake_release_dir(tmp_path, "v2.9.9beta")
            assets = m2r.collect_local_assets(tmp_path, "v2.9.9beta")
            res = m2r.sync_release_to_r2(
                s3_client=s3,
                settings=self.settings,
                channel="beta",
                tag="v2.9.9beta",
                release_name="ASSella v2.9.9beta",
                published_at="2026-11-11T15:00:00Z",
                prerelease=True,
                assets=assets,
                target_commitish="beta",
                skip_if_unchanged=True,
                log=lambda _msg: None,
            )
            self.assertEqual(res["status"], "updated")
            self.assertIn("releases/channels/beta.json", s3.objects)
            self.assertIn("releases/beta/v2.9.9beta/ASSella.AppImage", s3.objects)
            self.assertIn("releases/stable/v3.1.0/ASSella.AppImage", s3.objects)
            manifest = json.loads(s3.objects["releases/channels/beta.json"].decode("utf-8"))
            self.assertEqual(manifest["channel"], "beta")
            self.assertTrue(manifest["prerelease"])
            self.assertEqual(manifest["tag_name"], "v2.9.9beta")
            self.assertEqual(manifest.get("target_commitish"), "beta")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MirrorReleaseToR2Tests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)

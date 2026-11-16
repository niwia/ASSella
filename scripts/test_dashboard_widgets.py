#!/usr/bin/env python3
"""Headless Qt behavioral checks for dashboard UpdateItemWidget and RecentActivityItemWidget.

Run with:
    QT_QPA_PLATFORM=offscreen python3 scripts/test_dashboard_widgets.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())

from PyQt6.QtCore import QPoint, QSize  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from ui.dialogs.library.widgets import GameItemWidget  # noqa: E402
from ui.widgets.active_game_card import ActiveGameCard  # noqa: E402
from ui.widgets.artwork_fade import build_faded_artwork  # noqa: E402
from ui.widgets.recent_activity_item import RecentActivityItemWidget  # noqa: E402
from ui.widgets.updates_panel import UpdateItemWidget, _build_faded_artwork  # noqa: E402


_APP = None


def _ensure_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication(["test_dashboard_widgets"])
    return _APP


def _make_sample_pixmap(width=460, height=215) -> QPixmap:
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    painter = QPainter(img)
    painter.fillRect(0, 0, width // 2, height, QColor(230, 60, 140))
    painter.fillRect(width // 2, 0, width - width // 2, height, QColor(250, 210, 40))
    painter.end()
    return QPixmap.fromImage(img)


class TestBuildFadedArtwork(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_app()

    def test_smooth_s_curve_alpha_profile_has_no_hard_seam(self):
        src = _make_sample_pixmap(460, 215)
        target_size = QSize(260, 38)
        faded = _build_faded_artwork(src, target_size)
        self.assertEqual(faded.size(), target_size)

        img = faded.toImage()
        mid_y = target_size.height() // 2
        alphas = [QColor(img.pixelColor(x, mid_y)).alpha() for x in range(0, target_size.width(), 10)]

        # Left edge fades completely to transparent so there is never a hard cutoff
        self.assertEqual(QColor(img.pixelColor(0, mid_y)).alpha(), 0)
        # Middle stays partial and monotonically non-decreasing across the fade zone
        self.assertLess(alphas[1], alphas[len(alphas) // 2])
        self.assertLess(alphas[len(alphas) // 2], alphas[-1])
        # Right edge reaches high alpha but preserves smooth step deltas (no jump > 45 per 10px)
        self.assertGreaterEqual(alphas[-1], 220)
        max_step = max(alphas[i + 1] - alphas[i] for i in range(len(alphas) - 1))
        self.assertLessEqual(max_step, 45)

    def test_empty_or_null_pixmap_returns_null(self):
        self.assertTrue(_build_faded_artwork(None, QSize(100, 38)).isNull())
        self.assertTrue(_build_faded_artwork(QPixmap(), QSize(100, 38)).isNull())
        self.assertTrue(_build_faded_artwork(_make_sample_pixmap(), QSize(0, 38)).isNull())

    def test_fade_out_right_and_left_corner_rounding(self):
        src = _make_sample_pixmap(460, 215)
        faded = build_faded_artwork(
            src,
            QSize(260, 128),
            direction="fade_out_right",
            fade_start=0.28,
            fade_end=0.98,
            start_alpha=255,
            end_alpha=0,
            edge_alpha=215,
            edge_span=0.12,
            corner_radius=11.0,
            round_corners="left",
        )
        self.assertEqual(faded.size(), QSize(260, 128))
        img = faded.toImage()
        # Top-left corner (0, 0) clipped cleanly by 11px radius while inner solid region is opaque
        self.assertEqual(QColor(img.pixelColor(0, 0)).alpha(), 0)
        self.assertGreaterEqual(QColor(img.pixelColor(30, 64)).alpha(), 250)
        # Right edge fades out completely with smooth monotonic steps
        alphas = [QColor(img.pixelColor(x, 64)).alpha() for x in range(70, 260, 15)]
        self.assertEqual(QColor(img.pixelColor(258, 64)).alpha(), 0)
        self.assertGreater(alphas[0], alphas[len(alphas) // 2])
        self.assertGreater(alphas[len(alphas) // 2], alphas[-1])


class TestActiveGameCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_app()

    def test_adapts_measurements_between_wide_single_game_and_compact_queue_modes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        art_path = Path(tmp.name) / "55555.jpg"
        _make_sample_pixmap().save(str(art_path), "JPG")

        with mock.patch("utils.image_fetcher.ImageFetcher.get_cache_path", return_value=art_path):
            card = ActiveGameCard()
            card.set_game("55555", "Single Game Test", "#8AB4F8")

            # Wide single-game layout (~780px)
            card.resize(780, 120)
            card.show()
            _ensure_app().processEvents()
            card.grab()
            self.assertEqual(card._cached_fade_mode, "wide")
            self.assertIsNotNone(card._scaled_pixmap)
            wide_art_w = card._scaled_pixmap.width()
            self.assertGreaterEqual(wide_art_w, 440)  # ~58% of 780

            # Resize into compact queue side-by-side layout (~380px)
            card.resize(380, 120)
            _ensure_app().processEvents()
            card.grab()
            self.assertEqual(card._cached_fade_mode, "compact")
            self.assertIsNotNone(card._scaled_pixmap)
            compact_art_w = card._scaled_pixmap.width()
            self.assertGreaterEqual(compact_art_w, 250)  # ~68% of 380
            self.assertLess(compact_art_w, wide_art_w)
            card.close()

    def test_async_fetch_populates_uncached_artwork(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        missing_path = Path(tmp.name) / "missing.jpg"

        sample_path = Path(tmp.name) / "sample.png"
        self.assertTrue(_make_sample_pixmap().save(str(sample_path), "PNG"))
        raw_bytes = sample_path.read_bytes()

        class FakeFetcher:
            @staticmethod
            def get_cache_path(_appid):
                return missing_path

            @staticmethod
            def get_header_image_url(appid):
                return f"https://example.invalid/{appid}/header.jpg"

            def __init__(self, url, parent=None):
                self.url = url
                self._callbacks = []
                self.finished = mock.MagicMock()
                self.finished.connect.side_effect = self._callbacks.append

            def start(self):
                for cb in self._callbacks:
                    cb(raw_bytes)

        with mock.patch("utils.image_fetcher.ImageFetcher", FakeFetcher):
            card = ActiveGameCard()
            card.set_game("77777", "Uncached Game", "#8AB4F8")
            self.assertIsNotNone(card.pixmap)
            self.assertFalse(card.pixmap.isNull())


class TestGameLibraryLeftThumbnail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_app()

    def test_widened_260x128_thumbnail_has_smooth_fade_and_grayscale_support(self):
        pm = _make_sample_pixmap(460, 215)
        with mock.patch("utils.dlc_helpers.is_dlc_only_mode", return_value=False):
            card = GameItemWidget(
                {"appid": "88888", "game_name": "Library Game", "is_steam_tab": False},
                "12.3 GB",
                "#8AB4F8",
                "#121214",
                applist_2_0_enabled=True,
            )
            self.assertEqual(card.image_label.size(), QSize(260, 128))
            card.set_image(pm)
            faded = card.image_label.pixmap()
            self.assertEqual(faded.size(), QSize(260, 128))
            img = faded.toImage()
            self.assertEqual(QColor(img.pixelColor(0, 0)).alpha(), 0)
            self.assertGreaterEqual(QColor(img.pixelColor(40, 64)).alpha(), 250)
            self.assertEqual(QColor(img.pixelColor(258, 64)).alpha(), 0)

            # Steam catalogue (non-DLC) stays grayscale with the same smooth fade
            steam_card = GameItemWidget(
                {"appid": "88889", "game_name": "Catalogue Game", "is_steam_tab": True},
                "5.0 GB",
                "#8AB4F8",
                "#121214",
                applist_2_0_enabled=True,
            )
            steam_card.set_image(pm)
            sg_img = steam_card.image_label.pixmap().toImage()
            px = sg_img.pixelColor(40, 64)
            self.assertLessEqual(abs(px.red() - px.green()), 2)
            self.assertEqual(QColor(sg_img.pixelColor(258, 64)).alpha(), 0)


class TestUpdateItemWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_app()

    def test_paints_wide_faded_artwork_without_bleeding_left_edge(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        art_path = Path(tmp.name) / "12345.jpg"
        _make_sample_pixmap().save(str(art_path), "JPG")

        with mock.patch("utils.image_fetcher.ImageFetcher.get_cache_path", return_value=art_path):
            w = UpdateItemWidget("12345", "Enter the Chronosphere", "#8AB4F8")
            w.resize(400, 38)
            w.show()
            _ensure_app().processEvents()

            pm = w.grab()
            img = pm.toImage()
            # Left margin pixel (inside solid dark zone) remains dark background color
            left_px = img.pixelColor(QPoint(8, 19))
            self.assertLess(left_px.red(), 50)
            self.assertLess(left_px.green(), 50)
            # Right side shows yellow artwork strongly
            right_px = img.pixelColor(QPoint(390, 19))
            self.assertGreater(right_px.red(), 160)
            self.assertGreater(right_px.green(), 130)
            # Scaled pixmap was cached to ~65% of widget width (260px)
            self.assertIsNotNone(w._scaled_pixmap)
            self.assertGreaterEqual(w._scaled_pixmap.width(), 250)

            w.close()

    def test_badge_and_action_button_present_and_clickable(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        art_path = Path(tmp.name) / "99999.jpg"
        _make_sample_pixmap().save(str(art_path), "JPG")

        clicked = []
        with mock.patch("utils.image_fetcher.ImageFetcher.get_cache_path", return_value=art_path):
            w = UpdateItemWidget(
                "99999",
                "AT0-M Game",
                "#CE93D8",
                badge_text="AT0-M",
                action_text="Sync Keys",
                action_callback=clicked.append,
            )
            w.resize(400, 38)
            self.assertIsNotNone(w.badge)
            self.assertEqual(w.badge.text(), "AT0-M")
            self.assertIsNotNone(w.action_btn)
            w.action_btn.click()
            self.assertEqual(clicked, ["99999"])


class TestRecentActivityItemWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_app()

    def _rich_text(self, entry: dict) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        missing_art = Path(tmp.name) / "missing.jpg"
        with mock.patch("utils.image_fetcher.ImageFetcher.get_cache_path", return_value=missing_art):
            w = RecentActivityItemWidget(entry, "#8AB4F8")
            labels = w.findChildren(QLabel)
            return "\n".join(lbl.text() for lbl in labels)

    def test_hides_ach_and_drm_line_only_when_both_are_skipped(self):
        text = self._rich_text(
            {
                "appid": "100",
                "game_name": "Test Game",
                "status": "Success",
                "total_size": 1000,
                "download_size": 1000,
                "download_duration": 10,
                "ach_status": "Skipped",
                "steamless_status": "Skipped",
            }
        )
        self.assertNotIn("Ach:", text)
        self.assertNotIn("DRM:", text)

    def test_keeps_ach_and_drm_line_when_linux_native_or_non_skipped(self):
        linux_native = self._rich_text(
            {
                "appid": "101",
                "game_name": "Native Game",
                "status": "Success",
                "ach_status": "Skipped",
                "steamless_status": "Skipped (Linux Native)",
            }
        )
        self.assertIn("Ach: Skipped", linux_native)
        self.assertIn("DRM: Skipped (Linux Native)", linux_native)

        drm_clean = self._rich_text(
            {
                "appid": "102",
                "game_name": "Clean Game",
                "status": "Success",
                "ach_status": "Skipped",
                "steamless_status": "Clean",
            }
        )
        self.assertIn("Ach: Skipped • DRM: Clean", drm_clean)

        ach_generated = self._rich_text(
            {
                "appid": "103",
                "game_name": "Ach Game",
                "status": "Success",
                "ach_status": "Generated",
                "steamless_status": "Skipped",
            }
        )
        self.assertIn("Ach: Generated • DRM: Skipped", ach_generated)

    def test_patch_label_is_italicized_for_patch_updates(self):
        patch_text = self._rich_text(
            {
                "appid": "104",
                "game_name": "Patched Game",
                "status": "Success",
                "total_size": 10_000_000,
                "uncompressed_download_size": 2_000_000,
                "download_size": 1_500_000,
                "download_duration": 5,
                "ach_status": "Skipped",
                "steamless_status": "Skipped",
            }
        )
        self.assertIn("<i>patch</i>", patch_text)
        self.assertNotIn("Ach:", patch_text)

        full_text = self._rich_text(
            {
                "appid": "105",
                "game_name": "Full Game",
                "status": "Success",
                "total_size": 10_000_000,
                "uncompressed_download_size": 10_000_000,
                "download_size": 9_500_000,
                "download_duration": 5,
                "ach_status": "Skipped",
                "steamless_status": "Skipped",
            }
        )
        self.assertNotIn("patch", full_text)


if __name__ == "__main__":
    unittest.main()

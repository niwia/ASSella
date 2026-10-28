# MainWindow Refactor Plan & Dependency Upgrades

## Current State

- **File:** `src/ui/main_window.py`
- **Size:** 2867 lines, 105 methods, 173 instance attributes
- **Coupling:** 473 references from other modules (`self.main_window.*`)
- **Key dependents:** `ui_state_manager.py` (20+ direct attribute accesses), `simplified_terminal.py`, `game_details/workshop_tab.py`, `resize_handle.py`
- **Managers owned:** TaskManager, UIStateManager, JobQueueManager, GameManager, AppUpdateManager, WebServerManager

## Recent Dependency Upgrades (Q4 2026)

These upgrades have been applied and should be factored into any refactor work:

### 1. Qt 6.11.0 / PyQt6 6.11.0
- **Impact:** Native Wayland `fractional-scale-v1` and `cursor-shape-v1` protocol support
- **Benefit:** Crisp rendering at 125%, 150%, 175% scaling on docked Steam Deck
- **Required change:** Add DPI PassThrough policy to `src/main.py` before `QApplication()` init:

```python
from PyQt6.QtCore import Qt
# BEFORE app = QApplication(sys.argv):
app.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
```

- **Note:** This has NOT been applied yet - add it during refactor Phase 0.

### 2. Python Rich CLI Migration
- **Impact:** Replaced `urwid` with `rich` for terminal workflows
- **New files:** `src/ui/text_menus.py` (1010 lines of Rich-based menus)
- **Updated files:** `src/managers/cli_manager.py` (840 lines updated)
- **Benefit:** Interactive CLI for depot selection, DLC choices, SSH access without X11 forwarding

### 3. Python 3.13.18 Standalone Build
- **Impact:** Stripped CPython reduces AppImage size
- **Benefit:** Smaller downloads, slightly reduced disk footprint

## Problem

MainWindow is a god object. It owns UI construction, manager orchestration, boot sequencing, dashboard stats, progress tracking, theme state, drag-and-drop, web server bridging, resize handles, quote rotation, and update checking — all in one file. This makes it hard to test, hard to navigate, and creates deep coupling where managers reach into MainWindow's widget tree directly.

## Proposed Module Split

| Target Module | Lines (est.) | Responsibility | Extracted From |
|---|---|---|---|
| `main_window.py` | ~700 | Window shell, manager orchestration, event routing, drag/drop, resize | Remains |
| `ui/dashboard.py` | ~400 | Stats display, SLS status, Denuvo sync, Hubcap stats, quotes | `_create_drop_zone` L1173, `refresh_system_status` L1559, `_collect_system_status` L1598, `refresh_hubcap_stats` L1918, `rotate_quote` L1802 |
| `ui/progress.py` | ~350 | Download progress bar, speed label, media controls, style updates | `_create_progress_section` L1373, `_update_progress_bar_style` L1479, `update_progress_bar_style` L1447 |
| `ui/boot.py` | ~300 | Startup checks, steamdb warmup, plugin archiving, Denuvo cache, image cleanup | `__init__` L299-345 (`run_boot_checks`), `_schedule_steamdb_warmup` L895 |
| `ui/theme_proxy.py` | ~100 | Accent/background color, font, semantic color resolution | Scattered across `__init__`, `ui_state_manager.py` accesses |

## Refactoring Phases

### Phase 0: DPI PassThrough Fix (1 line, do first)
**File:** `src/main.py`
**Change:** Add before `app = QApplication(sys.argv)`:
```python
app.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
```
**Why:** Qt 6.11 supports fractional scaling natively, but default rounding causes blurry UI on Wayland with 125%/150%/175% scaling. This is a one-line fix that takes precedence over all other work.

### Phase 1: Extract Progress Section (Low risk, quick win)
- Create `ui/progress.py` with `ProgressSection(QWidget)` class
- Move `_create_progress_section()` (L1373), `_update_progress_bar_style()` (L1479), `update_progress_bar_style()` (L1447), `update_media_buttons_style()` (L1451)
- Attributes to move: `progress_container`, `progress_layout`, `progress_bar`, `speed_label`, `progress_controls_widget`, `media_pause_button`, `media_cancel_button`, `_web_status_snapshot`
- Expose signals: `pause_clicked`, `cancel_clicked`
- MainWindow holds `self.progress_section = ProgressSection()` and connects signals

### Phase 2: Extract Dashboard (Medium risk)
- Create `ui/dashboard.py` with `DashboardWidget(QWidget)` class
- Move `_create_drop_zone()` dashboard portion (L1203-1370), `refresh_system_status()` (L1559), `_collect_system_status()` (L1598), `refresh_hubcap_stats()` (L1918), `rotate_quote()` (L1802), `_refresh_quote_display()` (L1814)
- Attributes to move: `dashboard_widget`, `usage_value`, `expiry_value`, `update_all_btn`, `steam_updates_value`, `sls_lbl`, `sls_status_value`, `slssteam_lbl`, `slssteam_status_value`, `denuvo_sync_lbl`, `denuvo_sync_value`, `hubcap_api_value`, `quote_label`, `quote_timer`, `quotes` list
- StatusPager stays separate (already its own widget at `ui/status_pager.py`)
- Dashboard emits signals: `update_all_clicked`, `status_refresh_needed`

### Phase 3: Theme Proxy (Reduces coupling)
- Create `ui/theme_proxy.py` with `ThemeProxy(QObject)` class
- Properties: `accent_color`, `background_color`, `font`
- Emits `theme_changed(accent, background, font)` signal
- Replace all `self.main_window.accent_color` → `self.main_window.theme.accent_color`
- Update `ui_state_manager.py` to read/write through proxy instead of direct attribute access
- This alone eliminates ~20 cross-module attribute accesses

### Phase 4: Boot Manager (Higher risk, needs testing)
- Create `ui/boot.py` with `BootSequence` class (not a widget — pure orchestrator)
- Move `run_boot_checks()` inner function (L299-345), `_schedule_steamdb_warmup()` (L895), `_show_canary_welcome()` (L355), `_prompt_experimental_features_once_if_needed()` (L364)
- BootSequence takes `main_window` reference and `settings` as constructor args
- Runs on background thread, emits signals for main-thread UI updates
- MainWindow calls `self.boot = BootSequence(self, self.settings); self.boot.run()`

### Phase 5: Cleanup
- Remove redundant `from ui.main_window import MainWindow` type-hint imports where possible
- Update `ui_state_manager.py` to use theme proxy
- Verify no regressions in boot time or UI responsiveness

## Hard Constraints

1. **No behavior changes** — this is pure structural refactoring
2. **All Qt signals/slots must remain functional** — extracted widgets emit signals, MainWindow connects them
3. **`self.main_window` references in managers** — reduce via theme proxy, but don't eliminate entirely (managers legitimately need window context for dialogs)
4. **Drag and drop stays in MainWindow** — `dragEnterEvent`/`dropEvent` are tightly coupled to window geometry
5. **Manager initialization stays in MainWindow** — `_initialize_managers()` (line 553) is the composition root
6. **Rich CLI must continue working** — do not break `ui/text_menus.py` or `cli_manager.py` during refactor

## Success Metrics

- `main_window.py` under 800 lines
- Zero `self.main_window.accent_color` style accesses outside MainWindow
- Each extracted module independently importable without triggering full UI stack
- No regression in boot time or UI responsiveness
- DPI PassThrough policy applied (fractional scaling works on Wayland)

## Dependencies to Consider

- `ui/widgets/simplified_terminal.py` - type-hint import of MainWindow
- `ui/widgets/resize_handle.py` - type-hint import of MainWindow
- `ui/dialogs/game_details/workshop_tab.py` - type-hint import of MainWindow
- `managers/ui_state_manager.py` - 20+ direct attribute accesses
- `managers/cli_manager.py` - uses Rich CLI, ensure compatibility
- `ui/text_menus.py` - Rich-based menus, should not be affected by MainWindow refactor

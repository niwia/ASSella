## Description

<!-- Provide a clear, concise summary of the changes in this pull request. -->

Fixes # <!-- Issue number, if applicable (e.g. Fixes #123) -->

## Target Branch

- [ ] `canary` (Default for experimental features, major refactors, and bleeding-edge changes)
- [ ] `beta` (Preview fixes and staging for upcoming releases)
- [ ] `stable` (Critical cherry-picked hotfixes only)

## Type of Change

- [ ] 🐛 Bug fix (non-breaking change fixing an issue)
- [ ] ✨ New feature (non-breaking change adding functionality)
- [ ] ⚡ Performance optimization (startup speed, UI responsiveness, lazy loading)
- [ ] ♻️ Code refactoring / Cleanup
- [ ] 📝 Documentation update

## ASSella Architectural & Invariant Checklist

Please verify that your changes adhere to ASSella's core development invariants:

- [ ] **No Hardcoded Home Paths**: All path operations use `os.path.expanduser("~")` or `Path.home()`. Never hardcodes `/home/deck/` or `/home/<user>/`.
- [ ] **Strict No-ACF Invariant**: In ACF-Independent mode, manifest creation is delegated to Steam via SLSsteam (`install|<appid>|<lib_idx>`). The code never writes or generates `.acf` files.
- [ ] **SLSsteam Inode Preservation**: Modifications to SLSsteam `config.yaml` are written in-place (or via `_atomic_write` / `BatchConfigEditor`) to preserve the system inode and protect `inotify` watchers.
- [ ] **Decryption Keys & AdditionalDepots Rules**:
  - [ ] 64-hex decryption keys are never blocked or sanitized in `DecryptionKeys`.
  - [ ] Base AppIDs never enter `AdditionalDepots` (only content depot IDs belong there).
  - [ ] DLC-only games never add base AppIDs to `AdditionalApps`.
- [ ] **GUI Thread Performance**:
  - [ ] No slow I/O, subprocess calls, network requests, or heavy SQLite queries on the Qt GUI thread.
  - [ ] Heavy dependencies (`curl_cffi`, `bs4`, scrapers) are imported lazily where used, not at top-level.
  - [ ] Game Details opens immediately; artwork/metadata fetches occur asynchronously.

## Testing & Verification

- [ ] Tested locally on Linux / Steam Deck environment.
- [ ] Verified startup time remains instant (`python -X importtime src/main.py`).
- [ ] Checked for any UI freezing or regression during library operations.

## AI-Assisted Contribution Disclosure

If this contribution was authored or assisted with AI tools (Gemini, Claude, Copilot, ChatGPT, etc.):

- [ ] I have personally reviewed, understood, and tested every line of the submitted code.
- [ ] I take full human responsibility for the correctness and maintainability of these changes.
- [ ] The AI agent was informed of and followed repository rules (`.agents/AGENTS.md`).
- [ ] No editor configuration files, AI conversation logs, or scratch artifacts are included in this PR.

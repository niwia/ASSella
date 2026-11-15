# Contributing to ASSella

Thank you for your interest in contributing to ASSella! This document outlines the branch structure, local environment setup, architectural invariants, and contribution standards—including our policy on AI-assisted development.

---

## 1. Branching Strategy & Workflow

ASSella operates on a 4-branch model:

* **`canary` (Bleeding Edge)**: Active development for upcoming features, major refactorings, and experimental additions. **Most new PRs should target this branch.**
* **`beta` (Staging & Preview)**: Stabilization branch for upcoming release candidates. Receives targeted bug fixes and polish.
* **`stable` (Production Releases)**: The current production release. Only critical security or blocker hotfixes are cherry-picked here.
* **`arm64`**: Automated port branch kept in sync with upstream changes.

---

## 2. Local Environment Setup

### Prerequisites

- Linux (SteamOS, Arch, Bazzite, CachyOS, Fedora, Ubuntu, NixOS, etc.)
- Python 3.10+
- Qt6 / PySide6 dependencies (`xcb-util-cursor`, `libnotify`, `p7zip`)

### Setting Up a Development Clone

```bash
# 1. Clone the repository (check out the canary branch for new features)
git clone -b canary https://github.com/niwia/ASSella.git
cd ASSella

# 2. Create and activate a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install development dependencies
pip install -r requirements.txt

# 4. Launch ASSella from source
python src/main.py
```

---

## 3. Core Architectural Rules & Invariants

ASSella is built for speed and reliability on the Steam Deck and desktop Linux. All contributions must respect these invariants:

### A. Performance & UI Responsiveness
- **Fast Startup**: ASSella launches its main window in under one second. Never perform disk scans, network requests, or subprocess calls on the Qt GUI thread.
- **Lazy Imports**: Heavy modules (e.g. `curl_cffi`, `bs4`, scrapers, API clients) must be imported inside worker functions where they are used, never at the top level of UI modules.
- **Background Workers**: Run long-running tasks via `QThread`, `TaskRunner`, or background workers, posting results back via Qt signals or `utils.main_thread.post_to_main_thread`.

### B. Steam & SLSsteam Integration Invariants
- **No Hardcoded Home Paths**: Always use `Path.home()` or `os.path.expanduser("~")`. Never hardcode `/home/deck/`.
- **Strict No-ACF Invariant**: In ACF-Independent mode, manifest creation is natively handled by Steam (`install|<appid>|<lib_idx>`). The codebase must **never** generate or write `.acf` manifest files.
- **SLSsteam Inode Preservation**: Updates to `~/.config/SLSsteam/config.yaml` must be written in-place (or via `BatchConfigEditor` / `_atomic_write`) to preserve the file inode and prevent breaking SLSsteam's `inotify` file watcher.
- **Depot & AppID Rules**:
  - Base AppIDs must never be added to `AdditionalDepots` (only content depot IDs belong there).
  - Valid 64-hex keys in `DecryptionKeys` must never be stripped or sanitized.
  - DLC-only games must only have DLC AppIDs added to `AdditionalApps` (never the base AppID).

### C. Lightweight Repository
- Heavy binary tools (Goldberg, Steamless, SLScheevo under `src/deps/`) are dynamically fetched on demand from R2 and must **never** be committed to git.

---

## 4. AI-Assisted Contributions Policy

ASSella has been developed in close collaboration with AI agents. We welcome AI-assisted contributions (using Gemini, Claude, Copilot, ChatGPT, etc.), subject to the following rules:

1. **Human Accountability**:
   - You, the human contributor, are 100% responsible for the code you submit.
   - Never submit code you do not fully understand. You must be able to explain the architecture, reasoning, and edge-case handling in review.
2. **Context & Invariant Alignment**:
   - Before prompting an AI agent to edit ASSella, provide it with the repository rules in [`.agents/AGENTS.md`](file:///.agents/AGENTS.md).
   - AI tools frequently hallucinate non-existent Qt methods or attempt to generate `.acf` files; you must verify that the AI has followed the repository invariants.
3. **Workspace Hygiene**:
   - Do **NOT** commit AI conversation logs, assistant instructions, transcripts (`.gemini/`, `.claude/`), or local scratch scripts to git.
   - Clean up untracked temporary files before pushing.

---

## 5. Submitting a Pull Request

1. **Create a Topic Branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. **Commit Your Changes**:
   - Write clear, imperative commit messages (e.g. `Fix DLC depot mapping target reset`).
3. **Verify Locally**:
   - Confirm that the application boots without delay and logs no syntax warnings:
     ```bash
     python -X importtime src/main.py
     ```
4. **Open a PR**:
   - Target the appropriate branch (`canary` or `beta`).
   - Fill out the [Pull Request Template](file:///.github/PULL_REQUEST_TEMPLATE.md) completely, verifying all invariant and AI-disclosure checkboxes.

Thank you for helping make ASSella even better!

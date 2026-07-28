# Workspace Customization Rules for ASSella

Whenever the user requests a software update, packaging, or release to GitHub, you MUST automatically apply the following rules:

## 1. Incremental Versioning
- Automatically calculate the next version string.
- For small updates or hotfixes with very little changes, increment only the pre-release number (e.g., `2.2.4-rc2` -> `2.2.4-rc3`) or the patch version (e.g., `2.2.3` -> `2.2.4`).
- Write the updated version string locally to [version](file:///home/deck/Projects/ASSella/src/res/version) before building.

## 2. Changelogs & Release Notes
- Draft clean, professional release notes listing only the changes introduced in the current version.
- **Strictly DO NOT use any emojis** in the changelog text.
- Do not repeat or include items from older releases or changelogs. Keep the focus entirely on the current build/hotfix.



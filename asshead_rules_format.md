# ASShead Config Validation Rules Format (ASSella Integration)

This document explains the format of the `asshead_rules.json` file which is hosted in the **ASSfixer** GitHub repository:
`https://github.com/niwia/ASSfixer`

ASSella fetches this file dynamically on boot to dynamically update its validation rules for the SLSsteam `config.yaml` file without requiring a tool rebuild or update.

---

## 1. File Naming & Location
Host this file at the root of the `ASSfixer` repository under the name:
`asshead_rules.json`

So that the raw URL resolves to:
`https://raw.githubusercontent.com/niwia/ASSfixer/main/asshead_rules.json`

---

## 2. Structure & Keys

The file must be a valid JSON object containing the following keys (all values must be arrays of strings representing YAML config key names):

| Key | Description | Example |
|---|---|---|
| `SCALAR_KEYS` | Config options that hold a single value (string, number, boolean) | `"FakeEmail"`, `"LogLevel"`, `"DisableFamilyShareLock"` |
| `BOOLEAN_KEYS` | Options from `SCALAR_KEYS` that are strictly parsed as booleans (`true`/`false`) | `"SafeMode"`, `"Notifications"`, `"DisableCloud"` |
| `LIST_KEYS` | Config options that contain list items (using `-` format) | `"AppIds"`, `"AdditionalApps"`, `"FakeOffline"` |
| `MAP_KEYS` | Options that contain key-value mappings (dictionary format) | `"AppTokens"`, `"FakeAppIds"`, `"GameTitles"` |
| `MAP_OF_LIST_KEYS` | Options that map keys to a list of sub-items | `"DenuvoGames"` |

### JSON Example:
```json
{
  "SCALAR_KEYS": [
    "DisableFamilyShareLock",
    "UseWhitelist",
    "AutoFilterList",
    "PlayNotOwnedGames",
    "SafeMode",
    "Notifications",
    "WarnHashMissmatch",
    "NotifyInit",
    "API",
    "DisableCloud",
    "FakeEmail",
    "FakeWalletBalance",
    "LogLevel",
    "ExtendedLogging",
    "MaxSchemaTries",
    "DisableUpdates"
  ],
  "BOOLEAN_KEYS": [
    "DisableFamilyShareLock",
    "UseWhitelist",
    "AutoFilterList",
    "PlayNotOwnedGames",
    "SafeMode",
    "Notifications",
    "WarnHashMissmatch",
    "NotifyInit",
    "API",
    "DisableCloud",
    "ExtendedLogging",
    "MaxSchemaTries",
    "DisableUpdates"
  ],
  "LIST_KEYS": [
    "AppIds",
    "AdditionalApps",
    "FakeOffline",
    "DepotBlacklist"
  ],
  "MAP_KEYS": [
    "AppTokens",
    "FakeAppIds",
    "GameTitles",
    "SubscriptionTimestamps",
    "DlcData",
    "ManifestIds"
  ],
  "MAP_OF_LIST_KEYS": [
    "DenuvoGames"
  ]
}
```

---

## 3. How to Update
When a new configuration option is added upstream in SLSsteam (e.g. inside `src/config_default.hpp` / `config.yaml`):
1. Determine the key type (e.g. scalar, list, map).
2. Append the new key to the appropriate array in `asshead_rules.json`.
3. Commit and push the updated `asshead_rules.json` to the main branch of `ASSfixer`.
4. ASSella will automatically use the updated validation list on its next boot!

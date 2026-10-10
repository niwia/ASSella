#!/usr/bin/env python3
"""Test the shared depot-selection resolver used by every download path.

`_resolve_depot_selection` on LibraryActionsMixin is the single place that
decides whether the user gets asked which depots to install. It used to be two
near-identical copies, one of which silently reused the cached selection during
a verify, so "Verify" appeared to do nothing.

Hermetic: settings are stubbed in-process, so nothing touches QSettings, the
network, or any user data.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_depot_selection_resolve.py
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []
PASSED = 0


def check(cond: bool, label: str) -> bool:
    global PASSED
    if cond:
        PASSED += 1
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return bool(cond)


class _StubSettings:
    """Stands in for QSettings with just the two keys the resolver reads."""

    def __init__(self, values: dict):
        self._values = values

    def value(self, key, default=None, type=None):
        return self._values.get(key, default)


def _install_stub_settings(values: dict):
    """Point utils.settings.get_settings at a stub for the duration of a call.

    The resolver imports get_settings inside the function body, so patching the
    module attribute is enough.
    """
    import utils.settings as settings_mod

    stub = _StubSettings(values)
    original = settings_mod.get_settings
    settings_mod.get_settings = lambda: stub
    return original


def _restore_settings(original) -> None:
    import utils.settings as settings_mod

    settings_mod.get_settings = original


def _resolve(mixin, depots, *, cache=None, smart=True, auto_skip=False,
             installed=None, is_verify=False, parsed_extra=None):
    """Call the resolver with stubbed settings and return its DepotSelection."""
    values = {"smart_depot_selection": smart, "auto_skip_single_choice": auto_skip}
    if cache is not None:
        values["depot_selection/2185060"] = cache
    original = _install_stub_settings(values)
    try:
        parsed = {"appid": "2185060", "depots": depots}
        parsed.update(parsed_extra or {})
        return mixin._resolve_depot_selection(
            appid="2185060",
            depots=depots,
            parsed_data=parsed,
            installed_depots=installed,
            is_verify=is_verify,
        )
    finally:
        _restore_settings(original)


def _cache_of(selected, all_available):
    return json.dumps({"selected": selected, "all_available": all_available})


def _fn_body(src: str, name: str) -> str:
    """Return the source of a method, from its def to the next member.

    Slicing by index alone is unsafe here: members are not defined in
    alphabetical order, so a naive src.index(a) < src.index(b) assumption
    silently yields an empty (and therefore always-passing) slice.
    """
    import re

    start = re.search(rf"^    def {re.escape(name)}\(", src, re.M)
    if not start:
        return ""
    nxt = re.search(r"^    (?:def |@|class )", src[start.end():], re.M)
    return src[start.start(): start.end() + nxt.start()] if nxt else src[start.start():]


def main() -> int:
    from ui.dialogs.library.actions import LibraryActionsMixin

    # The resolver touches no instance state, so a bare instance is enough.
    mixin = LibraryActionsMixin.__new__(LibraryActionsMixin)

    depots = {"1": {}, "2": {}, "3": {}}

    # ------------------------------------------------- no cached selection
    print("\n=== no cached selection: always ask ===")
    r = _resolve(mixin, depots)
    check(r.should_prompt is True, "no cache -> should_prompt")
    check(r.selected_depots is None, "no cache -> nothing preselected")
    check(r.cached_selected is None, "no cache -> no dialog preselection")
    check(r.auto_skip is False, "no cache -> no auto-skip with 3 depots")

    # ---------------------------------------------- smart cache reuse
    print("\n=== smart selection reuses a valid cache ===")
    r = _resolve(mixin, depots, cache=_cache_of(["1", "3"], ["1", "2", "3"]))
    check(r.should_prompt is False, "valid cache + no new depots -> do not prompt")
    check(r.selected_depots == ["1", "3"], "cached depots reused verbatim")
    check(r.cached_selected == ["1", "3"], "cached list handed to the dialog")

    print("\n=== a depot that was not in the snapshot forces a prompt ===")
    r = _resolve(mixin, depots, cache=_cache_of(["1", "3"], ["1", "3"]))
    check(r.should_prompt is True, "depots appeared since last choice -> prompt")
    check(r.selected_depots is None, "no silent partial reuse on a new depot")

    print("\n=== smart selection off ignores the cache ===")
    r = _resolve(mixin, depots, cache=_cache_of(["1"], ["1", "2", "3"]), smart=False)
    check(r.should_prompt is True, "smart off -> prompt even with a valid cache")

    # ------------------------------------- filtering against what is fetchable
    print("\n=== stale cached depots are filtered out ===")
    r = _resolve(mixin, depots, cache=_cache_of(["1", "99"], ["1", "99", "2", "3"]))
    check(r.selected_depots == ["1"], "cached depot 99 is not fetchable -> dropped")

    print("\n=== recovered DLC depots survive the filter ===")
    # The bug: filtering against parsed depots alone dropped depots Hubcap was
    # missing but we recovered, so they vanished from a reused selection.
    r = _resolve(
        mixin,
        depots,
        cache=_cache_of(["1", "77"], ["1", "77", "2", "3"]),
        parsed_extra={"missing_depots_info": {"77": {"name": "dlc"}}},
    )
    check(r.should_prompt is False, "recovered DLC does not force a prompt")
    check(r.selected_depots == ["1", "77"],
          "depot 77 kept because it is in missing_depots_info")

    # ------------------------------------------------------ verify forces UI
    print("\n=== verify always prompts, never reuses the cache ===")
    r = _resolve(mixin, depots, cache=_cache_of(["1", "3"], ["1", "2", "3"]), is_verify=True)
    check(r.should_prompt is True, "verify ignores an otherwise valid cache")
    check(r.selected_depots is None, "verify preselects nothing implicitly")

    # ----------------------------------------------------------- auto-skip
    print("\n=== auto-skip with a single depot ===")
    single = {"1": {}}
    r = _resolve(mixin, single, auto_skip=True)
    check(r.auto_skip is True, "single depot + auto-skip -> skip the dialog")
    check(r.selected_depots == ["1"], "single depot auto-selected")
    check(r.should_prompt is True, "should_prompt stays True so callers still cache the choice")

    print("\n=== auto-skip never applies to a verify ===")
    r = _resolve(mixin, single, auto_skip=True, is_verify=True)
    check(r.auto_skip is False, "verify is never auto-skipped")
    check(r.selected_depots is None, "verify with one depot still asks")

    print("\n=== auto-skip off leaves a single depot to the dialog ===")
    r = _resolve(mixin, single, auto_skip=False)
    check(r.auto_skip is False, "auto-skip disabled -> no skip")
    check(r.should_prompt is True, "single depot still prompts when auto-skip is off")

    # --------------------------------------------------- installed fallback
    print("\n=== installed ACF depots seed the dialog preselection ===")
    r = _resolve(mixin, depots, installed=[1, 3])
    check(r.cached_selected == ["1", "3"], "installed depots normalised to strings")
    check(r.selected_depots is None, "installed depots are preselection, not a decision")
    check(r.should_prompt is True, "installed depots do not skip the prompt")

    print("\n=== a real cache wins over the installed ACF ===")
    r = _resolve(mixin, depots, cache=_cache_of(["2"], ["1", "2", "3"]), installed=[1, 3])
    check(r.cached_selected == ["2"], "cached choice takes precedence")

    # --------------------------------------------------------- bad input
    print("\n=== corrupt cache does not raise ===")
    r = _resolve(mixin, depots, cache="{not json")
    check(r.should_prompt is True, "unparseable cache falls back to prompting")
    check(r.selected_depots is None, "unparseable cache selects nothing")

    print("\n=== missing_depots_info of None does not raise ===")
    r = _resolve(mixin, depots, cache=_cache_of(["1"], ["1", "2", "3"]),
                 parsed_extra={"missing_depots_info": None})
    check(r.selected_depots == ["1"], "None missing_depots_info handled")

    # ------------------------------------------- callers stay wired up
    print("\n=== both callers delegate, and the batch path forwards verify ===")
    src = (SRC_DIR / "ui/dialogs/library/actions.py").read_text()

    check(src.count("self._resolve_depot_selection(") == 2,
          "resolver is defined once and called from exactly two places")

    # The regression: the batch queue used to decide "should I ask?" with its
    # own copy of the logic that ignored is_verify. Guard the wiring so it
    # cannot silently drift back.
    batch = _fn_body(src, "_enqueue_single_game")
    check('is_verify=bool(game_data.get("_is_verify"))' in batch,
          "batch queue forwards _is_verify into the shared resolver")
    check(batch.strip() != "", "batch queue slice located")
    check("has_new_depot" not in batch,
          "batch queue no longer keeps its own copy of the cache decision")
    check("smart_depot_selection" not in batch,
          "batch queue no longer reads smart_depot_selection directly")

    complete = _fn_body(src, "_on_zip_parse_complete")
    check(complete.strip() != "", "_on_zip_parse_complete slice located")
    check("has_new_depot" not in complete,
          "_on_zip_parse_complete no longer keeps its own copy")
    check("smart_depot_selection" not in complete,
          "_on_zip_parse_complete no longer reads smart_depot_selection directly")
    check("is_verify=is_verify" in complete,
          "_on_zip_parse_complete forwards its verify flag")

    # The resolver must be the only place either decision is written.
    resolver = _fn_body(src, "_resolve_depot_selection")
    check("smart_depot_selection" in resolver,
          "resolver is where smart_depot_selection is read")
    check("has_new_depot" in resolver,
          "resolver is where the new-depot check lives")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL DEPOT SELECTION RESOLVE TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Behavioural checks for the edition catalogue.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_edition_catalog.py

The store and app-info endpoints are replaced with canned answers, so this
suite never touches the network. The cache is redirected to a temp directory.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import utils.edition_catalog as ec  # noqa: E402

_failures: list[str] = []
PASSED = 0


def check(condition: bool, label: str) -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        _failures.append(label)
        print(f"  [FAIL] {label}")


# ------------------------------------------------------------------- mocks

APPINFO = {
    "status": "success",
    "data": {
        "703080": {
            "common": {"oslist": "windows", "name": "Planet Zoo"},
            "extended": {"listofdlc": "1471590, 2675740, 2837730"},
        }
    },
}

# GetItems response for appid 703080: packages + bundles
GETITEMS_APP_703080 = {
    "response": {
        "store_items": [{
            "appid": 703080,
            "name": "Planet Zoo",
            "purchase_options": [
                {"packageid": 13930, "purchase_option_name": "Planet Zoo"},
                {"bundleid": 13931, "purchase_option_name": "Premium Edition"},
                {"bundleid": 22933, "purchase_option_name": "Deluxe Bundle"},
            ],
        }]
    }
}

# GetItems response for bundle 13931 (Premium Edition - valid)
GETITEMS_BUNDLE_13931 = {
    "response": {
        "store_items": [{
            "id": 13931,
            "name": "Planet Zoo: Premium Edition",
            "included_appids": [703080, 1471590],
        }]
    }
}

# GetItems response for bundle 22933 (delisted - empty)
GETITEMS_BUNDLE_22933 = {
    "response": {
        "store_items": []
    }
}

# GetItems response for package-only app (no bundles)
GETITEMS_APP_NO_BUNDLES = {
    "response": {
        "store_items": [{
            "appid": 555,
            "name": "Probe Game",
            "purchase_options": [
                {"packageid": 1001, "purchase_option_name": "Probe Game - $4.99 $3.74", "final_price_in_cents": 374},
                {"packageid": 1002, "purchase_option_name": "Probe Deluxe", "final_price_in_cents": 999},
            ],
        }]
    }
}


def main() -> int:
    print("=== titles come out of the page, not the discount text ===")
    check(
        ec._clean_title("Save 60% on Darkest Dungeon&#39;s Edition on Steam")
        == "Darkest Dungeon's Edition",
        "a discount title loses its prefix, entities and the Steam suffix",
    )
    check(
        ec._clean_title("Planet Zoo: Premium Edition\t") == "Planet Zoo: Premium Edition",
        "whitespace is collapsed",
    )
    check(ec._clean_title("") == "", "an empty title stays empty")

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "catalog.json"
        ec._cache_file = lambda: cache  # type: ignore[assignment]

        calls: list[str] = []

        def fake_get_json(url: str, timeout: float = 20.0) -> dict | None:
            calls.append(url)
            if "steamcmd" in url:
                return APPINFO
            if "GetItems" in url:
                # URL is percent-encoded; decode the input_json parameter
                _, _, rest = url.partition("input_json=")
                body = json.loads(urllib.parse.unquote(rest)) if rest else {}
                ids = body.get("ids", [])
                # Check if it's an appid query
                bundle_items = []
                for item_id in ids:
                    if "appid" in item_id:
                        aid = item_id["appid"]
                        if aid == 703080:
                            return GETITEMS_APP_703080
                        if aid == 555:
                            return GETITEMS_APP_NO_BUNDLES
                    elif "bundleid" in item_id:
                        bid = item_id["bundleid"]
                        if bid == 13931:
                            bundle_items.extend(GETITEMS_BUNDLE_13931["response"]["store_items"])
                        elif bid == 22933:
                            bundle_items.extend(GETITEMS_BUNDLE_22933["response"]["store_items"])
                if bundle_items:
                    return {"response": {"store_items": bundle_items}}
                if any("bundleid" in item_id for item_id in ids):
                    return {"response": {"store_items": []}}
            return None

        ec._get_json = fake_get_json  # type: ignore[assignment]

        print("=== a bundle gives its name and the apps it carries ===")
        bundle = ec.fetch_bundle(13931)
        check(bundle is not None, "the bundle answers")
        assert bundle is not None
        check(bundle["name"] == "Planet Zoo: Premium Edition", "the name comes from JSON")
        check(bundle["app_ids"] == ["703080", "1471590"], "included_appids are returned")
        check(ec.fetch_bundle(22933) is None, "a delisted bundle returns None")

        print("=== a cached bundle needs no network ===")
        before = len(calls)
        again = ec.fetch_bundle(13931)
        check(again is not None and again["name"] == bundle["name"], "the cache answers the name")
        check(len(calls) == before, "nothing was fetched a second time")
        check(ec.fetch_bundle(13931, allow_network=False) is not None, "offline reads the cache")

        print("=== bundle ids are read from GetItems purchase_options ===")
        ids = ec.discover_bundle_ids("703080")
        check(ids == [13931, 22933], "the ids come from purchase_options without duplicates")
        check(cache.exists(), "the catalogue is written to disk")

        print("=== app facts give the platforms and the DLC app ids ===")
        facts = ec.fetch_app_facts("703080")
        check(facts is not None, "the app info answers")
        assert facts is not None
        check(facts["platforms"] == ["windows"], "the oslist is read")
        check(
            facts["dlc_appids"] == ["1471590", "2675740", "2837730"],
            "the comma separated listofdlc is split",
        )
        check(facts["name"] == "Planet Zoo", "the app's own name comes with the facts")
        check(ec.get_app_name("703080") == "Planet Zoo", "the name is readable on its own")
        check(
            ec.get_supported_platforms("703080") == {"windows"},
            "the platform set is exposed",
        )

        print("=== only bundles made of this game's DLC become editions ===")
        editions = ec.bundle_editions("703080", facts["dlc_appids"])
        check(len(editions) == 1, "the delisted bundle is skipped")
        check(editions[0]["id"] == "bundle:13931", "the edition id carries the bundle id")
        check(editions[0]["name"] == "Planet Zoo: Premium Edition", "the edition keeps the name")
        check(editions[0]["source"] == "bundle", "the edition says where it came from")

        crossover = {"id": 900, "name": "Unrelated Crossover", "app_ids": ["703080", "999"]}
        saved = ec.fetch_bundle
        ec.fetch_bundle = lambda bundle_id, **kwargs: crossover if int(bundle_id) == 900 else saved(bundle_id, **kwargs)  # type: ignore[assignment]
        ec.discover_bundle_ids = lambda appid, **kwargs: [900]  # type: ignore[assignment]
        check(
            ec.bundle_editions("703080", facts["dlc_appids"]) == [],
            "a bundle carrying an app outside listofdlc is not an edition",
        )

        # An edition that also ships an upgrade or soundtrack pack is still this
        # game's edition as long as its DLC apps outnumber the outsiders.
        mixed = {"id": 901, "name": "Planet Zoo: Deluxe Plus", "app_ids": ["703080", "1471590", "2675740", "999"]}
        ec.fetch_bundle = lambda bundle_id, **kwargs: mixed if int(bundle_id) == 901 else saved(bundle_id, **kwargs)  # type: ignore[assignment]
        ec.discover_bundle_ids = lambda appid, **kwargs: [901]  # type: ignore[assignment]
        kept = ec.bundle_editions("703080", facts["dlc_appids"])
        check(len(kept) == 1 and kept[0]["id"] == "bundle:901",
              "a bundle with one outsider and two DLC apps is still an edition")
        ec.fetch_bundle = saved  # type: ignore[assignment]

        print("=== a stale entry is fetched again, a fresh one is not ===")
        data = json.loads(cache.read_text(encoding="utf-8"))
        data["bundle_data"]["13931"]["ts"] = 0
        cache.write_text(json.dumps(data), encoding="utf-8")
        before = len(calls)
        ec.fetch_bundle(13931)
        check(len(calls) == before + 1, "an expired entry goes back to the store")

        print("=== store packages are cached on disk and named from option_text ===")
        check(
            ec.clean_package_name("Vampire Survivors - $4.99 $3.74") == "Vampire Survivors",
            "two appended prices are both dropped",
        )
        check(
            ec.clean_package_name("Darkest Dungeon - $24.99") == "Darkest Dungeon",
            "one appended price is dropped",
        )

        first = ec.fetch_store_packages("555")
        check(len(first) == 2, "both packages come back")
        check(first[0]["name"] == "Probe Game", "the package purchase_option_name is cleaned")
        check(first[0]["price"] == 374, "the discounted price in cents is kept")

        data = json.loads(cache.read_text(encoding="utf-8"))
        check(
            data["packages"]["555"]["packages"][0]["packageid"] == 1001,
            "the answer is written to the packages bucket",
        )
        before = len(calls)
        again = ec.fetch_store_packages("555")
        check(again == first and len(calls) == before, "a fresh answer never touches the network")

        data["packages"]["555"]["ts"] = 0
        cache.write_text(json.dumps(data), encoding="utf-8")
        before = len(calls)
        ec.fetch_store_packages("555")
        check(len(calls) == before + 1, "an expired package answer goes back to the store")

        ec._get_json = lambda url, timeout=20.0: None  # type: ignore[assignment]
        check(
            ec.fetch_store_packages("999", allow_network=False) == [],
            "the network is never asked when it is disabled",
        )
        ec._get_json = fake_get_json  # type: ignore[assignment]

        print("=== single round-trip populates both packages and bundle ids ===")
        before = len(calls)
        pkgs_703080 = ec.fetch_store_packages("703080")
        check(len(calls) == before, "packages for 703080 were already cached by discover_bundle_ids")
        check(len(pkgs_703080) == 1 and pkgs_703080[0]["packageid"] == 13930, "package was cached correctly")

        print("=== batched bundle fetching resolves multiple bundles in one call ===")
        data = json.loads(cache.read_text(encoding="utf-8"))
        data["bundle_data"] = {}
        cache.write_text(json.dumps(data), encoding="utf-8")
        before = len(calls)
        batch = ec.fetch_bundles_batch([13931, 22933])
        check(len(calls) == before + 1, "all missing bundles are fetched in exactly one network call")
        check(len(batch) == 2, "both requested bundles have entries in result")
        check(batch[0] is not None and batch[0]["id"] == 13931, "valid bundle is returned")
        check(batch[1] is None, "delisted bundle returns None")

        print("=== an unreadable cache starts empty ===")
        cache.write_text("{not json", encoding="utf-8")
        check(ec._read_cache()["bundles"] == {}, "a broken cache is ignored")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

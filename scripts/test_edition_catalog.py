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


BUNDLE_HTML = """
<html><head><title>Save 60% on Planet Zoo: Premium Edition on Steam</title></head>
<body>
<div class="pageheader">Planet Zoo: Premium Edition</div>
<a href="https://store.steampowered.com/app/703080/Planet_Zoo/"><img></a>
<a href="https://store.steampowered.com/app/1471590/Deluxe/"><img></a>
<a href="https://store.steampowered.com/app/1471590/Deluxe/"><img></a>
<div class="tab_item_name">Planet Zoo</div>
</body></html>
"""

APP_HTML = """
<html><body>
<a class="btn" href="https://store.steampowered.com/bundle/13931/?snr=1_5_9__603">Bundle info</a>
<a class="btn" href="https://store.steampowered.com/bundle/22933/">Bundle info</a>
<a class="btn" href="https://store.steampowered.com/bundle/13931/">again</a>
<div class="game_area_purchase">...</div>
</body></html>
"""

FRONT_PAGE_HTML = "<html><title>Welcome to Steam</title><body>Welcome to Steam</body></html>"

APPINFO = {
    "status": "success",
    "data": {
        "703080": {
            "common": {"oslist": "windows", "name": "Planet Zoo"},
            "extended": {"listofdlc": "1471590, 2675740, 2837730"},
        }
    },
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

        def fake_get(url: str, timeout: float) -> str | None:
            calls.append(url)
            if "/bundle/13931/" in url:
                return BUNDLE_HTML
            if "/bundle/22933/" in url:
                return FRONT_PAGE_HTML
            if "/app/703080/" in url:
                return APP_HTML
            if "steamcmd" in url:
                return json.dumps(APPINFO)
            return None

        ec._store_get = fake_get  # type: ignore[assignment]

        print("=== a bundle page gives its name and the apps it carries ===")
        bundle = ec.fetch_bundle(13931)
        check(bundle is not None, "the bundle answers")
        assert bundle is not None
        check(bundle["name"] == "Planet Zoo: Premium Edition", "the pageheader wins over the title")
        check(bundle["app_ids"] == ["703080", "1471590"], "app links are deduped in page order")
        check(ec.fetch_bundle(22933) is None, "a front page answer means the bundle is delisted")

        print("=== a cached bundle needs no network ===")
        before = len(calls)
        again = ec.fetch_bundle(13931)
        check(again is not None and again["name"] == bundle["name"], "the cache answers the name")
        check(len(calls) == before, "nothing was fetched a second time")
        check(ec.fetch_bundle(13931, allow_network=False) is not None, "offline reads the cache")

        print("=== bundle ids are read from the app page ===")
        ids = ec.discover_bundle_ids("703080")
        check(ids == [13931, 22933], "the ids come out in page order without duplicates")
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

        print("=== store packages are cached on disk and named without the price ===")
        check(
            ec.clean_package_name("Vampire Survivors - $4.99 $3.74") == "Vampire Survivors",
            "two appended prices are both dropped",
        )
        check(
            ec.clean_package_name("Darkest Dungeon - $24.99") == "Darkest Dungeon",
            "one appended price is dropped",
        )
        store_answer = json.dumps({
            "555": {
                "success": True,
                "data": {
                    "package_groups": [{
                        "subs": [
                            {"packageid": 1001, "option_text": "Probe Game - $4.99 $3.74",
                             "price_in_cents_with_discount": 374},
                            {"packageid": 1002, "option_description": "Probe Deluxe",
                             "price_in_cents_with_discount": 999},
                        ]
                    }]
                },
            }
        })

        def fake_appdetails(url: str, timeout: float) -> str | None:
            calls.append(url)
            if "appdetails" in url and "appids=555" in url:
                return store_answer
            return None

        ec._store_get = fake_appdetails  # type: ignore[assignment]
        first = ec.fetch_store_packages("555")
        check(len(first) == 2, "both packages come back")
        check(first[0]["name"] == "Probe Game", "the package option text is cleaned")
        check(first[0]["price"] == 374, "the discounted price is kept")
        check(first[1]["name"] == "Probe Deluxe", "a description is used when there is no text")

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

        ec._store_get = lambda url, timeout: None  # type: ignore[assignment]
        check(
            ec.fetch_store_packages("999", allow_network=False) == [],
            "the network is never asked when it is disabled",
        )

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

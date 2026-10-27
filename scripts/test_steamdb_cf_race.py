#!/usr/bin/env python3
"""
scripts/test_steamdb_cf_race.py

Tester and proof-of-concept for multi-profile concurrent racing against SteamDB.
Races multiple TLS / HTTP/2 browser profiles (Chromium, Safari iOS, macOS, Android)
simultaneously using curl_cffi. The first profile to return a clean HTTP 200 without
Cloudflare Turnstile mitigation wins the race and yields the parsed data.
"""

import argparse
import concurrent.futures
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import curl_cffi
    from curl_cffi import requests
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"Error: Required dependency missing ({e}). Run: uv pip install curl-cffi beautifulsoup4")
    sys.exit(1)

# Profiles to race: (name, impersonate_target, user_agent, sec_ch_headers)
RACE_PROFILES = [
    {
        "name": "Chrome 152 (Windows)",
        "impersonate": "chrome152",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="152", "Not(A:Brand";v="24", "Google Chrome";v="152"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        },
    },
    {
        "name": "Chrome 152 (macOS)",
        "impersonate": "chrome152_macos",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="152", "Not(A:Brand";v="24", "Google Chrome";v="152"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        },
    },
    {
        "name": "Safari 270 (macOS)",
        "impersonate": "safari270",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Safari/605.1.15",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
    {
        "name": "Safari 180 (iOS / iPhone)",
        "impersonate": "safari180_ios",
        "headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
    {
        "name": "Safari 17.0 (macOS)",
        "impersonate": "safari17_0",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
    {
        "name": "Chrome 131 (Android Mobile)",
        "impersonate": "chrome131_android",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?1",
            "Sec-Ch-Ua-Platform": '"Android"',
        },
    },
]


def test_single_profile(profile: Dict[str, Any], url: str, timeout: int = 8) -> Dict[str, Any]:
    """Tests a single profile against the target URL and measures timing."""
    start_time = time.perf_counter()
    name = profile["name"]
    imp = profile["impersonate"]
    headers = profile["headers"]

    try:
        r = requests.get(
            url,
            impersonate=imp,
            headers=headers,
            timeout=timeout,
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        cf_mitigated = r.headers.get("Cf-Mitigated", "none")
        is_challenge = cf_mitigated.lower() == "challenge" or "challenges.cloudflare.com" in r.text

        is_success = (r.status_code == 200) and (not is_challenge)

        return {
            "name": name,
            "impersonate": imp,
            "status_code": r.status_code,
            "cf_mitigated": cf_mitigated,
            "elapsed_ms": elapsed_ms,
            "success": is_success,
            "length": len(r.text),
            "text": r.text if is_success else "",
            "error": None,
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        return {
            "name": name,
            "impersonate": imp,
            "status_code": None,
            "cf_mitigated": "error",
            "elapsed_ms": elapsed_ms,
            "success": False,
            "length": 0,
            "text": "",
            "error": str(e),
        }


def race_profiles(url: str, timeout: int = 8) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Races all candidate profiles concurrently.
    Returns:
        (winner_dict_or_None, list_of_all_results)
    """
    all_results = []
    winner = None

    start_all = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(RACE_PROFILES)) as executor:
        future_map = {
            executor.submit(test_single_profile, p, url, timeout): p
            for p in RACE_PROFILES
        }

        # As each future completes, the first successful 200 is chosen as the winner
        for future in concurrent.futures.as_completed(future_map):
            res = future.result()
            all_results.append(res)
            if res["success"] and winner is None:
                winner = res

    # Sort results by response time
    all_results.sort(key=lambda x: x["elapsed_ms"])
    return winner, all_results


def parse_depots_content(html: str) -> List[Dict[str, str]]:
    """Helper to parse depot rows from SteamDB /depots/ page."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    depots = []
    for tr in soup.select("tr[data-depotid]"):
        depot_id = tr["data-depotid"]
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        depots.append({
            "depot_id": depot_id,
            "name": cols[1] if len(cols) > 1 else "",
            "size": cols[2] if len(cols) > 2 else "",
        })
    return depots


def main():
    parser = argparse.ArgumentParser(description="Multi-profile Cloudflare bypass racing tester for SteamDB.")
    parser.add_argument("--appid", type=int, default=2364580, help="Steam AppID to test against /depots/ (default: 2364580)")
    parser.add_argument("--url", type=str, default="", help="Custom SteamDB URL to race against (overrides --appid)")
    parser.add_argument("--timeout", type=int, default=8, help="Per-profile timeout in seconds (default: 8)")
    args = parser.parse_args()

    target_url = args.url or f"https://steamdb.info/app/{args.appid}/depots/"

    print("=" * 72)
    print("SteamDB Cloudflare Multi-Profile Racing Tester")
    print(f"curl_cffi Version: {curl_cffi.__version__} ({curl_cffi.__file__})")
    print(f"Target URL:        {target_url}")
    print(f"Total Profiles:    {len(RACE_PROFILES)}")
    print("=" * 72)

    print("\nRacing all profiles simultaneously in parallel...")
    winner, results = race_profiles(target_url, timeout=args.timeout)

    print("\nResults by latency:")
    print(f"{'Profile Name':<28} {'Target':<18} {'Status':<8} {'Mitigated':<12} {'Latency':<10} {'Outcome'}")
    print("-" * 92)

    for r in results:
        status_str = str(r["status_code"]) if r["status_code"] else "ERR"
        mit_str = str(r["cf_mitigated"])
        latency_str = f"{r['elapsed_ms']:.1f} ms"
        if r["success"]:
            outcome = "PASSED (200 OK)"
        elif r["error"]:
            outcome = f"FAILED: {r['error'][:24]}"
        else:
            outcome = "CHALLENGED (403)"

        prefix = "-> WINNER " if (winner and r["name"] == winner["name"]) else "          "
        print(f"{r['name']:<28} {r['impersonate']:<18} {status_str:<8} {mit_str:<12} {latency_str:<10} {outcome}")

    print("-" * 92)

    if winner:
        print(f"\nWinning Profile: {winner['name']} ({winner['impersonate']})")
        print(f"Winning Latency: {winner['elapsed_ms']:.1f} ms | Payload Size: {winner['length']:,} bytes")
        
        # Verify content parsing
        depots = parse_depots_content(winner["text"])
        if depots:
            print(f"Extracted {len(depots)} depots successfully:")
            for d in depots[:5]:
                print(f"  - Depot {d['depot_id']}: {d['name']} ({d['size']})")
            if len(depots) > 5:
                print(f"  - ... and {len(depots) - 5} more depots")
        else:
            soup = BeautifulSoup(winner["text"], "html.parser")
            title = soup.title.string if soup.title else "No Title"
            print(f"Page Title: {title}")
    else:
        print("\nAll profiles failed or were challenged by Cloudflare.")
        print("Fallback to Byparr / Camoufox headless solver is required.")

    print("=" * 72)


if __name__ == "__main__":
    main()

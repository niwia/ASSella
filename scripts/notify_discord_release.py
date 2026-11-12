#!/usr/bin/env python3
"""Send release announcements to Discord channels via Webhooks.

Supports dedicated channels for Beta and Main/Stable releases via:
  DISCORD_WEBHOOK_BETA_URL
  DISCORD_WEBHOOK_MAIN_URL
  DISCORD_WEBHOOK_URL (fallback for any channel)

Can be run directly from GitHub Actions or locally in --dry-run mode.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("notify_discord")

# Discord embed color codes (decimal)
COLOR_STABLE = 0x2ECC71   # Emerald Green
COLOR_BETA   = 0xF39C12   # Amber Orange
COLOR_CANARY = 0x9B59B6   # Purple

CHANNEL_CONFIG = {
    "stable": {
        "name": "Stable",
        "emoji": "🚀",
        "color": COLOR_STABLE,
        "env_var": "DISCORD_WEBHOOK_MAIN_URL",
    },
    "main": {
        "name": "Stable",
        "emoji": "🚀",
        "color": COLOR_STABLE,
        "env_var": "DISCORD_WEBHOOK_MAIN_URL",
    },
    "beta": {
        "name": "Beta Preview",
        "emoji": "🧪",
        "color": COLOR_BETA,
        "env_var": "DISCORD_WEBHOOK_BETA_URL",
    },
    "canary": {
        "name": "Canary Bleeding-Edge",
        "emoji": "⚡",
        "color": COLOR_CANARY,
        "env_var": "DISCORD_WEBHOOK_BETA_URL",
    },
}


def resolve_webhook_url(channel: str) -> str:
    """Resolve the appropriate Discord webhook URL for the given channel."""
    chan_key = channel.lower().strip()
    cfg = CHANNEL_CONFIG.get(chan_key, CHANNEL_CONFIG["stable"])
    
    primary_env = cfg["env_var"]
    url = os.getenv(primary_env, "").strip()
    if url:
        return url

    # Check fallback general webhook URL
    fallback_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if fallback_url:
        return fallback_url

    # For canary, try main webhook if beta not set
    if chan_key == "canary":
        alt_url = os.getenv("DISCORD_WEBHOOK_MAIN_URL", "").strip()
        if alt_url:
            return alt_url

    return ""


def format_changelog(body: str, max_chars: int = 1500) -> str:
    """Format and safely truncate changelog for Discord embed description."""
    text = body.strip()
    if not text:
        return "New release is ready for installation."

    if len(text) <= max_chars:
        return text

    # Truncate at last newline or sentence before limit
    truncated = text[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars * 0.7:
        truncated = truncated[:last_newline]
    else:
        truncated = truncated.rstrip() + "..."

    return f"{truncated}\n\n*... [Full changelog available on GitHub]*"


def build_payload(
    tag: str,
    channel: str,
    body: str,
    repo: str,
    commit: str,
    ping: str = "",
) -> dict:
    """Build the Discord Webhook JSON payload."""
    chan_key = channel.lower().strip()
    cfg = CHANNEL_CONFIG.get(chan_key, CHANNEL_CONFIG["stable"])

    release_url = f"https://github.com/{repo}/releases/tag/{tag}"
    download_appimage = f"https://github.com/{repo}/releases/download/{tag}/ASSella.AppImage"
    download_zsync = f"https://github.com/{repo}/releases/download/{tag}/ASSella.AppImage.zsync"
    avatar_url = f"https://raw.githubusercontent.com/{repo}/main/src/res/logo/icon.png"

    short_sha = commit[:7] if commit else ""
    commit_field_val = f"[`{short_sha}`](https://github.com/{repo}/commit/{commit})" if short_sha else "N/A"

    embed = {
        "title": f"{cfg['emoji']} ASSella {tag} Released! ({cfg['name']})",
        "url": release_url,
        "description": format_changelog(body),
        "color": cfg["color"],
        "fields": [
            {
                "name": "📥 Downloads",
                "value": (
                    f"• [ASSella.AppImage]({download_appimage})\n"
                    f"• [Delta Update (.zsync)]({download_zsync})"
                ),
                "inline": False,
            },
            {
                "name": "🏷️ Channel",
                "value": f"{cfg['emoji']} {cfg['name']}",
                "inline": True,
            },
            {
                "name": "🔗 Commit",
                "value": commit_field_val,
                "inline": True,
            },
            {
                "name": "📄 Release Page",
                "value": f"[View on GitHub]({release_url})",
                "inline": True,
            },
        ],
        "footer": {
            "text": "ASSella CI/CD • GitHub Actions",
            "icon_url": avatar_url,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    payload: dict = {
        "username": "ASSella Releases",
        "avatar_url": avatar_url,
        "embeds": [embed],
    }

    if ping:
        payload["content"] = ping

    return payload


def send_discord_notification(webhook_url: str, payload: dict) -> bool:
    """Send webhook request to Discord API."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ASSella-ReleaseBot/1.0 (GitHubActions)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            if status in (200, 204):
                logger.info(f"Successfully posted release notification to Discord (HTTP {status})")
                return True
            logger.warning(f"Unexpected HTTP status from Discord: {status}")
            return False
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        logger.error(f"Discord Webhook HTTP {e.code} Error: {err_msg}")
        return False
    except Exception as e:
        logger.error(f"Failed to deliver Discord notification: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Send release announcement to Discord webhook")
    parser.add_argument("--channel", default=os.getenv("RELEASE_CHANNEL", "stable"), help="Release channel")
    parser.add_argument("--tag", default=os.getenv("RELEASE_TAG", "v0.0.0"), help="Release tag")
    parser.add_argument("--body-file", default=os.getenv("RELEASE_BODY_FILE", ""), help="Path to release notes file")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", "niwia/ASSella"), help="GitHub repository")
    parser.add_argument("--commit", default=os.getenv("GITHUB_SHA", ""), help="Commit SHA")
    parser.add_argument("--ping", default=os.getenv("DISCORD_PING", ""), help="Optional text or role ping to prepend")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without sending")

    args = parser.parse_args()

    body_text = ""
    if args.body_file and Path(args.body_file).exists():
        try:
            body_text = Path(args.body_file).read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.warning(f"Could not read release body file '{args.body_file}': {e}")

    payload = build_payload(
        tag=args.tag,
        channel=args.channel,
        body=body_text,
        repo=args.repo,
        commit=args.commit,
        ping=args.ping,
    )

    if args.dry_run:
        print("[DRY-RUN] Discord Payload:\n" + json.dumps(payload, indent=2))
        return 0

    webhook_url = resolve_webhook_url(args.channel)
    if not webhook_url:
        logger.info(
            f"No Discord webhook configured for channel '{args.channel}'. "
            f"Ensure DISCORD_WEBHOOK_BETA_URL or DISCORD_WEBHOOK_MAIN_URL secret is set in GitHub repo settings."
        )
        return 0

    ok = send_discord_notification(webhook_url, payload)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

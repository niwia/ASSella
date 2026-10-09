#!/usr/bin/env python3
import sys
import os
import subprocess
import shutil
from pathlib import Path

# Add src/ to path so we can import helpers
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from utils.helpers import (
    encrypt_string,
    decrypt_string,
    get_schema_grabber_path,
    get_slscheevo_save_path,
    get_steam_stats_dir,
    get_dotnet_env,
)
from utils.settings import get_settings

def configure_credentials(settings):
    print("\n" + "-" * 60)
    print("      Configure & Save Achievements Credentials (One-time Setup)")
    print("-" * 60)
    print("This option will verify your credentials and save them encrypted locally.")
    print("This enables automatic achievements downloads in the background.")
    print("-" * 60)
    
    username = input("Enter Steam Username: ").strip()
    if not username:
        print("Username cannot be empty. Setup aborted.")
        return
        
    import getpass
    password = getpass.getpass("Enter Steam Password: ")
    if not password:
        print("Password cannot be empty. Setup aborted.")
        return

    print("\nVerifying credentials by establishing connection to Steam...")
    success = run_schema_grabber(username, password, "480")
    if success:
        print("\nAuthentication successful!")
        settings.setValue("steam_username", username)
        # The password is deliberately NOT persisted. It used to be stored
        # "encrypted" with a machine-derived key, which is reversible by anyone
        # who can read the file - and argv exposure means it was briefly visible
        # to other local users anyway. schema-grabber is deprecated; enter the
        # password again when you need it.
        try:
            settings.remove("steam_password")
        except Exception:
            pass
        print("Steam username saved. Password is NOT saved to disk (enter it when needed).")
    else:
        print("\nAuthentication failed! Credentials were not saved.")
        print("Please check your credentials or 2FA guard code and try again.")

def fetch_schemas(settings, app_id):
    username = settings.value("steam_username", "", type=str)
    encrypted_pass = settings.value("steam_password", "", type=str)
    password = decrypt_string(encrypted_pass)
    
    if not username or not password:
        print("\nNo saved credentials found.")
        print("You can enter them now for this download, or use Option 1 to save them.")
        print("-" * 60)
        username = input("Enter Steam Username: ").strip()
        if not username:
            return
        import getpass
        password = getpass.getpass("Enter Steam Password: ")
        if not password:
            return

    target_label = "all games" if app_id == "0" else f"Game AppID {app_id}"
    print(f"\nFetching schemas for {target_label}...")
    success = run_schema_grabber(username, password, app_id)
    if success:
        print(f"\nSuccessfully downloaded schemas for {target_label}!")
    else:
        print(f"\nFailed to download schemas for {target_label}.")

def run_schema_grabber(username, password, app_id):
    path = get_schema_grabber_path()
    if not path.exists():
        print(f"Error: schema-grabber binary not found at: {path}")
        return False

    # Run inside the bins directory so the files are generated in the right place
    cwd = get_slscheevo_save_path() / "data" / "bins"
    cwd.mkdir(parents=True, exist_ok=True)
    
    cmd = [str(path), username, password, app_id]
    env = get_dotnet_env()
    
    try:
        # Run schema-grabber interactive subprocess
        res = subprocess.run(cmd, cwd=str(cwd), env=env)
        if res.returncode == 0:
            post_fetch_sync(cwd, app_id)
            return True
        return False
    except Exception as e:
        print(f"Execution error: {e}")
        return False

def post_fetch_sync(cwd, app_id):
    # Copy template to bins for all logged in accounts
    try:
        from utils.paths import Paths
        steam_stats_dir = get_steam_stats_dir()
        if steam_stats_dir:
            login_users_file = steam_stats_dir.parent / "config" / "loginusers.vdf"
            if login_users_file.exists():
                import vdf
                with open(login_users_file, "r", encoding="utf-8") as f:
                    loginusers = vdf.load(f)
                accounts = loginusers.get("users", {})

                template_path = get_slscheevo_save_path() / "data" / "UserGameStats_TEMPLATE.bin"
                if not template_path.exists():
                    template_path = Paths.deps("SLScheevo/data/UserGameStats_TEMPLATE.bin")

                if template_path.exists():
                    for steamid64_str in accounts.keys():
                        try:
                            steamid64 = int(steamid64_str)
                            account_id = steamid64 & 0xFFFFFFFF
                            stats_name = f"UserGameStats_{account_id}_{app_id}.bin"
                            archive_stats = cwd / stats_name
                            if not archive_stats.exists():
                                shutil.copy2(template_path, archive_stats)
                        except Exception:
                            pass
    except Exception:
        pass

    # Sync bin files to Steam stats directory
    try:
        steam_stats_dir = get_steam_stats_dir()
        if steam_stats_dir:
            steam_stats_dir.mkdir(parents=True, exist_ok=True)
        
        bin_files = list(cwd.glob("**/*.bin"))
        for bin_file in bin_files:
            if steam_stats_dir:
                dest_path = steam_stats_dir / bin_file.name
                if bin_file.name.startswith("UserGameStatsSchema_") or not dest_path.exists():
                    shutil.copy2(bin_file, dest_path)
    except Exception:
        pass

def main():
    settings = get_settings()
    
    while True:
        print("\n" + "=" * 60)
        print("           ASSella Steam Achievements Tool")
        print("=" * 60)
        
        # Check current config status
        username = settings.value("steam_username", "", type=str)
        encrypted_pass = settings.value("steam_password", "", type=str)
        is_configured = bool(username and encrypted_pass)
        status_str = f"Configured (User: {username})" if is_configured else "Not configured"
        
        print(f"Status: {status_str}")
        print("-" * 60)
        print("1. One-time Setup: Configure & Save Credentials (Encrypted)")
        print("2. Fetch/Refresh schemas for ALL installed games")
        print("3. Fetch schemas for a specific game (AppID)")
        print("4. Clear saved credentials")
        print("5. Exit")
        print("-" * 60)
        
        choice = input("Select an option (1-5): ").strip()
        
        if choice == "1":
            configure_credentials(settings)
        elif choice == "2":
            fetch_schemas(settings, app_id="0")
        elif choice == "3":
            app_id = input("Enter Steam AppID: ").strip()
            if not app_id.isdigit():
                print("Invalid AppID. Must be a number.")
                continue
            fetch_schemas(settings, app_id=app_id)
        elif choice == "4":
            settings.remove("steam_username")
            settings.remove("steam_password")
            print("\nCredentials cleared successfully.")
        elif choice == "5":
            print("\nExiting...")
            break
        else:
            print("\nInvalid selection. Please enter 1-5.")

if __name__ == "__main__":
    main()

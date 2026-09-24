import sys
from pathlib import Path


class Paths:
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent)).resolve()
    RES = BASE_DIR / "res"
    DEPS = BASE_DIR / "deps"

    @classmethod
    def deps(cls, relative_path=None) -> Path:
        """Grabs from the dependencies folder by relative name.

        If no relative path is specified, it returns the /deps/ folder.
        """
        if relative_path is not None:
            return cls.DEPS / relative_path
        return cls.DEPS

    @classmethod
    def resource(cls, relative_path=None) -> Path:
        """Grabs a resource by relative name.

        If no relative path is specified, it returns the /res/ folder.
        """
        if relative_path is not None:
            return cls.RES / relative_path
        return cls.RES

    @classmethod
    def icon(cls, filename: str) -> Path:
        """Returns Path to an SVG icon from bundled /res/icons/ or user media/icons/ fallback."""
        res_icon = cls.RES / "icons" / filename
        if res_icon.exists():
            return res_icon
        try:
            from utils.helpers import get_base_path
            media_icon = get_base_path() / "media" / "icons" / filename
            if media_icon.exists():
                return media_icon
        except Exception:
            pass
        return res_icon

    @classmethod
    def base(cls, relative_path=None) -> Path:
        """Grabs a resource from the base path.

        If no relative path is specified, it returns the base path.
        """
        if relative_path is not None:
            return cls.BASE_DIR / relative_path
        return cls.BASE_DIR

    @classmethod
    def absolute(cls, path: str) -> Path:
        """Return the absolute, expanded path as a Path object."""
        return Path(path).expanduser().resolve()

    @classmethod
    def workshop_keys(cls) -> Path:
        """Returns Path to db/workshop_keys.txt, automatically migrating any legacy root file."""
        import shutil
        from utils.helpers import get_base_path
        base = get_base_path()
        db_dir = base / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        target = db_dir / "workshop_keys.txt"
        legacy = base / "workshop_keys.txt"
        if legacy.exists():
            try:
                if not target.exists():
                    shutil.move(str(legacy), str(target))
                else:
                    existing = set(target.read_text(encoding="utf-8").splitlines()) if target.exists() else set()
                    with open(target, "a", encoding="utf-8") as tf:
                        for line in legacy.read_text(encoding="utf-8").splitlines():
                            s = line.strip()
                            if s and s not in existing:
                                tf.write(s + "\n")
                                existing.add(s)
                    legacy.unlink()
            except Exception:
                pass
        return target


def get_jumpscare_gif(filename: str) -> str:
    """Resolve path to a jumpscare GIF from bundled /res/jumpscare/ or ~/.local/share/ACCELA/jumpscare/."""
    # 1. Bundled in AppImage / source tree
    bundled = Paths.resource(f"jumpscare/{filename}")
    if bundled.exists():
        return str(bundled)
    # 2. User directory fallback
    user_path = Path.home() / ".local" / "share" / "ACCELA" / "jumpscare" / filename
    if user_path.exists():
        return str(user_path)
    return ""


def is_valid_download_directory(path: str) -> bool:
    """Validates that a directory path is usable as a download/Steam library location.
    
    Rejects empty paths, non-existent directories, and transient system/mount directories
    such as /tmp, /var/tmp, AppImage mount directories, /proc, /dev, etc.
    """
    if not path or not isinstance(path, str):
        return False
    clean = path.strip()
    if not clean:
        return False
    try:
        p = Path(clean).expanduser().resolve()
        if not p.is_dir():
            return False
        real_str = str(p)
        if real_str in ("/tmp", "/var/tmp", "/proc", "/sys", "/dev"):
            return False
        if real_str.startswith(("/tmp/", "/var/tmp/", "/proc/", "/sys/", "/dev/")):
            return False
        return True
    except Exception:
        return False



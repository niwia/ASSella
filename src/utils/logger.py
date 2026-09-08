import logging
import os
import platform
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from utils.helpers import get_base_path

# Constants
APP_NAME = "assella"
MAX_PREVIOUS_LOGS = 3

logger = logging.getLogger(__name__)


class SensitiveDataFilter(logging.Filter):
    """
    Sanitizes log records to ensure no personal or sensitive data (API keys,
    passwords, auth tokens, usernames) is leaked into log files, console, or GUI.
    """

    PATTERNS = [
        # Hubcap / Morrenus API keys (e.g. smm_abc123...)
        (re.compile(r"smm_[a-zA-Z0-9_-]{16,}", re.IGNORECASE), "[REDACTED_API_KEY]"),
        # Bearer tokens in headers
        (re.compile(r"Bearer\s+[a-zA-Z0-9._~+/-]+", re.IGNORECASE), "Bearer [REDACTED_TOKEN]"),
        # URL or JSON auth parameters
        (
            re.compile(
                r"(api_key|apikey|morrenus_api_key|access_token|secret_key|private_key|token)[=:\s]+([\"']?)([^\s,\"\'&]+)([\"']?)",
                re.IGNORECASE,
            ),
            r"\1=\2[REDACTED_KEY]\4",
        ),
        (
            re.compile(
                r"(password|steam_password|passwd)[=:\s]+([\"']?)([^\s,\"\'&]+)([\"']?)",
                re.IGNORECASE,
            ),
            r"\1=\2[REDACTED_PASSWORD]\4",
        ),
        # CLI flag passwords (e.g. -password mypass, -pass mypass)
        (re.compile(r"(-password|-pass)\s+([^\s]+)", re.IGNORECASE), r"\1 [REDACTED_PASSWORD]"),
    ]

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text

        # Apply regex pattern redactions
        for pattern, replacement in cls.PATTERNS:
            text = pattern.sub(replacement, text)

        # Also redact the configured user API key and steam credentials if stored in settings
        try:
            from utils.settings import get_settings

            settings = get_settings()
            if settings:
                api_key = settings.value("morrenus_api_key", "", type=str)
                if api_key and len(api_key) > 5 and api_key in text:
                    text = text.replace(api_key, "[REDACTED_API_KEY]")
                steam_pass = settings.value("steam_password", "", type=str)
                if steam_pass and len(steam_pass) > 3 and steam_pass in text:
                    text = text.replace(steam_pass, "[REDACTED_PASSWORD]")
        except Exception:
            pass

        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg and isinstance(record.msg, str):
            record.msg = self.sanitize_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (self.sanitize_text(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(
                    self.sanitize_text(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        return True


class SanitizingFormatter(logging.Formatter):
    """Formatter that strips sensitive information from all log outputs."""

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        return SensitiveDataFilter.sanitize_text(s)


class QtLogHandler(QObject, logging.Handler):
    """Custom logging handler that emits signals to Qt widgets."""

    new_record = pyqtSignal(str)
    flushOnClose = False

    def __init__(self):
        super().__init__()
        QObject.__init__(self)
        logging.Handler.__init__(self)
        self.setFormatter(QtLogFormatter())
        self.addFilter(SensitiveDataFilter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            msg = SensitiveDataFilter.sanitize_text(msg)
            self.new_record.emit(msg)
        except RuntimeError:
            pass

    def flush(self) -> None:
        pass


class QtLogFormatter(logging.Formatter):
    """Custom formatter for GUI logs to keep it clean, minimal, and sanitized."""

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno >= logging.WARNING:
            msg = f"[{record.levelname}] {record.getMessage()}"
        else:
            msg = record.getMessage()
        return SensitiveDataFilter.sanitize_text(msg)


# Global handler instance
qt_log_handler = QtLogHandler()
_current_log_name: Optional[str] = None
_log_dir = get_base_path() / "logs"


class LineRotatingFileHandler(logging.FileHandler):
    """
    Handler that rotates logs based on maximum line count.
    Keeps at most max_lines in the file, dropping older lines.
    """

    def __init__(self, filename, mode="a", encoding=None, delay=False, max_lines=10000):
        super().__init__(filename, mode, encoding, delay)
        self.max_lines = max_lines
        self._emit_count = 0

    def emit(self, record):
        super().emit(record)
        self.flush()
        self._emit_count += 1
        # Truncate every 20 log records to keep disk I/O low
        if self._emit_count >= 20:
            self._emit_count = 0
            try:
                self.rotate_by_lines()
            except Exception:
                pass

    def rotate_by_lines(self):
        if not os.path.exists(self.baseFilename):
            return
        try:
            with open(
                self.baseFilename, "r", encoding=self.encoding or "utf-8", errors="ignore"
            ) as f:
                lines = f.readlines()
            if len(lines) > self.max_lines:
                keep_lines = lines[-self.max_lines :]
                with open(
                    self.baseFilename, "w", encoding=self.encoding or "utf-8"
                ) as f:
                    f.writelines(keep_lines)
        except Exception:
            pass

    def close(self):
        try:
            self.rotate_by_lines()
        except Exception:
            pass
        super().close()


class LogCategoryFilter(logging.Filter):
    def __init__(self, level_str="INFO", category_str="All Modules"):
        super().__init__()
        if level_str.upper() == "NONE":
            self.level = 100
        else:
            self.level = getattr(logging, level_str.upper(), logging.INFO)
        self.category = category_str

    def filter(self, record):
        # 1. Filter by level
        if record.levelno < self.level:
            return False

        # 2. Filter by category
        if self.category == "All Modules":
            return True
        elif self.category == "Only Steam Client & API":
            name = record.name.lower()
            return "steam" in name or "client" in name or "scheevo" in name
        elif self.category == "Only Downloads & Manifests":
            name = record.name.lower()
            return "download" in name or "manifest" in name or "task" in name or "job" in name
        elif self.category == "Only Database & Library":
            name = record.name.lower()
            return (
                "db_manager" in name
                or "database" in name
                or "game_manager" in name
                or "library" in name
            )

        return True


def update_log_filters():
    """Update active log filters from current settings."""
    try:
        from utils.settings import get_settings

        settings = get_settings()
        if not settings:
            return

        level_str = settings.value("log_filter_level", "INFO", type=str) or "INFO"
        category_str = settings.value("log_filter_category", "All Modules", type=str) or "All Modules"

        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            # Remove any existing LogCategoryFilters
            for filt in handler.filters[:]:
                if isinstance(filt, LogCategoryFilter):
                    handler.removeFilter(filt)

            # Ensure SensitiveDataFilter is attached
            has_sensitive_filter = any(
                isinstance(f, SensitiveDataFilter) for f in handler.filters
            )
            if not has_sensitive_filter:
                handler.addFilter(SensitiveDataFilter())

            # Add updated category and level filter
            handler.addFilter(LogCategoryFilter(level_str, category_str))

            # Update handler level
            if level_str.upper() == "NONE":
                level_num = 100
            else:
                level_num = getattr(logging, level_str.upper(), logging.INFO)
            handler.setLevel(level_num)

    except Exception as e:
        print(f"Error updating log filters: {e}", file=sys.stderr)


def _create_file_handler(log_path: Path) -> Optional[LineRotatingFileHandler]:
    """Attempt to create a line rotating file handler at the specified path."""
    formatter = SanitizingFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    try:
        handler = LineRotatingFileHandler(
            log_path,
            mode="w",  # Start fresh for each session
            encoding="utf-8",
            max_lines=10000,
            delay=False,
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveDataFilter())
        print(f"Log file created: {log_path}", file=sys.stderr)
        return handler
    except (PermissionError, OSError) as e:
        print(f"Error: Could not create log file at {log_path}: {e}", file=sys.stderr)
        return None


class _StderrTee:
    """Tee sys.stderr to a file stream so that 'Fatal Python error:' messages
    from hard crashes (PyQt6 SIGSEGV, GIL errors, etc.) end up in the log file
    as well as the original stderr."""

    def __init__(self, original_stderr, file_stream):
        self._original = original_stderr
        self._file = file_stream

    def write(self, data):
        try:
            self._original.write(data)
            self._original.flush()
        except Exception:
            pass
        try:
            if self._file and not self._file.closed:
                self._file.write(data)
                self._file.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass
        try:
            if self._file and not self._file.closed:
                self._file.flush()
        except Exception:
            pass

    def fileno(self):
        return self._original.fileno()

    def isatty(self):
        try:
            return self._original.isatty()
        except Exception:
            return False


def _install_stderr_tee(file_stream) -> None:
    """Replace sys.stderr with a tee writer so crash output goes to the log."""
    try:
        if not isinstance(sys.stderr, _StderrTee):
            sys.stderr = _StderrTee(sys.stderr, file_stream)
    except Exception as e:
        print(f"Failed to install stderr tee: {e}", file=sys.__stderr__)


def _install_global_exception_hooks() -> None:
    """Install sys.excepthook and threading.excepthook to route unhandled
    exceptions from all threads into the logger instead of stderr."""
    _exc_logger = logging.getLogger("assella.crash")

    def _handle_exception(exc_type, exc_value, exc_tb):
        """Main thread unhandled exception hook."""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        _exc_logger.critical(
            "Unhandled exception on main thread",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    def _handle_thread_exception(args):
        """Background thread unhandled exception hook (Python 3.8+)."""
        if args.exc_type is SystemExit:
            return
        thread_name = getattr(args.thread, "name", "<unknown>") if args.thread else "<unknown>"
        _exc_logger.critical(
            f"Unhandled exception in background thread '{thread_name}'",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    try:
        sys.excepthook = _handle_exception
    except Exception:
        pass

    try:
        import threading
        threading.excepthook = _handle_thread_exception
    except (AttributeError, Exception):
        # threading.excepthook is Python 3.8+; silently skip on older builds
        pass


def setup_logging() -> logging.Logger:
    """Setup logging with timestamped log files and sensitive data redaction."""
    cleanup_old_logs()

    log_path = get_log_path()
    system_platform = platform.system()
    formatter = SanitizingFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    handlers: List[logging.Handler] = []

    # 1. File Handler (Main Path)
    file_handler = _create_file_handler(log_path)

    # 2. File Handler (Fallback to TEMP if main fails)
    if not file_handler:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = Path(os.environ.get("TEMP", os.getcwd()))
        fallback_path = temp_dir / f"{APP_NAME}_{timestamp}.log"
        print(f"Attempting fallback log: {fallback_path}", file=sys.stderr)
        file_handler = _create_file_handler(fallback_path)

    if file_handler:
        handlers.append(file_handler)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(SensitiveDataFilter())
    handlers.append(console_handler)

    # Qt Handler
    qt_log_handler.setLevel(logging.INFO)
    qt_log_handler.setFormatter(QtLogFormatter())
    handlers.append(qt_log_handler)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addFilter(SensitiveDataFilter())

    # Reduce noise from third-party libraries when offline
    logging.getLogger("CMServerList").setLevel(logging.CRITICAL)

    # Clear existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add new handlers
    for handler in handlers:
        root_logger.addHandler(handler)

    # Apply saved log filters
    update_log_filters()

    # ── Redirect stderr to the log file ──────────────────────────────────────
    # "Fatal Python error:" and PyQt6 crash tracebacks are written to stderr,
    # which normally vanishes inside the AppImage. Tee stderr into the log file
    # so these hard crashes are visible in the log viewer.
    if file_handler and file_handler.stream:
        _install_stderr_tee(file_handler.stream)

    # ── Global exception hooks ────────────────────────────────────────────────
    # Catch unhandled exceptions on the main thread and in background threads
    # so they are written to the log instead of going silently to stderr.
    _install_global_exception_hooks()

    local_logger = logging.getLogger(__name__)

    local_logger.info("Logging Initialized (Sensitive data redaction active)")
    local_logger.info("Platform: %s", system_platform)
    local_logger.info("Python: %s", sys.version)
    local_logger.info("Log file: %s", log_path)

    return local_logger


def open_log_directory() -> bool:
    """Open the log directory in the system file manager."""
    global _log_dir

    try:
        system = platform.system().lower()
        cmd = ["xdg-open"]

        if system == "windows":
            cmd = ["explorer"]

        subprocess.run(cmd + [str(_log_dir)], check=False)
        return True
    except Exception as e:
        local_logger = logging.getLogger(__name__)
        local_logger.error("Failed to open log directory: %s", e)
        return False


def get_log_path() -> Path:
    """Return path to a timestamped log file with counter if needed."""
    global _current_log_name, _log_dir

    try:
        _log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        temp_dir = Path(os.environ.get("TEMP", os.getcwd())) / "logs" / APP_NAME
        temp_dir.mkdir(parents=True, exist_ok=True)
        _log_dir = temp_dir

    # If an active log is already set and exists, reuse it
    if _current_log_name and (_log_dir / _current_log_name).exists():
        return _log_dir / _current_log_name

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{APP_NAME}_{timestamp}"

    counter = 1
    while True:
        if counter == 1:
            log_name = f"{base_name}.log"
        else:
            log_name = f"{base_name}_{counter}.log"

        log_path = _log_dir / log_name
        if not log_path.exists():
            break
        counter += 1

    _current_log_name = log_name
    return log_path


def cleanup_old_logs() -> None:
    """Clean up old log files on startup."""
    global MAX_PREVIOUS_LOGS

    base_path = get_base_path()
    log_dir = base_path / "logs"

    if not log_dir.exists():
        return

    # Match both new assella_*.log and legacy accela_*.log files
    log_files = [
        f for f in log_dir.glob("*.log")
        if f.is_file() and (f.name.startswith("assella_") or f.name.startswith("accela_"))
    ]

    if not log_files:
        return

    log_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    for old_log in log_files[MAX_PREVIOUS_LOGS:]:
        try:
            old_log.unlink()
            print(f"Removed old log file: {old_log.name}", file=sys.stderr)
        except OSError as e:
            print(f"Could not remove {old_log.name}: {e}", file=sys.stderr)

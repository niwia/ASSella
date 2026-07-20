import logging
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from utils.paths import Paths

logger = logging.getLogger(__name__)


def _get_user_dotnet_path() -> str:
    """Get the path to user's .dotnet directory, platform-aware.

    For Windows, uses LocalAppData (default for dotnet-install.ps1).
    For Linux/macOS, uses HOME/.dotnet.
    """
    if sys.platform == "win32":
        # On Windows, dotnet-install.ps1 defaults to
        # %LocalAppData%\Microsoft\dotnet
        local_app_data = os.environ.get(
            "LOCALAPPDATA", os.path.expandvars("%LocalAppData%")
        )
        return os.path.join(local_app_data, "Microsoft", "dotnet", "dotnet")
    else:
        # On Linux/macOS, use HOME/.dotnet
        return os.path.expanduser("~/.dotnet/dotnet")


def _get_user_dotnet_root() -> str:
    """Get the path to user's .dotnet root directory, platform-aware.

    For Windows, uses LocalAppData (default for dotnet-install.ps1).
    For Linux/macOS, uses HOME/.dotnet.
    """
    if sys.platform == "win32":
        local_app_data = os.environ.get(
            "LOCALAPPDATA", os.path.expandvars("%LocalAppData%")
        )
        return os.path.join(local_app_data, "Microsoft", "dotnet")
    else:
        return os.path.expanduser("~/.dotnet")


def get_dotnet_path() -> str | None:
    """Get the path to dotnet executable, checking both locations.

    Prefers user-local and Program Files locations, then PATH.
    """
    candidates = []

    system_dotnet = shutil.which("dotnet")
    logger.debug(f"System dotnet from PATH: {system_dotnet}")
    if system_dotnet:
        candidates.append(system_dotnet)

    user_dotnet = _get_user_dotnet_path()

    if sys.platform == "win32":
        if user_dotnet.lower().endswith("dotnet.exe"):
            user_dotnet_exe = user_dotnet
        else:
            user_dotnet_exe = user_dotnet + ".exe"
    else:
        user_dotnet_exe = user_dotnet

    candidates.append(user_dotnet_exe)

    # Deduplicate candidates while preserving order
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    for dotnet_exe in unique_candidates:
        try:
            dotnet_exe = str(dotnet_exe)
            dotnet_root = os.path.dirname(dotnet_exe)
            env = os.environ.copy()
            env.setdefault("DOTNET_ROOT", dotnet_root)
            run_kwargs = {
                "capture_output": True,
                "text": True,
                "timeout": 10,
                "env": env,
            }
            if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            result = subprocess.run([dotnet_exe, "--list-runtimes"], **run_kwargs)
            if "Microsoft.NETCore.App 9." in result.stdout:
                logger.info(f"Found .NET 9 using {dotnet_exe}")
                return dotnet_exe

        except (OSError, subprocess.SubprocessError) as e:
            logger.debug(f"Error probing {dotnet_exe}: {e}")

    return None


def install_dotnet_9() -> bool:
    """Install .NET 9 runtime using official installer script."""
    try:
        if sys.platform == "win32":
            return _install_dotnet_9_windows()
        else:
            return _install_dotnet_9_linux()
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"Error installing .NET 9: {e}")
        return False


def _install_dotnet_9_linux() -> bool:
    """Install .NET 9 runtime on Linux using official installer script."""
    try:
        logger.info("Installing .NET 9 runtime via official installer script...")

        # Set DOTNET_ROOT to ensure the install script uses and exposes
        # the correct location
        env = os.environ.copy()
        dotnet_root = _get_user_dotnet_root()
        env["DOTNET_ROOT"] = dotnet_root

        # Download and run the install script using the official method
        install_script_path = os.path.join(dotnet_root, "dotnet-install.sh")

        # Create the dotnet root directory if it doesn't exist
        os.makedirs(dotnet_root, exist_ok=True)

        # Download the script first
        logger.info("Downloading .NET 9 installer script...")
        download_cmd = None
        if shutil.which("curl"):
            download_cmd = [
                "curl",
                "-sSL",
                "-o",
                install_script_path,
                "https://dot.net/v1/dotnet-install.sh",
            ]
        elif shutil.which("wget"):
            download_cmd = [
                "wget",
                "-q",
                "-O",
                install_script_path,
                "https://dot.net/v1/dotnet-install.sh",
            ]

        if not download_cmd:
            logger.error(
                "Neither curl nor wget is available to download the "
                ".NET installer script"
            )
            return False

        download_cmd_str: list[str] = [str(part) for part in download_cmd]
        download_result = subprocess.run(
            download_cmd_str,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if download_result.returncode != 0:
            logger.error(
                f"Failed to download .NET 9 installer script: {download_result.stderr}"
            )
            return False

        # Make it executable and run it
        os.chmod(install_script_path, 0o755)

        logger.info("Running .NET 9 installer script...")
        install_result = subprocess.run(
            [str(install_script_path), "--channel", "9.0", "--runtime", "dotnet"],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )

        # Cleanup script
        try:
            os.remove(install_script_path)
        except OSError:
            pass

        if install_result.returncode == 0:
            logger.info(".NET 9 runtime installed successfully")
            logger.debug(f"Install stdout: {install_result.stdout}")
            return True

        logger.error(
            f"Failed to install .NET 9 (exit code {install_result.returncode})"
        )
        logger.error(f"stdout: {install_result.stdout}")
        logger.error(f"stderr: {install_result.stderr}")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Timeout while installing .NET 9")
        return False
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"Exception during .NET 9 installation: {e}")
        return False


def _install_dotnet_9_windows() -> bool:
    """Install .NET 9 runtime on Windows using official installer script."""
    max_retries = 2
    dotnet_root = _get_user_dotnet_root()

    install_script_url = "https://dot.net/v1/dotnet-install.ps1"

    for attempt in range(max_retries):
        script_path = None
        try:
            logger.info(
                f"Installing .NET 9 runtime (attempt {attempt + 1}/{max_retries})..."
            )

            env = os.environ.copy()
            env["DOTNET_ROOT"] = dotnet_root

            with tempfile.NamedTemporaryFile(
                suffix=".ps1", delete=False, mode="wb"
            ) as tmp_script:
                script_path = tmp_script.name
                logger.info("Downloading installer script via Python...")

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(
                    install_script_url, context=ctx, timeout=30
                ) as response:
                    tmp_script.write(response.read())

            logger.info("Running .NET 9 installer script...")

            install_cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_path,
                "-Channel",
                "9.0",
                "-Runtime",
                "dotnet",
                "-InstallDir",
                dotnet_root,
            ]

            install_cmd_str: list[str] = [str(part) for part in install_cmd]
            run_kwargs = {
                "capture_output": True,
                "text": True,
                "timeout": 300,
                "env": env,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            install_result = subprocess.run(install_cmd_str, **run_kwargs)

            if install_result.returncode == 0:
                logger.info(".NET 9 runtime installed successfully.")
                dotnet_exe = os.path.join(dotnet_root, "dotnet.exe")
                if os.path.exists(dotnet_exe):
                    logger.debug(f"Confirmed executable exists at: {dotnet_exe}")
                    return True
                else:
                    logger.debug(
                        "Installer finished code 0 but dotnet.exe not "
                        "found in target dir."
                    )

        except subprocess.TimeoutExpired:
            logger.error("Timeout while installing .NET 9")
        except (OSError, subprocess.SubprocessError) as e:
            logger.error(f"Error during .NET 9 installation: {e}")
        finally:
            if script_path and os.path.exists(script_path):
                os.remove(script_path)

        if attempt < max_retries - 1:
            logger.info("Retrying installation...")

    return False


def ensure_dotnet_availability() -> bool:
    """Ensure .NET 9 runtime is available, install if missing."""
    if get_dotnet_path():
        return True

    logger.warning(".NET 9 not found, attempting automatic installation...")
    success = install_dotnet_9()

    if success:
        dotnet_exec = get_dotnet_path()
        if dotnet_exec:
            dotnet_root = os.path.dirname(dotnet_exec)
            try:
                os.environ["DOTNET_ROOT"] = dotnet_root
                current_path = os.environ.get("PATH", "")
                if dotnet_root not in current_path.split(os.pathsep):
                    os.environ["PATH"] = dotnet_root + os.pathsep + current_path
                logger.debug(
                    ".NET 9 is now available and DOTNET_ROOT/PATH set for this process"
                )
            except RuntimeError:
                logger.debug(
                    "Failed to set DOTNET_ROOT/PATH in current process environment"
                )
            return True
        logger.warning(".NET 9 installation completed but still not detected")
    logger.error("Failed to ensure .NET 9 availability")
    return False


def resource_path(relative_path: str) -> Path:
    """Get absolute path to resource, works for dev and for PyInstaller."""
    base_path = getattr(sys, "_MEIPASS", None)
    if base_path is None:
        base_path = os.path.dirname(os.path.abspath(sys.argv[0]))
    return Path(os.path.join(base_path, relative_path))


def get_base_path(app_name: str = "ACCELA") -> Path:
    """Return the base directory for the current platform (no logs dir)."""
    system = platform.system().lower()

    if system == "linux":
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / app_name

        home = os.environ.get("HOME")
        if home:
            return Path(home) / ".local" / "share" / app_name

        tilde = os.path.expanduser("~")
        if tilde not in ("~", ""):  # ensures it actually expanded
            return Path(tilde) / ".local" / "share" / app_name

        # If all fails resort to same dir save
        return Path(".") / app_name

    elif system == "windows":
        # Using AppData/Roaming instead of program directory
        appdata = os.environ.get("APPDATA")
        if appdata:
            base_path = Path(appdata) / app_name

            # Check for existing ACCELA folder in program directory and move it
            old_path = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / app_name
            if old_path.exists() and not base_path.exists():
                try:
                    shutil.move(str(old_path), str(base_path))
                except OSError as e:
                    logger.warning(f"Could not move existing data: {e}")

            return base_path
        else:
            # Fallback to program directory if APPDATA not found
            return Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / app_name

    elif system == "darwin":  # macOS
        # Standard macOS location
        return Path.home() / "Library" / "Logs" / app_name

    else:
        # Fallback directory for unknown platforms
        return Path.home() / ".logs" / app_name


def _get_slscheevo_path() -> Path:
    """Get path to SLScheevo executable or Python script."""

    # Running in a PyInstaller bundle -> use the embedded executable
    executable_name = "SLScheevo.exe" if sys.platform == "win32" else "SLScheevo"
    relative_path = f"SLScheevo/{executable_name}"

    # Use Path.depot() for relative pathing directly inside the deps folder.
    binary_path = Paths.deps(relative_path)
    script_path = Paths.deps("SLScheevo/SLScheevo.py")

    # Prefer the bundled executable over the Python script
    if binary_path.exists():
        logger.info(f"Using SLScheevo executable at: {binary_path}")
        return binary_path

    # Fallback to Python script if executable not found
    if script_path.exists():
        logger.info(f"Using SLScheevo script at: {script_path}")
        return script_path

    logger.error(f"Could not find SLScheevo (tried: {binary_path}, {script_path})")
    # Return binary_path anyway so error handling can deal with it
    return binary_path


def get_slscheevo_path() -> Path:
    return _get_slscheevo_path()


def get_schema_grabber_path() -> Path:
    binary_path = Paths.deps("schema-grabber/schema-grabber")
    if not binary_path.exists():
        fallback_path = Path("/home/deck/.local/share/ACCELA/schema-grabber/bin/Release/net9.0/linux-x64/publish/schema-grabber")
        if fallback_path.exists():
            return fallback_path
    return binary_path


def _ensure_template_file(save_dir: Path) -> None:
    """Ensure UserGameStats_TEMPLATE.bin exists in the save directory."""
    template_in_save_dir = save_dir / "data" / "UserGameStats_TEMPLATE.bin"

    # If template already exists, no need to copy
    if template_in_save_dir.exists():
        return

    # Find the original template file
    template_source = Paths.deps("SLScheevo/data/UserGameStats_TEMPLATE.bin")

    # If we found the source template, copy it
    if template_source and template_source.exists():
        # Create data directory if it doesn't exist
        (save_dir / "data").mkdir(exist_ok=True)
        # Copy the template file
        try:
            shutil.copy2(template_source, template_in_save_dir)
            logger.info(f"Copied {str(template_source)} to {template_in_save_dir}")
        except OSError as e:
            logger.warning(f"Failed to copy {str(template_source)}: {e}")
    else:
        logger.warning(f"Could not find {str(template_source)} source to copy")


def _get_slscheevo_save_path() -> Path:
    # Get save directory for credentials
    save_dir = get_base_path() / "SLScheevo"

    # Create directory tree
    save_dir.mkdir(parents=True, exist_ok=True)

    # Ensure template file exists
    _ensure_template_file(save_dir)

    logger.info(f"SLScheevo save directory: {save_dir}")
    return save_dir


def get_slscheevo_save_path() -> Path:
    return _get_slscheevo_save_path()


def is_running_in_pyinstaller() -> bool:
    """Check if the application is running as a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def check_venv(path: str | Path) -> Path | None:
    """Check if a path is a valid virtual environment."""
    # Convert to absolute path immediately
    venv_path = Path(path).resolve()

    if venv_path.exists() and venv_path.is_dir():
        # Check for standard venv markers
        has_cfg = (venv_path / "pyvenv.cfg").exists()
        # Check for the actual python binary
        has_bin = (venv_path / "bin" / "python").exists() or (
            venv_path / "Scripts" / "python.exe"
        ).exists()

        if has_cfg or has_bin:
            return venv_path

    return None


def get_venv_path() -> Path | None:
    """Get absolute path to venv Python."""
    # Return None if running from PyInstaller temp directory
    # The venv won't be accessible from the MEIPASS temp folder
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        logger.debug(
            "Running from PyInstaller - skipping venv lookup "
            "(will use bundled .exe if available)"
        )
        return None

    venv_dir = None

    # 1. Check AppImage environment (Highest priority for your use case)
    app_dir = os.environ.get("APPDIR")
    if app_dir:
        # Should be at {APPDIR}/bin/.venv
        venv_dir = check_venv(Path(app_dir) / "bin" / ".venv")
        if venv_dir:
            return venv_dir
        # The APPDIR env var is set but the venv wasn't found there.
        # Log what was tried so it's easier to debug future mismatches.
        logger.debug(f"AppImage APPDIR set to '{app_dir}' but no .venv found at {Path(app_dir) / 'bin' / '.venv'}")

    # 1b. Glob-based fallback for AppImages whose mount point has a random suffix
    # e.g. /tmp/.mount_ASSellXYZ/bin/.venv — the suffix changes every run.
    if not venv_dir and sys.platform != "win32":
        import glob
        for candidate in glob.glob("/tmp/.mount_*/bin/.venv"):
            venv_dir = check_venv(Path(candidate))
            if venv_dir:
                logger.debug(f"Found AppImage .venv via glob: {venv_dir}")
                return venv_dir

    # 2. Check relative to this script file (Absolute traversal)
    current_file_dir = Path(__file__).resolve().parent
    for _ in range(4):
        venv_dir = check_venv(current_file_dir / ".venv")
        if venv_dir:
            return venv_dir
        if current_file_dir == current_file_dir.parent:
            break
        current_file_dir = current_file_dir.parent

    # 3. Final Fallback: CWD (Forced to absolute)
    if not venv_dir:
        venv_dir = check_venv(Path.cwd() / ".venv")

    if venv_dir:
        logger.info(f"Found absolute venv path at: {venv_dir}")
    else:
        logger.debug("Could not locate .venv directory")

    return venv_dir


def get_venv_python() -> str | None:
    """Get Python executable path, preferring venv if available."""
    venv_path = get_venv_path()

    if venv_path:
        # Return Python from venv
        if sys.platform == "win32":
            python_exe = venv_path / "Scripts" / "python.exe"
        else:
            python_exe = venv_path / "bin" / "python"

        if python_exe.exists():
            return str(python_exe)

    return None


def get_venv_activate() -> str | None:
    """Get venv activate script path if available."""
    venv_path = get_venv_path()

    if venv_path:
        if sys.platform == "win32":
            activate_script = venv_path / "Scripts" / "activate.bat"
        else:
            activate_script = venv_path / "bin" / "activate"

        if activate_script.exists():
            return str(activate_script)

    return None


def add_gradient_border(
    element: QWidget, accent_color: str, background_color: str
) -> None:
    """Add a gradient border to a UI element."""
    accent_q_color = QColor(accent_color).darker().name()
    bg_q_color = QColor(background_color).darker().name()

    current_style = element.styleSheet()
    border_style = f"""
        border-top: 2px solid qlineargradient(
            x1:0, y1:0, x2:1, y2:0,
            stop:0 {accent_q_color},
            stop:0.5 {bg_q_color},
            stop:1 {accent_q_color}
        );
        border-bottom: 2px solid qlineargradient(
            x1:0, y1:0, x2:1, y2:0,
            stop:0 {accent_q_color},
            stop:0.5 {bg_q_color},
            stop:1 {accent_q_color}
        );
        border-left: 2px solid qlineargradient(
            x1:0, y1:0, x2:0, y2:1,
            stop:0 {accent_q_color},
            stop:0.5 {bg_q_color},
            stop:1 {accent_q_color}
        );
        border-right: 2px solid qlineargradient(
            x1:0, y1:0, x2:0, y2:1,
            stop:0 {accent_q_color},
            stop:0.5 {bg_q_color},
            stop:1 {accent_q_color}
        );
    """
    element.setStyleSheet(current_style + border_style)


def create_slider_setting(
    name: str,
    setting_key: str,
    default_value: int,
    parent_widget: Optional[QWidget] = None,
) -> Tuple[QHBoxLayout, QSlider, QLabel, QPushButton]:
    """Helper function to create a slider setting with value label/reset."""
    layout = QHBoxLayout()

    label = QLabel(f"{name}:")
    label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    label.setFixedWidth(105)

    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setTickPosition(QSlider.TickPosition.TicksBothSides)

    value_label = QLabel(f"{default_value}%")
    value_label.setFixedWidth(30)

    reset_button = QPushButton("Reset")
    reset_button.setFixedHeight(25)
    reset_button.clicked.connect(lambda: slider.setValue(default_value))

    if parent_widget and hasattr(parent_widget, "settings"):
        current_value = parent_widget.settings.value(
            setting_key, default_value, type=int
        )
        slider.setValue(current_value)
        value_label.setText(f"{current_value}%")

        # Connect value change to update label
        def update_label(value):
            value_label.setText(f"{value}%")
            if hasattr(parent_widget, f"on_{setting_key}_changed"):
                getattr(parent_widget, f"on_{setting_key}_changed")(value)

        slider.valueChanged.connect(update_label)

    layout.addWidget(label)
    layout.addWidget(slider, 1)
    layout.addWidget(value_label)
    layout.addWidget(reset_button)

    return layout, slider, value_label, reset_button


class CheckboxSetting(QWidget):
    """A small widget that contains a QCheckBox and an explanatory QLabel.

    It exposes a minimal QCheckBox-like interface (isChecked, setChecked,
    stateChanged signal proxy) so callers can use it like a plain checkbox.
    """

    def __init__(
        self,
        text: str,
        setting_key: str,
        default_value: bool,
        parent_widget: Optional[QWidget] = None,
        tooltip: Optional[str] = None,
    ):
        super().__init__()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.checkbox = QCheckBox(text)

        # Initialize checked state from settings when parent_widget provided
        if parent_widget and hasattr(parent_widget, "settings"):
            current_value = parent_widget.settings.value(
                setting_key, default_value, type=bool
            )
            self.checkbox.setChecked(current_value)

        self.explanation_label = None
        self._layout.addWidget(self.checkbox)
        if tooltip:
            self.checkbox.setToolTip(tooltip)

    def isChecked(self) -> bool:  # noqa: N802
        return self.checkbox.isChecked()

    def setChecked(self, value: bool) -> None:  # noqa: N802
        return self.checkbox.setChecked(value)

    @property
    def stateChanged(self):  # noqa: N802
        return self.checkbox.stateChanged

    def setToolTip(self, text: Optional[str]):  # noqa: N802
        self.checkbox.setToolTip(text)
        if self.explanation_label:
            self.explanation_label.setText(text if text is not None else "")


def create_checkbox_setting(
    text: str,
    setting_key: str,
    default_value: bool,
    parent_widget: Optional[QWidget] = None,
    tooltip: Optional[str] = None,
) -> CheckboxSetting:
    """Helper function to create a checkbox setting."""
    return CheckboxSetting(text, setting_key, default_value, parent_widget, tooltip)


def create_text_setting(
    name: str,
    setting_key: str,
    default_value: str,
    parent_widget: Optional[QWidget] = None,
    placeholder: Optional[str] = None,
    tooltip: Optional[str] = None,
) -> Tuple[QHBoxLayout, QLineEdit]:
    """Helper function to create a text input setting."""
    layout = QHBoxLayout()

    label = QLabel(f"{name}:")
    layout.addWidget(label)

    line_edit = QLineEdit()
    if placeholder:
        line_edit.setPlaceholderText(placeholder)

    if parent_widget and hasattr(parent_widget, "settings"):
        current_value = parent_widget.settings.value(
            setting_key, default_value, type=str
        )
        line_edit.setText(current_value)

    if tooltip:
        line_edit.setToolTip(tooltip)

    layout.addWidget(line_edit)

    return layout, line_edit


def create_color_setting(
    name: str,
    setting_key: str,
    default_color: str,
    parent_widget: Optional[QWidget] = None,
) -> Tuple[QHBoxLayout, QPushButton, QPushButton]:
    """Helper function to create a color picker setting."""
    layout = QHBoxLayout()

    label = QLabel(f"{name}:")

    color_button = QPushButton()
    if parent_widget and hasattr(parent_widget, "settings"):
        current_color = parent_widget.settings.value(
            setting_key, default_color, type=str
        )
        color_button.setStyleSheet(f"background-color: {current_color};")
    else:
        color_button.setStyleSheet(f"background-color: {default_color};")

    reset_button = QPushButton("Reset")

    layout.addWidget(label)
    layout.addWidget(color_button)
    layout.addWidget(reset_button)
    layout.addStretch()

    return layout, color_button, reset_button


def create_font_setting(
    parent_widget: Optional[QWidget] = None,
) -> Tuple[QHBoxLayout, QPushButton, QPushButton]:
    """Helper function to create a font chooser setting."""
    layout = QHBoxLayout()

    label = QLabel("Font:")

    font_button = QPushButton("Choose Font")

    if parent_widget and hasattr(parent_widget, "settings"):
        # Load current font settings
        current_font = QFont()
        current_font.setFamily(parent_widget.settings.value("font", "TrixieCyrG-Plain"))
        current_font.setPointSize(
            parent_widget.settings.value("font-size", 10, type=int)
        )

        font_style = parent_widget.settings.value("font-style", "Normal")
        if font_style == "Italic":
            current_font.setItalic(True)
        elif font_style == "Bold":
            current_font.setBold(True)
        elif font_style == "Bold Italic":
            current_font.setBold(True)
            current_font.setItalic(True)

        font_button.setFont(current_font)
        parent_widget.current_font = current_font

        # Update button text to show current font
        def update_font_text():
            font = parent_widget.current_font
            font_text = f"{font.family()} {font.pointSize()}pt"
            if font.bold() and font.italic():
                font_text += " Bold Italic"
            elif font.bold():
                font_text += " Bold"
            elif font.italic():
                font_text += " Italic"
            font_button.setText(font_text)
            font_button.setFont(font)

        update_font_text()
        parent_widget.update_font_button_text = update_font_text

    reset_button = QPushButton("Reset")

    layout.addWidget(label)
    layout.addWidget(font_button)
    layout.addWidget(reset_button)
    layout.addStretch()

    return layout, font_button, reset_button


def create_font_from_settings(settings) -> QFont:
    """Create a QFont object from application settings."""
    font_family = settings.value("font", "TrixieCyrG-Plain")
    font_size = settings.value("font-size", 10, type=int)
    font_style = settings.value("font-style", "Normal")

    font = QFont(font_family)
    font.setPointSize(font_size)
    if font_style == "Italic":
        font.setItalic(True)
    elif font_style == "Bold":
        font.setBold(True)
    elif font_style == "Bold Italic":
        font.setBold(True)
        font.setItalic(True)

    return font


def get_machine_id() -> bytes:
    import os
    import sys
    # 1. Try /etc/machine-id (Linux standard)
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        return content.encode("utf-8")
        except Exception:
            pass

    # 2. Try Windows registry GUID if on Windows
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography"
            )
            machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            if machine_guid:
                return machine_guid.strip().encode("utf-8")
        except OSError:
            pass

    # 3. Fallback to uuid.getnode()
    import uuid
    return str(uuid.getnode()).encode("utf-8")


def get_encryption_key() -> bytes:
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    # Derive a key from the machine's persistent ID
    machine_id = get_machine_id()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'assella_salt_key_123', # Fixed salt
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(machine_id))


def encrypt_string(plain_text: str) -> str:
    if not plain_text:
        return ""
    from cryptography.fernet import Fernet
    try:
        key = get_encryption_key()
        f = Fernet(key)
        return f.encrypt(plain_text.encode('utf-8')).decode('utf-8')
    except Exception:
        return ""


def decrypt_string(encrypted_text: str) -> str:
    if not encrypted_text:
        return ""
    from cryptography.fernet import Fernet
    
    # Try decrypting using the new persistent machine ID key
    try:
        key = get_encryption_key()
        f = Fernet(key)
        return f.decrypt(encrypted_text.encode('utf-8')).decode('utf-8')
    except Exception:
        pass

    # Fallback to the old MAC-address-based key in case they have an old config
    try:
        import uuid
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        old_machine_id = str(uuid.getnode()).encode('utf-8')
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'assella_salt_key_123',
            iterations=100000,
        )
        old_key = base64.urlsafe_b64encode(kdf.derive(old_machine_id))
        f = Fernet(old_key)
        return f.decrypt(encrypted_text.encode('utf-8')).decode('utf-8')
    except Exception:
        return ""


def get_steam_stats_dir() -> Path | None:
    import sys
    import os
    from pathlib import Path
    if sys.platform == "win32":
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"
            )
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
            winreg.CloseKey(key)
            dest = Path(os.path.normpath(steam_path)) / "appcache/stats"
            return dest
        except OSError:
            return None
    else:
        native_path = Path.home() / ".local/share/Steam"
        symlink_path = Path.home() / ".steam/steam"
        flatpak_path = Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam"
        
        for path in (native_path, symlink_path, flatpak_path):
            if path.exists():
                dest = path / "appcache/stats"
                return dest
    return None


def get_dotnet_env():
    import os
    from pathlib import Path
    import sys
    env = os.environ.copy()
    
    # 1. Clean AppImage library overrides that break .NET runtime host
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    
    # 2. Find local or system .dotnet directory
    local_dotnet = Path.home() / ".dotnet"
    system_dotnet = Path("/usr/share/dotnet")
    usr_lib_dotnet = Path("/usr/lib/dotnet")
    
    dotnet_dir = None
    if local_dotnet.exists():
        dotnet_dir = local_dotnet
    elif system_dotnet.exists():
        dotnet_dir = system_dotnet
    elif usr_lib_dotnet.exists():
        dotnet_dir = usr_lib_dotnet
        
    if dotnet_dir:
        # Set DOTNET_ROOT (required for .NET to find runtimes)
        env["DOTNET_ROOT"] = str(dotnet_dir)
        # Prepend to PATH so dotnet executable is found if needed
        env["PATH"] = f"{dotnet_dir}:{env.get('PATH', '')}"
        
    return env


from utils.dlc_helpers import get_dlc_only_info, is_dlc_only_mode




import logging
import random
import time
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.dialogs.themed_dialog import install_themed_frame

from components.custom_widgets import ScaledButton, ScaledFontLabel

logger = logging.getLogger(__name__)


class LainMinigameDialog(QDialog):
    """Serial Experiments Lain themed minigame: The Wired Terminal."""

    game_completed = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("The Wired Terminal")
        self.resize(800, 600)
        self.setMinimumSize(600, 500)

        # UI Elements
        self.score_label: Optional[ScaledFontLabel] = None
        self.level_label: Optional[ScaledFontLabel] = None
        self.completed_label: Optional[ScaledFontLabel] = None
        self.time_bar: Optional[QProgressBar] = None
        self.terminal_display: Optional[QTextEdit] = None
        self.target_label: Optional[ScaledFontLabel] = None
        self.start_button: Optional[QPushButton] = None
        self.command_buttons: List[ScaledButton] = []
        self.layout: Optional[QVBoxLayout] = None
        self.game_timer: Optional[QTimer] = None
        self.message_timer: Optional[QTimer] = None
        self.bonus_timer: Optional[QTimer] = None

        # Game state
        self.score = 0
        self.level = 1
        self.time_left = 0.0
        self.max_time = 0.0
        self.game_active = False
        self.current_commands: List[str] = []
        self.current_target = ""
        self.completed_sequences = 0
        self.grace_period_time = 0.0
        self.bonus_time_to_add = 0.0
        self.grace_duration = 0.0
        self.grace_duration_start_from = 0.0
        self.commands_pool: List[str] = []

        self._initialize_game_state()
        self._setup_ui()
        self._setup_timers()
        self._print_welcome_message()

        logger.debug("LainMinigameDialog initialized.")

    def _initialize_game_state(self) -> None:
        """Initialize all game state variables."""
        self.score = 0
        self.level = 1
        self.time_left = 120.0
        self.max_time = 120.0
        self.game_active = False
        self.current_commands: List[str] = []
        self.current_target = ""
        self.completed_sequences = 0
        self.grace_period_time = 0.0
        self.bonus_time_to_add = 0.0
        self.grace_duration = 0.0
        self.grace_duration_start_from = 3.0

        self.commands_pool = [
            "CONNECT",
            "DISCONNECT",
            "NAVI",
            "LAYER09",
            "SCHIZOPHRENIA",
            "KNIGHTS",
            "ACID",
            "PROTOCOL7",
            "WIRED",
            "TACHIKOMA",
            "LETSALLLOVELAIN",
            "ECHIDNA",
            "BLUE",
            "ROSE",
            "PSYCHO",
            "DIVINE",
            "CHIPS",
            "NEURONS",
            "MEMORY",
            "REALITY",
            "INTERFACE",
            "SERVER",
            "CLIENT",
            "UPLOAD",
            "GODISINTHEWIRED",
            "ASSELLA",
            "BABEL",
            "CYBERIA",
            "DEUS",
            "EPTO",
            "FRAGMENT",
            "GIG",
            "HORNET",
            "INFORNO",
            "JACKIN",
            "LILITH",
            "MASK",
            "NOISE",
            "OMEGA",
            "PHANTOM",
            "QUANTUM",
            "ROOT",
            "SCILAB",
            "TRANCE",
            "UNIX",
            "VOID",
            "WAVE",
            "XANADU",
            "YGGDRASIL",
            "ZERO",
            "ANOMALY",
            "BEAR",
            "CRYPT",
            "DARK",
            "ENTITY",
            "FLOW",
            "GHOST",
            "HACK",
            "ICON",
            "JUDGMENT",
            "KEY",
            "LOGOS",
            "METAVERSE",
            "NODE",
            "OSCILLATION",
            "PARADOX",
            "QUERY",
            "RIBBON",
            "SHADOW",
            "TUNNEL",
            "UNKNOWN",
            "VISION",
            "WALL",
            "XEROX",
            "YOUTH",
            "ZEAL",
            "ABSTRACT",
            "BOOT",
            "CHAIN",
            "DREAM",
            "ERROR",
            "FALSE",
            "GATE",
            "HELLO",
            "IDENTITY",
            "JUMP",
            "KNOT",
            "LEGACY",
            "MIRAGE",
            "NET",
            "ORACLE",
            "PRIME",
            "QUEST",
            "RIFT",
            "SIGNAL",
            "TRACE",
            "UTOPIA",
            "VEIL",
            "WITNESS",
            "XENON",
            "YIELD",
            "ZENITH",
        ]

    def _setup_ui(self) -> None:
        """Orchestrate the creation of the user interface."""
        self.layout = install_themed_frame(self, margins=(12, 10, 12, 12), spacing=8)
        self._create_header()
        self._create_stats_display()
        self._create_terminal_display()
        self._create_command_grid()
        self._create_controls()

    def _create_header(self) -> None:
        """Create the title and subtitle labels."""
        title = ScaledFontLabel("THE WIRED TERMINAL")
        title.setMinimumHeight(48)
        self.layout.addWidget(title)

        subtitle = ScaledFontLabel("Layer 0" + str(random.randint(1, 9)))
        subtitle.setFixedHeight(36)
        self.layout.addWidget(subtitle)

    def _create_stats_display(self) -> None:
        """Create the score, level, and time display area."""
        stats_layout = QHBoxLayout()

        self.score_label = ScaledFontLabel(f"SCORE: {self.score:06d}")
        self.score_label.setFixedHeight(36)
        stats_layout.addWidget(self.score_label)

        self.level_label = ScaledFontLabel(f"LAYER: {self.level}")
        self.level_label.setFixedHeight(36)
        stats_layout.addWidget(self.level_label)

        self.completed_label = ScaledFontLabel(f"SEQUENCES: {self.completed_sequences}")
        self.completed_label.setFixedHeight(36)
        stats_layout.addWidget(self.completed_label)

        self.layout.addLayout(stats_layout, 2)

        self.time_bar = QProgressBar()
        self.time_bar.setRange(0, int(self.max_time))
        self.time_bar.setValue(int(self.time_left))
        self.time_bar.setFormat("TIME REMAINING: %v SECONDS")
        self.layout.addWidget(self.time_bar, 1)

    def _create_terminal_display(self) -> None:
        """Create the terminal output area."""
        terminal_group = QGroupBox("TERMINAL OUTPUT")
        terminal_layout = QVBoxLayout()

        self.terminal_display = QTextEdit()
        self.terminal_display.setReadOnly(True)
        self.terminal_display.setMaximumHeight(150)
        terminal_layout.addWidget(self.terminal_display, 3)

        self.target_label = ScaledFontLabel("TARGET SEQUENCE:")
        self.target_label.setFixedHeight(38)
        self.target_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        terminal_layout.addWidget(self.target_label, 2)

        terminal_group.setLayout(terminal_layout)
        self.layout.addWidget(terminal_group, 1)

    def _create_command_grid(self) -> None:
        """Create the grid of command buttons."""
        commands_group = QGroupBox("AVAILABLE COMMANDS")
        commands_layout = QGridLayout()

        self.command_buttons = []
        for i in range(4):  # 4 rows
            for j in range(3):  # 3 columns
                btn = ScaledButton("")
                btn.setMinimumHeight(30)
                btn.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
                )
                btn.setHidden(True)
                index = i * 3 + j
                btn.clicked.connect(self._create_button_handler(index))
                commands_layout.addWidget(btn, i, j)
                self.command_buttons.append(btn)

        commands_group.setLayout(commands_layout)
        self.layout.addWidget(commands_group, 3)

    def _create_controls(self) -> None:
        """Create the bottom control buttons."""
        control_layout = QHBoxLayout()
        self.start_button = QPushButton("INITIALIZE CONNECTION")
        self.start_button.clicked.connect(self.start_game)
        control_layout.addWidget(self.start_button)
        self.layout.addLayout(control_layout, 1)

    def _setup_timers(self) -> None:
        """Initialize game timers."""
        # Main game loop (100ms for smooth UI updates)
        self.game_timer = QTimer()
        self.game_timer.timeout.connect(self.update_timer)

        # Flavor text timer
        self.message_timer = QTimer()
        self.message_timer.timeout.connect(self.add_terminal_message)
        self.message_timer.setInterval(3000)

        # Bonus time accumulator
        self.bonus_timer = QTimer()
        self.bonus_timer.timeout.connect(self.add_bonus_time)
        self.bonus_timer.setInterval(200)

    def _create_button_handler(self, index: int) -> Callable[[], None]:
        """Create a closure for button click handling."""
        return lambda: self.command_clicked(index)

    def _print_welcome_message(self) -> None:
        """Print initial terminal text."""
        self._add_terminal_text(">>> SYSTEM BOOT...\n")
        self._add_terminal_text(">>> WIRED TERMINAL v1.337\n")
        self._add_terminal_text(">>> WELCOME TO LAYER 09\n")
        self._add_terminal_text(">>> INITIALIZE CONNECTION TO BEGIN\n")

    def _add_terminal_text(self, text: str) -> None:
        """Add text to terminal and scroll to bottom."""
        if not self.terminal_display:
            return
        current = self.terminal_display.toPlainText()
        self.terminal_display.setText(current + text)
        scrollbar = self.terminal_display.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def start_game(self) -> None:
        """Initialize and start the gameplay loop."""
        if self.game_active:
            return

        self.game_active = True
        self.score = 0
        self.level = 1
        self.time_left = self.max_time
        self.completed_sequences = 0
        self.grace_period_time = 0.0
        self.bonus_time_to_add = 0.0
        self.update_display()

        if self.start_button:
            self.start_button.setHidden(True)

        for btn in self.command_buttons:
            btn.setHidden(False)

        self._add_terminal_text("\n>>> CONNECTION ESTABLISHED\n")
        self._add_terminal_text(">>> PROTOCOL SYNCHRONIZED\n")
        self._add_terminal_text(">>> BEGIN INPUT SEQUENCE\n")

        self.generate_new_sequence()
        self.game_timer.start(100)
        self.message_timer.start()

        logger.info("Lain minigame started.")

    def generate_new_sequence(self) -> None:
        """Generate a new target sequence and populate buttons."""
        length = min(3 + self.level // 2, 8)
        self.current_commands = random.sample(self.commands_pool, length)
        self.current_target = " -> ".join(self.current_commands)

        if self.target_label:
            self.target_label.setText(f"TARGET SEQUENCE: {self.current_target}")

        # Prepare button labels
        button_cmds = list(self.current_commands)
        all_cmds = list(self.commands_pool)
        random.shuffle(all_cmds)

        # Fill remaining slots with random commands
        for cmd in all_cmds:
            if len(button_cmds) >= 12:
                break
            if cmd not in button_cmds:
                button_cmds.append(cmd)

        while len(button_cmds) < 12:
            button_cmds.append(random.choice(self.commands_pool))

        random.shuffle(button_cmds)

        for i, btn in enumerate(self.command_buttons):
            btn.setText(button_cmds[i])
            btn.setEnabled(True)

        self._add_terminal_text(f">>> NEW TARGET: {self.current_target}\n")

    def command_clicked(self, index: int) -> None:
        """Handle button clicks."""
        if not self.game_active or self.grace_period_time > 0:
            return

        cmd = self.command_buttons[index].text()

        if self.current_commands and cmd == self.current_commands[0]:
            self._handle_correct_command(index, cmd)
        else:
            self._handle_wrong_command(cmd)

    def _handle_correct_command(self, index: int, cmd: str) -> None:
        """Process a correct input."""
        self.current_commands.pop(0)
        self.command_buttons[index].setEnabled(False)
        self._add_terminal_text(f">>> INPUT: {cmd} ✓ [+1.0s]\n")
        self.time_left += 0.5

        if not self.current_commands:
            self.sequence_completed()

    def _handle_wrong_command(self, cmd: str) -> None:
        """Process an incorrect input."""
        self.score = max(0, self.score - 50)
        self.time_left = max(10.0, self.time_left - 5.0)
        self._add_terminal_text(f">>> INPUT: {cmd} ✗ [-5.0s]\n")
        self.update_display()

    def sequence_completed(self) -> None:
        """Handle successful sequence completion."""
        seq_bonus = 100 * self.level
        time_bonus = int(self.time_left / self.max_time * 200)
        self.score += seq_bonus + time_bonus
        self.completed_sequences += 1

        self._add_terminal_text(">>> SEQUENCE COMPLETE ✓\n")
        self._add_terminal_text(
            f">>> BONUS: {seq_bonus} + TIME: {time_bonus * 0.01}s\n"
        )

        if self.completed_sequences % 3 == 0:
            self.level += 1
            self._add_terminal_text(f">>> ACCESSING LAYER {self.level:02d}\n")

        self.add_terminal_message()
        self._initiate_grace_period(time_bonus)

    def _initiate_grace_period(self, time_bonus: int) -> None:
        """Start the cool-down phase between sequences."""
        decrement = self.completed_sequences * 0.25
        self.grace_duration = max(0.5, self.grace_duration_start_from - decrement)
        self.bonus_time_to_add = float(time_bonus) * 0.01
        self.grace_period_time = self.grace_duration
        self.bonus_timer.start()

        msg = random.choice(
            [
                ">>> Taking a break... connecting to alternate reality",
                ">>> Relaxing for a moment in the Wired",
                ">>> Syncing neural patterns... momentary pause",
                ">>> Consciousness fragmentation in progress",
                ">>> Brief interface recalibration",
                ">>> Let's all love Lain for a moment",
                ">>> Experiencing temporal dilation",
                ">>> Memory fragment analysis initiated",
                ">>> Protocol 7: Momentary disconnection",
                ">>> Scanning for new layers... please wait",
                ">>> Reality distortion field stabilizing",
                ">>> ECHIDNA system processing complete sequences",
                ">>> Tachikoma units performing maintenance",
                ">>> Navi recommends a brief respite",
                ">>> Psyche integration in progress",
                ">>> Wired signal strength recalibrating",
                ">>> Consciousness upload paused",
                ">>> Interface with Layer 09 momentarily suspended",
            ]
        )
        self._add_terminal_text(f"{msg} [{self.grace_duration:.1f}s]\n")
        self.update_display()
        self.generate_new_sequence()

    def add_bonus_time(self) -> None:
        """Incrementally add bonus time during grace period."""
        if self.bonus_time_to_add > 0 and self.grace_period_time > 0:
            tick_add = min(0.2, self.bonus_time_to_add)
            self.time_left += tick_add
            self.bonus_time_to_add -= tick_add
            self.update_display()

            if self.bonus_time_to_add <= 0:
                self.bonus_timer.stop()

    def update_timer(self) -> None:
        """Main game loop tick handler."""
        if not self.game_active:
            return

        if self.grace_period_time > 0:
            self._update_grace_period()
        else:
            self._update_game_timer()

        self.update_display()

    def _update_grace_period(self) -> None:
        """Handle grace period countdown."""
        self.grace_period_time -= 0.1
        if self.grace_period_time <= 0:
            self.grace_period_time = 0.0
            self.time_left += self.bonus_time_to_add
            self.bonus_time_to_add = 0.0
            self.bonus_timer.stop()

            msg = random.choice(
                [
                    ">>> Entering the Wired once more",
                    ">>> Neural pathways re-engaged",
                    ">>> Protocol 7 reactivated",
                    ">>> Reconnecting to Layer 09",
                    ">>> Consciousness stream resumed",
                    ">>> Interface synchronization complete",
                    ">>> Present day, present time",
                    ">>> God is in the Wired",
                    ">>> Let's all love Lain",
                    ">>> ECHIDNA system online",
                    ">>> Navi connection restored",
                    ">>> Reality distortion: nominal",
                    ">>> Memory fragments integrated",
                    ">>> Psyche monitor: active",
                    ">>> Wired access: granted",
                    ">>> Tachikoma units: ready",
                    ">>> Schizophrenia protocol: standby",
                    ">>> Layer 09: accessible",
                ]
            )
            self._add_terminal_text(f"{msg}\n")

    def _update_game_timer(self) -> None:
        """Handle normal gameplay countdown."""
        self.time_left -= 0.1
        if self.time_left <= 0:
            self.end_game()

    def update_display(self) -> None:
        """Refresh UI elements."""
        if self.score_label:
            self.score_label.setText(f"SCORE: {self.score:06d}")
        if self.level_label:
            self.level_label.setText(f"LAYER: {self.level}")
        if self.completed_label:
            self.completed_label.setText(f"SEQUENCES: {self.completed_sequences}")
        if self.time_bar:
            self.time_bar.setValue(int(self.time_left))
            self._update_time_bar_style()

    def _update_time_bar_style(self) -> None:
        """Update progress bar color based on state."""
        if not self.time_bar:
            return

        if self.grace_period_time > 0:
            self.time_bar.setFormat(f"GRACE PERIOD: {self.grace_period_time:.1f}s")
            flash = int(time.time() * 5) % 2
            color = "white" if flash else "#00cc00"
            text_color = "black" if flash else "white"
            self.time_bar.setStyleSheet(
                f"""
                QProgressBar {{ color: {text_color}; text-align: center; }}
                QProgressBar::chunk {{ background-color: {color}; }}
            """
            )
        elif self.time_left <= 30:
            self.time_bar.setFormat(f"⚠ TIME CRITICAL: {self.time_left:.1f}s ⚠")
            flash = int(self.time_left * 5) % 2
            color = "#ff0066" if flash else "#00aaff"
            self.time_bar.setStyleSheet(
                f"""
                QProgressBar::chunk {{ background-color: {color}; }}
            """
            )
        else:
            self.time_bar.setFormat(f"TIME REMAINING: {self.time_left:.1f}s")
            self.time_bar.setStyleSheet("")

    def add_terminal_message(self) -> None:
        """Inject random atmospheric messages."""
        if not self.game_active:
            return

        messages = [
            ">>> NAVI: ALL IS WELL",
            ">>> SCANNING FREQUENCIES",
            ">>> MEMORY FRAGMENTS DETECTED",
            ">>> LET'S ALL LOVE LAIN",
            ">>> PROTOCOL 7 ACTIVE",
            ">>> CONSCIOUSNESS STREAMING",
            ">>> TACHIKOMA UNITS ONLINE",
            ">>> REALITY DISTORTION: 0." + str(random.randint(1, 99)),
            ">>> ECHIDNA SYSTEM NOMINAL",
            ">>> CONNECTING TO THE WIRED",
            ">>> PSYCHE MONITOR: STABLE",
            ">>> SCHIZOPHRENIA PROTOCOL: DISABLED",
            ">>> KNIGHTS OF THE EASTERN CALCULUS: ACTIVE",
            ">>> ASSella INTERFACE: STABLE",
            ">>> CYBERIA CAFE: CONNECTED",
            ">>> BABLE PROTOCOL: ENGAGED",
            ">>> DEUS EX MACHINA: STANDBY",
            ">>> PHANTOM CONSCIOUSNESS DETECTED",
            ">>> QUANTUM ENTANGLEMENT: NOMINAL",
            ">>> ROOT ACCESS: GRANTED",
            ">>> TRANCE STATE: MAINTAINED",
            ">>> VOID PROTOCOL: ACTIVE",
            ">>> WAVE FUNCTION COLLAPSED",
            ">>> XANADU ACCESS POINT: FOUND",
            ">>> YGGDRASIL CONNECTION: SECURE",
            ">>> ZERO DAY EXPLOIT: PATCHED",
            ">>> ANOMALY DETECTED IN LAYER 09",
            ">>> CRYPTIC MESSAGE DECODING",
            ">>> DARK MATTER INTERFACE: ONLINE",
            ">>> ENTITY RECOGNITION: ACTIVE",
            ">>> FLOW CONTROL: OPTIMAL",
            ">>> GHOST IN THE MACHINE: ABSENT",
            ">>> HACK ATTEMPT: DEFLECTED",
            ">>> ICON GENERATION: COMPLETE",
            ">>> JUDGMENT PROTOCOL: DISABLED",
            ">>> KEY EXCHANGE: SUCCESSFUL",
            ">>> LOGOS INTEGRATION: STABLE",
            ">>> METAVERSE GATEWAY: OPEN",
            ">>> NODE SYNCHRONIZATION: 100%",
            ">>> OSCILLATION FREQUENCY: LOCKED",
            ">>> PARADOX RESOLUTION: IN PROGRESS",
            ">>> QUERY RESOLVED",
            ">>> RIBBON CABLE: SECURE",
            ">>> SHADOW PROTOCOL: ENGAGED",
            ">>> TUNNEL TO LAYER 08: OPEN",
            ">>> UNKNOWN SIGNAL: ANALYZING",
            ">>> VISION AUGMENTATION: ACTIVE",
            ">>> WALL BREACH: CONTAINED",
            ">>> XEROX PARC PROTOCOL: ACTIVE",
            ">>> YOUTH MEMORY: ACCESSED",
            ">>> ZEAL MODE: DISABLED",
        ]

        if random.random() < 0.2:
            self._add_terminal_text(random.choice(messages) + "\n")

    def end_game(self) -> None:
        """Conclude the game session and show results."""
        self.game_active = False
        self.game_timer.stop()
        self.message_timer.stop()
        self.bonus_timer.stop()
        self.grace_period_time = 0.0
        self.bonus_time_to_add = 0.0

        lvl_bonus = self.level * 50
        seq_bonus = self.completed_sequences * 75
        final = self.score + lvl_bonus + seq_bonus

        self._add_terminal_text("\n>>> CONNECTION TERMINATED\n")
        self._add_terminal_text(">>> SESSION SUMMARY:\n")
        self._add_terminal_text(f">>> LEVEL REACHED: {self.level}\n")
        self._add_terminal_text(f">>> SEQUENCES: {self.completed_sequences}\n")
        self._add_terminal_text(f">>> FINAL SCORE: {final}\n")

        if final >= 1000:
            quote = ">>> LET'S ALL LOVE LAIN"
        elif final >= 500:
            quote = ">>> PRESENT DAY, PRESENT TIME"
        elif final >= 200:
            quote = ">>> AND YOU DON'T SEEM TO UNDERSTAND"
        else:
            quote = ">>> GOD IS IN THE WIRED"

        self._add_terminal_text(f">>> {quote}\n")

        if self.start_button:
            self.start_button.setHidden(False)
            self.start_button.setText("REINITIALIZE CONNECTION")

        for btn in self.command_buttons:
            btn.setText("")
            btn.setEnabled(False)
            btn.setHidden(True)

        if self.time_bar:
            self.time_bar.setStyleSheet("")

        self.game_completed.emit(final)
        logger.info(f"Lain minigame ended with score: {final}")

        reply = ThemedMessageBox.question(
            self,
            "Session Terminated",
            f"Final Score: {final}\n\nWould you like to play again?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
            ThemedMessageBox.StandardButton.Yes,
        )

        if reply == ThemedMessageBox.StandardButton.Yes:
            if self.terminal_display:
                self.terminal_display.clear()
            self._add_terminal_text(">>> REINITIALIZING...\n")
            self._add_terminal_text(">>> THE WIRED AWAITS\n")
            self.start_game()

    def closeEvent(self, event) -> None:
        """Clean up resources on close."""
        self.game_timer.stop()
        self.message_timer.stop()
        self.bonus_timer.stop()
        super().closeEvent(event)

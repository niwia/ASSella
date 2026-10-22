#!/usr/bin/env python3
"""Test the themed message box: the drop-in replacement for QMessageBox.

The dialog has to look like ASSella and behave like the native box it replaces,
so these checks pin the QMessageBox parity (button names and values, the four
classmethods and their default buttons) as well as the parts the previous
implementation got wrong: the window title, the requested default button, an
icon that says how bad the news is, and long text that stays on screen.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_themed_message_box.py
"""
import inspect
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []
PASSED = 0


def check(cond, label):
    global PASSED
    if cond:
        PASSED += 1
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return cond


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("XDG_RUNTIME_DIR", "/tmp")

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QScrollArea

    app = QApplication.instance() or QApplication([])

    from ui.dialogs.themed_message_box import (
        StandardButton,
        ThemedMessageBox,
        _SeverityIcon,
    )

    def build(title="Title", text="Some text", buttons=StandardButton.Ok,
              default_button=None, severity="information", informative=""):
        dlg = ThemedMessageBox(
            title=title,
            text=text,
            buttons=buttons,
            default_button=default_button,
            severity=severity,
            informative=informative,
        )
        return dlg

    def call(method_name, click, *args):
        """Run a classmethod with exec() stubbed out, returning (dialog, reply)."""
        created = []

        def fake_exec(self):
            created.append(self)
            self._buttons[click].click()

        original = ThemedMessageBox.exec
        ThemedMessageBox.exec = fake_exec
        try:
            reply = getattr(ThemedMessageBox, method_name)(None, *args)
        finally:
            ThemedMessageBox.exec = original
        assert created, f"{method_name} never built a dialog"
        return created[0], reply

    def call_choices(click_label, *args, **kwargs):
        """Run choices() with exec() stubbed, clicking the named button (None = just close)."""
        created = []

        def fake_exec(self):
            created.append(self)
            if click_label is None:
                return
            for btn in self.findChildren(QPushButton):
                if btn.text() == click_label:
                    btn.click()
                    return
            raise AssertionError(f"no button labelled {click_label}")

        original = ThemedMessageBox.exec
        ThemedMessageBox.exec = fake_exec
        try:
            reply = ThemedMessageBox.choices(None, *args, **kwargs)
        finally:
            ThemedMessageBox.exec = original
        assert created, "choices never built a dialog"
        return created[0], reply

    print("\n-- QMessageBox parity --")
    same = True
    for name in ("NoButton", "Ok", "Cancel", "Yes", "No"):
        ours = int(getattr(StandardButton, name))
        theirs = int(getattr(QMessageBox.StandardButton, name))
        if ours != theirs:
            same = False
            print(f"     {name}: {ours} != {theirs}")
    check(same, "StandardButton members carry QMessageBox's own values")

    # PyQt6's own statics are builtins with no inspectable signature, so the
    # parity that matters is pinned by calling them: same name, same argument
    # order, and the same buttons when none is asked for.
    for name in ("information", "warning", "critical", "question"):
        params = list(inspect.signature(getattr(ThemedMessageBox, name)).parameters)
        check(
            params[:5] == ["parent", "title", "text", "buttons", "default_button"],
            f"{name}() takes (parent, title, text, buttons, default) like QMessageBox",
        )
        check(
            params[5:] == ["informative"],
            f"{name}() adds an informative line on top of the QMessageBox signature",
        )

    for name, expected in (
        ("information", [StandardButton.Ok]),
        ("warning", [StandardButton.Ok]),
        ("critical", [StandardButton.Ok]),
        ("question", [StandardButton.Yes, StandardButton.No]),
    ):
        built, _reply = call(name, expected[0], "Title", "text")
        check(list(built._buttons) == expected, f"{name}() defaults to the same buttons as QMessageBox")

    check(ThemedMessageBox.Ok == StandardButton.Ok, "ThemedMessageBox.Ok is the same member")
    check(
        ThemedMessageBox.Yes | ThemedMessageBox.No
        == QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        "buttons combine the same way",
    )

    print("\n-- the box itself --")
    dlg = build(title="Heads up", text="Something happened")
    check(dlg.windowTitle() == "Heads up", "the window title is set")
    check(bool(dlg.windowFlags() & Qt.WindowType.FramelessWindowHint), "the dialog is frameless")
    check(
        dlg.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
        "the dialog asks for a translucent background",
    )
    check(dlg.windowModality() == Qt.WindowModality.WindowModal, "the dialog is window-modal")

    labels = [lbl for lbl in dlg.findChildren(QLabel) if lbl.text() == "Something happened"]
    check(len(labels) == 1, "the message text is rendered")
    msg = labels[0]
    check(msg.textFormat() == Qt.TextFormat.PlainText, "the text is plain, so markup is never eaten")
    check(
        bool(msg.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse),
        "the text can be selected",
    )
    check(msg.wordWrap(), "the text wraps")

    print("\n-- buttons --")
    dlg = build(buttons=StandardButton.Yes | StandardButton.No)
    check(list(dlg._buttons) == [StandardButton.Yes, StandardButton.No], "Yes | No lays out in order")
    check([b.text() for b in dlg._buttons.values()] == ["Yes", "No"], "with the platform's labels")

    dlg = build(buttons=StandardButton.Ok | StandardButton.Cancel)
    check(list(dlg._buttons) == [StandardButton.Ok, StandardButton.Cancel], "OK | Cancel lays out in order")

    dlg = build(
        buttons=StandardButton.Ok | StandardButton.Yes | StandardButton.No | StandardButton.Cancel
    )
    check(len(dlg._buttons) == 4, "all four buttons can coexist")

    dlg = build(buttons=StandardButton.Yes | StandardButton.No)
    dlg._buttons[StandardButton.Yes].click()
    check(dlg._result_value == StandardButton.Yes, "clicking Yes records Yes")
    check(dlg.result() == 1, "clicking Yes accepts the dialog")

    dlg = build(buttons=StandardButton.Yes | StandardButton.No)
    dlg._buttons[StandardButton.No].click()
    check(dlg._result_value == StandardButton.No, "clicking No records No")
    check(dlg.result() == 0, "clicking No rejects the dialog")
    check(dlg._result_value != StandardButton.NoButton, "a rejection is not confused with closing")

    print("\n-- the default button --")
    dlg = build(buttons=StandardButton.Yes | StandardButton.No)
    check(dlg._buttons[StandardButton.Yes].isDefault(), "Yes | No defaults to Yes when none is asked for")

    dlg = build(buttons=StandardButton.Yes | StandardButton.No, default_button=StandardButton.No)
    check(dlg._buttons[StandardButton.No].isDefault(), "an explicit default is honoured")

    dlg = build(buttons=StandardButton.Ok, default_button=StandardButton.Ok)
    check(dlg._buttons[StandardButton.Ok].isDefault(), "OK is the default when it is the only button")

    dlg = build(buttons=StandardButton.Yes | StandardButton.No, default_button=StandardButton.Cancel)
    check(dlg._buttons[StandardButton.Yes].isDefault(), "a default that is not on offer falls back to Yes")

    print("\n-- severity --")
    colours = {}
    for severity in ("information", "question", "warning", "critical"):
        dlg = build(severity=severity)
        icons = dlg.findChildren(_SeverityIcon)
        check(len(icons) == 1, f"the {severity} box carries one severity mark")
        colours[severity] = icons[0]._colour.name()
    check(colours["warning"] != colours["critical"], "warning and critical do not look alike")
    check(len(set(colours.values())) == 4, "each severity has its own colour")
    check(colours["critical"] == "#ef4444", "critical is red")
    check(colours["warning"] == "#f59e0b", "warning is amber")

    print("\n-- long text --")
    dlg = build(text="short")
    check(not dlg.findChildren(QScrollArea), "a short message needs no scroll area")
    dlg = build(text="line\n" * 200)
    check(bool(dlg.findChildren(QScrollArea)), "a long message gets a scroll area")

    print("\n-- the four entry points --")
    dlg, reply = call("information", StandardButton.Ok, "Info", "All good")
    check(reply == StandardButton.Ok and list(dlg._buttons) == [StandardButton.Ok], "information() offers OK")
    check(dlg.windowTitle() == "Info", "information() passes the title through")

    dlg, reply = call("warning", StandardButton.Ok, "Careful", "Watch out")
    check(reply == StandardButton.Ok, "warning() offers OK")
    check(dlg.windowTitle() == "Careful", "warning() passes the title through")

    dlg, reply = call("critical", StandardButton.Ok, "Failed", "It broke")
    check(reply == StandardButton.Ok, "critical() offers OK")
    check(dlg.windowTitle() == "Failed", "critical() passes the title through")

    dlg, reply = call("question", StandardButton.Yes, "Sure?", "Really do it?")
    check(reply == StandardButton.Yes, "question() returns the button that was clicked")
    check(list(dlg._buttons) == [StandardButton.Yes, StandardButton.No], "question() asks Yes/No")
    check(dlg.findChildren(_SeverityIcon)[0]._colour.name() == "#8b5cf6", "question() marks itself as a question")

    dlg, reply = call("question", StandardButton.Cancel, "Sure?", "Really do it?",
                      StandardButton.Yes | StandardButton.No | StandardButton.Cancel)
    check(reply == StandardButton.Cancel, "question() honours a wider button set")

    print("\n-- the informative line --")
    dlg = build(text="First thought", informative="And a second one")
    check(
        any(lbl.text() == "And a second one" for lbl in dlg.findChildren(QLabel)),
        "an informative line is drawn under the text",
    )
    dlg = build(text="First thought")
    check(
        not any(lbl.text() == "And a second one" for lbl in dlg.findChildren(QLabel)),
        "no informative line when none is asked for",
    )
    dlg, reply = call("warning", StandardButton.Ok, "Careful", "Watch out",
                      StandardButton.Ok, None, "with a caveat")
    check(
        any(lbl.text() == "with a caveat" for lbl in dlg.findChildren(QLabel)),
        "the classmethods pass the informative line through",
    )

    print("\n-- the caller's own buttons --")
    dlg, reply = call_choices("Retry", "Title", "Body",
                              [("Retry", "retry"), ("Cancel", "cancel")], default="cancel")
    check(reply == "retry", "choices() returns the value of the button that was clicked")
    check([b.text() for b in dlg.findChildren(QPushButton) if b.text()] == ["Retry", "Cancel"],
          "choices() lays the caller's buttons out in order")
    check([b for b in dlg.findChildren(QPushButton) if b.text() == "Cancel"][0].isDefault(),
          "choices() gives the default value the focus")

    dlg, reply = call_choices(None, "Title", "Body", [("Only", "only")])
    check(reply is None, "closing choices() without a click answers None")
    check([b for b in dlg.findChildren(QPushButton) if b.text() == "Only"][0].isDefault(),
          "choices() defaults to the first button when none is named")
    check(not [b for b in dlg.findChildren(QPushButton) if b.text() in ("OK", "Yes", "No")],
          "choices() adds no standard buttons of its own")

    print()
    if _failures:
        print(f"FAILED {len(_failures)} of {PASSED + len(_failures)} checks:")
        for label in _failures:
            print(f"  - {label}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

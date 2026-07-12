from mtyh.logic.macro import MacroSettings, TrainingExportMacro


class FakeGui:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def hotkey(self, *keys: str) -> None:
        self.calls.append(("hotkey", keys))

    def typewrite(self, text: str) -> None:
        self.calls.append(("typewrite", (text,)))

    def press(self, key: str) -> None:
        self.calls.append(("press", (key,)))


def test_training_macro_runs_expected_key_sequence() -> None:
    gui = FakeGui()
    sleeps: list[float] = []
    macro = TrainingExportMacro(
        MacroSettings(prefix="sample", counter=5, initial_delay_seconds=0.0),
        pyautogui_module=gui,
        sleeper=sleeps.append,
    )

    result = macro.run_step()

    assert result.filename == "sample5"
    assert result.next_counter == 6
    assert gui.calls == [
        ("hotkey", ("ctrl", "shift", "e")),
        ("typewrite", ("sample5",)),
        ("press", ("enter",)),
        ("press", ("enter",)),
        ("hotkey", ("ctrl", "z")),
    ]
    assert macro.counter == 6

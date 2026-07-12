from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)


@dataclass
class MacroSettings:
    prefix: str = "newtrainingdata"
    counter: int = 161
    initial_delay_seconds: float = 2.0


@dataclass(frozen=True)
class MacroStepResult:
    filename: str
    next_counter: int


class TrainingExportMacro:
    def __init__(
        self,
        settings: MacroSettings | None = None,
        pyautogui_module: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or MacroSettings()
        self._pyautogui_module = pyautogui_module
        self._sleep = sleeper

    @property
    def counter(self) -> int:
        return int(self.settings.counter)

    @counter.setter
    def counter(self, value: int) -> None:
        self.settings.counter = max(0, int(value))

    def build_filename(self) -> str:
        prefix = self.settings.prefix.strip() or "newtrainingdata"
        return f"{prefix}{self.counter}"

    def _pyautogui(self) -> Any:
        if self._pyautogui_module is not None:
            return self._pyautogui_module
        import pyautogui

        return pyautogui

    def run_step(self) -> MacroStepResult:
        delay = max(0.0, float(self.settings.initial_delay_seconds))
        if delay:
            LOGGER.info("Waiting %.1f seconds before running macro step", delay)
            self._sleep(delay)

        filename = self.build_filename()
        gui = self._pyautogui()

        LOGGER.info("Running export macro step for %s", filename)
        gui.hotkey("ctrl", "shift", "e")
        self._sleep(0.2)
        gui.typewrite(filename)
        self._sleep(0.2)
        gui.press("enter")
        self._sleep(1.0)
        gui.press("enter")
        self._sleep(0.2)
        gui.hotkey("ctrl", "z")

        self.counter = self.counter + 1
        LOGGER.info("Macro step completed; next counter is %s", self.counter)
        return MacroStepResult(filename=filename, next_counter=self.counter)

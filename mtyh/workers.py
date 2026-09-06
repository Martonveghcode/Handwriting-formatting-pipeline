from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

LOGGER = logging.getLogger(__name__)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()
    progress = Signal(str)


class FunctionWorker(QRunnable):
    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception as exc:
            LOGGER.exception("Background task failed")
            details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.signals.error.emit(details)
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


class ProgressFunctionWorker(QRunnable):
    """Run a function that accepts a thread-safe ``progress_callback`` keyword."""

    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(
                *self.args,
                progress_callback=self.signals.progress.emit,
                **self.kwargs,
            )
        except Exception as exc:
            LOGGER.exception("Background task failed")
            details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.signals.error.emit(details)
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()

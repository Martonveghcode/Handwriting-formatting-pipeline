from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from mtyh import __version__
from mtyh.logging_config import configure_logging
from mtyh.paths import APP_NAME, ORG_NAME, icon_path
from mtyh.ui.main_window import MainWindow

LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    args = parser.parse_args(argv)
    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0

    configure_logging()
    app = QApplication(sys.argv if argv is None else [APP_NAME, *argv])
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    app_icon = icon_path()
    if app_icon.exists():
        app.setWindowIcon(QIcon(str(app_icon)))

    def report_unhandled_exception(exception_type: type[BaseException], value: BaseException, traceback: object) -> None:
        LOGGER.critical("Unhandled UI exception", exc_info=(exception_type, value, traceback))
        try:
            QMessageBox.critical(
                None,
                "MTYH encountered an error",
                f"The operation could not be completed. MTYH will remain open.\n\n{value}\n\n"
                "Diagnostic details were written to the MTYH log.",
            )
        except Exception:
            LOGGER.exception("Could not display the unhandled-exception dialog")

    sys.excepthook = report_unhandled_exception

    window = MainWindow()
    window.show()
    LOGGER.info("MTYH started")
    return app.exec()

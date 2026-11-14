from __future__ import annotations

import argparse
import logging
import sys
from threading import Thread

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from mtyh import __version__
from mtyh.logging_config import configure_logging
from mtyh.paths import APP_NAME, ORG_NAME, icon_path
from mtyh.ui.main_window import MainWindow

LOGGER = logging.getLogger(__name__)


class ApiDraftBridge(QObject):
    draft = Signal(dict)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    parser.add_argument("--api-port", type=int, help="Also serve the local API on this port while MTYH is open.")
    args = parser.parse_args(argv)
    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args.api_port is not None and not 1 <= args.api_port <= 65535:
        parser.error("--api-port must be from 1 to 65535.")

    configure_logging()
    app = QApplication([APP_NAME])
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
    server = None
    server_thread = None
    if args.api_port is not None:
        from mtyh.api import AutoApi, LocalServer, local_token

        bridge = ApiDraftBridge(window)
        bridge.draft.connect(window.auto_page.show_api_draft)
        try:
            server = LocalServer(args.api_port, AutoApi(window.config_store, bridge.draft.emit), local_token())
        except (OSError, ValueError) as exc:
            LOGGER.error("Could not start the MTYH API: %s", exc)
            QMessageBox.critical(window, "Could not start MTYH API", str(exc))
            return 1
        server_thread = Thread(target=server.serve_forever, name="MTYH API", daemon=True)
        server_thread.start()
        LOGGER.info("MTYH API listening on http://127.0.0.1:%s", args.api_port)
    window.show()
    LOGGER.info("MTYH started")
    try:
        return app.exec()
    finally:
        if server is not None and server_thread is not None:
            server.shutdown()
            server.server_close()
            server_thread.join()

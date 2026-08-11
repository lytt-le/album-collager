"""AlbumCollage entry point."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from albumcollage.config import load_settings
from albumcollage.theme import apply_theme
from albumcollage.ui_main import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AlbumCollage")
    app.setOrganizationName("AlbumCollage")

    apply_theme(app, load_settings().get("theme", "dark"))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

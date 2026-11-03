"""Developer tool: open the app off-screen, wait for background work, and save screenshots of each screen."""
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from realbook.app.main import STYLE_SHEET, MainWindow  # noqa: E402


def wait(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.05)


def main(out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE_SHEET)
    w = MainWindow()
    w.resize(1400, 950)
    w.show()
    wait(app, 8)
    w.tabs.setCurrentIndex(1)
    wait(app, 6)
    w.grab().save(str(out / "review.png"))
    w.tabs.setCurrentIndex(0)
    wait(app, 10)
    w.grab().save(str(out / "lead_sheet.png"))
    QTimer.singleShot(0, app.quit)


if __name__ == "__main__":
    main(sys.argv[1])

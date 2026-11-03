"""Developer tool: drive an import through the real window (dialogs auto-accepted) and screenshot it over time."""
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from realbook.app import main as appmain  # noqa: E402


def run(pdf: str, out_dir: str, seconds: float):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyleSheet(appmain.STYLE_SHEET)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (pdf, "PDF (*.pdf)"))
    appmain.ImportDialog.exec = lambda self: QDialog.Accepted
    w = appmain.MainWindow()
    w.resize(1300, 850)
    w.show()
    app.processEvents()
    t0 = time.time()
    w.import_pdf()
    shots = [1, 5, 20, seconds]
    k = 0
    while time.time() - t0 < seconds + 1:
        app.processEvents()
        time.sleep(0.05)
        if k < len(shots) and time.time() - t0 >= shots[k]:
            w.grab().save(str(out / f"t{int(shots[k]):03d}.png"))
            print(f"t={shots[k]}s label={w.prog_label.text()!r} visible={w.prog_label.isVisible()} "
                  f"songs={len(w.lib.entries())}", flush=True)
            k += 1
    if w.import_thread:
        w.import_thread.cancel()
        w.import_thread.wait(60000)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], float(sys.argv[3]))

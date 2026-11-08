"""PDX Beacon Studio - prova dell'interfaccia senza schermo e senza scheda (python test_gui.py).

Controlla che gli esiti dei lavori in sottofondo arrivino all'interfaccia (anche alle funzioni lambda: nella 0.1.0-pre
si perdevano e la scheda «Carica sul beacon» restava bloccata) e crea un monoscopio dal pulsante, con il dialogo di
salvataggio sostituito.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

import pdx_studio as s  # noqa: E402

fails = 0


def check(name, cond, extra=""):
    global fails
    fails += not cond
    print(f"{'ok ' if cond else 'ERR'} {name} {extra}")


app = QApplication(sys.argv[:1])


def wait(cond, timeout=120.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()
    return cond()


# 1) consegna degli esiti a lambda, molte volte di seguito
got, errs = [], []
for i in range(50):
    j = s.Job(lambda log, prog, i=i: i * 2)
    j.s.done.connect(lambda r: got.append(r))
    s.QThreadPool.globalInstance().start(j)
    j = s.Job(lambda log, prog: 1 / 0)
    j.s.error.connect(lambda m: errs.append(m))
    s.QThreadPool.globalInstance().start(j)
del j
check("esiti consegnati alle lambda", wait(lambda: len(got) == 50 and len(errs) == 50, 30), f"| {len(got)}/50, {len(errs)}/50 errori")
check("lavori rilasciati dopo la consegna", wait(lambda: not s.Job._live, 5), f"| ancora vivi {len(s.Job._live)}")

# 2) monoscopio standard dal pulsante
out = Path(tempfile.gettempdir()) / "pdx_studio_test_gui.bin"
out.unlink(missing_ok=True)
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), ""))
w = s.MainWindow()
w.show()
w.mono.make_bin()
ok = wait(lambda: not w.mono.busy)
check("monoscopio dal pulsante", ok and out.exists() and w.mono.result.text().startswith("Fatto"),
      f"| {w.mono.result.text().splitlines()[-1] if w.mono.result.text() else '(nessun esito)'}")
check("pulsanti riattivati", w.mono.prev_btn.isEnabled() and w.mono.make_btn.isEnabled())
out.unlink(missing_ok=True)

print(f"\n{'TUTTO OK' if not fails else f'{fails} ERRORI'}")
sys.exit(1 if fails else 0)

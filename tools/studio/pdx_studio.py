"""PDX Beacon Studio (IZ7PDX, GPL-3.0) - app Windows per l'ESP32-S3-CAM_PDX DATV Beacon.

Schede (fase A1):
  Monoscopio .bin   video o immagine -> monoscopio PDXES001 pronto da caricare, con stima dei 3 MB e anteprima
  Carica sul beacon via Wi-Fi (dashboard) o USB (COM): stato della scheda, caricamento, cancellazione

  python pdx_studio.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSizePolicy,
                               QSlider, QTabWidget, QVBoxLayout, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio_core as core  # noqa: E402

APP_NAME = "PDX Beacon Studio"
APP_VERSION = "0.1.0-pre"
ACCENT = "#36c2ff"
WORK = Path(tempfile.gettempdir()) / "pdx_beacon_studio"

STYLE = f"""
QWidget {{ font-size: 10pt; }}
QGroupBox {{ border: 1px solid #2c3a46; border-radius: 6px; margin-top: 14px; padding: 8px 8px 6px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {ACCENT}; font-weight: 600; }}
QPushButton {{ background: #22303b; border: 1px solid #34495a; border-radius: 5px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5d6b76; border-color: #26323b; }}
QPushButton#primary {{ background: #0b5f8a; border-color: {ACCENT}; font-weight: 600; padding: 8px 16px; }}
QPushButton#primary:hover {{ background: #0d70a3; }}
QPushButton#danger {{ background: #4a1f24; border-color: #8a3a44; }}
QTabBar::tab {{ padding: 8px 18px; }}
QTabBar::tab:selected {{ color: {ACCENT}; }}
QProgressBar {{ border: 1px solid #34495a; border-radius: 4px; text-align: center; height: 18px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QProgressBar#over::chunk {{ background: #e0503c; }}
QPlainTextEdit {{ background: #0f171d; border: 1px solid #26323b; font-family: Consolas; font-size: 9pt; }}
QLabel#preview {{ background: #0b1115; border: 2px dashed #34495a; border-radius: 6px; color: #7f8f9b; }}
QLabel#hint {{ color: #8fa0ac; }}
QLabel#ok {{ color: #5fd38d; font-weight: 600; }}
QLabel#bad {{ color: #ff7a66; font-weight: 600; }}
"""


def dark_palette() -> QPalette:
    p = QPalette()
    for role, c in ((QPalette.Window, "#151e25"), (QPalette.WindowText, "#dfe7ee"), (QPalette.Base, "#0f171d"),
                    (QPalette.AlternateBase, "#1a252d"), (QPalette.Text, "#dfe7ee"), (QPalette.Button, "#22303b"),
                    (QPalette.ButtonText, "#dfe7ee"), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#081018"),
                    (QPalette.ToolTipBase, "#22303b"), (QPalette.ToolTipText, "#dfe7ee"), (QPalette.PlaceholderText, "#6d7c87")):
        p.setColor(role, QColor(c))
    return p


# ------------------------------------------------------------------------------------------------- lavori in sottofondo

class _Signals(QObject):
    log = Signal(str)
    progress = Signal(int, int)
    done = Signal(object)
    error = Signal(str)


class Job(QRunnable):
    """esegue fn(log, progress) fuori dall'interfaccia; i risultati tornano con i segnali"""

    def __init__(self, fn):
        super().__init__()
        self.fn, self.s = fn, _Signals()

    def run(self):
        try:
            self.s.done.emit(self.fn(self.s.log.emit, self.s.progress.emit))
        except Exception as e:  # noqa: BLE001 - qualsiasi errore va mostrato all'utente
            msg = str(e) or e.__class__.__name__
            if not isinstance(e, (RuntimeError, ValueError, OSError)):
                msg += "\n" + traceback.format_exc(limit=3)
            self.s.error.emit(msg)


# ------------------------------------------------------------------------------------------------- anteprima video

class PreviewDialog(QDialog):
    def __init__(self, mp4: Path, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Anteprima - {title}")
        self.resize(720, 480)
        lay = QVBoxLayout(self)
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except ImportError:
            lay.addWidget(QLabel("Riproduzione interna non disponibile: apro il lettore di Windows."))
            os.startfile(str(mp4))  # noqa: S606
            return
        self.video = QVideoWidget()
        self.audio = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setVideoOutput(self.video)
        self.player.setAudioOutput(self.audio)
        self.player.setLoops(QMediaPlayer.Infinite)
        self.player.setSource(QUrl.fromLocalFile(str(mp4)))
        lay.addWidget(self.video, 1)
        row = QHBoxLayout()
        note = QLabel("È il contenuto esatto del .bin, come lo trasmetterà il beacon (in loop).")
        note.setObjectName("hint")
        row.addWidget(note, 1)
        ext = QPushButton("Apri nel lettore di Windows")
        ext.clicked.connect(lambda: os.startfile(str(mp4)))  # noqa: S606
        row.addWidget(ext)
        lay.addLayout(row)
        self.player.play()

    def closeEvent(self, e):
        if hasattr(self, "player"):
            self.player.stop()
        super().closeEvent(e)


# ------------------------------------------------------------------------------------------------- scheda 1: monoscopio

class DropPreview(QLabel):
    dropped = Signal(str)

    def __init__(self):
        super().__init__("Trascina qui un video o un'immagine\n(MP4, MOV, MKV, AVI, JPG, PNG…)\n\noppure usa «Apri file»")
        self.setObjectName("preview")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAcceptDrops(True)
        self._pix: QPixmap | None = None

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.dropped.emit(urls[0].toLocalFile())

    def set_png(self, data: bytes):
        pix = QPixmap()
        pix.loadFromData(data, "PNG")
        self._pix = pix
        self._rescale()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale()

    def _rescale(self):
        if self._pix and not self._pix.isNull():
            self.setPixmap(self._pix.scaled(self.size() - QPixmap(2, 2).size() * 4, Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation))


class MonoscopeTab(QWidget):
    to_upload = Signal(str)

    def __init__(self, pool: QThreadPool):
        super().__init__()
        self.pool = pool
        self.info: core.SourceInfo | None = None
        self.last_bin: Path | None = None
        self.busy = False
        self._thumb_timer = QTimer(self, singleShot=True, interval=250, timeout=self._load_thumb)

        root = QHBoxLayout(self)
        # --- sinistra: sorgente e anteprima
        left = QVBoxLayout()
        self.preview = DropPreview()
        self.preview.dropped.connect(self.open_source)
        left.addWidget(self.preview, 1)
        self.src_label = QLabel("Nessuna sorgente: si userà il monoscopio standard (barre colorate).")
        self.src_label.setObjectName("hint")
        self.src_label.setWordWrap(True)
        left.addWidget(self.src_label)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Inizio"))
        self.start = QSlider(Qt.Horizontal)
        self.start.setEnabled(False)
        self.start.valueChanged.connect(self._start_changed)
        srow.addWidget(self.start, 1)
        self.start_lbl = QLabel("0,0 s")
        self.start_lbl.setMinimumWidth(110)
        srow.addWidget(self.start_lbl)
        left.addLayout(srow)
        brow = QHBoxLayout()
        b = QPushButton("Apri file…")
        b.clicked.connect(self._browse)
        brow.addWidget(b)
        b = QPushButton("Monoscopio standard")
        b.setToolTip("barre colorate HD, senza sorgente")
        b.clicked.connect(self.clear_source)
        brow.addWidget(b)
        brow.addStretch(1)
        left.addLayout(brow)
        root.addLayout(left, 3)

        # --- destra: impostazioni
        right = QVBoxLayout()
        g = QGroupBox("Video")
        f = QFormLayout(g)
        self.loops = QComboBox()
        for n in range(1, 6):
            self.loops.addItem(f"{core.LOOP_UNIT_S * n:.1f} s".replace(".", ","), n)
        self.loops.setToolTip("durata del loop: multipli di 19,2 s, il giro non si vede e non si sente")
        f.addRow("Durata del loop", self.loops)
        fr = QHBoxLayout()
        self.fill = QRadioButton("Riempi (ritaglia)")
        self.fitb = QRadioButton("Intero (bande nere)")
        self.fill.setChecked(True)
        self.fit_group = QButtonGroup(self)
        for w in (self.fill, self.fitb):
            self.fit_group.addButton(w)
            fr.addWidget(w)
        f.addRow("Inquadratura 16:9", fr)
        right.addWidget(g)

        g = QGroupBox("Audio")
        f = QFormLayout(g)
        self.audio = QComboBox()
        self.audio.addItem("Tono 1 kHz del beacon", "tone")
        self.audio.addItem("Audio del video", "source")
        self.audio.addItem("File audio…", "file")
        self.audio.currentIndexChanged.connect(self._audio_changed)
        f.addRow("Sorgente", self.audio)
        self.audio_file_lbl = QLabel("")
        self.audio_file_lbl.setObjectName("hint")
        f.addRow("", self.audio_file_lbl)
        self.audio_file: Path | None = None
        self.akbps = QComboBox()
        for k in (32, 40, 48, 56, 64):
            self.akbps.addItem(f"{k} kbit/s", k)
        self.akbps.setToolTip("più qualità all'audio = meno bitrate al video")
        f.addRow("Qualità", self.akbps)
        right.addWidget(g)

        g = QGroupBox("Trasmissione")
        f = QFormLayout(g)
        self.sr = QComboBox()
        for sr in core.SR_CHOICES:
            self.sr.addItem(f"{sr // 1000} kS", sr)
        self.sr.setCurrentIndex(core.SR_CHOICES.index(333000))
        self.sr.setToolTip("lo SR PIÙ BASSO con cui trasmetterai questo monoscopio")
        f.addRow("SR minimo", self.sr)
        self.fec = QComboBox()
        for fec in core.FEC_CHOICES:
            self.fec.addItem(fec, fec)
        self.fec.setToolTip("il FEC PIÙ ROBUSTO con cui trasmetterai (1/2 = va bene con tutti)")
        f.addRow("FEC più robusto", self.fec)
        right.addWidget(g)

        g = QGroupBox("Scritte")
        f = QFormLayout(g)
        self.text = QCheckBox("QRZ e frequenza nell'immagine")
        f.addRow(self.text)
        self.callsign = QLineEdit()
        self.callsign.setPlaceholderText("es. IZ7PDX/B")
        f.addRow("QRZ", self.callsign)
        self.freq = QLineEdit()
        self.freq.setPlaceholderText("es. 2375.000 MHz (facoltativo)")
        f.addRow("Frequenza", self.freq)
        self.band = QCheckBox("Fascia in basso con contatore dei fotogrammi")
        f.addRow(self.band)
        self.text.toggled.connect(self._text_toggled)
        self._text_toggled(False)
        right.addWidget(g)

        g = QGroupBox("Stima")
        v = QVBoxLayout(g)
        self.gauge = QProgressBar()
        self.gauge.setRange(0, 1000)
        v.addWidget(self.gauge)
        self.est_lbl = QLabel()
        self.est_lbl.setWordWrap(True)
        v.addWidget(self.est_lbl)
        right.addWidget(g)

        self.make_btn = QPushButton("Crea monoscopio .bin")
        self.make_btn.setObjectName("primary")
        self.make_btn.clicked.connect(self.make_bin)
        right.addWidget(self.make_btn)
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.hide()
        right.addWidget(self.busy_bar)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        right.addWidget(self.result)
        rrow = QHBoxLayout()
        self.prev_btn = QPushButton("Anteprima")
        self.prev_btn.clicked.connect(self.preview_bin)
        self.up_btn = QPushButton("Carica sul beacon →")
        self.up_btn.clicked.connect(lambda: self.last_bin and self.to_upload.emit(str(self.last_bin)))
        self.folder_btn = QPushButton("Apri cartella")
        self.folder_btn.clicked.connect(lambda: self.last_bin and os.startfile(str(self.last_bin.parent)))  # noqa: S606
        for w in (self.prev_btn, self.up_btn, self.folder_btn):
            w.setEnabled(False)
            rrow.addWidget(w)
        right.addLayout(rrow)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        right.addWidget(self.log)
        right.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(right)
        wrap.setMaximumWidth(460)
        root.addWidget(wrap, 2)

        for w in (self.loops, self.akbps, self.sr, self.fec):
            w.currentIndexChanged.connect(self.update_estimate)
        self.loops.currentIndexChanged.connect(self._update_start_range)
        self.fit_group.buttonToggled.connect(lambda *_: self._thumb_timer.start())
        self.update_estimate()

    # --- sorgente
    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Scegli un video o un'immagine", "",
                                           "Video e immagini (*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.ts *.mpg *.jpg *.jpeg *.png *.bmp *.gif *.webp);;Tutti i file (*)")
        if p:
            self.open_source(p)

    def open_source(self, path: str):
        try:
            self.info = core.probe(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Non riesco a leggere il file:\n{e}")
            return
        self.src_label.setText(f"{Path(path).name}: {self.info.describe()}")
        self.src_label.setObjectName("")
        self.audio.model().item(1).setEnabled(not self.info.is_image and self.info.has_audio)
        if self.audio.currentData() == "source" and not (self.info.has_audio and not self.info.is_image):
            self.audio.setCurrentIndex(0)
        elif not self.info.is_image and self.info.has_audio and self.audio.currentData() == "tone":
            self.audio.setCurrentIndex(1)
        self._update_start_range()
        self._thumb_timer.start(10)

    def clear_source(self):
        self.info = None
        self.preview.setPixmap(QPixmap())
        self.preview._pix = None
        self.preview.setText("Monoscopio standard: barre colorate HD 640x360\n\nTrascina qui un video o un'immagine per cambiarlo")
        self.src_label.setText("Nessuna sorgente: si userà il monoscopio standard (barre colorate).")
        self.audio.model().item(1).setEnabled(False)
        if self.audio.currentData() == "source":
            self.audio.setCurrentIndex(0)
        self.start.setEnabled(False)

    def _update_start_range(self):
        if not self.info or self.info.is_image:
            self.start.setEnabled(False)
            self.start_lbl.setText("immagine fissa" if self.info else "")
            return
        loop_s = core.LOOP_UNIT_S * self.loops.currentData()
        span = max(0.0, self.info.duration - loop_s)
        self.start.setEnabled(span > 0.05)
        self.start.setRange(0, int(span * 10))
        if self.info.duration < loop_s:
            self.start_lbl.setText("video ripetuto")
            self.start_lbl.setToolTip(f"il video ({self.info.duration:.1f} s) è più corto del loop: viene ripetuto")
        else:
            self._start_changed(self.start.value())

    def _start_changed(self, v: int):
        if self.info and not self.info.is_image:
            loop_s = core.LOOP_UNIT_S * self.loops.currentData()
            self.start_lbl.setText(f"{v / 10:.1f} – {v / 10 + loop_s:.1f} s".replace(".", ","))
            self._thumb_timer.start()

    def _load_thumb(self):
        if not self.info:
            return
        t = 0.0 if self.info.is_image else self.start.value() / 10 + 0.5
        fit = "fill" if self.fill.isChecked() else "fit"
        path = self.info.path
        job = Job(lambda log, prog: core.thumbnail(path, t, fit))
        job.s.done.connect(self.preview.set_png)
        self.pool.start(job)

    def _audio_changed(self):
        if self.audio.currentData() == "file":
            p, _ = QFileDialog.getOpenFileName(self, "Scegli un file audio", "", "Audio (*.wav *.mp3 *.m4a *.aac *.flac *.ogg);;Tutti i file (*)")
            if p:
                self.audio_file = Path(p)
                self.audio_file_lbl.setText(Path(p).name + "  (ripetuto se più corto del loop)")
            else:
                self.audio.setCurrentIndex(0)
                return
        else:
            self.audio_file_lbl.setText("")

    def _text_toggled(self, on: bool):
        self.callsign.setEnabled(on)
        self.freq.setEnabled(on)

    # --- stima
    def update_estimate(self):
        e = core.estimate(self.sr.currentData(), self.fec.currentData(), self.loops.currentData(), self.akbps.currentData())
        frac = e.bytes_est / core.USER_MAX
        self.gauge.setValue(min(1000, int(frac * 1000)))
        self.gauge.setFormat(f"circa {e.bytes_est / 1048576:.2f} MB su 3 MB ({frac * 100:.0f}%)".replace(".", ","))
        self.gauge.setObjectName("over" if frac > 1 else "")
        self.gauge.style().unpolish(self.gauge)
        self.gauge.style().polish(self.gauge)
        txt = (f"TS {e.ts_bps / 1000:.0f} kbit/s → video <b>{e.video_k} kbit/s</b>, audio {e.audio_k} kbit/s, "
               f"loop {e.loop_s:.1f} s.").replace(".", ",")
        if e.video_k < 40:
            txt += "<br><span style='color:#ff7a66'>Troppo poco bitrate per il video: alzare lo SR o abbassare l'audio.</span>"
        elif not e.fits:
            txt += "<br><span style='color:#ff7a66'>Non entra nei 3 MB: ridurre la durata del loop.</span>"
        elif e.borderline:
            txt += ("<br><span style='color:#e0a020'>Al limite dei 3 MB: si può provare, la dimensione vera viene "
                    "controllata alla fine.</span>")
        elif e.video_k < 120:
            txt += "<br><span style='color:#e0a020'>Bitrate basso: meglio immagini semplici e poco movimento.</span>"
        self.est_lbl.setText(txt)
        self.make_btn.setEnabled(e.fits and not self.busy)

    # --- creazione
    def make_bin(self):
        stem = self.info.path.stem if self.info else "monoscopio_std"
        default = str(Path.home() / "Documents" / f"{stem}_es.bin")
        out, _ = QFileDialog.getSaveFileName(self, "Salva il monoscopio", default, "Monoscopio beacon (*.bin)")
        if not out:
            return
        o = core.BinOptions(source=self.info.path if self.info else None, out=Path(out),
                            start=self.start.value() / 10 if self.start.isEnabled() else 0.0,
                            loops=self.loops.currentData(), fit="fill" if self.fill.isChecked() else "fit",
                            audio=self.audio.currentData(), audio_file=self.audio_file, audio_k=self.akbps.currentData(),
                            sr=self.sr.currentData(), fec=self.fec.currentData(), text=self.text.isChecked(),
                            band=self.band.isChecked(), callsign=self.callsign.text().strip(),
                            label_freq=self.freq.text().strip())
        self.log.clear()
        self._set_busy(True)
        job = Job(lambda log, prog: (core.build_bin(o, log), core.read_bin(o.out))[1])
        job.s.log.connect(self.log.appendPlainText)
        job.s.done.connect(self._made)
        job.s.error.connect(self._failed)
        self.pool.start(job)

    def _set_busy(self, on: bool):
        self.busy = on
        self.busy_bar.setVisible(on)
        self.make_btn.setEnabled(not on)
        if on:
            self.result.setText("")
            for w in (self.prev_btn, self.up_btn, self.folder_btn):
                w.setEnabled(False)

    def _made(self, info: core.BinInfo):
        self._set_busy(False)
        self.update_estimate()
        self.last_bin = info.path
        self.result.setObjectName("ok" if info.size <= core.USER_MAX else "bad")
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)
        self.result.setText(f"Fatto: {info.path.name}\n{info.describe()}")
        for w in (self.prev_btn, self.up_btn, self.folder_btn):
            w.setEnabled(True)

    def _failed(self, msg: str):
        self._set_busy(False)
        self.update_estimate()
        self.log.appendPlainText("ERRORE: " + msg)
        QMessageBox.critical(self, APP_NAME, f"Il monoscopio non è stato creato:\n\n{msg}")

    def preview_bin(self):
        if not self.last_bin:
            return
        WORK.mkdir(parents=True, exist_ok=True)
        mp4 = WORK / (self.last_bin.stem + "_anteprima.mp4")
        job = Job(lambda log, prog: core.bin_to_mp4(self.last_bin, mp4))
        job.s.done.connect(lambda p: PreviewDialog(Path(p), self.last_bin.name, self).exec())
        job.s.error.connect(lambda m: QMessageBox.warning(self, APP_NAME, m))
        self.pool.start(job)


# ------------------------------------------------------------------------------------------------- scheda 2: caricamento

class UploadTab(QWidget):
    def __init__(self, pool: QThreadPool):
        super().__init__()
        self.pool = pool
        self.busy = False
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        g = QGroupBox("Collegamento con il beacon")
        grid = QGridLayout(g)
        self.via_wifi = QRadioButton("Wi-Fi (dashboard)")
        self.via_usb = QRadioButton("USB (porta COM)")
        self.via_wifi.setChecked(True)
        grid.addWidget(self.via_wifi, 0, 0)
        self.host = QLineEdit(core.DEFAULT_HOST)
        self.host.setMaximumWidth(160)
        grid.addWidget(self.host, 0, 1)
        grid.addWidget(self.via_usb, 1, 0)
        self.port = QComboBox()
        self.port.setMinimumWidth(240)
        grid.addWidget(self.port, 1, 1)
        rb = QPushButton("Aggiorna porte")
        rb.clicked.connect(self.refresh_ports)
        grid.addWidget(rb, 1, 2)
        hint = QLabel("Wi-Fi: collegare il PC alla rete <b>&lt;QRZ&gt;-BEACON</b> (attiva solo a TX ferma).<br>"
                      "USB: cavo sulla porta TTL (CH340) della scheda; la TX dev'essere ferma.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        grid.addWidget(hint, 2, 0, 1, 3)
        self.check_btn = QPushButton("Leggi lo stato della scheda")
        self.check_btn.clicked.connect(self.read_status)
        grid.addWidget(self.check_btn, 3, 0, 1, 3)
        left.addWidget(g)

        g = QGroupBox("Sulla scheda")
        v = QVBoxLayout(g)
        self.status = QLabel("—")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.status)
        erow = QHBoxLayout()
        self.er_video = QPushButton("Cancella video personalizzato")
        self.er_image = QPushButton("Cancella immagine personalizzata")
        for w, k in ((self.er_video, "VIDEO"), (self.er_image, "IMAGE")):
            w.setObjectName("danger")
            w.clicked.connect(lambda _=False, k=k: self.erase(k))
            erow.addWidget(w)
        v.addLayout(erow)
        note = QLabel("Il monoscopio standard e lo sfondo standard del PiP sono protetti: non si possono cancellare.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        v.addWidget(note)
        left.addWidget(g)
        left.addStretch(1)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        g = QGroupBox("File da caricare")
        v = QVBoxLayout(g)
        frow = QHBoxLayout()
        self.file = QLineEdit()
        self.file.setPlaceholderText("monoscopio .bin (video) oppure un'immagine JPG/PNG")
        self.file.textChanged.connect(self._file_changed)
        frow.addWidget(self.file, 1)
        b = QPushButton("Sfoglia…")
        b.clicked.connect(self._browse)
        frow.addWidget(b)
        v.addLayout(frow)
        self.file_info = QLabel("")
        self.file_info.setWordWrap(True)
        v.addWidget(self.file_info)
        self.up_btn = QPushButton("Carica sul beacon")
        self.up_btn.setObjectName("primary")
        self.up_btn.setEnabled(False)
        self.up_btn.clicked.connect(self.upload)
        v.addWidget(self.up_btn)
        self.bar = QProgressBar()
        self.bar.setValue(0)
        v.addWidget(self.bar)
        after = QLabel("Dopo il caricamento, nella dashboard: <i>Monoscopio da trasmettere = video personalizzato</i> "
                       "(o <i>immagine personalizzata</i>), poi <b>Applica e salva</b>.")
        after.setObjectName("hint")
        after.setWordWrap(True)
        v.addWidget(after)
        right.addWidget(g)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        right.addWidget(self.log, 1)
        root.addLayout(right, 1)
        self.kind: str | None = None
        self.refresh_ports()

    # --- porte e file
    def refresh_ports(self):
        cur = self.port.currentData()
        self.port.clear()
        for dev, desc in core.serial_ports():
            self.port.addItem(f"{dev}  {desc}", dev)
        if self.port.count() == 0:
            self.port.addItem("nessuna porta COM", None)
        else:
            i = max(0, self.port.findData(cur))
            for k in range(self.port.count()):                  # preferisce il CH340 della scheda
                if cur is None and "CH340" in self.port.itemText(k):
                    i = k
            self.port.setCurrentIndex(i)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "File da caricare", "",
                                           "Monoscopi e immagini (*.bin *.jpg *.jpeg *.png *.bmp *.webp);;Tutti i file (*)")
        if p:
            self.file.setText(p)

    def set_file(self, path: str):
        self.file.setText(path)

    def _file_changed(self, text: str):
        self.kind = None
        p = Path(text.strip().strip('"'))
        if not text or not p.is_file():
            self.file_info.setText("")
            self.up_btn.setEnabled(False)
            return
        try:
            if p.suffix.lower() == ".bin":
                info = core.read_bin(p)
                self.kind = info.kind
                ok = info.kind == "IMAGE" or info.size <= core.USER_MAX
                self.file_info.setText(("✔ " if ok else "✘ ") + info.describe())
                self.file_info.setObjectName("ok" if ok else "bad")
                if not ok:
                    self.kind = None
            else:
                si = core.probe(p)
                if not si.is_image:
                    raise ValueError("i video vanno prima trasformati in .bin (scheda «Monoscopio .bin»)")
                self.kind = "IMAGE"
                self.file_info.setText(f"✔ immagine {si.width}x{si.height}: verrà ritagliata al centro in 16:9 e "
                                       "ridotta a 320x180 (come fa la dashboard)")
                self.file_info.setObjectName("ok")
        except Exception as e:  # noqa: BLE001
            self.file_info.setText(f"✘ {e}")
            self.file_info.setObjectName("bad")
        self.file_info.style().unpolish(self.file_info)
        self.file_info.style().polish(self.file_info)
        self.up_btn.setEnabled(self.kind is not None and not self.busy)

    # --- operazioni sulla scheda
    def _target(self) -> tuple[str, str]:
        if self.via_wifi.isChecked():
            return "wifi", self.host.text().strip() or core.DEFAULT_HOST
        port = self.port.currentData()
        if not port:
            raise ValueError("nessuna porta COM scelta")
        return "usb", port

    def _run(self, fn, done, what: str):
        if self.busy:
            return
        try:
            mode, where = self._target()
        except ValueError as e:
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        self.busy = True
        for w in (self.up_btn, self.check_btn, self.er_video, self.er_image):
            w.setEnabled(False)
        self.log.appendPlainText(f"{what} ({'Wi-Fi ' if mode == 'wifi' else ''}{where})…")
        job = Job(lambda log, prog: fn(mode, where, log, prog))
        job.s.log.connect(self.log.appendPlainText)
        job.s.progress.connect(lambda a, b: (self.bar.setMaximum(b), self.bar.setValue(a)))
        job.s.done.connect(lambda r: (self._idle(), done(r)))
        job.s.error.connect(lambda m: (self._idle(), self.log.appendPlainText("ERRORE: " + m),
                                       QMessageBox.critical(self, APP_NAME, m)))
        self.pool.start(job)

    def _idle(self):
        self.busy = False
        for w in (self.check_btn, self.er_video, self.er_image):
            w.setEnabled(True)
        self.up_btn.setEnabled(self.kind is not None)

    def read_status(self):
        def fn(mode, where, log, prog):
            if mode == "wifi":
                j = core.wifi_status(where)
                m = j.get("media", {})
                cfg = j.get("cfg", {})
                return [f"Firmware {j.get('fw', '?')}, modo {j.get('mode', '?')}, temperatura {j.get('temp', '?')} °C",
                        f"QRZ {cfg.get('call', '?')}, monoscopio scelto: {cfg.get('monosrc', '?')}",
                        f"Monoscopio standard: {m.get('es_std', '?')}",
                        f"Video personalizzato: {m.get('es_usr', '?')}",
                        f"Sfondo PiP standard: {m.get('img_std', '?')}",
                        f"Immagine personalizzata: {m.get('img_usr', '?')}"]
            with core.Board(where) as b:
                return [line for line in b.media()]
        self._run(fn, lambda lines: (self.status.setText("<br>".join(lines)), self.log.appendPlainText("stato letto")),
                  "Leggo lo stato")

    @staticmethod
    def _present(mode: str, where: str, kind: str) -> str:
        """descrizione di ciò che la scheda ha oggi nell'area personalizzata di quel tipo ('assente' se vuota)"""
        if mode == "wifi":
            m = core.wifi_status(where).get("media", {})
            return str(m.get("es_usr" if kind == "VIDEO" else "img_usr", "?"))
        key = "video personalizzato" if kind == "VIDEO" else "immagine personalizzata"
        with core.Board(where) as b:
            for line in b.media():
                if line.startswith(key):
                    return line[len(key):].strip()
        return "?"

    def _ask_over(self, kind: str, action: str, then):
        """legge la scheda, poi chiede conferma dicendo COSA verrà sostituito o cancellato"""
        def done(cur: str):
            empty = cur.lower().startswith("assente")
            what = "il video personalizzato" if kind == "VIDEO" else "l'immagine personalizzata"
            if empty:
                if action == "erase":
                    QMessageBox.information(self, APP_NAME, f"Sulla scheda {what} è già assente.")
                    return
                msg = f"Sulla scheda l'area è vuota.\n\nCaricare «{Path(self.file.text().strip().strip(chr(34))).name}»?"
            else:
                verb = "CANCELLATO" if action == "erase" else "SOSTITUITO"
                msg = (f"Sulla scheda c'è già {what}:\n\n    «{cur}»\n\nVerrà {verb} e non si potrà recuperare "
                       "dalla scheda: procedi solo se ne hai una copia.\n\nContinuare?")
            box = QMessageBox(QMessageBox.Question if empty else QMessageBox.Warning, APP_NAME, msg,
                              QMessageBox.Yes | QMessageBox.No, self)
            box.setDefaultButton(QMessageBox.Yes if empty else QMessageBox.No)
            if box.exec() == QMessageBox.Yes:
                then()
        self._run(lambda mode, where, log, prog: self._present(mode, where, kind), done,
                  "Controllo cosa c'è sulla scheda")

    def upload(self):
        p = Path(self.file.text().strip().strip('"'))
        kind = self.kind
        if not kind:
            return
        self._ask_over(kind, "upload", lambda: self._do_upload(p, kind))

    def _do_upload(self, p: Path, kind: str):
        def fn(mode, where, log, prog):
            data = p.read_bytes() if p.suffix.lower() == ".bin" else core.image_bin(p)
            core.check_upload_data(kind, data)
            log(f"{len(data)} byte da caricare" + ("" if mode == "wifi" else
                f" (via USB circa {len(data) / 5200 / 60:.0f} min)"))
            if mode == "wifi":
                return core.wifi_upload(kind, data, where, prog)
            with core.Board(where) as b:
                return b.upload(kind, data, prog)
        self._run(fn, lambda msg: (self.log.appendPlainText(f"OK: {msg}"), self.read_status()), "Carico")

    def erase(self, kind: str):
        self._ask_over(kind, "erase", lambda: self._do_erase(kind))

    def _do_erase(self, kind: str):
        def fn(mode, where, log, prog):
            if mode == "wifi":
                return core.wifi_erase(kind, where)
            with core.Board(where) as b:
                return b.erase(kind)
        self._run(fn, lambda msg: (self.log.appendPlainText(f"OK: {msg}"), self.read_status()), "Cancello")


# ------------------------------------------------------------------------------------------------- finestra

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — ESP32-S3-CAM_PDX DATV Beacon")
        self.resize(1180, 760)
        self.pool = QThreadPool.globalInstance()
        central = QWidget()
        lay = QVBoxLayout(central)
        head = QHBoxLayout()
        t = QLabel(APP_NAME)
        f = QFont()
        f.setPointSize(17)
        f.setBold(True)
        t.setFont(f)
        t.setStyleSheet(f"color: {ACCENT};")
        head.addWidget(t)
        sub = QLabel("monoscopi e caricamento per l'ESP32-S3-CAM_PDX DATV Beacon")
        sub.setObjectName("hint")
        head.addWidget(sub)
        head.addStretch(1)
        ver = QLabel(f"IZ7PDX · GPL-3.0 · {APP_VERSION}")
        ver.setObjectName("hint")
        head.addWidget(ver)
        lay.addLayout(head)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #2c3a46;")
        lay.addWidget(line)
        self.tabs = QTabWidget()
        self.mono = MonoscopeTab(self.pool)
        self.upload = UploadTab(self.pool)
        self.tabs.addTab(self.mono, "Monoscopio .bin")
        self.tabs.addTab(self.upload, "Carica sul beacon")
        self.mono.to_upload.connect(self._go_upload)
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def _go_upload(self, path: str):
        self.upload.set_file(path)
        self.tabs.setCurrentWidget(self.upload)


def selftest(out_dir: str) -> int:
    """controllo del pacchetto senza interfaccia: monoscopio standard + anteprima, esito in selftest.txt"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"{APP_NAME} {APP_VERSION}"]
    try:
        lines.append(f"ffmpeg: {core.ffmpeg()}")
        o = core.BinOptions(None, out / "selftest.bin", text=True, callsign="TEST", label_freq="2375.000 MHz")
        core.build_bin(o, log=lambda s: None)
        info = core.read_bin(o.out)
        lines.append(f"bin: {info.describe()}")
        p = core.probe(core.bin_to_mp4(o.out, out / "selftest.mp4"))
        lines.append(f"anteprima: {p.describe()}")
        ok = info.kind == "VIDEO" and info.video_units == 480 and p.width == 640
    except BaseException as e:  # noqa: BLE001
        lines.append(f"ERRORE: {e!r}")
        ok = False
    lines.append("SELFTEST OK" if ok else "SELFTEST FALLITO")
    (out / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2])
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE)
    icon = Path(__file__).with_name("pdx_studio.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    try:
        core.ffmpeg()
    except SystemExit:
        QMessageBox.critical(None, APP_NAME, "ffmpeg non trovato: installare ffmpeg (build «full» con libx265).")
        return 1
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

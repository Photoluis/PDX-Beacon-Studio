"""PDX Beacon Studio (IZ7PDX, GPL-3.0) - logica dell'app, senza interfaccia grafica (provabile da sola).

Usa gli strumenti del repository così come sono: make_es.py (monoscopio PDXES001), make_bg.py (immagine PDXIMG01),
lo stesso protocollo di upload_media.py (seriale) e le API della dashboard (/api/status, /api/upload, /api/erase).
"""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

if getattr(sys, "frozen", False):                       # .exe di PyInstaller: strumenti e ffmpeg dentro il pacchetto
    BASE = Path(getattr(sys, "_MEIPASS"))
    TOOLS = BASE / "tools"
    os.environ["PATH"] = str(BASE / "ffmpeg") + os.pathsep + os.environ.get("PATH", "")
else:
    TOOLS = Path(__file__).resolve().parent.parent
for p in (TOOLS, TOOLS / "dvbs2"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import encoder as dvbs2  # noqa: E402
import make_bg  # noqa: E402
import make_es  # noqa: E402
from make_symbols import find_ffmpeg  # noqa: E402

LOOP_UNIT_S = make_es.LOOP_UNIT_S                       # 19,2 s
USER_MAX = make_es.USER_MAX                             # 3 MB
SR_CHOICES = (125000, 250000, 333000, 500000, 1000000)
FEC_CHOICES = ("1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10")
NOWIN = 0x08000000 if os.name == "nt" else 0             # niente finestre di console per ffmpeg

ProgressFn = Callable[[int, int], None]
LogFn = Callable[[str], None]


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, creationflags=NOWIN, **kw)


def ffmpeg() -> str:
    return find_ffmpeg("ffmpeg")


def ffprobe() -> str:
    return find_ffmpeg("ffprobe")


# ------------------------------------------------------------------------------------------------- sorgente

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".heic"}


@dataclass
class SourceInfo:
    path: Path
    is_image: bool
    width: int
    height: int
    duration: float          # 0 per le immagini
    has_audio: bool

    def describe(self) -> str:
        if self.is_image:
            return f"immagine {self.width}x{self.height}"
        a = "con audio" if self.has_audio else "senza audio"
        return f"video {self.width}x{self.height}, {self.duration:.1f} s, {a}"


def probe(path: str | Path) -> SourceInfo:
    path = Path(path)
    r = _run([ffprobe(), "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
              "-of", "json", str(path)])
    j = json.loads(r.stdout or b"{}")
    streams = j.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise ValueError("nessuna immagine o video nel file")
    dur = float(j.get("format", {}).get("duration") or 0)
    is_image = path.suffix.lower() in IMAGE_EXT or dur < 0.2
    return SourceInfo(path, is_image, int(v.get("width", 0)), int(v.get("height", 0)), 0.0 if is_image else dur,
                      any(s.get("codec_type") == "audio" for s in streams))


def _fit_filter(fit: str) -> str:
    """'fill' = riempie il 16:9 ritagliando al centro; 'fit' = intero con bande nere"""
    if fit == "fill":
        return "scale=640:360:force_original_aspect_ratio=increase,crop=640:360,setsar=1"
    return "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1"


def thumbnail(path: str | Path, t: float = 0.0, fit: str = "fill") -> bytes:
    """un fotogramma 640x360 in PNG, come apparirà nel monoscopio"""
    r = _run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
              "-vf", _fit_filter(fit), "-f", "image2pipe", "-c:v", "png", "-"])
    return r.stdout


# ------------------------------------------------------------------------------------------------- stima

@dataclass
class Estimate:
    ts_bps: float
    video_k: int
    audio_k: int
    loop_s: float
    bytes_est: int

    @property
    def fits(self) -> bool:
        """la stima è prudente: fino al 115% dei 3 MB si prova (make_es controlla la dimensione vera)"""
        return self.bytes_est <= USER_MAX * 1.15 and self.video_k >= 40

    @property
    def borderline(self) -> bool:
        return USER_MAX < self.bytes_est <= USER_MAX * 1.15


def estimate(sr: int, fec: str, loops: int, audio_k: int) -> Estimate:
    """stessa formula di make_symbols.make_ts: video = 80% del TS meno l'audio (margine per PSI, PCR, riempimento)"""
    ts = dvbs2.Encoder(fec, "normal", True).ts_bitrate(sr)
    video_k = int(ts * 0.80 / 1000) - audio_k
    loop_s = LOOP_UNIT_S * loops
    # x265 con VBV sta di solito sotto il nominale, ma ai bitrate bassi (125 kS) lo supera fino al ~9%: +10% prudente;
    # più 16 byte di tabella per unità e l'intestazione
    units = int(loop_s * 25) + int(loop_s * 48000 / 1024)
    return Estimate(ts, video_k, audio_k, loop_s, int((video_k + audio_k) * 1000 / 8 * loop_s * 1.10) + 16 * units + 64)


# ------------------------------------------------------------------------------------------------- monoscopio .bin

@dataclass
class BinOptions:
    source: Path | None                    # None = monoscopio standard (barre)
    out: Path
    start: float = 0.0
    loops: int = 1
    fit: str = "fill"                      # fill | fit
    audio: str = "tone"                    # tone | source | file
    audio_file: Path | None = None
    audio_k: int = 32
    sr: int = 333000
    fec: str = "1/2"
    text: bool = False
    band: bool = False
    callsign: str = ""
    label_freq: str = ""


def prepare_source(o: BinOptions, info: SourceInfo | None, workdir: Path, log: LogFn) -> tuple[list[str], list[str]]:
    """taglio (inizio e durata del loop) e inquadratura 16:9 della sorgente in un file intermedio quasi senza perdite;
    ritorna gli argomenti video e audio per make_es"""
    # il nome del file intermedio diventa l'etichetta del monoscopio (make_es usa il nome del file)
    stem = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_") else "_" for ch in (o.source.stem if o.source else "std"))[:35] or "monoscopio"
    if o.source is None or info is None:
        vargs = []
    elif info.is_image:
        png = workdir / f"{stem}.png"
        _run([ffmpeg(), "-v", "error", "-y", "-i", str(o.source), "-frames:v", "1", "-vf", _fit_filter(o.fit), str(png)])
        vargs = ["--image", str(png)]
    else:
        loop_s = LOOP_UNIT_S * o.loops
        mp4 = workdir / f"{stem}.mp4"
        log(f"preparo la sorgente: da {o.start:.1f} s, {loop_s:.1f} s, 640x360 25 fps ({'ritaglio' if o.fit == 'fill' else 'bande nere'})")
        cmd = [ffmpeg(), "-v", "error", "-y", "-ss", f"{o.start:.3f}", "-i", str(o.source), "-t", f"{loop_s + 1.0:.3f}",
               "-vf", _fit_filter(o.fit) + ",fps=25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
               "-pix_fmt", "yuv420p"]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "1", "-ar", "48000"] if info.has_audio else ["-an"]
        _run(cmd + [str(mp4)])
        vargs = ["--video", str(mp4)]
    aargs: list[str] = []
    if o.audio == "source":
        if info is None or info.is_image or not info.has_audio:
            raise ValueError("la sorgente non ha audio: scegliere il tono o un file")
        aargs = ["--audio", vargs[1]]
    elif o.audio == "file":
        if not o.audio_file:
            raise ValueError("scegliere il file audio")
        aargs = ["--audio", str(o.audio_file)]
    return vargs, aargs


def build_bin(o: BinOptions, log: LogFn = print) -> Path:
    """crea il monoscopio PDXES001 con make_es.py; ritorna il percorso del file"""
    info = probe(o.source) if o.source else None
    with tempfile.TemporaryDirectory(prefix="pdxstudio_") as td:
        vargs, aargs = prepare_source(o, info, Path(td), log)
        argv = ["make_es.py", "--sr", str(o.sr), "--rate-fec", o.fec, "--loops", str(o.loops),
                "--audio-kbps", str(o.audio_k), "-o", str(o.out), *vargs, *aargs]
        if not o.text:
            argv.append("--no-text")
        else:
            if o.callsign:
                argv += ["--callsign", o.callsign]
            if o.label_freq:
                argv += ["--label-freq", o.label_freq]
        if not o.band:
            argv.append("--no-band")
        log("codifica H.265 + AAC (può richiedere qualche minuto)…")
        buf = io.StringIO()
        old = sys.argv
        try:
            sys.argv = argv
            with contextlib.redirect_stdout(buf):
                make_es.main()
        except SystemExit as e:
            raise RuntimeError(str(e) or "make_es: errore") from None
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg: {(e.stderr or b'').decode('utf-8', 'replace')[-400:]}") from None
        finally:
            sys.argv = old
            for line in buf.getvalue().splitlines():
                log(line)
    return o.out


# ------------------------------------------------------------------------------------------------- lettura .bin

@dataclass
class BinInfo:
    path: Path
    size: int
    kind: str              # VIDEO (PDXES001) | IMAGE (PDXIMG01)
    label: str
    loop_s: float = 0.0
    video_units: int = 0
    audio_units: int = 0
    vcodec: str = ""

    def describe(self) -> str:
        if self.kind == "IMAGE":
            return f"immagine PDXIMG01 \"{self.label}\", {self.size / 1024:.0f} KB"
        ok = "entra nei 3 MB" if self.size <= USER_MAX else "OLTRE i 3 MB"
        return (f"monoscopio \"{self.label}\": {self.vcodec}, loop {self.loop_s:.1f} s, {self.video_units} immagini, "
                f"{self.audio_units} frame audio, {self.size / 1048576:.2f} MB ({ok})")


def read_bin(path: str | Path) -> BinInfo:
    path = Path(path)
    d = path.read_bytes()
    if d[:8] == b"PDXIMG01":
        _, w, h, fmt, n, crc, lab = struct.unpack_from("<8sHHIII36s", d)
        return BinInfo(path, len(d), "IMAGE", lab.split(b"\0")[0].decode("ascii", "replace"))
    if d[:8] != b"PDXES001":
        raise ValueError("non è un file del beacon (PDXES001 o PDXIMG01)")
    _, units, ticks, vtype, atype, off, lab = struct.unpack_from("<8sIIIII36s", d)
    kinds = [struct.unpack_from("<B3xIII", d, 64 + 16 * i)[0] for i in range(units)]
    return BinInfo(path, len(d), "VIDEO", lab.split(b"\0")[0].decode("ascii", "replace"), ticks / 90000,
                   kinds.count(0), kinds.count(1), "H.265" if vtype == 0x24 else "H.264")


def bin_to_mp4(path: str | Path, out: str | Path) -> Path:
    """ricostruisce dal .bin un MP4 da guardare: è esattamente il contenuto che il beacon trasmetterà"""
    d = Path(path).read_bytes()
    if d[:8] != b"PDXES001":
        raise ValueError("anteprima solo per i monoscopi video (PDXES001)")
    _, units, ticks, vtype, atype, off, _ = struct.unpack_from("<8sIIIII36s", d)
    v, a = bytearray(), bytearray()
    for i in range(units):
        k, pts, o, n = struct.unpack_from("<B3xIII", d, 64 + 16 * i)
        (v if k == 0 else a).extend(d[off + o:off + o + n])
    out = Path(out)
    with tempfile.TemporaryDirectory(prefix="pdxstudio_") as td:
        vf, af = Path(td) / ("v.hevc" if vtype == 0x24 else "v.h264"), Path(td) / "a.aac"
        vf.write_bytes(v)
        af.write_bytes(a)
        _run([ffmpeg(), "-v", "error", "-y", "-r", "25", "-i", str(vf), "-i", str(af), "-c:v", "copy", "-c:a", "copy",
              *(["-tag:v", "hvc1"] if vtype == 0x24 else []), str(out)])
    return out


def image_bin(path: str | Path) -> bytes:
    """immagine qualsiasi -> PDXIMG01 (ritaglio al centro 16:9, 320x180), come la pagina web"""
    return make_bg.build(str(path), Path(path).stem)


# ------------------------------------------------------------------------------------------------- scheda via Wi-Fi

DEFAULT_HOST = "192.168.4.1"


def wifi_status(host: str = DEFAULT_HOST, timeout: float = 3.0) -> dict:
    c = http.client.HTTPConnection(host, 80, timeout=timeout)
    try:
        c.request("GET", "/api/status")
        r = c.getresponse()
        return json.loads(r.read().decode("utf-8", "replace"))
    finally:
        c.close()


def wifi_upload(kind: str, data: bytes, host: str = DEFAULT_HOST, progress: ProgressFn | None = None) -> str:
    """POST /api/upload?kind=video|image come la dashboard, a blocchi per la barra di avanzamento"""
    c = http.client.HTTPConnection(host, 80, timeout=60)
    try:
        c.putrequest("POST", f"/api/upload?kind={kind.lower()}")
        c.putheader("Content-Type", "application/octet-stream")
        c.putheader("Content-Length", str(len(data)))
        c.endheaders()
        step = 4096
        for off in range(0, len(data), step):
            c.send(data[off:off + step])
            if progress:
                progress(min(off + step, len(data)), len(data))
        j = json.loads(c.getresponse().read().decode("utf-8", "replace"))
    finally:
        c.close()
    if not j.get("ok"):
        raise RuntimeError(j.get("msg", "caricamento non riuscito"))
    return j.get("msg", "caricato")


def wifi_erase(kind: str, host: str = DEFAULT_HOST) -> str:
    c = http.client.HTTPConnection(host, 80, timeout=30)
    try:
        c.request("POST", f"/api/erase?kind={kind.lower()}", body=b"")
        j = json.loads(c.getresponse().read().decode("utf-8", "replace"))
    finally:
        c.close()
    if not j.get("ok"):
        raise RuntimeError(j.get("msg", "cancellazione non riuscita"))
    return j.get("msg", "cancellato")


# ------------------------------------------------------------------------------------------------- scheda via USB

def serial_ports() -> list[tuple[str, str]]:
    from serial.tools import list_ports
    return [(p.device, p.description) for p in sorted(list_ports.comports(), key=lambda p: p.device)]


class Board:
    """porta seriale del beacon (115200, senza reset all'apertura); stesso protocollo di upload_media.py"""
    BLOCK = 2048

    def __init__(self, port: str):
        import serial
        self.p = serial.Serial()
        self.p.port, self.p.baudrate, self.p.timeout = port, 115200, 0.2
        self.p.dtr = self.p.rts = False
        self.p.open()
        time.sleep(0.2)
        self.p.reset_input_buffer()
        self._buf = b""

    def close(self):
        self.p.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _line(self, prefixes: tuple, timeout: float) -> str:
        end = time.time() + timeout
        while True:
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                s = line.decode("utf-8", "replace").strip()
                if s.startswith(prefixes):
                    return s
            if time.time() > end:
                raise RuntimeError("nessuna risposta dalla scheda (TX ferma? porta giusta?)")
            self._buf += self.p.read(256)

    def media(self) -> list[str]:
        self.p.write(b"MEDIA\n")
        out = [self._line(("MEDIA",), 5)]
        for _ in range(3):
            out.append(self._line(("MEDIA",), 2))
        return [s[6:].strip() for s in out]

    def erase(self, kind: str) -> str:
        self.p.write(f"MEDIAERASE {kind.upper()}\n".encode())
        r = self._line(("OK", "ERR"), 15)
        if r.startswith("ERR"):
            raise RuntimeError(r)
        return r

    def upload(self, kind: str, data: bytes, progress: ProgressFn | None = None) -> str:
        self.p.write(f"MEDIAW {kind.upper()} {len(data)} {zlib.crc32(data):08x}\n".encode())
        r = self._line(("READY", "ERR"), 15)
        if r.startswith("ERR"):
            raise RuntimeError(r)
        for off in range(0, len(data), self.BLOCK):
            self.p.write(data[off:off + self.BLOCK])
            r = self._line(("ACK", "ERR"), 15)
            if r.startswith("ERR"):
                raise RuntimeError(r)
            if progress:
                progress(min(off + self.BLOCK, len(data)), len(data))
        r = self._line(("OK", "ERR"), 30)
        if r.startswith("ERR"):
            raise RuntimeError(r)
        return r


def check_upload_data(kind: str, data: bytes) -> None:
    magic = b"PDXES001" if kind.upper() == "VIDEO" else b"PDXIMG01"
    if data[:8] != magic:
        raise ValueError(f"il file non è un {'monoscopio video (PDXES001)' if kind.upper() == 'VIDEO' else 'immagine PDXIMG01'}")
    if kind.upper() == "VIDEO" and len(data) > USER_MAX:
        raise ValueError("file oltre i 3 MB dell'area del video personalizzato")

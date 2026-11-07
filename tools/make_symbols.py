"""ESP32-S3-CAM_PDX DATV Beacon (IZ7PDX, GPL-3.0) - file di simboli per la partizione "symbols" del firmware (comando PLAY).

Formato: intestazione di 64 byte (vedi firmware/main/sym_player.h) + simboli QPSK, 4 per byte, primo simbolo nei bit bassi,
bit 0 = I, bit 1 = Q (1 = +1).

Uso:
  python make_symbols.py prbs --sr 333000 --seconds 10 -o out/prbs.bin
  python make_symbols.py dvbs2 --sr 333000 --fec 3/4 --seconds 20 --callsign IZ7PDX -o out/beacon.bin
      monoscopio (immagine --image, altrimenti barre SMPTE) 640x360 25 fps H.265 + tono 1 kHz AAC, TS CBR al bitrate
      esatto del MODCOD, codificato in DVB-S2 QPSK; loop con un numero intero di pacchetti TS e di PLFRAME
  python make_symbols.py dvbs2 --ts mio.ts ...   usa un TS già pronto (allungato con pacchetti nulli o tagliato)
Scrittura sulla scheda: python -m esptool --port COM3 write-flash 0x210000 out/beacon.bin
"""
import argparse
import glob
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "dvbs2"))
import encoder as dvbs2  # noqa: E402

MAGIC = b"PDXSYM01"
PART_OFFSET = 0x210000
PART_SIZE = 0xDF0000
NULL_PACKET = bytes([0x47, 0x1F, 0xFF, 0x10]) + bytes([0xFF] * 184)


def pack_symbols(i_bits: np.ndarray, q_bits: np.ndarray) -> bytes:
    """i_bits, q_bits: array 0/1 della stessa lunghezza (multiplo di 4)."""
    sym = (i_bits.astype(np.uint8) & 1) | ((q_bits.astype(np.uint8) & 1) << 1)
    sym = sym.reshape(-1, 4)
    packed = sym[:, 0] | (sym[:, 1] << 2) | (sym[:, 2] << 4) | (sym[:, 3] << 6)
    return packed.astype(np.uint8).tobytes()


def write_file(path: str, sr: int, payload: bytes, label: str) -> None:
    nsym = len(payload) * 4
    lab = label.encode("utf-8")[:39]
    hdr = struct.pack("<8sIIII40s", MAGIC, sr, nsym, zlib.crc32(payload) & 0xFFFFFFFF, 0, lab)
    assert len(hdr) == 64
    if len(hdr) + len(payload) > PART_SIZE:
        raise SystemExit(f"file troppo grande per la partizione: {len(hdr) + len(payload)} byte")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(hdr + payload)
    print(f"{path}: SR {sr} S/s, {nsym} simboli = {nsym / sr:.2f} s, {len(hdr) + len(payload)} byte, CRC {zlib.crc32(payload):08x}")


def cmd_prbs(a):
    n = int(a.sr * a.seconds) // 4 * 4
    rng = np.random.default_rng(a.seed)
    bits = rng.integers(0, 2, size=(2, n), dtype=np.uint8)
    write_file(a.output, a.sr, pack_symbols(bits[0], bits[1]), f"PRBS {a.sr // 1000} kS seed {a.seed}")


def find_ffmpeg(name: str = "ffmpeg") -> str:
    exe = shutil.which(name)
    if exe:
        return exe
    pat = os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\{name}.exe")
    hits = glob.glob(pat)
    if not hits:
        raise SystemExit(f"{name} non trovato")
    return hits[0]


def make_ts(a, bitrate: float, duration: float, path: str, keyint: int = 50, loop_s: float | None = None) -> None:
    """monoscopio H.265 + tono AAC in un TS CBR a bitrate bit/s (ffmpeg).
    loop_s: aggiunge in basso una fascia per vedere i fotogrammi persi: barra a scanner (avanti e indietro in un GOP,
    un passo a ogni fotogramma), avanzamento del loop su una scala con una tacca al secondo, contatore e tempo."""
    audio_k = getattr(a, "audio_kbps", None) or 32
    video_k = int(bitrate * 0.80 / 1000) - audio_k            # margine per PSI, PCR e riempimento CBR
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    fec_txt = getattr(a, "fec_label", a.fec)
    # no_sr: monoscopi del beacon (make_es.py), trasmessi a qualsiasi SR: niente SR nell'immagine (lo mostra il ricevitore)
    sr_txt = "" if getattr(a, "no_sr", False) else f"SR {a.sr // 1000} kS  "
    lines = [a.callsign, f"BEACON DATV  {a.label_freq}" if a.label_freq else "BEACON DATV",
             f"DVB-S2 QPSK  {fec_txt + '  ' if fec_txt else ''}{sr_txt}H.265"]
    text = ",".join(
        f"drawtext=fontfile='{font}':text='{t}':fontcolor=white:fontsize={56 if k == 0 else 26}:box=1:boxcolor=black@0.6"
        f":boxborderw=8:x=(w-text_w)/2:y={40 if k == 0 else (200 if loop_s else 250) + 36 * (k - 1)}"
        for k, t in enumerate(lines))
    if getattr(a, "no_text", False):
        text = "null"
    fit = "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25"
    if getattr(a, "video", None):
        vin = ["-stream_loop", "-1", "-i", a.video]          # ripetuto se più corto del loop
    elif a.image:
        vin = ["-loop", "1", "-framerate", "25", "-i", a.image]
    else:
        vin = ["-f", "lavfi", "-i", "smptehdbars=size=640x360:rate=25"]
        fit = "scale=640:360"
    if loop_s and not getattr(a, "no_band", False):
        # fascia in basso: scala del loop (tacca ogni secondo), cursore di avanzamento e barra a scanner disegnati con
        # overlay, che ricalcola la posizione a ogni fotogramma (n = numero del fotogramma; drawbox la calcola una volta)
        nfr = int(round(loop_s * 25))
        band = ["drawbox=x=0:y=300:w=640:h=60:color=black@0.85:t=fill"]
        band += [f"drawbox=x={10 + int(s * 620 / loop_s)}:y=304:w=2:h=10:color=white:t=fill" for s in range(int(loop_s) + 1)]
        band.append(f"drawtext=fontfile='{font}':text='FRAME %{{eif\\:n\\:d\\:3}}/{nfr}   "
                    "T %{eif\\:n/25\\:d}.%{eif\\:mod(n\\,25)*4\\:d\\:2} s':fontcolor=white:fontsize=18:x=640-text_w-8:y=333")
        vf = (f"color=c=yellow:s=20x28:r=25[sc];color=c=0x36c2ff:s=6x14:r=25[pg];"
              f"[0:v]{fit},format=yuv420p,{text},{','.join(band)}[b];"
              f"[b][sc]overlay=x='10+330*(1-abs(1-2*mod(n,{keyint})/{keyint}))':y=328:shortest=1[c];"
              f"[c][pg]overlay=x='7+n*620/{nfr}':y=303:shortest=1,format=yuv420p[v]")
        vargs = ["-filter_complex", vf, "-map", "[v]", "-map", "1:a"]
    else:
        vargs = ["-filter_complex", f"[0:v]{fit},format=yuv420p,{text}[v]", "-map", "[v]", "-map", "1:a"]
    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *vin,
           *(["-stream_loop", "-1", "-i", a.audio] if getattr(a, "audio", None)
             else ["-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000"]),
           "-t", f"{duration:.3f}",
           *vargs,
           "-c:v", "libx265", "-preset", "medium", "-b:v", f"{video_k}k",
           "-x265-params", f"keyint={keyint}:min-keyint={keyint}:scenecut=0:bframes=0:vbv-maxrate={video_k}:vbv-bufsize={video_k}:log-level=error",
           # AAC mono 32 kbit/s come la catena TX del Pd4Pdx (con MP2 il lettore del Portsdown 4 tace dopo ~8 s);
           # il sine di ffmpeg è già a -18 dBFS
           "-c:a", "aac", "-b:a", f"{audio_k}k", "-ac", "1", "-ar", "48000",
           # il loop riparte da capo: il primo pacchetto di ogni flusso segnala la discontinuità di PCR/PTS e dei contatori,
           # così il ricevitore riaggancia anche l'audio a ogni giro
           "-f", "mpegts", "-muxrate", str(int(bitrate)), "-pcr_period", "40", "-mpegts_flags", "+initial_discontinuity",
           "-metadata", f"service_name={a.callsign}", "-metadata", "service_provider=IZ7PDX BEACON",
           path]
    print("ffmpeg: video", video_k, "kbit/s, audio", audio_k, "kbit/s, TS", int(bitrate), "bit/s,", f"{duration:.2f} s")
    subprocess.run(cmd, check=True)


def cmd_dvbs2(a):
    e = dvbs2.Encoder(a.fec, a.frame, not a.no_pilots)
    bitrate = e.ts_bitrate(a.sr)
    f0 = e.loop_frames()
    while (f0 * e.frame_symbols) % 4:                         # simboli multipli di 4 (byte interi)
        f0 *= 2
    t0 = f0 * e.frame_symbols / a.sr
    nloop = max(1, round(a.seconds / t0))
    max_sym = (PART_SIZE - 64) * 4
    while nloop > 1 and nloop * f0 * e.frame_symbols > max_sym:
        nloop -= 1
    frames = nloop * f0
    packets = frames * e.dfl // 1504
    duration = packets * 1504 / bitrate
    print(f"DVB-S2 QPSK {a.fec} {a.frame} pilot {'no' if a.no_pilots else 'si'}: TS {bitrate:.0f} bit/s, "
          f"{e.frame_symbols} simboli/PLFRAME, loop {frames} PLFRAME = {packets} pacchetti = {duration:.2f} s")
    with tempfile.TemporaryDirectory() as td:
        ts_path = a.ts or os.path.join(td, "beacon.ts")
        if not a.ts:
            # il contenuto finisce prima della fine del loop: il resto sono pacchetti nulli, e ogni giro riparte da
            # PAT/PMT e da un'immagine IDR invece di tagliare a metà di un GOP
            make_ts(a, bitrate, duration - 0.5, ts_path)
        ts = Path(ts_path).read_bytes()
        if a.keep_ts:
            Path(a.keep_ts).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ts_path, a.keep_ts)
    if len(ts) % 188 or ts[0] != 0x47:
        raise SystemExit("il TS non è allineato a pacchetti da 188 byte")
    have = len(ts) // 188
    if have < packets:
        print(f"TS di {have} pacchetti: aggiunti {packets - have} pacchetti nulli fino alla fine del loop")
        ts += NULL_PACKET * (packets - have)
    elif have > packets:
        print(f"ATTENZIONE: TS di {have} pacchetti, tagliato a {packets} (il loop riparte a metà del contenuto)")
    ts = ts[:packets * 188]
    si, sq = e.encode_loop(ts)
    assert len(si) == frames * e.frame_symbols
    write_file(a.output, a.sr, dvbs2.pack(si, sq), f"{a.callsign} S2 QPSK{a.fec} {a.frame[0].upper()} {a.sr // 1000}k")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prbs", help="simboli QPSK casuali (prova del lettore)")
    s.add_argument("--sr", type=int, default=333000)
    s.add_argument("--seconds", type=float, default=10.0)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("-o", "--output", default="symbols.bin")
    s.set_defaults(func=cmd_prbs)
    d = sub.add_parser("dvbs2", help="monoscopio o TS codificato in DVB-S2 QPSK")
    d.add_argument("--sr", type=int, default=333000)
    d.add_argument("--fec", default="3/4", choices=dvbs2.RATES)
    d.add_argument("--frame", default="normal", choices=["normal", "short"])
    d.add_argument("--no-pilots", action="store_true")
    d.add_argument("--seconds", type=float, default=20.0, help="durata desiderata del loop")
    d.add_argument("--callsign", default="IZ7PDX")
    d.add_argument("--label-freq", default="2375.000 MHz")
    d.add_argument("--image", help="immagine del monoscopio (altrimenti barre SMPTE)")
    d.add_argument("--ts", help="TS già pronto al bitrate giusto")
    d.add_argument("--keep-ts", help="salva anche il TS generato")
    d.add_argument("-o", "--output", default="symbols.bin")
    d.set_defaults(func=cmd_dvbs2)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()

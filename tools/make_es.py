"""ESP32-S3-CAM_PDX DATV Beacon (IZ7PDX, GPL-3.0) - monoscopio come flussi elementari per il multiplexer TS del firmware (ESPLAY, fase C2).

Genera con ffmpeg il monoscopio (H.265 640x360 25 fps, GOP 48 = 1,92 s, AAC mono 32 kbit/s, tono 1 kHz) lungo
esattamente 19,2 s: multiplo comune delle immagini (40 ms) e dei frame AAC (1024 campioni a 48 kHz = 21,33 ms), così il
loop riparte da un'immagine IDR e l'audio resta in passo. Poi estrae dal TS i PES (un'unità = un PES) con il PTS relativo.

Formato "PDXES001" (little endian):
  intestazione 64 byte: magic[8], unità, durata del loop (tick a 90 kHz), stream_type video, stream_type audio,
                         offset dei dati, label[36]
  tabella: per unità  tipo (0 video, 1 audio), 3 byte riservati, pts relativo (90 kHz), offset, lunghezza
  dati: contenuto dei PES senza intestazione PES
Monoscopio standard (protetto, lo scrive solo chi prepara la scheda, a 6 MB nella partizione "symbols"):
  python make_es.py -o out/mono_es.bin
  python -m esptool --port COM3 write-flash 0x810000 out/mono_es.bin
Monoscopio personalizzato (video o immagine; poi pagina web o tools/upload_media.py, vedi docs/MONOSCOPIO.md):
  python make_es.py --video mio.mp4 --loops 2 --no-band -o out/mio_es.bin
Il bitrate del video è calcolato per il FEC più robusto (--rate-fec, predefinito 1/2): il file va bene con tutti i FEC.
"""
import argparse
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "dvbs2"))
import encoder as dvbs2  # noqa: E402
import make_symbols  # noqa: E402

LOOP_UNIT_S = 19.2                         # 480 immagini e 900 frame AAC esatti
ES_OFFSET = 0x600000                       # dentro la partizione "symbols"
USER_MAX = 0x300000                        # area del video personalizzato


def parse_pes(ts: bytes, pid: int):
    """[(pts, payload)] dei PES del PID dato."""
    out, cur = [], None
    for i in range(0, len(ts) - 187, 188):
        p = ts[i:i + 188]
        if ((p[1] & 0x1F) << 8 | p[2]) != pid:
            continue
        afc = (p[3] >> 4) & 3
        off = 4 + (1 + p[4] if afc & 2 else 0)
        if not afc & 1:
            continue
        if p[1] & 0x40:
            if cur:
                out.append(cur)
            h = p[off:]
            assert h[:3] == b"\x00\x00\x01"
            hl = h[8]
            q = h[9:14]
            pts = ((q[0] >> 1) & 7) << 30 | q[1] << 22 | (q[2] >> 1) << 15 | q[3] << 7 | q[4] >> 1
            cur = [pts, bytearray(h[9 + hl:])]
        elif cur:
            cur[1] += p[off:]
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sr", type=int, default=333000)
    ap.add_argument("--rate-fec", default="1/2", help="FEC per il calcolo del bitrate (il più robusto che si userà)")
    ap.add_argument("--callsign", default="ESP32-S3-CAM_PDX", help="prima riga del monoscopio (il monoscopio standard pubblico porta il nome del progetto; il nominativo di chi trasmette arriva dalla SDT)")
    ap.add_argument("--label-freq", default="", help="frequenza scritta nell'immagine (predefinito: nessuna; la frequenza finale arriva dalla SDT e dai modi dal vivo)")
    ap.add_argument("--image", help="immagine fissa (ritagliata/riquadrata a 640x360)")
    ap.add_argument("--video", help="video (ripetuto se più corto, tagliato se più lungo)")
    ap.add_argument("--audio", help="audio al posto del tono da 1 kHz (ripetuto se più corto; per un loop perfetto: 19,2 s)")
    ap.add_argument("--audio-kbps", type=int, default=32, help="bitrate AAC mono (32..64; il video si riduce di conseguenza)")
    ap.add_argument("--loops", type=int, default=1, help="durata del loop in multipli di 19,2 s (1..5)")
    ap.add_argument("--no-text", action="store_true", help="senza le scritte QRZ / frequenza / modulazione")
    ap.add_argument("--no-band", action="store_true", help="senza la fascia con scanner e contatore in basso")
    ap.add_argument("-o", "--output", default="out/mono_es.bin")
    a = ap.parse_args()
    if not 1 <= a.loops <= 5:
        raise SystemExit("--loops 1..5")
    LOOP_S = LOOP_UNIT_S * a.loops
    LOOP_TICKS = int(round(LOOP_S * 90000))
    a.fec = a.rate_fec
    e = dvbs2.Encoder(a.rate_fec, "normal", True)
    bitrate = e.ts_bitrate(a.sr)
    with tempfile.TemporaryDirectory() as td:
        tsp = str(Path(td) / "m.ts")
        # audio più lungo del loop: se ne prendono 900 frame consecutivi (il primo arriva dopo il ritardo dell'encoder)
        make_symbols.make_ts(SimpleNamespace(callsign=a.callsign, label_freq=a.label_freq, fec=a.fec, fec_label="",
                                             sr=a.sr, image=a.image, video=a.video, no_text=a.no_text,
                                             audio=a.audio, audio_kbps=a.audio_kbps,
                                             no_band=a.no_band, no_sr=True),
                             bitrate, LOOP_S + 1.0, tsp, keyint=48, loop_s=LOOP_S)
        ts = Path(tsp).read_bytes()
    video = parse_pes(ts, 0x100)
    audio = parse_pes(ts, 0x101)
    base = video[0][0]
    units = [(0, pts - base, bytes(d)) for pts, d in video if 0 <= pts - base < LOOP_TICKS]
    # audio: un'unità per frame AAC (ADTS, 1024 campioni = 1920 tick); 900 frame consecutivi = 19,2 s esatti, ridistribuiti
    # da 0: il tono da 1 kHz fa 19200 periodi interi nel loop, quindi la fase coincide al giro
    frames = []
    for _, d in audio:
        i = 0
        while i + 7 <= len(d):
            assert d[i] == 0xFF and d[i + 1] & 0xF0 == 0xF0, "sync ADTS perso"
            fl = ((d[i + 3] & 3) << 11) | (d[i + 4] << 3) | (d[i + 5] >> 5)
            frames.append(bytes(d[i:i + fl]))
            i += fl
    need = LOOP_TICKS // 1920
    if len(frames) < need:
        raise SystemExit(f"solo {len(frames)} frame AAC, ne servono {need}")
    units += [(1, k * 1920, frames[k]) for k in range(need)]
    units.sort(key=lambda u: (u[1], u[0]))
    nv = sum(1 for u in units if u[0] == 0)
    print(f"video: {nv} immagini ({len(video)} nel TS), audio: {sum(1 for u in units if u[0] == 1)} frame AAC "
          f"(da {len(frames)} disponibili); loop {LOOP_S} s = {LOOP_TICKS} tick")
    if nv != int(LOOP_S * 25):
        print("ATTENZIONE: numero di immagini diverso da 25 fps x durata del loop")
    table, data = b"", b""
    hdr_len = 64
    data_off = hdr_len + 16 * len(units)
    for k, pts, d in units:
        table += struct.pack("<B3xIII", k, pts, len(data), len(d))
        data += d
    src = Path(a.video or a.image).stem if (a.video or a.image) else a.callsign
    label = f"{src}".encode("ascii", "replace")[:35]                 # senza SR: il monoscopio vale per ogni SR
    hdr = struct.pack("<8sIIIII36s", b"PDXES001", len(units), LOOP_TICKS, 0x24, 0x0F, data_off, label)
    assert len(hdr) == 64
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    if (a.video or a.image) and len(hdr + table + data) > USER_MAX:
        raise SystemExit(f"file di {len(hdr + table + data)} byte: oltre i {USER_MAX} dell'area personalizzata (ridurre --loops)")
    Path(a.output).write_bytes(hdr + table + data)
    print(f"{a.output}: {len(units)} unità, {len(hdr + table + data)} byte (TS {bitrate:.0f} bit/s, "
          f"contenuto {len(data) * 8 / LOOP_S / 1000:.0f} kbit/s)")


if __name__ == "__main__":
    main()

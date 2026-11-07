"""ESP32-S3-CAM_PDX DATV Beacon (IZ7PDX, GPL-3.0) - immagine 320x240 YUYV "PDXIMG01" per il PiP e per il monoscopio fisso personalizzato.

Formato (little endian): intestazione di 64 byte  magic[8] "PDXIMG01", larghezza u16, altezza u16, formato u32 (1 = YUYV),
byte di dati u32, CRC-32 dei dati u32, label[36], 4 byte riservati; poi 320x240x2 byte YUYV (Y0 U Y1 V).

Sfondo STANDARD del PiP (barre colorate; protetto, lo scrive solo chi prepara la scheda):
  python make_bg.py -o out/bg_std.bin
  python -m esptool --port COM3 write-flash 0xC10000 out/bg_std.bin
Immagine PERSONALIZZATA (poi pagina web o tools/upload_media.py IMAGE):
  python make_bg.py --image foto.jpg -o out/mia_img.bin
L'immagine è ritagliata al centro a 4:3 (come fa la pagina web).
"""
import argparse
import struct
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_symbols import find_ffmpeg  # noqa: E402

W, H = 320, 240


def build(image: str | None, label: str) -> bytes:
    if image:
        vin = ["-i", image]
        # dalla v1.2.0 si trasmette la fascia centrale 16:9 (320x180, righe 30..209): l'immagine va lì, nero sopra e sotto
        vf = f"scale={W}:180:force_original_aspect_ratio=increase,crop={W}:180,pad={W}:{H}:0:30:black,setsar=1"
    else:
        vin = ["-f", "lavfi", "-i", f"smptebars=size={W}x{H}:rate=1"]
        vf = "null"
    raw = subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", *vin, "-frames:v", "1", "-vf", vf,
                          "-pix_fmt", "yuyv422", "-f", "rawvideo", "-"], check=True, capture_output=True).stdout
    if len(raw) != W * H * 2:
        raise SystemExit(f"ffmpeg ha prodotto {len(raw)} byte invece di {W * H * 2}")
    lab = label.encode("ascii", "replace")[:35]
    hdr = struct.pack("<8sHHIII36s4x", b"PDXIMG01", W, H, 1, len(raw), zlib.crc32(raw), lab)
    assert len(hdr) == 64
    return hdr + raw


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", help="immagine personalizzata (altrimenti barre colorate standard)")
    ap.add_argument("--label", help="nome mostrato nella pagina web")
    ap.add_argument("-o", "--output", default="out/bg_std.bin")
    a = ap.parse_args()
    data = build(a.image, a.label or (Path(a.image).stem if a.image else "barre standard"))
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_bytes(data)
    print(f"{a.output}: {len(data)} byte, 320x240 YUYV")


if __name__ == "__main__":
    main()

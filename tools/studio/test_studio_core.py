"""PDX Beacon Studio - prova del nucleo senza scheda e senza interfaccia (python test_studio_core.py).

Crea monoscopi da video (audio del video, inizio spostato, bande nere), da immagine e standard; li rilegge, ne
ricostruisce l'anteprima MP4 e confronta la stima dei 3 MB con il file vero. Controlla anche i rifiuti dei file errati.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio_core as core  # noqa: E402

fails = 0


def make_inputs(td: Path) -> tuple[Path, Path]:
    """video di prova 4:3 da 25 s con audio e immagine 1280x720, generati con ffmpeg (nessun file esterno)"""
    video, image = td / "sample_video.mp4", td / "sample_image.png"
    ff = core.ffmpeg()
    core._run([ff, "-y", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=25:duration=25",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", str(video)])
    core._run([ff, "-y", "-f", "lavfi", "-i", "smptehdbars=size=1280x720", "-frames:v", "1", str(image)])
    return video, image


def check(name, cond, extra=""):
    global fails
    fails += not cond
    print(f"{'ok ' if cond else 'ERR'} {name} {extra}")


with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    VIDEO, IMAGE = make_inputs(td)
    cases = [
        ("video, audio del video, da 3 s", core.BinOptions(VIDEO, td / "v1.bin", start=3, audio="source", audio_k=48)),
        ("video 2 loop, bande nere, tono", core.BinOptions(VIDEO, td / "v2.bin", loops=2, fit="fit")),
        ("video a 125 kS con scritte e fascia", core.BinOptions(VIDEO, td / "v3.bin", sr=125000, text=True, band=True,
                                                              callsign="IZ7PDX/B", label_freq="2375.000 MHz")),
        ("immagine fissa", core.BinOptions(IMAGE, td / "i1.bin")),
        ("monoscopio standard", core.BinOptions(None, td / "s1.bin", text=True)),
    ]
    for name, o in cases:
        core.build_bin(o, log=lambda s: None)
        info = core.read_bin(o.out)
        est = core.estimate(o.sr, o.fec, o.loops, o.audio_k)
        check(name, info.kind == "VIDEO" and info.video_units == 480 * o.loops and info.size <= core.USER_MAX,
              f"| {info.size} byte, stima {est.bytes_est} ({est.bytes_est / info.size:.2f}x), etichetta '{info.label}'")
        check(f"  stima prudente ({name})", est.bytes_est >= info.size * 0.95)
        mp4 = core.bin_to_mp4(o.out, td / (o.out.stem + ".mp4"))
        p = core.probe(mp4)
        check(f"  anteprima MP4 ({name})", p.width == 640 and p.height == 360 and abs(p.duration - info.loop_s) < 0.5,
              f"| {p.describe()}")
    check("etichetta dal nome della sorgente", core.read_bin(td / "v1.bin").label == "sample_video")
    img = core.image_bin(IMAGE)
    check("immagine -> PDXIMG01", img[:8] == b"PDXIMG01" and len(img) == 64 + 320 * 240 * 2)
    core.check_upload_data("IMAGE", img)
    core.check_upload_data("VIDEO", (td / "v1.bin").read_bytes())
    for kind, data, why in (("VIDEO", img, "immagine come video"), ("IMAGE", (td / "v1.bin").read_bytes(), "video come immagine"),
                            ("VIDEO", b"PDXES001" + bytes(core.USER_MAX), "video oltre 3 MB")):
        try:
            core.check_upload_data(kind, data)
            check(f"rifiuto: {why}", False)
        except ValueError:
            check(f"rifiuto: {why}", True)

print(f"\n{'TUTTO OK' if not fails else f'{fails} ERRORI'}")
sys.exit(1 if fails else 0)

# Changelog

## 0.1.1-pre — 2026-11-08

**Fix — please update from 0.1.0-pre.**
- In 0.1.0-pre the results of background jobs were lost when the slot was a lambda: in the *Carica sul beacon* tab
  "Leggi lo stato della scheda", upload and erase never completed and the buttons stayed disabled (the monoscope tab
  was not affected). Jobs are now kept alive until their result is delivered.
- New interface test `tools/studio/test_gui.py` (offscreen): 50/50 results delivered (0/50 with 0.1.0-pre), monoscope
  created from the button.
- Verified with the GUI on beacon firmware v1.2.0 via USB: board status read, upload confirmation naming the file on
  the board.

## 0.1.0-pre — 2026-11-07

First pre-release.
- **Monoscopio .bin**: video or picture → PDXES001 monoscope (H.265 640x360, AAC), start point, loop 19.2 s × 1–5,
  fill/fit 16:9, audio tone / source / file, SR and FEC → video bitrate, optional callsign/frequency text and frame
  counter band, size estimate against 3 MB, MP4 preview of the `.bin`.
- **Carica sul beacon**: Wi-Fi (dashboard API) or USB (serial `MEDIAW`), board status, upload of the custom video or
  picture, erase; the board is read first and the confirmation names the file that will be replaced or erased.
- Tested with beacon firmware v1.2.0: USB upload of 0.53 MB and 1.92 MB monoscopes (about 5 KB/s). Wi-Fi upload not
  yet tested on hardware.
- Interface in Italian.

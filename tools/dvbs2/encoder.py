"""ESP32-S3-CAM_PDX DATV Beacon (IZ7PDX, GPL-3.0) - codificatore DVB-S2 (QPSK, CCM, un solo Transport Stream), scritto dalla norma
ETSI EN 302 307-1 V1.4.1:
  5.1   mode adaptation: CRC-8 dei pacchetti, BBHEADER (MATYPE, UPL, DFL, SYNC, SYNCD, CRC-8)
  5.2   stream adaptation: BB scrambling con PRBS 1 + X^14 + X^15
  5.3   BCH (tabelle 5a/5b, polinomi 6a/6b) e LDPC (annessi B e C, in ldpc_tables.json)
  5.4.1 mappatura QPSK (bit 2i -> I, bit 2i+1 -> Q, 0 = +)
  5.5   PLHEADER (SOF + PLS code), pilot, scrambling PL (sequenza di Gold n)

Uscita: simboli QPSK come due array di +1/-1 (I, Q) per PLFRAME, oppure impacchettati 4 per byte nel formato del
firmware (bit 0 = I, bit 1 = Q, 1 = +1, primo simbolo nei bit bassi).
"""
import json
from pathlib import Path

import numpy as np

_TABLES = json.loads(Path(__file__).with_name("ldpc_tables.json").read_text(encoding="utf-8"))

RATES = ["1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10"]
MODCOD_QPSK = {r: i + 1 for i, r in enumerate(RATES)}                     # tabella 12

# tabelle 6a e 6b: esponenti dei polinomi g1 ... g12
BCH_NORMAL = [
    [0, 2, 3, 5, 16], [0, 1, 4, 5, 6, 8, 16], [0, 2, 3, 4, 5, 7, 8, 9, 10, 11, 16], [0, 2, 4, 6, 9, 11, 12, 14, 16],
    [0, 1, 2, 3, 5, 8, 9, 10, 11, 12, 16], [0, 2, 4, 5, 7, 8, 9, 10, 12, 13, 14, 15, 16],
    [0, 2, 5, 6, 8, 9, 10, 11, 13, 15, 16], [0, 1, 2, 5, 6, 8, 9, 12, 13, 14, 16], [0, 5, 7, 9, 10, 11, 16],
    [0, 1, 2, 5, 7, 8, 10, 12, 13, 14, 16], [0, 2, 3, 5, 9, 11, 12, 13, 16], [0, 1, 5, 6, 7, 9, 11, 12, 16],
]
BCH_SHORT = [
    [0, 1, 3, 5, 14], [0, 6, 8, 11, 14], [0, 1, 2, 6, 9, 10, 14], [0, 4, 7, 8, 10, 12, 14], [0, 2, 4, 6, 8, 9, 11, 13, 14],
    [0, 3, 7, 8, 9, 13, 14], [0, 2, 5, 6, 7, 10, 11, 13, 14], [0, 5, 8, 9, 10, 11, 14], [0, 1, 2, 3, 9, 10, 14],
    [0, 3, 6, 9, 11, 12, 14], [0, 4, 11, 12, 14], [0, 1, 2, 3, 5, 6, 7, 8, 10, 13, 14],
]

SOF = 0x18D2E82                                                         # 5.5.2.1, 26 bit
PLS_ROWS = [0x55555555, 0x33333333, 0x0F0F0F0F, 0x00FF00FF, 0x0000FFFF, 0xFFFFFFFF]   # figura 13b (righe di G)
PLS_SCRAMBLE = "0111000110011101100000111100100101010011010000100010110111111010"   # 5.5.2.4


def bits_msb(value: int, n: int) -> list:
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def crc8(data: bytes) -> int:
    """5.1.4: g(X) = X^8 + X^7 + X^6 + X^4 + X^2 + 1, registro a zero, bit più significativo per primo."""
    crc = 0
    for byte in data:
        for k in range(7, -1, -1):
            fb = ((crc >> 7) & 1) ^ ((byte >> k) & 1)
            crc = (crc << 1) & 0xFF
            if fb:
                crc ^= 0xD5                                            # X^7 + X^6 + X^4 + X^2 + 1
    return crc


def bb_prbs(n: int) -> np.ndarray:
    """5.2.2: PRBS 1 + X^14 + X^15, registro (stadi 1..15) caricato con 100101010000000 a ogni BBFRAME."""
    reg = [int(c) for c in "100101010000000"]
    out = np.empty(n, np.uint8)
    for i in range(n):
        b = reg[13] ^ reg[14]
        out[i] = b
        reg = [b] + reg[:14]
    return out


def gf2_mul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1
        b >>= 1
    return r


class Bch:
    """5.3.1: codice BCH sistematico; generatore = prodotto dei primi t polinomi delle tabelle 6a/6b."""

    def __init__(self, frame: str, kbch: int, nbch: int, t: int):
        polys = BCH_NORMAL if frame == "normal" else BCH_SHORT
        g = 1
        for exps in polys[:t]:
            g = gf2_mul(g, sum(1 << e for e in exps))
        self.deg = g.bit_length() - 1
        assert self.deg == nbch - kbch, (self.deg, nbch, kbch)
        self.g, self.k, self.n = g, kbch, nbch
        # riga j = x^(n-1-j) mod g: il bit j del messaggio (j = 0 il primo trasmesso) vale m_(k-1-j) x^(k-1-j) * x^(n-k)
        rows = np.zeros((kbch, self.deg), np.uint8)
        r = 1 << self.deg                                              # x^(n-k), poi ridotto
        r = self._mod(r)
        rem = []
        for _ in range(kbch):
            rem.append(r)
            r <<= 1
            if r >> self.deg:
                r ^= g
        for j in range(kbch):
            v = rem[kbch - 1 - j]                                      # x^(n-k + (k-1-j))
            rows[j] = bits_msb(v, self.deg)
        self.rows = rows

    def _mod(self, v: int) -> int:
        while v.bit_length() - 1 >= self.deg:
            v ^= self.g << (v.bit_length() - 1 - self.deg)
        return v

    def parity(self, msg: np.ndarray) -> np.ndarray:
        """msg: k bit (0/1); restituisce i n-k bit di parità, il coefficiente di grado più alto per primo."""
        return (msg.astype(np.int32) @ self.rows.astype(np.int32) & 1).astype(np.uint8)


class Ldpc:
    """5.3.2: accumulo degli indirizzi delle tabelle B/C, poi p_i = p_i xor p_(i-1)."""

    def __init__(self, frame: str, rate: str, nldpc: int):
        t = _TABLES[frame][rate]
        self.k, self.q = t["kldpc"], t["q"]
        self.n_k = nldpc - self.k
        info, addr = [], []
        for r, row in enumerate(t["rows"]):
            m = np.arange(360)
            for x in row:
                info.append(360 * r + m)
                addr.append((x + m * self.q) % self.n_k)
        self.info = np.concatenate(info)
        self.addr = np.concatenate(addr)

    def encode(self, bits: np.ndarray) -> np.ndarray:
        assert len(bits) == self.k
        sel = self.addr[bits[self.info] == 1]
        p = (np.bincount(sel, minlength=self.n_k) & 1).astype(np.uint8)
        p = np.bitwise_xor.accumulate(p)
        return np.concatenate((bits.astype(np.uint8), p))

    def check(self, cw: np.ndarray) -> bool:
        """controllo di parità: per ogni equazione j, xor degli info collegati + p_j + p_(j-1) = 0."""
        bits, p = cw[:self.k], cw[self.k:]
        s = (np.bincount(self.addr[bits[self.info] == 1], minlength=self.n_k) & 1).astype(np.uint8)
        s ^= p
        s[1:] ^= p[:-1]
        return not s.any()


def gold_rotation(n_code: int, length: int) -> np.ndarray:
    """5.5.4: R_n(i) = 2 z_n((i + 131072) mod (2^18 - 1)) + z_n(i), con z_n(i) = x(i + n) + y(i)."""
    N = (1 << 18) - 1
    x = np.zeros(N + 18, np.uint8)
    y = np.ones(N + 18, np.uint8)
    x[0] = 1
    x[1:18] = 0
    for i in range(N):
        x[i + 18] = x[i + 7] ^ x[i]
        y[i + 18] = y[i + 10] ^ y[i + 7] ^ y[i + 5] ^ y[i]
    i = np.arange(N)
    z = x[(i + n_code) % N] ^ y[:N]
    k = np.arange(length)
    return (2 * z[(k + 131072) % N] + z[k]).astype(np.uint8)


class Encoder:
    def __init__(self, rate: str = "3/4", frame: str = "normal", pilots: bool = True, gold_n: int = 0, roll_off: str = "0.35"):
        if rate not in _TABLES[frame]:
            raise ValueError(f"rate {rate} non disponibile per i frame {frame}")
        t = _TABLES[frame][rate]
        self.rate, self.frame, self.pilots = rate, frame, pilots
        self.nldpc = 64800 if frame == "normal" else 16200
        self.kbch, self.dfl = t["kbch"], t["kbch"] - 80
        self.bch = Bch(frame, t["kbch"], t["nbch"], t["t"])
        self.ldpc = Ldpc(frame, rate, self.nldpc)
        self.prbs = bb_prbs(self.kbch)
        self.slots = self.nldpc // 2 // 90
        npil = 36 * ((self.slots - 1) // 16) if pilots else 0
        self.frame_symbols = 90 * (self.slots + 1) + npil
        self.rot = gold_rotation(gold_n, self.frame_symbols - 90)
        ro = {"0.35": 0, "0.25": 1, "0.20": 2}[roll_off]
        self.matype1 = 0b11110000 | ro                                 # TS, SIS, CCM, ISSYI 0, NPD 0, RO
        self.header_i, self.header_q = self._plheader()

    # ---------------------------------------------------------------- PLHEADER
    def _plscode(self) -> list:
        modcod = MODCOD_QPSK[self.rate]
        b = bits_msb((modcod << 2) | ((self.frame == "short") << 1) | int(self.pilots), 7)   # b1..b7
        y = 0
        for k in range(6):                                             # MODCOD e MSB di TYPE: righe di G
            if b[k]:
                y ^= PLS_ROWS[k]
        yb = bits_msb(y, 32)
        out = []
        for v in yb:                                                   # LSB di TYPE: ripetizione (complementata se 1)
            out += [v, v ^ b[6]]
        return [o ^ int(s) for o, s in zip(out, PLS_SCRAMBLE)]

    def _plheader(self):
        y = bits_msb(SOF, 26) + self._plscode()
        a = 1 - 2 * np.array(y, np.int8)
        idx = np.arange(90)                                            # pi/2 BPSK: dispari (1-based) I = Q = a; pari I = -a, Q = a
        hi = np.where(idx % 2 == 0, a, -a).astype(np.int8)
        hq = a.astype(np.int8)
        return hi, hq

    # ---------------------------------------------------------------- frame
    def bbframe(self, data_field: bytes, syncd: int) -> np.ndarray:
        assert len(data_field) * 8 == self.dfl
        h = bytes([self.matype1, 0x00, 1504 >> 8, 1504 & 255, self.dfl >> 8, self.dfl & 255, 0x47, syncd >> 8, syncd & 255])
        h += bytes([crc8(h)])
        bits = np.unpackbits(np.frombuffer(h + data_field, np.uint8))
        return bits ^ self.prbs

    def plframe(self, bb_bits: np.ndarray):
        cw = self.ldpc.encode(np.concatenate((bb_bits, self.bch.parity(bb_bits))))
        si = 1 - 2 * cw[0::2].astype(np.int8)                          # 5.4.1: MSB (bit 2i) -> I, 0 = +
        sq = 1 - 2 * cw[1::2].astype(np.int8)
        if self.pilots:                                                # 5.5.3: 36 pilot (1+j) ogni 16 slot, non alla fine
            parts_i, parts_q = [], []
            for s in range(0, self.slots, 16):
                e = min(s + 16, self.slots)
                parts_i.append(si[90 * s:90 * e])
                parts_q.append(sq[90 * s:90 * e])
                if e < self.slots:
                    parts_i.append(np.ones(36, np.int8))
                    parts_q.append(np.ones(36, np.int8))
            si, sq = np.concatenate(parts_i), np.concatenate(parts_q)
        r = self.rot                                                   # 5.5.4: (I + jQ) * j^R
        ci = np.select([r == 0, r == 1, r == 2], [si, -sq, -si], sq)
        cq = np.select([r == 0, r == 1, r == 2], [sq, si, -sq], -si)
        return np.concatenate((self.header_i, ci)).astype(np.int8), np.concatenate((self.header_q, cq)).astype(np.int8)

    # ---------------------------------------------------------------- flusso
    def ts_bitrate(self, sr: float) -> float:
        return sr * self.dfl / self.frame_symbols

    def loop_frames(self) -> int:
        """numero minimo di PLFRAME per cui il loop contiene un numero intero di pacchetti TS."""
        from math import gcd
        return 1504 // gcd(self.dfl, 1504)

    def encode_loop(self, ts: bytes, prev_crc: int | None = None):
        """ts: pacchetti TS (multiplo di 188 byte) che riempiono esattamente un numero intero di DATA FIELD.
        Il CRC-8 che sostituisce il primo sync byte è quello dell'ultimo pacchetto (loop ciclico), salvo prev_crc.
        Restituisce (I, Q) di tutti i PLFRAME."""
        pk = np.frombuffer(ts, np.uint8).reshape(-1, 188)
        if not np.all(pk[:, 0] == 0x47):
            raise ValueError("pacchetto TS senza sync byte 0x47")
        if (len(ts) * 8) % self.dfl:
            raise ValueError(f"il TS ({len(ts)} byte) non riempie un numero intero di DATA FIELD da {self.dfl} bit")
        stream = bytearray()
        crc_prev = crc8(pk[-1, 1:].tobytes()) if prev_crc is None else prev_crc
        for row in pk:
            stream.append(crc_prev)
            stream += row[1:].tobytes()
            crc_prev = crc8(row[1:].tobytes())
        nb = self.dfl // 8
        out_i, out_q = [], []
        for f in range(len(stream) // nb):
            pos = f * nb
            first = -(-pos // 188) * 188                               # primo pacchetto che inizia in questo DATA FIELD
            syncd = (first - pos) * 8 if first < pos + nb else 65535
            i, q = self.plframe(self.bbframe(bytes(stream[pos:pos + nb]), syncd))
            out_i.append(i)
            out_q.append(q)
        return np.concatenate(out_i), np.concatenate(out_q)


def pack(si: np.ndarray, sq: np.ndarray) -> bytes:
    """formato del firmware: bit 0 = I, bit 1 = Q (1 = +1), 4 simboli per byte, primo nei bit bassi."""
    if len(si) % 4:
        raise ValueError("il numero di simboli deve essere multiplo di 4")
    s = ((si > 0).astype(np.uint8) | ((sq > 0).astype(np.uint8) << 1)).reshape(-1, 4)
    return (s[:, 0] | s[:, 1] << 2 | s[:, 2] << 4 | s[:, 3] << 6).astype(np.uint8).tobytes()

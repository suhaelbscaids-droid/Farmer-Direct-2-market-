"""
qr.py - tiny dependency-free QR Code generator (byte mode, error-correction level M,
versions 1-10, i.e. up to 213 bytes). Used to draw the UPI payment QR code as SVG.

    svg = qr_svg("upi://pay?pa=ravi@oksbi&pn=Ravi&am=260.00&cu=INR")
"""

# version -> (ecc codewords per block, [(block count, data codewords per block), ...])  [level M]
_EC_M = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
    5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_FORMAT_LEVEL_M = 0  # the two format bits for level M


# ----------------------------------------------------------------- Reed-Solomon
_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i], _LOG[_x] = _x, _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_remainder(data, degree):
    gen = [1]
    for i in range(degree):  # multiply by (x - alpha^i)
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= c
            nxt[j + 1] ^= _gmul(c, _EXP[i])
        gen = nxt
    rem = [0] * degree
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(degree):
            rem[i] ^= _gmul(gen[i + 1], factor)
    return rem


# ----------------------------------------------------------------- data codewords
def _capacity(version):
    ecc, blocks = _EC_M[version]
    return sum(n * d for n, d in blocks)


def _encode_data(data, version):
    bits = [0, 1, 0, 0]  # byte mode indicator
    count_bits = 8 if version <= 9 else 16
    bits += [(len(data) >> i) & 1 for i in range(count_bits - 1, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    cap_bits = _capacity(version) * 8
    bits += [0] * min(4, cap_bits - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    while len(words) < _capacity(version):
        words.append(pad[(len(words) - len(bits) // 8) % 2])
    return words


def _interleave(words, version):
    ecc_len, groups = _EC_M[version]
    blocks, pos = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(words[pos:pos + size])
            pos += size
    eccs = [_rs_remainder(b, ecc_len) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ecc_len):
        out += [e[i] for e in eccs]
    return out


# ----------------------------------------------------------------- matrix drawing
class _Matrix:
    def __init__(self, version):
        self.version, self.size = version, version * 4 + 17
        self.m = [[0] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]
        self._function_patterns()

    def _set(self, x, y, dark):
        self.m[y][x] = 1 if dark else 0
        self.fn[y][x] = True

    def _function_patterns(self):
        s = self.size
        for i in range(s):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (s - 4, 3), (3, s - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < s and 0 <= y < s:
                        self._set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.version]
        for i, cy in enumerate(pos):
            for j, cx in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = (self.version << 12) | rem
            for i in range(18):
                a, b = s - 11 + i % 3, i // 3
                self._set(a, b, (bits >> i) & 1)
                self._set(b, a, (bits >> i) & 1)

    def draw_format(self, mask):
        s = self.size
        data = (_FORMAT_LEVEL_M << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data << 10) | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1
        for i in range(6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(8):
            self._set(s - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, s - 15 + i, bit(i))
        self._set(8, s - 8, True)

    def place(self, codewords):
        s, i = self.size, 0
        total = len(codewords) * 8
        for right in range(s - 1, 0, -2):
            if right == 6:
                right = 5
            for vert in range(s):
                for j in range(2):
                    x = right - j
                    y = s - 1 - vert if ((right + 1) & 2) == 0 else vert
                    if not self.fn[y][x] and i < total:
                        self.m[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1
                        i += 1

    _MASKS = (
        lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
        lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
        lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
    )

    def apply_mask(self, mask):
        f = self._MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and f(x, y):
                    self.m[y][x] ^= 1

    def penalty(self):
        s, m, score = self.size, self.m, 0
        for grid in (m, [list(col) for col in zip(*m)]):  # rows, then columns
            for row in grid:
                run = 1
                for k in range(1, s):
                    if row[k] == row[k - 1]:
                        run += 1
                        if run == 5:
                            score += 3
                        elif run > 5:
                            score += 1
                    else:
                        run = 1
                text = "".join(map(str, row))
                score += 40 * (text.count("10111010000") + text.count("00001011101"))
        for y in range(s - 1):
            for x in range(s - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(map(sum, m))
        score += 10 * (abs(dark * 20 - s * s * 10) // (s * s))
        return score


def make_matrix(text):
    """Return the QR code as a list of rows of 0/1 modules."""
    data = text.encode("utf-8")
    version = next((v for v in range(1, 11) if len(data) <= _capacity(v) - (2 if v <= 9 else 3)), None)
    if version is None:
        raise ValueError("Text is too long for this QR encoder (max about 200 bytes).")
    codewords = _interleave(_encode_data(data, version), version)
    qr = _Matrix(version)
    qr.place(codewords)
    best, best_score = 0, None
    for mask in range(8):
        qr.apply_mask(mask)
        qr.draw_format(mask)
        score = qr.penalty()
        if best_score is None or score < best_score:
            best, best_score = mask, score
        qr.apply_mask(mask)  # undo
    qr.apply_mask(best)
    qr.draw_format(best)
    return qr.m


def qr_svg(text, scale=6, border=4):
    """The QR code as an inline <svg> string (black on white, with a quiet zone)."""
    m = make_matrix(text)
    n = len(m) + 2 * border
    cells = "".join(f"M{x + border},{y + border}h1v1h-1z"
                    for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{n * scale}" '
            f'height="{n * scale}" shape-rendering="crispEdges" role="img" aria-label="UPI payment QR code">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{cells}" fill="#000"/></svg>')

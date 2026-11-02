"""Print a generated test pattern on a NIIMBOT B1 Pro (FW 2.12) over USB CDC with the captured A7 job format.

Sequence from the vendor-app capture: 0x23 label type, 0x21 density, 0x01 start, 0x13 page size,
0xA7 LZO1X raster blocks (576 dots wide, 72 bytes per row, MSB first, set bit = black), 0xE3 page end,
0x88 progress polling via 0x89, 0xF3 job end. The raster is wrapped in literal-only LZO1X streams
(valid for the firmware's decoder, no compression gain). Only fixed test patterns are offered; the
head temperature is checked first (a loose NTC plug reads -20 degC and the firmware does not catch it).
"""
import argparse
import hashlib
import re
import sys
import time

WIDTH = 576
ROW_BYTES = WIDTH // 8
BLOCK_ROWS = 332          # the vendor app uses 332 rows (23904 bytes) per A7 block
MAX_ROWS = 400            # about 34 mm; larger jobs are refused here
PATTERNS = ('bars', 'checker', 'box', 'dots', 'grid', 'text-test', 'compare', 'fullblack')


def xor(data):
    x = 0
    for b in data:
        x ^= b
    return x


def frame(cmd, payload=b'', extended=False):
    length = len(payload).to_bytes(4 if extended else 1, 'big')
    body = bytes([cmd]) + length + payload
    return b'\x55\x55' + body + bytes([xor(body)]) + b'\xaa\xaa'


def lzo1x_literals(data):
    """Valid literal-only LZO1X stream: one initial literal run plus the 11 00 00 end marker."""
    n = len(data)
    if n < 4:
        raise ValueError('block too short for a literal-only stream')
    if n <= 238:
        head = bytes([n + 17])
    else:
        rest = n - 18                    # literals = 18 + 255*k + b with b in 1..255
        k = (rest - 1) // 255
        head = b'\x00' * (k + 1) + bytes([rest - 255 * k])
    return head + data + b'\x11\x00\x00'


def new_raster(rows):
    return bytearray(rows * ROW_BYTES)


def set_px(buf, x, y, rows):
    if 0 <= x < WIDTH and 0 <= y < rows:
        buf[y * ROW_BYTES + (x >> 3)] |= 0x80 >> (x & 7)


def pattern(name, rows):
    r = new_raster(rows)
    if name == 'bars':      # vertical bars 1, 2, 4, 8 px wide with equal gaps, repeated
        x = 8
        for w in (1, 2, 4, 8) * 4:
            for xx in range(x, x + w):
                for y in range(rows):
                    set_px(r, xx, y, rows)
            x += w + w
    elif name == 'checker':
        for y in range(rows):
            for x in range(WIDTH):
                if ((x // 8) + (y // 8)) % 2 == 0:
                    set_px(r, x, y, rows)
    elif name == 'box':     # frame and both diagonals
        for x in range(WIDTH):
            set_px(r, x, 0, rows); set_px(r, x, rows - 1, rows)
        for y in range(rows):
            set_px(r, 0, y, rows); set_px(r, WIDTH - 1, y, rows)
            set_px(r, y * (WIDTH - 1) // max(rows - 1, 1), y, rows)
            set_px(r, WIDTH - 1 - y * (WIDTH - 1) // max(rows - 1, 1), y, rows)
    elif name == 'dots':    # single isolated dots every 24 px
        for y in range(12, rows, 24):
            for x in range(12, WIDTH, 24):
                set_px(r, x, y, rows)
    elif name == 'grid':    # 1 px lines every 32 px
        for y in range(0, rows, 32):
            for x in range(WIDTH):
                set_px(r, x, y, rows)
        for x in range(0, WIDTH, 32):
            for y in range(rows):
                set_px(r, x, y, rows)
    elif name == 'text-test':   # rough 5x7 letters T E S T scaled by 8
        glyph = {'T': ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
                 'E': ['11111', '10000', '10000', '11110', '10000', '10000', '11111'],
                 'S': ['01111', '10000', '10000', '01110', '00001', '00001', '11110']}
        x0 = (WIDTH - (3 * 56 + 40)) // 2       # four letters, 40 px wide, 56 px advance
        y0 = max((rows - 56) // 2, 0)           # glyph height 7 * 8 = 56 px, centred on the label
        for ch in 'TEST':
            for gy, line in enumerate(glyph[ch]):
                for gx, c in enumerate(line):
                    if c == '1':
                        for dy in range(8):
                            for dx in range(8):
                                set_px(r, x0 + gx * 8 + dx, y0 + gy * 8 + dy, rows)
            x0 += 56
    elif name == 'compare':     # density comparison: checker in the upper half, 1/2/4/8 px bars in the lower half
        half = rows // 2
        for y in range(half):
            for x in range(WIDTH):
                if ((x // 8) + (y // 8)) % 2 == 0:
                    set_px(r, x, y, rows)
        x = 8
        for w in (1, 2, 4, 8) * 4:
            for xx in range(x, x + w):
                for y in range(half + 8, rows - 8):
                    set_px(r, xx, y, rows)
            x += w + w
    elif name == 'fullblack':
        for i in range(len(r)):
            r[i] = 0xFF
    else:
        raise ValueError('unknown pattern')
    return bytes(r)


def build_job(raster, rows, density, job_id=None):
    # 0 sets the firmware's low-energy override (density table value 200 instead of 440..560); any
    # following job with 1..5 clears it again
    if not 0 <= density <= 5:
        raise ValueError('density must be 0..5')
    if not 8 <= rows <= MAX_ROWS or len(raster) != rows * ROW_BYTES:
        raise ValueError('rows must be 8..%d and match the raster size' % MAX_ROWS)
    job_id = job_id or hashlib.md5(raster).hexdigest()
    page = rows.to_bytes(2, 'big') + WIDTH.to_bytes(2, 'big') + b'\x00\x01' + b'\x00' * 7 + job_id.encode('ascii')
    assert len(page) == 45
    frames = {'type': frame(0x23, b'\x01'), 'density': frame(0x21, bytes([density])),
              'start': frame(0x01, bytes.fromhex('000100000000000100')), 'page': frame(0x13, page), 'blocks': [],
              'pageend': frame(0xE3, b'\x01'), 'end': frame(0xF3, b'\x01')}
    seq = 0
    for r0 in range(0, rows, BLOCK_ROWS):
        chunk = raster[r0 * ROW_BYTES:min(rows, r0 + BLOCK_ROWS) * ROW_BYTES]
        frames['blocks'].append(frame(0xA7, seq.to_bytes(2, 'big') + lzo1x_literals(chunk), extended=True))
        seq += 1
    return frames


def read_reply(port, expect, timeout=2.0):
    """Read until a frame with the given command byte arrives; return its payload."""
    buf = b''
    end = time.time() + timeout
    while time.time() < end:
        buf += port.read(256)
        for m in re.finditer(b'\x55\x55(.)(.)', buf, re.S):
            cmd, n = m.group(1)[0], m.group(2)[0]
            start = m.end()
            if len(buf) >= start + n + 3 and buf[start + n + 1:start + n + 3] == b'\xaa\xaa' and cmd == expect:
                return buf[start:start + n]
    raise TimeoutError('no reply 0x%02x within %.1f s' % (expect, timeout))


def head_check(port, allow_err=()):
    port.reset_input_buffer()
    port.write(b'test get sensor\r\n')
    buf = b''
    end = time.time() + 3
    while time.time() < end and b'error code id' not in buf:
        buf += port.read(256)
    txt = buf.decode('latin1')
    t = re.search(r'temp:(-?[\d.]+)', txt)
    e = re.search(r'error code id:(\d+)', txt)
    if not t or not e:
        raise RuntimeError('sensor status unreadable; not printing')
    temp, err = float(t.group(1)), e.group(1)
    if temp <= -19.0 or temp >= 45.0 or err != '00' and err not in allow_err:
        raise RuntimeError('head check failed (temp %.2f, error %s); not printing' % (temp, err))
    return temp


def send(port, data, expect=None):
    port.write(data)
    port.flush()
    return read_reply(port, expect) if expect is not None else None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pattern', choices=PATTERNS, required=True)
    p.add_argument('--rows', type=int, default=240)
    p.add_argument('--density', type=int, default=3)
    p.add_argument('--port', default='COM3')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--allow-fullblack', action='store_true')
    p.add_argument('--allow-no-tag', action='store_true', help='accept sensor error 80000 (roll tag not read); only with firmware without the 0xFD density lock')
    a = p.parse_args()
    if a.pattern == 'fullblack' and not a.allow_fullblack:
        p.error('fullblack needs --allow-fullblack (maximum heater load)')
    raster = pattern(a.pattern, a.rows)
    job = build_job(raster, a.rows, a.density)
    black = sum(bin(b).count('1') for b in raster)
    print('pattern %s, %d rows, density %d, black dots %d (%.1f %%), %d A7 block(s)' % (
        a.pattern, a.rows, a.density, black, 100.0 * black / (a.rows * WIDTH), len(job['blocks'])))
    if a.dry_run:
        return 0
    import serial
    port = serial.Serial(a.port, 115200, timeout=0.3)
    try:
        print('head temperature %.2f degC' % head_check(port, ('80000',) if a.allow_no_tag else ()))
        send(port, job['type'], 0x33)
        send(port, job['density'], 0x31)
        send(port, job['start'], 0x02)
        send(port, frame(0x89, b'\x00\x00'), 0x88)
        send(port, job['page'], 0x14)
        for block in job['blocks']:
            port.write(block)
            port.flush()
            time.sleep(0.15)
        send(port, job['pageend'], 0xE4)
        begin = time.time()
        end = begin + 40
        done = False
        while time.time() < end:
            st = send(port, frame(0x89, b'\x00\x00'), 0x88)
            # progress 0x64 (100) sits at index 5 in the status seen after our job and at index 6 in the
            # capture; the previous job leaves 0x64 behind, so accept it only 1.5 s after the page end
            if len(st) > 6 and 0x64 in (st[5], st[6]) and time.time() - begin > 1.5:
                done = True
                break
            time.sleep(0.1)
        send(port, job['end'], 0xF4)
        print('job finished' if done else 'job end sent after timeout')
        port.reset_input_buffer()
        port.write(b'test get sensor\r\n')
        text = b''
        stop = time.time() + 3
        while time.time() < stop and b'error code id' not in text:
            text += port.read(256)
        after = re.search(r'temp:(-?[\d.]+)', text.decode('latin1'))
        print('head temperature after job: %s degC' % (after.group(1) if after else 'unreadable'))
    finally:
        port.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

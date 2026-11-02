#!/usr/bin/env python3
"""flash_guard.py - pure check script for NIIMBOT B1 Pro (SF32LB52x) flash images.

Only reads files (standard library), writes nothing, talks to no device.
All images are flat files, offset 0 = flash address 0x10000000
(exception: image/plan with --base).

Usage:
  flash_guard.py backup <fileA> <fileB>
  flash_guard.py image <image.bin> --backup <fileA> [--base 0x10000000]
  flash_guard.py plan <image.bin> --backup <fileA> [--base 0x10000000]
  flash_guard.py restore-check <backup> <readback> [--allow-diff START:END ...]

Exit code: 0 = all OK, 1 = at least one ERROR, 2 = usage/file error.
"""
import argparse
import hashlib
import os
import stat
import sys

FLASH_BASE = 0x10000000
SECTOR = 0x1000

# Only area cleared for writing (planning: app 0x10009000..0x1007C1D0 + free up to 0x10089000).
WRITE_LO, WRITE_HI = 0x10009000, 0x10089000

# Additionally explicitly forbidden areas (only for clear messages; everything outside
# WRITE_LO..WRITE_HI is blocked anyway).
FORBIDDEN = [
    (0x10000000, 0x10009000, "table/bootloader"),
    (0x10007000, 0x10009000, "FAL partition ble"),
    (0x100FC000, 0x10100000, "crash log, block A (factory data), block B (settings)"),
]
# Area whose use is not yet cleared (open item for the user).
UNCLEARED = [(0x100DA000, 0x100FC000, "extra area, use not cleared")]


class Result:
    def __init__(self):
        self.errors = 0

    def ok(self, msg):
        print("OK:      " + msg)

    def info(self, msg):
        print("INFO:    " + msg)

    def warn(self, msg):
        print("WARNING: " + msg)

    def err(self, msg):
        self.errors += 1
        print("ERROR:   " + msg)

    def finish(self, what):
        if self.errors:
            print("RESULT: ERROR (%s, %d errors)" % (what, self.errors))
            return 1
        print("RESULT: OK (%s)" % what)
        return 0


def read_file(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        print("ERROR:   File not readable: %s (%s)" % (path, e))
        sys.exit(2)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def num(s):
    return int(s, 0)


def hx(a):
    return "0x%08X" % a


def diff_ranges(a, b, start=0, end=None):
    """List of (lo, hi) contiguous differing byte ranges (offsets) in a/b."""
    if end is None:
        end = min(len(a), len(b))
    out = []
    i = start
    while i < end:
        n = min(4096, end - i)
        if a[i:i + n] == b[i:i + n]:
            i += n
            continue
        for j in range(i, i + n):
            if a[j] != b[j]:
                if out and out[-1][1] == j:
                    out[-1] = (out[-1][0], j + 1)
                else:
                    out.append((j, j + 1))
        i += n
    return out


def overlap(lo, hi, rlo, rhi):
    return max(lo, rlo) < min(hi, rhi)


def show_ranges(res, ranges, base, limit=20, label="differs"):
    for lo, hi in ranges[:limit]:
        res.info("%s: %s..%s (%d bytes)" % (label, hx(base + lo), hx(base + hi), hi - lo))
    if len(ranges) > limit:
        res.info("... and %d more ranges" % (len(ranges) - limit))


# ---------------------------------------------------------------- backup
def cmd_backup(args):
    res = Result()
    a, b = read_file(args.fileA), read_file(args.fileB)
    sa, sb = sha(a), sha(b)
    res.info("A: %s  %d bytes  sha256 %s" % (args.fileA, len(a), sa))
    res.info("B: %s  %d bytes  sha256 %s" % (args.fileB, len(b), sb))
    if os.path.abspath(args.fileA) == os.path.abspath(args.fileB):
        res.err("A and B are the same file; two independent reads required")
    if len(a) == 0 or len(b) == 0:
        res.err("File empty")
    if len(a) != len(b):
        res.err("Sizes differ (%d vs %d)" % (len(a), len(b)))
    else:
        res.ok("same size (%d bytes)" % len(a))
    if sa != sb:
        res.err("sha256 differs")
        if len(a) == len(b):
            r = diff_ranges(a, b)
            res.info("%d differing ranges, %d bytes" % (len(r), sum(h - l for l, h in r)))
            show_ranges(res, r, FLASH_BASE)
    else:
        res.ok("sha256 of both files equal")
    for name, d in (("A", a), ("B", b)):
        if d and all(x == 0xFF for x in d):
            res.err("%s consists only of 0xFF (empty/failed read)" % name)
        if d and all(x == 0x00 for x in d):
            res.err("%s consists only of 0x00" % name)
        if len(d) >= 0x9000:
            boot = d[:0x9000]
            if all(x == 0xFF for x in boot) or all(x == 0 for x in boot):
                res.err("%s: boot area 0x10000000..0x10009000 empty" % name)
    if len(a) == 0x100000:
        res.info("Size = 1 MiB = occupied window 0x10000000..0x100FFFFF; actual flash size unverified "
                 "(driver reported size:0x100000, JEDEC ID 0x146085)")
    elif len(a):
        res.warn("Size %d != 1 MiB; check deviation from the occupied window" % len(a))
    for p in (args.fileA, args.fileB):
        try:
            if os.stat(p).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                res.warn("%s is writable; make it read-only after the check (e.g. attrib +R)" % p)
        except OSError:
            pass
    return res.finish("backup")


# ---------------------------------------------------------------- image / plan
def check_image(res, image_path, backup_path, base):
    img = read_file(image_path)
    bak = read_file(backup_path)
    res.info("Backup: %s  %d bytes  sha256 %s" % (backup_path, len(bak), sha(bak)))
    res.info("Image:  %s  %d bytes  sha256 %s  (base %s)" % (image_path, len(img), sha(img), hx(base)))
    ok_geom = True
    if len(bak) == 0:
        res.err("Backup empty")
        ok_geom = False
    if len(img) == 0:
        res.err("Image empty")
        ok_geom = False
    if base < FLASH_BASE or (base - FLASH_BASE) % SECTOR:
        res.err("Base %s outside flash or not 4 KiB aligned" % hx(base))
        ok_geom = False
    off = base - FLASH_BASE
    if ok_geom and off + len(img) > len(bak):
        res.err("Image extends to %s, backup only to %s (image larger than backup)"
                % (hx(base + len(img)), hx(FLASH_BASE + len(bak))))
        ok_geom = False
    if ok_geom and base == FLASH_BASE and len(img) > len(bak):
        res.err("Image (%d) larger than backup (%d)" % (len(img), len(bak)))
    if not ok_geom:
        return img, bak, None
    res.ok("Size fits: image %s..%s lies within the backup" % (hx(base), hx(base + len(img))))
    if len(img) < len(bak) - off:
        res.info("Image shorter than backup: bytes %s..%s are not touched (check only, no writing)"
                 % (hx(base + len(img)), hx(FLASH_BASE + len(bak))))
    seg = bak[off:off + len(img)]
    diffs = diff_ranges(img, seg)  # offsets relative to the image
    total = sum(h - l for l, h in diffs)
    res.info("%d differing ranges, %d bytes compared to the backup" % (len(diffs), total))
    bad = False
    for lo, hi in diffs:
        alo, ahi = base + lo, base + hi
        if alo < WRITE_LO or ahi > WRITE_HI:
            bad = True
            why = "outside write range %s..%s" % (hx(WRITE_LO), hx(WRITE_HI))
            for flo, fhi, name in FORBIDDEN + UNCLEARED:
                if overlap(alo, ahi, flo, fhi):
                    why += "; touches %s..%s (%s)" % (hx(flo), hx(fhi), name)
            res.err("Difference %s..%s (%d bytes) %s" % (hx(alo), hx(ahi), ahi - alo, why))
    if not bad:
        res.ok("all differences lie within %s..%s" % (hx(WRITE_LO), hx(WRITE_HI)))
    # Forbidden areas explicitly byte-identical (if covered by the image)
    for flo, fhi, name in FORBIDDEN + UNCLEARED:
        lo, hi = max(flo, base), min(fhi, base + len(img))
        if lo >= hi:
            res.info("%s..%s (%s): not covered by the image, untouched" % (hx(flo), hx(fhi), name))
            continue
        if img[lo - base:hi - base] == bak[lo - FLASH_BASE:hi - FLASH_BASE]:
            res.ok("%s..%s (%s) byte-identical to the backup" % (hx(lo), hx(hi), name))
        else:
            res.err("%s..%s (%s) differs from the backup" % (hx(lo), hx(hi), name))
    return img, bak, diffs


def cmd_image(args):
    res = Result()
    base = num(args.base)
    img, bak, diffs = check_image(res, args.image, args.backup, base)
    if diffs is not None:
        print("SHA256 Image: %s" % sha(img))
    return res.finish("image")


def cmd_plan(args):
    res = Result()
    base = num(args.base)
    img, bak, diffs = check_image(res, args.image, args.backup, base)
    if res.errors or diffs is None:
        print("NO write plan: check failed.")
        return res.finish("plan")
    sectors = set()
    for lo, hi in diffs:
        a = (base + lo - FLASH_BASE) // SECTOR
        z = (base + hi - 1 - FLASH_BASE) // SECTOR
        sectors.update(range(a, z + 1))
    sectors = sorted(sectors)
    print("WRITE PLAN (%d sectors of 4 KiB, write only these):" % len(sectors))
    for s in sectors:
        print("  Sector %s..%s" % (hx(FLASH_BASE + s * SECTOR), hx(FLASH_BASE + (s + 1) * SECTOR)))
    runs = []
    for s in sectors:
        if runs and runs[-1][1] == s:
            runs[-1][1] = s + 1
        else:
            runs.append([s, s + 1])
    for a, z in runs:
        print("  contiguous: %s..%s (%d KiB)" % (hx(FLASH_BASE + a * SECTOR), hx(FLASH_BASE + z * SECTOR),
                                                       (z - a) * 4))
    if not sectors:
        print("  (empty: image is identical to the backup)")
    print("SHA256 Image: %s" % sha(img))
    print("NOTE: The plan lists only the sectors; the write tool (fixed windows) must be checked separately.")
    return res.finish("plan")


# ---------------------------------------------------------------- restore-check
def cmd_restore(args):
    res = Result()
    a, b = read_file(args.backup), read_file(args.readback)
    res.info("Backup:   %s  %d bytes  sha256 %s" % (args.backup, len(a), sha(a)))
    res.info("Readback: %s  %d bytes  sha256 %s" % (args.readback, len(b), sha(b)))
    if len(a) != len(b):
        res.err("Sizes differ (%d vs %d)" % (len(a), len(b)))
        return res.finish("restore-check")
    allow = []
    for s in args.allow_diff or []:
        lo, hi = (num(x) for x in s.split(":"))
        allow.append((lo, hi))
    diffs = diff_ranges(a, b)
    if not diffs:
        res.ok("byte-for-byte equal, 0 differing bytes")
    else:
        for lo, hi in diffs:
            alo, ahi = FLASH_BASE + lo, FLASH_BASE + hi
            if any(x <= alo and ahi <= y for x, y in allow):
                res.warn("allowed difference %s..%s (%d bytes)" % (hx(alo), hx(ahi), hi - lo))
            else:
                res.err("Difference %s..%s (%d bytes)" % (hx(alo), hx(ahi), hi - lo))
        res.info("total %d differing bytes in %d ranges" % (sum(h - l for l, h in diffs), len(diffs)))
    return res.finish("restore-check")


def main(argv=None):
    p = argparse.ArgumentParser(description="Check script for flash images (only reads files).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("backup")
    s.add_argument("fileA")
    s.add_argument("fileB")
    s.set_defaults(fn=cmd_backup)
    for name, fn in (("image", cmd_image), ("plan", cmd_plan)):
        s = sub.add_parser(name)
        s.add_argument("image")
        s.add_argument("--backup", required=True)
        s.add_argument("--base", default="0x10000000", help="Flash address of byte 0 of the image (default 0x10000000)")
        s.set_defaults(fn=fn)
    s = sub.add_parser("restore-check")
    s.add_argument("backup")
    s.add_argument("readback")
    s.add_argument("--allow-diff", action="append", metavar="START:END",
                   help="Address range in which differences count as WARNING instead of ERROR (default: none)")
    s.set_defaults(fn=cmd_restore)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

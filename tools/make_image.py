#!/usr/bin/env python3
"""Apply the M1 delta patch to YOUR OWN dump of the B1 Pro FW 2.12 app region and write the flashable image.

Usage:
  make_image.py --backup <flash_1MiB_dump.bin> --patch patch/m1_tools6.patch.json --out m1_image.bin

The backup is a 1 MiB read starting at 0x10000000. The app region 0x10009000.. must match the sha256 recorded in the patch
(original FW 2.12), otherwise the tool stops. The output is an app image (base 0x10009000) for
`niimbot_flash_window.py plan <image> --backup <backup> --base 0x10009000`. Do not publish the output (contains firmware).
"""
import argparse
import hashlib
import json
import sys

FLASH_BASE = 0x10000000


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--patch", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    patch = json.load(open(a.patch, encoding="utf-8"))
    base = int(patch["base"], 16)
    ln = patch["original_length"]
    bak = open(a.backup, "rb").read()
    if len(bak) != 0x100000:
        sys.exit("STOP: backup must be exactly 1 MiB (read from 0x10000000), got %d bytes" % len(bak))
    img = bytearray(bak[base - FLASH_BASE:base - FLASH_BASE + ln])
    got = hashlib.sha256(img).hexdigest()
    if got != patch["original_sha256"]:
        sys.exit("STOP: app region of your backup is not the expected original FW 2.12\n  expected %s\n  got      %s"
                 % (patch["original_sha256"], got))
    for r in patch["records"]:
        off = int(r["addr"], 16) - base
        new = bytes.fromhex(r["new"])
        if off < 0 or off + len(new) > ln:
            sys.exit("STOP: record %s outside image" % r["addr"])
        img[off:off + len(new)] = new
    res = hashlib.sha256(img).hexdigest()
    if res != patch["result_sha256"]:
        sys.exit("STOP: result hash mismatch (%s)" % res)
    open(a.out, "wb").write(img)
    print("OK: %s written, %d bytes, sha256 %s" % (a.out, len(img), res))
    print("Next: python tools/niimbot_flash_window.py plan %s --backup %s --base 0x10009000" % (a.out, a.backup))


if __name__ == "__main__":
    main()

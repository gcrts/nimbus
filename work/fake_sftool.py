#!/usr/bin/env python3
"""Fake of sftool.exe 0.2.5 (test only). Works on a file instead of the device.

Accepts the same arguments the wrappers pass to sftool.exe:
  -c SF32LB52 -m nor -p COMx -b 1000000 --before no_reset --after no_reset --connect-attempts 60 --compat true
  read_flash <file>@<adr>:<len>   |   write_flash --verify <file>@<adr>
Environment:
  FAKE_FLASH         Path of the flash file (offset 0 = 0x10000000), default work/fake_flash.bin
  FAKE_SFTOOL_FAULT  e.g. write_fail@1, mismatch@1, abort@2  (number = n-th write_flash call)
Counter in <FAKE_FLASH>.state. Opens no port.
"""
import argparse
import os
import sys
import zlib

BASE = 0x10000000
HERE = os.path.dirname(os.path.abspath(__file__))
FLASH = os.environ.get("FAKE_FLASH", os.path.join(HERE, "fake_flash.bin"))


def counter():
    st = FLASH + ".state"
    n = int(open(st).read()) + 1 if os.path.exists(st) else 1
    open(st, "w").write(str(n))
    return n


def fault(n):
    f = os.environ.get("FAKE_SFTOOL_FAULT", "")
    for item in filter(None, f.split(",")):
        kind, _, k = item.partition("@")
        if int(k or 1) == n:
            return kind
    return None


def main():
    p = argparse.ArgumentParser()
    for o in ("-c", "-m", "-p", "-b", "--before", "--after", "--connect-attempts", "--compat"):
        p.add_argument(o, required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read_flash"); r.add_argument("spec")
    w = sub.add_parser("write_flash"); w.add_argument("--verify", action="store_true"); w.add_argument("spec")
    a = p.parse_args()
    assert a.c == "SF32LB52" and a.m == "nor" and a.p.startswith("COM"), "wrong base arguments"
    flash = bytearray(open(FLASH, "rb").read())
    print("fake: Enter OK, port %s, size:0x%x sector:0x1000 id:0x146085" % (a.p, len(flash)))
    if a.cmd == "read_flash":
        rf = os.environ.get("FAKE_SFTOOL_READ_FAULT", "")        # e.g. fail@1 (n-th read_flash call)
        if rf:
            rst = FLASH + ".rstate"
            rn = int(open(rst).read()) + 1 if os.path.exists(rst) else 1
            open(rst, "w").write(str(rn))
            kind, _, k = rf.partition("@")
            if kind == "fail" and int(k or 1) == rn:
                print("Error: Failed to execute read_flash command; protocol error: unexpected CRC line: fake")
                return 1
        path, _, rest = a.spec.rpartition("@")
        addr, ln = (int(x, 16) for x in rest.split(":"))
        off = addr - BASE
        open(path, "wb").write(bytes(flash[off:off + ln]))
        print("read_flash %s 0x%08x 0x%08x OK" % (path, addr, ln))
        return 0
    path, _, ad = a.spec.rpartition("@")
    addr = int(ad, 16)
    data = open(path, "rb").read()
    off, ln = addr - BASE, len(data)
    n = counter()
    fk = fault(n)
    crc = zlib.crc32(data)
    cur = zlib.crc32(bytes(flash[off:off + ln]))
    print("msh >burn_verify 0x%08x 0x%08x 0x%08x\r\nV: 0x%08x vs 0x%08x\r\n%s"
          % (addr, ln, crc, crc, cur, "OK" if cur == crc else "Fail"))
    if fk == "write_fail":
        print("msh >burn_erase_write 0x%08x 0x%08x\r\nERROR: write failed" % (addr, ln))
        return 3
    print("msh >burn_erase_write 0x%08x 0x%08x\r\nRX_WAIT" % (addr, ln))
    if fk == "abort":
        half = (ln // 2) & ~0xFFF
        flash[off:off + ln] = b"\xff" * ln
        flash[off:off + half] = data[:half]
        open(FLASH, "wb").write(flash)
        print("ERROR: connection lost")
        return 4
    flash[off:off + ln] = data
    if fk == "mismatch":
        flash[off + 0x10] ^= 0x01  # flash differs, the fake verify lies (for the readback check)
        open(FLASH, "wb").write(flash)
        print("msh >burn_verify 0x%08x 0x%08x 0x%08x\r\nOK" % (addr, ln, crc))
        return 0
    open(FLASH, "wb").write(flash)
    print("msh >burn_verify 0x%08x 0x%08x 0x%08x\r\nV: 0x%08x vs 0x%08x\r\nOK" % (addr, ln, crc, crc, crc))
    return 0


if __name__ == "__main__":
    sys.exit(main())

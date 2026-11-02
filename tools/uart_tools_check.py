#!/usr/bin/env python3
"""Reads the boot log (seconds --boot) and then sends read-only msh commands (help, recon_version, recon_uptime, recon_rfid, free)."""
import argparse, pathlib, sys, time, serial
p = argparse.ArgumentParser(); p.add_argument("--port", default="COM8"); p.add_argument("--boot", type=float, default=60)
p.add_argument("--out", default="uart_logs", help="Output folder (one subfolder per run)"); p.add_argument("--cmds", default="help,recon_version,recon_uptime,recon_rfid,free"); a = p.parse_args()
out = pathlib.Path(a.out) / ("tools-check-" + time.strftime("%Y%m%d-%H%M%S")); out.mkdir(parents=True)
s = serial.Serial(a.port, 1000000, timeout=0.2); raw = bytearray()
def pump(sec):
    end = time.time() + sec
    while time.time() < end: raw.extend(s.read(4096))
print("READY, reading boot ...", flush=True); pump(a.boot)
for c in a.cmds.split(","):
    s.write(c.encode() + b"\r\n"); pump(2.5)
(out / "log.txt").write_bytes(bytes(raw)); print(out); print(bytes(raw).decode("latin-1"))

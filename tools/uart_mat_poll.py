#!/usr/bin/env python3
"""Polls recon_mat every --every seconds (pass-through sketch, COM8) and prints timestamp + line, for --total seconds."""
import argparse, re, time, serial
p = argparse.ArgumentParser(); p.add_argument("--port", default="COM8"); p.add_argument("--every", type=float, default=10); p.add_argument("--total", type=float, default=360)
a = p.parse_args(); s = serial.Serial(a.port, 1000000, timeout=0.2); t0 = time.time(); print("READY", flush=True)
while time.time() - t0 < a.total:
    s.reset_input_buffer(); s.write(b"\r\nrecon_mat\r\n"); end = time.time() + a.every; buf = b""
    while time.time() < end: buf += s.read(4096)
    m = re.findall(rb"v\d+ n\d+ l\d+ p\d+ s[0-9a-f]+ o[0-9a-f]+", buf)
    print("%5.0fs" % (time.time() - t0), m[-1].decode() if m else ("no match (%d B)" % len(buf)), flush=True)

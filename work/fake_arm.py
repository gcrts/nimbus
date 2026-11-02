#!/usr/bin/env python3
"""Fake of scripts/printers/esp32_sftool_control.py --arm-bridge (test only). Opens no port.
Environment: FAKE_ARM_FAIL=1 -> exits without 'READY' (exit 1)."""
import argparse
import os
import pathlib
import sys
import time

p = argparse.ArgumentParser()
p.add_argument("--arm-bridge", action="store_true")
p.add_argument("--seconds", type=int, required=True)
p.add_argument("--output", required=True)
a = p.parse_args()
out = pathlib.Path(a.output)
out.mkdir(parents=True, exist_ok=False)
if os.environ.get("FAKE_ARM_FAIL"):
    print("could not open port 'COM8': PermissionError(13, 'Access is denied', None, 5)", flush=True)
    sys.exit(1)
(out / "started.txt").write_text(str(time.time()))
print('{"event": "info", "firmware": "fake"}', flush=True)
print("READY: bridge armed, waiting for printer Power.", flush=True)
end = time.time() + a.seconds
while time.time() < end:
    (out / "alive.txt").write_text(str(time.time()))
    time.sleep(0.2)

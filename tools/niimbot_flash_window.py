#!/usr/bin/env python3
"""niimbot_flash_window.py - tightly bounded window writer for NIIMBOT B1 Pro (SF32LB52x).

Standard library only + tools/flash_guard.py (import only). Flash base 0x10000000, backup = 1 MiB.
Only 0x10009000..0x10089000 may be written, at most 128 KiB per sftool run,
4 KiB aligned. sftool is invoked ONLY via --sftool or the environment variable SFTOOL_EXE,
and ONLY in the `execute` subcommand (with all mandatory flags and interactive input 'WRITE').

Subcommands:
  plan <image.bin> --backup <file> [--base ADR]   Plan from an image (only differing sectors)
  testbyte-plan --backup <file>                   Stage 1: write a test byte to 0x1007F000 and erase it again
  restore-plan <backup> --app-only                Restore plan for the app area 0x10009000..0x10089000
  execute <plan.json> --port COMx --yes-write <sha8>  [--sftool PFAD] [--control DIR]
  verify <plan.json> --readback <file> [--allow-diff A:B ...]

Exit: 0 OK, 1 check/execution error (STOP), 2 usage/file error.
"""
import argparse
import contextlib
import datetime
import hashlib
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORK = ROOT / "work"
sys.path.insert(0, str(HERE))
import flash_guard as fg  # noqa: E402  (import only, not modified)

FLASH_BASE = fg.FLASH_BASE
SECTOR = fg.SECTOR
WRITE_LO, WRITE_HI = 0x10009000, 0x10089000
MAX_WINDOW = 0x20000  # 128 KiB per run
BACKUP_SIZE = 0x100000
APP_END_ORIG = 0x1007E1D0
TESTBYTE_SECTOR = 0x1007F000
FORBIDDEN = [
    (0x10000000, 0x10009000, "table/bootloader"),
    (0x10007000, 0x10009000, "FAL partition ble"),
    (0x10089000, 0x100DA000, "update storage/unknown"),
    (0x100DA000, 0x10100000, "extra area (not cleared)"),
    (0x100FC000, 0x10100000, "crash log, block A, block B"),
]
SFTOOL_PREFIX = ["-c", "SF32LB52", "-m", "nor", "-p", None, "-b", "1000000",
                 "--before", "no_reset", "--after", "no_reset", "--connect-attempts", "60", "--compat", "true"]
FORBIDDEN_OPTIONS = ("--erase-all", "-e", "erase_flash", "erase_region", "--no-compress", "-u")
CONFIRM = "WRITE"
SFTOOL_TIMEOUT = 280
ARM_READY_TIMEOUT = 25      # s until 'READY' from the arm script
ARM_MIN_LEFT = 40           # s of arming time remaining; below this the bridge is re-armed
ARM_SCRIPT_DEFAULT = HERE / "esp32_sftool_control.py"   # not in the package; pass via --arm-script


class Stop(Exception):
    pass


class StdinClosed(Stop):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


def hx(a):
    return "0x%08X" % a


def rb(p):
    with open(p, "rb") as f:
        return f.read()


def check_window(addr, length):
    """Hard limit: raises Stop unless the window lies entirely inside the write range."""
    if addr % SECTOR or length % SECTOR or length <= 0:
        raise Stop("Window %s+0x%X not 4 KiB aligned" % (hx(addr), length))
    if length > MAX_WINDOW:
        raise Stop("Window %s longer than 128 KiB" % hx(addr))
    end = addr + length
    if addr < WRITE_LO or end > WRITE_HI:
        why = [n for lo, hi, n in FORBIDDEN if max(lo, addr) < min(hi, end)]
        raise Stop("Window %s..%s outside %s..%s (%s)" % (hx(addr), hx(end), hx(WRITE_LO), hx(WRITE_HI),
                                                          ", ".join(why) or "blocked"))


def group_windows(sectors):
    runs = []
    for s in sorted(sectors):
        if runs and runs[-1][1] == s and (runs[-1][1] - runs[-1][0]) * SECTOR < MAX_WINDOW:
            runs[-1][1] = s + 1
        else:
            runs.append([s, s + 1])
    return [(FLASH_BASE + a * SECTOR, (z - a) * SECTOR) for a, z in runs]


def plan_digest(plan):
    body = {k: v for k, v in plan.items() if k != "plan_sha256"}
    return sha(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())


def sftool_argv(sftool, port, tail):
    pre = [port if x is None else x for x in SFTOOL_PREFIX]
    cmd = ([sys.executable, str(sftool)] if str(sftool).lower().endswith(".py") else [str(sftool)]) + pre + tail
    if any(o in cmd for o in FORBIDDEN_OPTIONS):
        raise Stop("forbidden option in command")
    return cmd


def display_cmd(port, tail):
    return "<SFTOOL_EXE> " + " ".join(x if x is not None else port for x in SFTOOL_PREFIX) + " " + " ".join(tail)


def write_plan(kind, image_bytes, image_label, backup_path, bak, steps, note):
    """steps: list of (addr, data_bytes, before_bytes). Writes window files and the plan JSON."""
    WORK.mkdir(exist_ok=True)
    img_sha = sha(image_bytes)
    sha8 = img_sha[:8]
    wdir = WORK / ("flash_plan_%s_%s" % (kind, sha8))
    wdir.mkdir(exist_ok=True)
    end_state = bytearray(bak)
    windows = []
    for i, (addr, data, before) in enumerate(steps):
        check_window(addr, len(data))
        f = wdir / ("w%02d_%08X_%X.bin" % (i, addr, len(data)))
        f.write_bytes(data)
        off = addr - FLASH_BASE
        end_state[off:off + len(data)] = data
        windows.append({
            "index": i, "address": hx(addr), "length": hx(len(data)), "file": os.path.relpath(f, ROOT),
            "sha256": sha(data), "expected_readback_sha256": sha(data),
            "expected_before_sha256": sha(before),
            "before_equals_backup": before == bak[off:off + len(data)],
            "sftool_write": display_cmd("COMx", ["write_flash", "--verify", "%s@%s" % (f, hex(addr))]),
            "sftool_readback": display_cmd("COMx", ["read_flash", "<rb>@%s:%s" % (hex(addr), hex(len(data)))]),
        })
    plan = {
        "tool": "niimbot_flash_window.py", "kind": kind, "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "image": image_label, "image_sha256": img_sha, "image_sha8": sha8,
        "backup": os.path.abspath(backup_path), "backup_sha256": sha(bak), "backup_size": len(bak),
        "write_range": [hx(WRITE_LO), hx(WRITE_HI)], "max_window": hx(MAX_WINDOW),
        "windows": windows, "expected_end_sha256": sha(bytes(end_state)), "note": note,
    }
    plan["plan_sha256"] = plan_digest(plan)
    pf = WORK / ("flash_plan_%s.json" % sha8) if kind == "image" else WORK / ("flash_plan_%s_%s.json" % (kind, sha8))
    pf.write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
    print("PLAN: %s  (%s, %d windows, %d bytes)" % (os.path.relpath(pf, ROOT), kind, len(windows),
                                                   sum(len(s[1]) for s in steps)))
    for w in windows:
        print("  Window %d: %s length %s sha256 %s" % (w["index"], w["address"], w["length"], w["sha256"]))
        print("    " + w["sftool_write"])
    print("Image sha8 for --yes-write: %s" % sha8)
    print("plan_sha256: %s" % plan["plan_sha256"])
    print("Execute: python tools/niimbot_flash_window.py execute %s --port COM7 --yes-write %s"
          % (os.path.relpath(pf, ROOT), sha8))
    return pf


def load_backup(path):
    bak = rb(path)
    if len(bak) != BACKUP_SIZE:
        raise Stop("Backup %s has %d bytes, expected 1 MiB" % (path, len(bak)))
    return bak


# ------------------------------------------------------------------ plan
def cmd_plan(a):
    img = rb(a.image)
    base = a.base
    if base is None:
        meta = Path(a.image).with_suffix(".json")
        base = FLASH_BASE
        if meta.exists():
            with contextlib.suppress(Exception):
                base = int(json.loads(meta.read_text(encoding="utf-8")).get("load_address", "0x10000000"), 16)
        if len(img) == BACKUP_SIZE:
            base = FLASH_BASE
    else:
        base = int(base, 0)
    print("Image base: %s" % hx(base))
    bak = load_backup(a.backup)
    res = fg.Result()
    _, _, diffs = fg.check_image(res, a.image, a.backup, base)
    if res.errors or diffs is None:
        raise Stop("flash_guard check reports %d errors; no plan" % res.errors)
    secs = set()
    for lo, hi in diffs:
        secs.update(range((base + lo - FLASH_BASE) // SECTOR, (base + hi - 1 - FLASH_BASE) // SECTOR + 1))
    full = bytearray(bak)
    full[base - FLASH_BASE:base - FLASH_BASE + len(img)] = img
    steps = []
    for addr, ln in group_windows(secs):
        check_window(addr, ln)
        o = addr - FLASH_BASE
        steps.append((addr, bytes(full[o:o + ln]), bak[o:o + ln]))
    if not steps:
        print("Plan empty: image equals backup. Use testbyte-plan for stage 1.")
    write_plan("image", img, os.path.abspath(a.image), a.backup, bak, steps,
               "Only differing 4 KiB sectors, base %s" % hx(base))
    return 0


def cmd_testbyte(a):
    bak = load_backup(a.backup)
    o = TESTBYTE_SECTOR - FLASH_BASE
    orig = bak[o:o + SECTOR]
    if any(x != 0xFF for x in orig):
        raise Stop("Sector %s in backup not empty (0xFF); testbyte plan rejected" % hx(TESTBYTE_SECTOR))
    if TESTBYTE_SECTOR < APP_END_ORIG:
        raise Stop("Test sector overlaps the original app")
    test = bytearray(orig)
    test[0:16] = b"RECON-TESTBYTE\x00\x5A"
    test = bytes(test)
    steps = [(TESTBYTE_SECTOR, test, orig), (TESTBYTE_SECTOR, orig, test)]
    write_plan("testbyte", test + orig, "testbyte@%s" % hx(TESTBYTE_SECTOR), a.backup, bak, steps,
               "Stage 1: write a test pattern to an unused sector, read it back, then write 0xFF back")
    return 0


def restore_steps(bak):
    steps = []
    for addr in range(WRITE_LO, WRITE_HI, MAX_WINDOW):
        ln = min(MAX_WINDOW, WRITE_HI - addr)
        o = addr - FLASH_BASE
        steps.append((addr, bak[o:o + ln], bak[o:o + ln]))
    return steps


def cmd_restore_plan(a):
    if not a.app_only:
        raise Stop("restore-plan only with --app-only (blocks A/B are never restored)")
    bak = load_backup(a.backup)
    tail = bak[APP_END_ORIG - FLASH_BASE:WRITE_HI - FLASH_BASE]
    print("Backup 0x1007E1D0..0x10089000: %s" % ("only 0xFF" if all(x == 0xFF for x in tail)
                                                 else "NOT only 0xFF (backup content is adopted)"))
    img = bak[WRITE_LO - FLASH_BASE:WRITE_HI - FLASH_BASE]
    return write_plan("restore", img, "backup-app %s..%s" % (hx(WRITE_LO), hx(WRITE_HI)), a.backup, bak,
                      restore_steps(bak), "App area from backup, blocks A/B untouched") and 0


# ------------------------------------------------------------------ execute
class Runner:
    def __init__(self, sftool, port, rundir, log, arm=None, retry_reads=0):
        self.sftool, self.port, self.rundir, self.log, self.n = sftool, port, rundir, log, 0
        self.arm, self.retry_reads = arm, retry_reads      # arm: {"script": path, "seconds": n} or None
        self.arm_proc, self.arm_t0 = None, 0.0

    def say(self, msg):
        print(msg, flush=True)
        self.log.write(msg + "\n")
        self.log.flush()

    def arm_stop(self):
        """Stops the arm script started by the wrapper (frees COM8). Safe to call repeatedly."""
        p, self.arm_proc = self.arm_proc, None
        if p is None:
            return
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)

    def arm_start(self, what):
        """Starts esp32_sftool_control.py --arm-bridge and waits for 'READY'. Otherwise STOP (nothing is sent)."""
        self.arm_stop()
        d = self.rundir / ("arm_%02d" % (self.n + 1))
        cmd = [sys.executable, "-u", str(self.arm["script"]), "--arm-bridge", "--seconds", str(self.arm["seconds"]),
               "--output", str(d)]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        q = queue.Queue()

        def pump():
            for line in p.stdout:
                q.put(line.rstrip())
            q.put(None)
        threading.Thread(target=pump, daemon=True).start()
        lines, ready, t_end = [], False, time.monotonic() + ARM_READY_TIMEOUT
        while time.monotonic() < t_end:
            try:
                line = q.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                break
            lines.append(line)
            if line.startswith("READY"):
                ready = True
                break
        if not ready:
            self.arm_proc = p
            self.arm_stop()
            raise Stop("Bridge not armed (%s): %s" % (what, " | ".join(lines)[-300:] or "no output"))
        self.arm_proc, self.arm_t0 = p, time.monotonic()
        self.say("  Bridge armed (%s)" % d.name)

    def arm_left(self):
        return self.arm["seconds"] - (time.monotonic() - self.arm_t0)

    def _enter(self, what):
        if sys.stdin.readline() == "":
            self.arm_stop()
            raise StdinClosed("no confirmation (stdin closed) before: %s" % what)

    def prompt(self, what, secs):
        if not self.arm:
            self.say("READY? %s: re-arm the bridge (esp32_sftool_control.py --arm-bridge), Enter-ACK counter 0, "
                     "press the power button and hold it continuously for at least %d s. Enter = start." % (what, secs))
            self._enter(what)
            return
        # --auto-arm: Enter 1 = arm the bridge, Enter 2 = start sftool, then it switches off automatically
        self.say("READY? %s: printer unpowered? Enter = arm the bridge." % what)
        self._enter(what)
        while True:
            self.arm_start(what)
            self.say("  Bridge armed (still %d s). Press the power button and hold it continuously for at least %d s. "
                     "Enter = start sftool." % (self.arm_left(), secs))
            self._enter(what)
            if self.arm_left() >= ARM_MIN_LEFT:
                return
            self.say("  Arming almost expired (still %d s): re-arming." % max(0, self.arm_left()))

    def run(self, tail, what):
        self.n += 1
        cmd = sftool_argv(self.sftool, self.port, tail)
        if self.arm and (self.arm_proc is None or self.arm_proc.poll() is not None):
            raise Stop("%s: bridge no longer armed (arm script ended); sftool is not started" % what)
        self.say("SFTOOL %02d: %s" % (self.n, " ".join(cmd)))
        lf = self.rundir / ("sftool_%02d.log" % self.n)
        env = os.environ.copy()
        env["RUST_LOG"] = "sftool_lib=trace"
        try:
            with open(lf, "wb") as f:
                r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=SFTOOL_TIMEOUT, env=env)
        except subprocess.TimeoutExpired:
            raise Stop("%s: sftool aborted after %d s" % (what, SFTOOL_TIMEOUT))
        finally:
            self.arm_stop()          # free COM8 after EVERY sftool run (success or failure)
        text = lf.read_text(encoding="utf-8", errors="replace")
        self.say("  Exit %d, Log %s" % (r.returncode, lf.name))
        if r.returncode != 0:
            raise Stop("%s: sftool Exit %d" % (what, r.returncode))
        return text

    def read(self, addr, ln, tag):
        """Reading is harmless: with --retry-reads N a failed read step is retried up to N times.
        Write steps are never retried automatically."""
        tries = 1 + self.retry_reads
        for k in range(1, tries + 1):
            try:
                self.prompt("Read %s+0x%X" % (hx(addr), ln), 20)
                out = self.rundir / ("read_%s_%08X.bin" % (tag, addr))
                if out.exists():
                    out.unlink()
                self.run(["read_flash", "%s@%s:%s" % (out, hex(addr), hex(ln))], "Read %s" % hx(addr))
                data = rb(out) if out.exists() else b""
                if len(data) != ln:
                    raise Stop("Read %s: %d instead of %d bytes" % (hx(addr), len(data), ln))
                return data
            except StdinClosed:
                raise
            except Stop as e:
                if k >= tries:
                    raise
                self.say("  Read failed (%s); attempt %d/%d follows. Remove power from the printer." % (e, k + 1, tries))

    def write(self, addr, ln, path):
        self.prompt("Write %s+0x%X" % (hx(addr), ln), 40)
        text = self.run(["write_flash", "--verify", "%s@%s" % (path, hex(addr))], "Write %s" % hx(addr))
        tag = "0x%08x 0x%08x" % (addr, ln)
        ew = re.search(r"burn_erase_write " + tag, text)
        vs = list(re.finditer(r"burn_verify " + tag + r".*?(OK|Fail)", text, re.S))
        if not vs or vs[-1].group(1) != "OK":
            raise Stop("Write %s: no 'burn_verify ... OK' in the sftool log" % hx(addr))
        if not ew:
            self.say("  WARNING: no burn_erase_write in the log (sftool may not have written because the content "
                     "is identical; behavior on the device unverified). The readback check decides.")
        else:
            self.say("  Log: burn_erase_write and burn_verify OK")


def load_plan(path):
    raw = Path(path).read_text(encoding="utf-8")
    plan = json.loads(raw)
    if plan.get("plan_sha256") != plan_digest(plan):
        raise Stop("Plan file modified (plan_sha256 mismatch)")
    return plan


def check_control(ctrl):
    ev = Path(ctrl) / "events.jsonl"
    entries = [json.loads(s) for s in ev.read_text().splitlines() if s.strip()]
    arms = [x for x in entries if x.get("event", {}).get("event") == "armed" and x["event"].get("mode") == 2]
    if not arms:
        raise Stop("Bridge not armed (no armed event in %s)" % ev)
    age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(arms[-1]["utc"])).total_seconds()
    if not 0 <= age < 45:
        raise Stop("Arming too old (%.0f s)" % age)


def cmd_execute(a):
    sftool = a.sftool or os.environ.get("SFTOOL_EXE")
    if not sftool:
        raise Stop("no sftool path (--sftool or SFTOOL_EXE)")
    if not Path(sftool).is_file():
        raise Stop("sftool not found: %s" % sftool)
    if not re.fullmatch(r"COM\d{1,3}", a.port):
        raise Stop("Port must be COMn")
    plan = load_plan(a.plan)
    if not re.fullmatch(r"[0-9a-f]{8}", a.yes_write or "") or a.yes_write != plan["image_sha8"]:
        raise Stop("--yes-write %r does not match the image (%s)" % (a.yes_write, plan["image_sha8"]))
    bpath = plan["backup"]
    if not Path(bpath).is_file():
        raise Stop("Backup missing: %s" % bpath)
    bak = rb(bpath)
    if sha(bak) != plan["backup_sha256"] or len(bak) != BACKUP_SIZE:
        raise Stop("Backup hash differs from the plan")
    pin = (getattr(a, "expect_backup_sha", None) or "").lower()
    if pin and not sha(bak).startswith(pin):
        raise Stop("Backup hash %s does not match the expected %s" % (sha(bak)[:8], pin))
    if plan["kind"] == "image" and Path(plan["image"]).is_file():
        if sha(rb(plan["image"])) != plan["image_sha256"]:
            raise Stop("Image hash differs from the plan")
    wins = []
    for w in plan["windows"]:
        addr, ln = int(w["address"], 16), int(w["length"], 16)
        check_window(addr, ln)
        p = ROOT / w["file"]
        d = rb(p)
        if len(d) != ln or sha(d) != w["sha256"]:
            raise Stop("Window file %s modified" % w["file"])
        wins.append((w, addr, ln, p, d))
    if not wins:
        raise Stop("Plan contains no windows; nothing to write")
    if a.control:
        check_control(a.control)
    print("PLAN %s (%s): %d windows, image %s, backup %s" % (a.plan, plan["kind"], len(wins), plan["image_sha8"],
                                                           plan["backup_sha256"][:8]))
    print("WARNING: This WRITES flash. Every sftool stage: hold the power button >= 40 s (read >= 20 s).")
    print("To continue, type exactly '%s':" % CONFIRM, flush=True)
    if sys.stdin.readline().strip() != CONFIRM:
        raise Stop("no confirmation '%s'; nothing executed" % CONFIRM)
    WORK.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base_ts, k = ts, 1
    while (WORK / ("flash_run_%s" % ts)).exists():
        k += 1
        ts = "%s-%d" % (base_ts, k)
    rundir = WORK / ("flash_run_%s" % ts)
    rundir.mkdir()
    with open(WORK / ("flash_run_%s.log" % ts), "w", encoding="utf-8") as log:
        arm = None
        if getattr(a, "auto_arm", False):
            script = Path(a.arm_script) if getattr(a, "arm_script", None) else ARM_SCRIPT_DEFAULT
            if not script.is_file():
                raise Stop("Arm script not found: %s" % script)
            if not 30 <= a.arm_seconds <= 300:
                raise Stop("--arm-seconds must be 30..300")
            arm = {"script": script, "seconds": a.arm_seconds}
        retries = getattr(a, "retry_reads", 0)
        if not 0 <= retries <= 5:
            raise Stop("--retry-reads must be 0..5")
        r = Runner(sftool, a.port, rundir, log, arm=arm, retry_reads=retries)
        r.say("START %s plan=%s plan_sha256=%s kind=%s" % (ts, a.plan, plan["plan_sha256"], plan["kind"]))
        try:
            # 1) fresh read image of all target regions BEFORE writing
            regions = sorted({(addr, ln) for _, addr, ln, _, _ in wins})
            for addr, ln in regions:
                cur = r.read(addr, ln, "pre")
                ref = bak[addr - FLASH_BASE:addr - FLASH_BASE + ln]
                if cur != ref:
                    nd = sum(1 for x, y in zip(cur, ref) if x != y)
                    if plan["kind"] == "restore":
                        r.say("  INFO: %s differs from the backup (%d bytes) - restore intended" % (hx(addr), nd))
                    else:
                        raise Stop("Pre-read %s differs from the backup (%d bytes); abort before writing"
                                   % (hx(addr), nd))
                else:
                    r.say("  Pre-read %s equals backup" % hx(addr))
            # 2) write windows, readback check per window
            for w, addr, ln, p, d in wins:
                r.say("WINDOW %d %s+0x%X sha256 %s" % (w["index"], hx(addr), ln, w["sha256"][:16]))
                if w["index"] > 0 or plan["kind"] == "testbyte":
                    cur = r.read(addr, ln, "before%02d" % w["index"])
                    if sha(cur) != w["expected_before_sha256"] and plan["kind"] != "restore":
                        raise Stop("Window %d: state before writing unexpected" % w["index"])
                r.write(addr, ln, p)
                back = r.read(addr, ln, "after%02d" % w["index"])
                if sha(back) != w["expected_readback_sha256"]:
                    nd = sum(1 for x, y in zip(back, d) if x != y)
                    raise Stop("Window %d: readback check differs (%d bytes)" % (w["index"], nd))
                r.say("  Readback check OK")
            r.say("RESULT: OK - all %d windows written and read back. Next: full readback image + verify."
                  % len(wins))
            return 0
        except Stop as e:
            r.say("STOP: %s" % e)
            r.say("Do not repeat anything. Way back: python tools/niimbot_flash_window.py restore-plan \"%s\" --app-only"
                  % bpath)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rp = cmd_restore_plan(argparse.Namespace(backup=bpath, app_only=True))
            r.say(buf.getvalue().rstrip())
            return 1
        finally:
            r.arm_stop()


# ------------------------------------------------------------------ verify
def cmd_verify(a):
    plan = load_plan(a.plan)
    bak = rb(plan["backup"])
    if sha(bak) != plan["backup_sha256"]:
        raise Stop("Backup hash differs from the plan")
    exp = bytearray(bak)
    for w in plan["windows"]:
        addr = int(w["address"], 16)
        d = rb(ROOT / w["file"])
        if sha(d) != w["sha256"]:
            raise Stop("Window file %s modified" % w["file"])
        exp[addr - FLASH_BASE:addr - FLASH_BASE + len(d)] = d
    exp = bytes(exp)
    if sha(exp) != plan["expected_end_sha256"]:
        raise Stop("Final state does not match expected_end_sha256")
    got = rb(a.readback)
    print("Expected sha256 %s, readback image sha256 %s (%d bytes)" % (sha(exp), sha(got), len(got)))
    if len(got) != len(exp):
        print("ERROR: size %d instead of %d" % (len(got), len(exp)))
        return 1
    allow = [tuple(int(x, 0) for x in s.split(":")) for s in (a.allow_diff or [])]
    errs = 0
    for lo, hi in fg.diff_ranges(exp, got):
        alo, ahi = FLASH_BASE + lo, FLASH_BASE + hi
        if any(x <= alo and ahi <= y for x, y in allow):
            print("WARNING: allowed difference %s..%s (%d bytes)" % (hx(alo), hx(ahi), hi - lo))
        else:
            errs += 1
            print("ERROR: difference %s..%s (%d bytes)" % (hx(alo), hx(ahi), hi - lo))
    print("RESULT: %s" % ("OK (0 differences outside allowed ranges)" if not errs else "ERROR (%d ranges)" % errs))
    return 1 if errs else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan"); s.add_argument("image"); s.add_argument("--backup", required=True)
    s.add_argument("--base", default=None); s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("testbyte-plan"); s.add_argument("--backup", required=True); s.set_defaults(fn=cmd_testbyte)
    s = sub.add_parser("restore-plan"); s.add_argument("backup"); s.add_argument("--app-only", action="store_true")
    s.set_defaults(fn=cmd_restore_plan)
    s = sub.add_parser("execute"); s.add_argument("plan"); s.add_argument("--port", required=True)
    s.add_argument("--yes-write", required=True); s.add_argument("--sftool"); s.add_argument("--control")
    s.add_argument("--expect-backup-sha", default=None, help="Prefix of the expected backup sha256 (e.g. a1b2c3d4)")
    s.add_argument("--auto-arm", action="store_true",
                   help="Wrapper itself starts esp32_sftool_control.py --arm-bridge before every sftool step and stops it afterwards")
    s.add_argument("--arm-script", default=None, help="Path of the arm script (required for --auto-arm; script is not in the package)")
    s.add_argument("--arm-seconds", type=int, default=150, help="Arming duration per step in s (30..300, default 150)")
    s.add_argument("--retry-reads", type=int, default=0, help="Retry failed read steps up to N times (0..5); never writes")
    s.set_defaults(fn=cmd_execute)
    s = sub.add_parser("verify"); s.add_argument("plan"); s.add_argument("--readback", required=True)
    s.add_argument("--allow-diff", action="append"); s.set_defaults(fn=cmd_verify)
    a = p.parse_args(argv)
    try:
        return a.fn(a) or 0
    except Stop as e:
        print("STOP: %s" % e, flush=True)
        return 1
    except OSError as e:
        print("ERROR: file: %s" % e, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())

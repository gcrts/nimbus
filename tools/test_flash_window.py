#!/usr/bin/env python3
"""Offline test for niimbot_flash_window.py against the fake work/fake_sftool.py.

No device, no real sftool, no port. Backup and image are synthetic (pseudo-random, no firmware)
and are generated under work/. Log: work/flash_window_test.log
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)
TOOL = ROOT / "tools" / "niimbot_flash_window.py"
FAKE = WORK / "fake_sftool.py"
FLASH = WORK / "fake_flash.bin"
BACKUP = WORK / "synthetic_backup.bin"
IMAGE = WORK / "synthetic_image.bin"


def _make_synthetic():
    """1 MiB pseudo-random as 'backup'; 'image' = backup with changes in two sectors (0x10042000, 0x10077000)."""
    import random
    rnd = random.Random(12345)
    bak = bytearray(rnd.getrandbits(8) for _ in range(0x100000))
    bak[0x7F000:0x80000] = bytes([0xFF]) * 0x1000      # empty test sector 0x1007F000 (as in a real backup)
    img = bytearray(bak)
    for off in (0x42000, 0x77000):
        for i in range(16):
            img[off + 0x100 + i] ^= 0xA5
    BACKUP.write_bytes(bytes(bak))
    IMAGE.write_bytes(bytes(img))
    return hashlib.sha256(bytes(bak)).hexdigest()


BACKUP_SHA = _make_synthetic()
T = WORK / "fw_test"
LOG = open(WORK / "flash_window_test.log", "w", encoding="utf-8")
results = []


def log(s):
    print(s)
    LOG.write(s + "\n")
    LOG.flush()


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def state():
    st = Path(str(FLASH) + ".state")
    return sha(FLASH), (st.read_text() if st.exists() else "-")


def reset(mod=None):
    shutil.copyfile(BACKUP, FLASH)
    for suffix in (".state", ".rstate"):
        st = Path(str(FLASH) + suffix)
        if st.exists():
            st.unlink()
    if mod:
        b = bytearray(FLASH.read_bytes())
        for addr, val in mod:
            b[addr - 0x10000000] = val
        FLASH.write_bytes(b)


def run(args, stdin="", fault="", extra_env=None):
    env = os.environ.copy()
    env.update({"SFTOOL_EXE": str(FAKE), "FAKE_FLASH": str(FLASH), "FAKE_SFTOOL_FAULT": fault})
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, "-I", str(TOOL)] + args, input=stdin, capture_output=True, text=True,
                       env=env, cwd=ROOT, timeout=600)
    LOG.write("$ %s  (fault=%s)\n%s%s-> exit %d\n" % (" ".join(args), fault or "-", r.stdout, r.stderr, r.returncode))
    return r


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    log("%s  %s %s" % ("PASS" if cond else "FAIL", name, detail))


def plan_of(out):
    for line in out.splitlines():
        if line.startswith("PLAN: "):
            return line.split()[1]


def yes_of(plan):
    return json.loads((ROOT / plan).read_text(encoding="utf-8"))["image_sha8"]


OKIN = "WRITE\n" + "\n" * 60


def main():
    T.mkdir(exist_ok=True)
    assert sha(BACKUP) == BACKUP_SHA, "unexpected backup hash"
    log("Backup %s (synthetic) sha256 %s" % (BACKUP.name, BACKUP_SHA))

    # (a) plan for the synthetic image, execution, verify
    reset()
    r = run(["plan", str(IMAGE), "--backup", str(BACKUP)])
    pa = plan_of(r.stdout)
    plan = json.loads((ROOT / pa).read_text(encoding="utf-8")) if pa else {}
    wins = [(w["address"], w["length"]) for w in plan.get("windows", [])]
    check("a1 plan synthetic image exit 0", r.returncode == 0)
    check("a2 windows = 0x10042000 + 0x10077000, 4 KiB each (in-app image)",
          wins == [("0x10042000", "0x00001000"), ("0x10077000", "0x00001000")], str(wins))
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)], OKIN)
    check("a3 execute against fake exit 0", r.returncode == 0, r.stdout.strip().splitlines()[-1])
    shutil.copyfile(FLASH, T / "readback_a.bin")
    r = run(["verify", pa, "--readback", str(T / "readback_a.bin")])
    check("a4 verify OK", r.returncode == 0)
    r = run(["verify", pa, "--readback", str(BACKUP)])
    check("a5 verify reports difference for unchanged backup", r.returncode == 1)
    # Stage 1: write and erase the test byte
    reset()
    r = run(["testbyte-plan", "--backup", str(BACKUP)])
    pt = plan_of(r.stdout)
    r = run(["execute", pt, "--port", "COM7", "--yes-write", yes_of(pt)], OKIN)
    check("a6 testbyte-plan executed, exit 0", r.returncode == 0)
    check("a7 after testbyte plan fake == backup", sha(FLASH) == BACKUP_SHA)
    shutil.copyfile(FLASH, T / "readback_t.bin")
    check("a8 verify testbyte OK", run(["verify", pt, "--readback", str(T / "readback_t.bin")]).returncode == 0)

    # (b) change in a forbidden area
    bk = bytearray(BACKUP.read_bytes())
    for addr, name in ((0x10008010, "ble"), (0x10004000, "Bootloader"), (0x100FF100, "Block B"),
                       (0x100E0000, "0x100DA000.."), (0x10090000, "update storage")):
        img = bytearray(bk)
        img[addr - 0x10000000] ^= 0xFF
        p = T / ("bad_%08X.bin" % addr)
        p.write_bytes(img)
        r = run(["plan", str(p), "--backup", str(BACKUP)])
        check("b  plan rejects change %s (%s)" % (hex(addr), name),
              r.returncode == 1 and "STOP" in r.stdout and plan_of(r.stdout) is None)

    # (c) wrong preconditions -> fake untouched
    reset()
    before = state()
    r = run(["execute", pa, "--port", "COM7", "--yes-write", "deadbeef"], OKIN)
    check("c1 wrong --yes-write aborts, fake untouched", r.returncode == 1 and state() == before)
    tp = T / "plan_tampered.json"
    pj = json.loads((ROOT / pa).read_text(encoding="utf-8"))
    pj["windows"][0]["address"] = "0x10078000"
    tp.write_text(json.dumps(pj, indent=1), encoding="utf-8")
    r = run(["execute", str(tp), "--port", "COM7", "--yes-write", yes_of(pa)], OKIN)
    check("c2 modified plan file aborts, fake untouched",
          r.returncode == 1 and "modified" in r.stdout and state() == before)
    bcopy = T / "backup_copy.bin"
    shutil.copyfile(BACKUP, bcopy)
    r = run(["plan", str(IMAGE), "--backup", str(bcopy)])
    pc = plan_of(r.stdout)
    b2 = bytearray(bcopy.read_bytes()); b2[0x50000] ^= 1; bcopy.write_bytes(b2)
    r = run(["execute", pc, "--port", "COM7", "--yes-write", yes_of(pc)], OKIN)
    check("c3 wrong backup aborts, fake untouched",
          r.returncode == 1 and "Backup hash" in r.stdout and state() == before)
    run(["plan", str(IMAGE), "--backup", str(BACKUP)])  # plan again with the real backup (same file name)
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa), "--sftool", str(T / "missing.exe")], OKIN)
    check("c4 missing sftool path aborts", r.returncode == 1 and state() == before)
    # pre-read differs from backup (device not in backup state)
    reset([(0x10077100, 0x00)])
    before2 = state()
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)], OKIN)
    check("c5 pre-read != backup: abort without writing", r.returncode == 1 and "Pre-read" in r.stdout
          and sha(FLASH) == before2[0] and not Path(str(FLASH) + ".state").exists())

    # (d) readback difference after window 1 -> STOP + restore-plan; restore recreates the backup
    for fault in ("mismatch@1", "write_fail@1", "abort@2"):
        reset([(0x100FF300, 0x11), (0x100FE400, 0x22)])  # blocks A/B changed by operation
        r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)], OKIN, fault)
        check("d  %s: STOP with restore-plan" % fault,
              r.returncode == 1 and "STOP" in r.stdout and "restore-plan" in r.stdout and plan_of(r.stdout))
        pr = plan_of(r.stdout)
        r = run(["execute", pr, "--port", "COM7", "--yes-write", yes_of(pr)], OKIN)
        check("d  %s: restore-plan on fake exit 0" % fault, r.returncode == 1 - 1)
        a, b = FLASH.read_bytes(), BACKUP.read_bytes()
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        outside = [i for i in diff if not (0xFC000 <= i < 0x100000)]
        check("d  %s: fake == backup except blocks A/B" % fault, not outside and len(diff) == 2,
              "differing %d (outside A/B: %d)" % (len(diff), len(outside)))
        shutil.copyfile(FLASH, T / "readback_r.bin")
        r = run(["verify", pr, "--readback", str(T / "readback_r.bin"), "--allow-diff", "0x100FC000:0x10100000"])
        check("d  %s: verify restore (A/B allowed) OK" % fault, r.returncode == 0)

    # (e) without interactive confirmation
    reset()
    before = state()
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)], "")
    check("e1 no input: abort, fake untouched", r.returncode == 1 and state() == before)
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)], "yes\n" + "\n" * 10)
    check("e2 wrong input 'yes': abort, fake untouched", r.returncode == 1 and state() == before)
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)], "WRITE\n\n")
    check("e3 confirmation, but stdin ends before a step: abort before writing",
          r.returncode == 1 and sha(FLASH) == before[0])
    env_less = os.environ.copy(); env_less.pop("SFTOOL_EXE", None)
    rr = subprocess.run([sys.executable, "-I", str(TOOL), "execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)],
                        input=OKIN, capture_output=True, text=True, env=env_less, cwd=ROOT)
    check("e4 without SFTOOL_EXE/--sftool: abort", rr.returncode == 1 and "no sftool path" in rr.stdout)

    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa), "--expect-backup-sha", "00000000"], OKIN)
    check("e5 wrong --expect-backup-sha: abort before writing, fake untouched",
          r.returncode == 1 and "does not match the expected" in r.stdout and state() == before)

    # (f) --auto-arm and --retry-reads (fakes: work/fake_arm.py, work/fake_sftool.py)
    def arm_procs():
        q = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                            "Where-Object { $_.CommandLine -match 'fake_arm' }).Count"],
                           capture_output=True, text=True)
        return int((q.stdout.strip() or "0"))
    arm_args = ["--auto-arm", "--arm-script", str(WORK / "fake_arm.py"), "--arm-seconds", "60"]
    reset()
    OKIN2 = "WRITE" + chr(10) + chr(10) * 120      # two Enters per step
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)] + arm_args, OKIN2)
    last = sorted(WORK.glob("flash_run_*/"))[-1] if list(WORK.glob("flash_run_*/")) else None
    arms = len(list(last.glob("arm_*"))) if last else -1
    check("f1 --auto-arm: run exit 0, bridge armed automatically per step (7 steps)",
          r.returncode == 0 and r.stdout.count("Bridge armed (still") == 7 and arms == 7,
          "arm_dirs=%d" % arms)
    check("f2 --auto-arm: no arm script running after the run", arm_procs() == 0)
    reset()
    before = state()
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)] + arm_args, OKIN2, extra_env={"FAKE_ARM_FAIL": "1"})
    check("f3 --auto-arm, arm script fails: STOP 'Bridge not armed', fake untouched",
          r.returncode == 1 and "Bridge not armed" in r.stdout and state() == before)
    reset()
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa)], OKIN, extra_env={"FAKE_SFTOOL_READ_FAULT": "fail@1"})
    check("f4 read error without --retry-reads: STOP as before", r.returncode == 1 and "STOP" in r.stdout)
    reset()
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa), "--retry-reads", "1"], OKIN,
            extra_env={"FAKE_SFTOOL_READ_FAULT": "fail@1"})
    check("f5 --retry-reads 1: first read step fails, retry succeeds, run exit 0",
          r.returncode == 0 and "attempt 2/2" in r.stdout, r.stdout.strip().splitlines()[-1])
    reset()
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa), "--retry-reads", "1"], OKIN, "write_fail@1",
            extra_env={"FAKE_SFTOOL_READ_FAULT": "fail@1"})
    check("f6 write error is not retried even with --retry-reads (STOP)",
          r.returncode == 1 and "STOP: Write" in r.stdout)
    r = run(["execute", pa, "--port", "COM7", "--yes-write", yes_of(pa), "--retry-reads", "9"], OKIN)
    check("f7 --retry-reads 9 outside 0..5: STOP", r.returncode == 1 and "retry-reads" in r.stdout)

    n_ok = sum(1 for _, ok in results if ok)
    log("TOTAL: %d/%d PASS" % (n_ok, len(results)))
    reset()
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

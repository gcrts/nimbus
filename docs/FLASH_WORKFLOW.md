# Flash procedure (window writer `tools/niimbot_flash_window.py`)

Derived from the records of 2026-11-02. No device or personal data.
Everything here applies to a NIIMBOT B1 Pro (SF32LB52, FW 2.12) with an ESP32-S3 bridge and `sftool` 0.2.5.
**Own device, own risk.** The ESP32 sketch is in `bridge/`, the arm script is `tools/esp32_sftool_control.py`.

## What the wrapper enforces

- Writes only to `0x10009000..0x10089000`, 4 KiB aligned, at most 128 KiB per sftool run.
- Locked: `0x10000000..0x10009000` (table/bootloader), `0x10007000..0x10009000`, `0x10089000..0x100DA000` (update storage),
  `0x100DA000..0x10100000` (extra area), `0x100FC000..` (crash log, block A/B). `flash_guard.check_image` checks additionally.
- sftool only via `--sftool` or `SFTOOL_EXE`, only in the `execute` subcommand; never `--erase-all`.
  `plan`, `testbyte-plan`, `restore-plan`, `verify` only read files and write only under `work/`.
- `execute` checks beforehand: plan unchanged (plan_sha256), window file hashes, backup present and same hash,
  image hash, `--yes-write <sha8>` equals the image, port `COMn`, then the typed confirmation `WRITE`.
- `--expect-backup-sha <prefix>` checks the prior state against the backup.
- Not included: checking sftool against a release archive and the port identity. Check these by hand.

## Procedure

1. **Full backup**: read 1 MiB from `0x10000000` twice, `flash_guard.py backup A B` (same sha256, not empty).
   Set write protection. Keep the boot log and shell output as the reference.
2. **Stage 1, test byte**: `testbyte-plan --backup <backup>` creates two windows at the same free sector `0x1007F000`
   (write, write back `0xFF`). Proves only the write chain. Success: both windows `burn_verify ... OK`.
3. **Build the image from your own backup** (or use the ready-made `image/m1_tools6_app_0x10009000.bin`, same sha256):
   `python tools/make_image.py --backup <backup> --patch patch/m1_tools6.patch.json --out m1_image.bin`.
   It stops if the app region of your dump does not match the expected original sha256.
4. **Check the image**: `flash_guard.py image <image> --backup <backup> --base 0x10009000`.
5. **Plan**: `niimbot_flash_window.py plan <image> --backup <backup> --base 0x10009000` (only differing sectors, 4 KiB each). Note the plan file and `sha8`.
6. **Execute**:
   `python -I tools/niimbot_flash_window.py execute work/flash_plan_<sha8>.json --port COM7 --yes-write <sha8> --expect-backup-sha <prefix> --auto-arm --arm-script <path/to/your_bridge_script.py> --retry-reads 2`
   Type `WRITE` when asked.
7. **Afterwards**: full readback image and `verify <plan> --readback <full.bin>` (not done on 11-02, recommended), power on, compare boot log/shell with the reference.

## Per step (manual work)

Before every sftool run: Enter 1 = arm the bridge (with `--auto-arm` the wrapper starts the arm script itself and ends it afterwards),
press and hold the printer's power button (>= 40 s when writing, >= 20 s when reading), Enter 2 = start sftool.

## Switches

| Switch | Effect |
|---|---|
| `--auto-arm` | Before each step the wrapper starts `esp32_sftool_control.py --arm-bridge`, waits for `READY`, ends it after each run |
| `--arm-seconds N` | Arming duration 30..300 s (default 150) |
| `--arm-script PATH` | Path of the arm script (default `tools/esp32_sftool_control.py`) |
| `--retry-reads N` | Retry failed **read** steps up to N times (0..5); writes are never retried automatically |
| `--expect-backup-sha` | Check the prior state against the backup hash |

## Connection problems (experience)

The chain is unreliable at **connecting**, not at writing: in the runs of 11-02, 15 of 32 sftool calls failed,
all before any write (CRC errors, stub abort after handshake, read timeouts). Cause unclear, hypothesis: power button/contact.

**Hang rule:** if the printer stays dark after failed handshakes (even without a cable), **remove the battery**.
As long as the bridge is active, the printer stays dark (normal).

## STOP and way back

On readback mismatch, write error or abort, the wrapper prints STOP and names a restore plan.
`restore-plan <backup> --app-only` restores `0x10009000..0x10089000` (4 windows of 128 KiB) from the backup, block A/B stay;
then `verify ... --allow-diff 0x100FC000:0x10100000`. Tested with the dummy; **the complete way back was never tried on the device.**

## Open

- Behavior of sftool with identical content (skip or erase+write) not tested.
- Whether the device stays in ROM between sftool calls: not proven (hence arm anew for each step).
- `--auto-arm` and `--retry-reads` are tested in this package only against dummies (cases f1 to f7); the real-run evidence is in the author's private log.

## Tests

`python tools/test_flash_window.py`: 42 cases against the dummies `work/fake_sftool.py` and `work/fake_arm.py` with a
**synthetic** 1 MiB backup (pseudo-random) and a synthetic image. No device, no firmware.

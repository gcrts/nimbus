# NIIMBOT B1 Pro, FW 2.12: Milestone M1 ("Path D")

Own experiments on my own device: the **original app** of the firmware stays, plus a small **mod** (`recon_tools`)
with two read-only shell commands and **three one-byte patches** that allow a print start when the roll's RFID tag cannot be read.
This package contains a delta patch, tools, documentation and - at the maintainer's decision - the patched app image `image/m1_tools6_app_0x10009000.bin`, which **contains the manufacturer's firmware**.

> **Disclaimer.** Own device, own risk. Not affiliated with the manufacturer (NIIMBOT) and not endorsed by it.
> Flashing can brick the device and void the warranty. The patched image in `image/` contains the manufacturer's firmware; redistribution is at your own risk.
> Prefer building the image from your own backup with the patch; The mod bypasses the roll/consumption lock **only on the owner's own device**
> and is not meant to circumvent third-party rights or license terms. **No warranty, no liability.**

## What was achieved (as of 2026-11-02, tested on the device)

| Stage | Content |
|---|---|
| 1 | Test byte in a free sector: write chain proven |
| 2 | Mod with `recon_version` |
| 3 | + `recon_uptime` (tick rate about 1000/s) |
| 4 | + `recon_rfid` (hex dump of the material object) |
| 5 | + `recon_dens`, then `recon_mat` (material state) |
| 6 | + three one-byte patches (below) |

The final state has `recon_rfid` and `recon_mat`; `recon_version`/`recon_uptime`/`recon_dens` were dropped for space reasons.

**Commands (read-only):** `recon_rfid` prints 0x84 bytes of the material object in RAM as hex. `recon_mat` prints one line
`v n l p s o` (valid byte, consumption counter, limit, pending increment, status mask, density override).

**One-byte patches** (details in [mod/MOD.md](mod/MOD.md) and [docs/FINDINGS.md](docs/FINDINGS.md)):

| Address | old -> new | Purpose |
|---|---|---|
| `0x10024538` | `0xFD` -> `0x00` | Density override `0xFD` (empty raster masks = empty label) is not set |
| `0x10014ed6` | `0xFD` -> `0x00` | second location of the same condition |
| `0x100346aa` | `0x14` -> `0x00` | Status mask bit 19 yields code 0 instead of `0x14` (print start was rejected) |

Effect: printing works even without a tag being read. Temperature, strobe, lid and paper checks are unchanged.
Test print with `tools/niimbot_b1pro_print.py --pattern bars` on my own device: clean.

## What is in the package (and what is not)

You can either flash the ready-made image in `image/` or - preferred - build it from your own backup:

- `patch/m1_tools6.patch.json` is a **delta**: 17 records, 248 bytes in total, only the new bytes plus their addresses. It is verified
  against the sha256 of the original app region of **your own** dump.
- `tools/make_image.py` applies the delta to your own backup and produces the flashable app image.

The patch applies only to the exact original (FW 2.12 app region, sha256
`3fef8421bf72366a82e81efc1c1be51c8cf9ecacc1c8297a1e9cb942e6df5a98`); otherwise `make_image.py` stops.
The resulting image (base `0x10009000`, 479696 bytes) has sha256
`859bb64322b4dc94bf0c7ecf299249fcb954b824f4da503bedeb2f344b458811`. The ready-made copy is `image/m1_tools6_app_0x10009000.bin` (same hash; it contains manufacturer firmware, see `image/README.md`).

## Prerequisites

- NIIMBOT B1 Pro with FW 2.12 (SF32LB52) and **your own full backup** (1 MiB from `0x10000000`, **read twice, same sha256**).
- ESP32-S3 as write bridge: sketch `bridge/esp32-sftool-bridge-v5.ino` (see `bridge/README.md`), arm script `tools/esp32_sftool_control.py` (used by `--auto-arm`).
- `sftool` 0.2.5 (path via `--sftool` or `SFTOOL_EXE`), Python 3, `pyserial` for the UART/print scripts.

## Reproduce it

1. **Read your own full backup twice** (1 MiB from `0x10000000`) with `sftool read_flash`; both files must have the same sha256.
2. **Check the backup:** `python tools/flash_guard.py backup A.bin B.bin` (same sha256, not empty). Write-protect the files.
3. **Build the image:** `python tools/make_image.py --backup <own 1 MiB dump> --patch patch/m1_tools6.patch.json --out m1_image.bin`
4. **Check the image:** `python tools/flash_guard.py image m1_image.bin --backup <own dump> --base 0x10009000`
5. **Plan:** `python tools/niimbot_flash_window.py plan m1_image.bin --backup <own dump> --base 0x10009000`. Note the plan file and its `sha8`.
6. **Execute:** `python -I tools/niimbot_flash_window.py execute work/flash_plan_<sha8>.json --port COM7 --yes-write <sha8> --expect-backup-sha <prefix of own backup sha256> --auto-arm --arm-script <your bridge script> --retry-reads 2`
   and type the confirmation `WRITE` when asked.
7. **Verify** with a full readback: `python tools/niimbot_flash_window.py verify <plan> --readback <full.bin>`.
8. **Check on the device:** run `recon_mat` and `recon_rfid` over UART (1 Mbaud), then a test print:
   `python tools/niimbot_b1pro_print.py --pattern bars --rows 120 --density 3 --allow-no-tag`

Details of the flash procedure: [docs/FLASH_WORKFLOW.md](docs/FLASH_WORKFLOW.md).

## Safety rules

- Only `0x10009000..0x10089000` is written, in 4 KiB sectors, at most 128 KiB per run. Bootloader, partition table,
  update storage and the areas from `0x100DA000` are hard-locked (`flash_guard.py`).
- Beforehand: create a plan, check the backup hash (`--expect-backup-sha`), typed confirmation `WRITE`.
- Writes are never retried automatically. Reads may be retried with `--retry-reads`.
- Hang rule: if the printer stays dark after failed handshakes, **remove the battery**.

## Way back

From your own backup: `restore-plan <backup> --app-only` creates a plan that restores the app region; then `execute`
and `verify`. Back to an earlier stage: `plan --backup <device state> --base 0x10009000 <earlier image>`, then
`execute ... --auto-arm --retry-reads 2 --expect-backup-sha <prefix>`.
**The complete way back via `restore-plan --app-only` was never tried on the device** (only tested with a dummy).

## Contents

- `docs/FLASH_WORKFLOW.md`, `docs/FINDINGS.md`, `mod/MOD.md`
- `patch/m1_tools6.patch.json` delta patch (no original bytes); `image/` patched app image (contains firmware)
- `tools/` flash wrapper, `flash_guard`, `make_image`, tests, UART and print scripts; `work/` dummies for the tests

Tests: `python tools/test_flash_window.py` (synthetic backup, no firmware, expects 42/42).


## License

The tools, scripts, sketch, patch description and documentation are under the MIT License (see `LICENSE`).
The MIT License does **not** cover `image/m1_tools6_app_0x10009000.bin`: it contains the manufacturer's firmware, which remains
the property of its owner.

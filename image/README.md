# Flashable app image (M1, stage 6)

`m1_tools6_app_0x10009000.bin` - NIIMBOT B1 Pro, FW 2.12 app region, base address `0x10009000`, 479696 bytes,
sha256 `859bb64322b4dc94bf0c7ecf299249fcb954b824f4da503bedeb2f344b458811`.

It is the original FW 2.12 app with the `recon_tools` mod and the three 1-byte patches applied (248 bytes differ from the original).
**It contains the manufacturer's firmware.** Redistribution is at the publisher's own risk; the maintainer decided to include it.
Not affiliated with the manufacturer; no warranty; own device, own risk.

Prefer building it yourself from your own backup (no firmware needed from here):
`python tools/make_image.py --backup <your 1 MiB dump> --patch patch/m1_tools6.patch.json --out my.bin` and compare the sha256.
Flash only with the guarded wrapper (`tools/niimbot_flash_window.py plan ... --base 0x10009000`) after a verified full backup.

License note: the repository's MIT License does not apply to this file; the firmware it contains belongs to its rights holder.

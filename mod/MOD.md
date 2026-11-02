# Mod `recon_tools` (description, no source code and no firmware bytes)

The mod is placed into function bodies of the original firmware that the user does not need (`list_mempool`, `list_event`),
and adds two entries to the shell command table (152 B code, 142 B data). A patched image is in `image/` and a delta patch in `patch/`;
only the effect is described. The package ships only the delta patch `patch/m1_tools6.patch.json` (17 records, 248 bytes), which
`tools/make_image.py` applies to your own backup of the original app region.

## Shell commands (read-only)

| Command | Effect |
|---|---|
| `recon_rfid` | prints 0x84 bytes from RAM address `0x2001C520` (material object of the roll) as hex |
| `recon_mat` | one line `v.. n.. l.. p.. s.. o..`: valid byte (`0x2001C529`), numOfPrint (`0x2001C52E`), limitNum (`0x2001C56E`), pending increment (`0x20010162`), status mask 4 bytes (`0x2000F575`), density override (`0x2001C6F8`) |

Output goes through the firmware's console printf function (UART, 1 Mbaud).

## One-byte patches (the only changes to firmware data besides the mod code)

| Address | old | new | Effect |
|---|---|---|---|
| `0x10024538` | `0xFD` | `0x00` | Material policy: override `0xFD` is no longer set for invalid material, consumption over the limit or series 5; return value unchanged |
| `0x10014ed6` | `0xFD` | `0x00` | second location of the same condition |
| `0x100346aa` | `0x14` | `0x00` | Status mapping: mask `0x80000` (bit 19) yields code 0 instead of `0x14`; code `0x14` rejected the print start |

Note: the mod otherwise has no effect on print parameters. It does **not** disable safety checks for head temperature, strobe,
lid or paper.

# Findings (B1 Pro, FW 2.12)

Proven on my own device unless marked as a hypothesis. No firmware bytes in this text; addresses are given for orientation.

## Roll material object (RAM `0x2001C520`, 0x84 bytes)

The object is read from the roll's RFID tag. Field offsets (relative to the start of the object):

| Offset | Type | Field |
|---|---|---|
| `+0x01` | Bytes | Tag UID (not documented, person/roll specific) |
| `+0x09` | u8 | Material valid (RAM state) |
| `+0x0E` | u16 | numOfPrint (consumption counter) |
| `+0x34` | String | labelBarCode (barcode of the roll) |
| `+0x4E` | u16 | limitNum (limit for numOfPrint) |
| `+0x50` | u8 | paperType |
| `+0x52` | String | Color name |
| `+0x67` | u8 | paperHeight (unit probably mm, hypothesis) |
| `+0x78` | u8 | series (value 5 = locked state) |

Real UID, barcode and serial numbers are deliberately not listed. Open: meaning of many further bytes.

## Print lock `0xFD`

For invalid material, consumption over the limit or series 5, the material policy sets the density override to `0xFD`.
This clears the raster sub-masks: the print head gets empty blocks and an empty label results. The two
one-byte patches (`0x10024538`, `0x10014ed6`) set the override to 0 instead.

## Status mapping and print start

A mapping at `0x100345f4` converts the status mask to a code. Bit 19 (mask `0x80000`, limit/cache policy, set from boot on when the
tag cannot be read) yields code `0x14`; this code rejects the print start (reply to the start command `55 55 DB 01 14 ..`).
The patch at `0x100346aa` turns this into code 0.

## Tick rate

`recon_uptime` showed a tick counter of about 1000 ticks per second (RT-Thread tick, measured on the device).

## Miscellaneous

- Since the battery reset the device no longer read the roll's tag (`recon_mat`: v0 n0 l0). Cause open (hypothesis: different roll/read error).
- Running the print script with `--allow-no-tag` accepts the sensor error `80000`; the head temperature check stays.
- For flash chain connection errors see [FLASH_WORKFLOW.md](FLASH_WORKFLOW.md).

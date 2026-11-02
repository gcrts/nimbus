# ESP32-S3 write bridge (v5)

`esp32-sftool-bridge-v5.ino` (Arduino-ESP32 3.x, ESP32-S3): connects `sftool` (over the ESP32 native USB, raw data) to the printer's
SF32LB52 UART. Control/log goes over a CH343 UART bridge (921600 baud, pins 44/43). Printer side: ESP32 GPIO4 = RX, GPIO5 = TX,
1 Mbaud. It only forwards sftool traffic after a SF32LB52-like chip ID was seen (ARM SFT52).

`tools/esp32_sftool_control.py` arms the bridge (`--arm-bridge`) and logs; it expects the CH343 control port on `COM8`
(VID 1A86 / PID 55D3). Adapt the port constant in the script for other setups.

Hardware notes: printer power button must be held during the ROM handshake; while the bridge is active the printer stays dark (normal).
Own hardware, own risk; no warranty. Build with Arduino CLI, board `esp32:esp32:esp32s3` (USB CDC on boot enabled, HWCDC used for sftool data).

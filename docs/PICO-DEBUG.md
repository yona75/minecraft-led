# Pico 2 W install & debug log

Everything done to bring the Raspberry Pi Pico 2 W from a blank board to
running `main.py`, including the failure we hit and how to recover from it.
All commands were run on the Mac (macOS, Apple Silicon) unless noted.

## 1. Detecting the board

A blank Pico 2 W (empty flash) always boots into **BOOTSEL mode**: it
enumerates as a USB mass-storage drive named `RP2350` and exposes the
PICOBOOT USB interface. No serial port exists yet in this mode.

```sh
# Is a Pico connected, and in which mode?
system_profiler SPUSBDataType | grep -i -B2 -A6 "rp2\|pico\|micropython"

# BOOTSEL mode  -> "RP2350 Boot", Vendor ID 0x2e8a, drive at /Volumes/RP2350
# MicroPython   -> a serial device appears instead:
ls /dev/cu.usbmodem*
```

What we saw: `RP2350 Boot` (Product ID 0x000f, Vendor ID 0x2e8a), a 134 MB
FAT16 volume mounted at `/Volumes/RP2350` — board present, no firmware.

## 2. Getting MicroPython firmware

The Pico 2 W needs the `RPI_PICO2_W` build (RP2350 **with wireless** — the
plain `RPI_PICO2` build boots but has no WiFi/onboard-LED support, since the
LED on the W hangs off the radio chip).

```sh
# Find the latest stable build
curl -s https://micropython.org/download/RPI_PICO2_W/ \
  | grep -oE 'resources/firmware/RPI_PICO2_W-[0-9]+-v[0-9.]+\.uf2' | head -3

# Download (we used v1.29.0)
curl -sL -o RPI_PICO2_W-v1.29.0.uf2 \
  https://micropython.org/resources/firmware/RPI_PICO2_W-20260824-v1.29.0.uf2
```

## 3. ⚠️ The failed attempt: copying the UF2 (do not do this)

The "official" flashing method is dragging the UF2 onto the `RP2350` drive.
We tried the CLI equivalent:

```sh
cp RPI_PICO2_W-v1.29.0.uf2 /Volumes/RP2350/     # ← WEDGED THE WHOLE MACHINE
```

**What happened:** `cp` entered uninterruptible disk I/O and never returned.
This is a known macOS bug with RP2-bootloader mass-storage writes.
The fallout cascaded:

- `cp` showed state `U` in `ps` — uninterruptible; **`kill -9` has no effect**
  on a process stuck in kernel I/O wait.
- Everything that later touched the volume queued up behind it and also hung:
  Finder, `diskutil list`, and `brew install` (Homebrew stats every mounted
  volume to check free space).
- Two Claude Code sessions hung the same way ("the process hanged up twice").

**Diagnosis commands that identified it:**

```sh
# Find processes in uninterruptible wait (STAT contains "U")
ps -axo pid,stat,wchan,comm | awk '$2 ~ /U/ {print}'

# Confirm the specific stuck cp
ps aux | grep "cp /" | grep -v grep
```

**Recovery:** the only fix is to make the device disappear —
**physically unplug the Pico**. The pending I/O errors out instantly, the
stuck `cp` dies, and Finder/diskutil/brew all recover. macOS shows a
"Disk Not Ejected Properly" warning; ignore it. Because the flash was still
empty, replugging brought the board straight back into BOOTSEL mode (no need
to hold the BOOTSEL button).

## 4. The working method: picotool

`picotool` flashes over the PICOBOOT USB interface directly — no filesystem,
no mass-storage bug.

```sh
brew install picotool                     # v2.3.0 at time of writing

picotool info                             # sanity check: board visible?
# "Program Information: none"  = blank board in BOOTSEL, ready

diskutil unmount /Volumes/RP2350          # keep macOS off the drive during reboot

picotool load -x RPI_PICO2_W-v1.29.0.uf2  # flash; -x = reboot into app after
```

Output ended with `The device was rebooted to start the application.` —
a few seconds later the serial device appeared:

```sh
ls /dev/cu.usbmodem*        # -> /dev/cu.usbmodem101
```

## 5. Verifying MicroPython and uploading main.py

Tooling: `mpremote` (in the project venv, `pip install mpremote`). It
auto-detects the serial port.

```sh
cd minecraft-led

# Confirm the interpreter and board
./.venv/bin/mpremote exec "import sys; print(sys.implementation)"
# (name='micropython', version=(1, 29, 0, ''), _machine='Raspberry Pi Pico 2 W with RP2350', ...)

# Hardware sanity: blink the onboard LED 3x
./.venv/bin/mpremote exec "from machine import Pin; import time; \
  led=Pin('LED',Pin.OUT); \
  [(led.on(), time.sleep(0.15), led.off(), time.sleep(0.15)) for _ in range(3)]"

# Install our script so it runs on every boot, then restart the board
./.venv/bin/mpremote cp pico/main.py :main.py
./.venv/bin/mpremote reset
```

## 6. End-to-end serial protocol test

`main.py` listens for `1`/`0` on USB serial and answers `ack 1` / `ack 0`:

```sh
./.venv/bin/python -c "
import serial, time
s = serial.Serial('/dev/cu.usbmodem101', 115200, timeout=1)
time.sleep(0.5); s.reset_input_buffer()
s.write(b'1'); s.flush(); print('sent 1, got:', s.readline().decode().strip())
time.sleep(2)
s.write(b'0'); s.flush(); print('sent 0, got:', s.readline().decode().strip())
"
# sent 1, got: ack 1     (onboard LED on)
# sent 0, got: ack 0     (onboard LED off)
```

## Quick reference

| Task | Command |
|---|---|
| Which mode is the board in? | `system_profiler SPUSBDataType \| grep -i rp2` / `ls /dev/cu.usbmodem*` |
| Force BOOTSEL on a flashed board | hold BOOTSEL button while plugging in, or `picotool reboot -f -u` |
| Flash firmware | `picotool load -x <file.uf2>` (never `cp` to the drive) |
| Interactive REPL | `./.venv/bin/mpremote repl` (Ctrl-X to exit; Ctrl-C stops `main.py`) |
| List files on the board | `./.venv/bin/mpremote fs ls` |
| Re-upload main.py | `./.venv/bin/mpremote cp pico/main.py :main.py && ./.venv/bin/mpremote reset` |
| Watch main.py's print output | `./.venv/bin/mpremote repl` (acks appear as the bridge sends states) |
| Wipe MicroPython filesystem | `./.venv/bin/mpremote exec "import os; os.remove('main.py')"` |

## Failure signatures

- **Mac hangs / Finder beachballs after touching the Pico drive** → the §3 bug.
  Unplug the board; check leftovers with `ps -axo pid,stat,comm | awk '$2~/U/'`.
- **`picotool info` says "No accessible RP-series devices"** → board not in
  BOOTSEL mode, not enumerated (bad cable/port — we hit this once; a
  different USB port fixed it), or it's already running MicroPython
  (then use `mpremote` instead, or `picotool` with `-f`).
- **No `/dev/cu.usbmodem*` after flashing** → wrong firmware build, or
  charge-only cable. Re-check `system_profiler SPUSBDataType`.
- **Pico onboard LED short-blinks every second** → our `main.py` heartbeat:
  board is fine, bridge isn't connected/talking.

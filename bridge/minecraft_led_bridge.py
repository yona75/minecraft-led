"""Poll a vanilla Minecraft server over RCON for a lamp's lit state and
mirror it to a Raspberry Pi Pico over USB serial.

Usage:
    python3 minecraft_led_bridge.py            # uses config below
    python3 minecraft_led_bridge.py --once     # single query, print state, exit

The lamp must be a block with a `lit` blockstate (redstone_lamp or
copper_bulb) and its chunk should be forceloaded on the server:
    /forceload add <X> <Z>
"""
import argparse
import glob
import sys
import time

from rcon import Rcon, RconError

# ---------------------------------------------------------------- config ---
RCON_HOST = "lat.tailafe6c1.ts.net"
RCON_PORT = 25575
RCON_PASSWORD = "5c657e3b833e9e529da13c76"

# Coordinates of the lamp block (get them in-game with F3 -> "Targeted Block").
LAMP_X, LAMP_Y, LAMP_Z = 108, 81, -113
LAMP_BLOCK = "minecraft:redstone_wall_torch"   # torch: lit=false when powered

POLL_SECONDS = 1.0
RESEND_SECONDS = 5.0     # re-send current state so a replugged Pico syncs up
# ---------------------------------------------------------------------------


def find_pico_port() -> str | None:
    ports = glob.glob("/dev/cu.usbmodem*")
    return ports[0] if ports else None


def open_serial(path: str):
    import serial  # pyserial
    return serial.Serial(path, 115200, timeout=0.1)


def query_lamp(rcon: Rcon) -> bool | None:
    """True = lit, False = unlit, None = can't tell (chunk not loaded etc.)."""
    resp = rcon.command(
        f"execute if block {LAMP_X} {LAMP_Y} {LAMP_Z} {LAMP_BLOCK}[lit=true]"
    )
    if "passed" in resp:
        return True
    if "failed" in resp:
        # distinguish "unlit lamp" from "wrong block/coords"
        there = rcon.command(
            f"execute if block {LAMP_X} {LAMP_Y} {LAMP_Z} {LAMP_BLOCK}"
        )
        if "passed" in there:
            return False
        print(f"warn: no {LAMP_BLOCK} at {LAMP_X} {LAMP_Y} {LAMP_Z}: {resp!r}")
        return None
    print(f"warn: unexpected RCON reply: {resp!r}")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="query once and exit")
    ap.add_argument("--no-serial", action="store_true",
                    help="poll and print only, don't talk to the Pico")
    args = ap.parse_args()

    if args.once:
        with Rcon(RCON_HOST, RCON_PORT, RCON_PASSWORD) as r:
            state = query_lamp(r)
        print({True: "LIT", False: "UNLIT", None: "UNKNOWN"}[state])
        return

    ser = None
    rcon = None
    last_state = None
    last_sent = 0.0

    while True:
        try:
            if rcon is None:
                rcon = Rcon(RCON_HOST, RCON_PORT, RCON_PASSWORD)
                rcon.connect()
                print(f"connected to RCON at {RCON_HOST}:{RCON_PORT}")

            state = query_lamp(rcon)

            if state is not None:
                changed = state != last_state
                if changed:
                    print(f"lamp is now {'LIT' if state else 'UNLIT'}")
                    last_state = state
                if not args.no_serial and (changed or time.time() - last_sent > RESEND_SECONDS):
                    if ser is None:
                        port = find_pico_port()
                        if port:
                            ser = open_serial(port)
                            print(f"connected to Pico at {port}")
                        else:
                            print("warn: no Pico serial port found, retrying...")
                    if ser is not None:
                        # Drain anything the Pico sent (acks). If nobody reads
                        # these, the Pico's USB TX buffer fills and its
                        # firmware blocks forever inside print() — this
                        # wedged the board hard enough to need a replug.
                        n = ser.in_waiting
                        if n:
                            ser.read(n)
                        ser.write(b"1" if state else b"0")
                        ser.flush()
                        last_sent = time.time()

        except RconError as e:
            print(f"RCON error: {e}; reconnecting in 5s")
            if rcon:
                rcon.close()
            rcon = None
            time.sleep(4)
        except OSError as e:
            print(f"I/O error: {e}; retrying in 5s")
            if ser:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
            if rcon:
                rcon.close()
                rcon = None
            time.sleep(4)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

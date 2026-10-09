"""Minecraft lamp -> physical LED (Raspberry Pi Pico 2 W, MicroPython).

Listens on USB serial (stdin) for single-character commands sent by the
bridge script running on the computer:
    "1"  -> LED on
    "0"  -> LED off

Drives both the onboard LED and an external LED on GP15
(GP15 -> 330 ohm resistor -> LED anode, LED cathode -> GND).

While no command has arrived yet (or the bridge is down for >10s), the
onboard LED does a slow heartbeat blink so you can tell the Pico is alive
but not connected.
"""
import select
import sys
import time

from machine import Pin

onboard = Pin("LED", Pin.OUT)
external = Pin(15, Pin.OUT)

poller = select.poll()
poller.register(sys.stdin, select.POLLIN)

state = None          # None = no data from bridge yet
last_rx = time.ticks_ms()
hb = False

while True:
    if poller.poll(200):
        ch = sys.stdin.read(1)
        if ch in ("0", "1"):
            state = ch == "1"
            last_rx = time.ticks_ms()
            onboard.value(state)
            external.value(state)
            # ack so the bridge can log round-trips
            print("ack", ch)
    stale = time.ticks_diff(time.ticks_ms(), last_rx) > 10_000
    if state is None or stale:
        # heartbeat: short blink every ~1s means "waiting for bridge"
        hb = not hb
        onboard.value(hb and (time.ticks_ms() % 1000) < 200)
        if state is None:
            external.value(0)

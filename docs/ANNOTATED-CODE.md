# Annotated code walkthrough

Line-by-line commentary for the three Python files in this project:

1. [`bridge/rcon.py`](#bridgerconpy) — RCON protocol client (runs on the Mac)
2. [`bridge/minecraft_led_bridge.py`](#bridgeminecraft_led_bridgepy) — the poller/mirror loop (runs on the Mac)
3. [`pico/main.py`](#picomainpy) — LED driver (runs on the Pico 2 W, MicroPython)

---

## bridge/rcon.py

RCON is the remote-console protocol built into every vanilla Minecraft
server (originally from Valve's Source engine). It is a simple TCP protocol:
authenticate once with a password, then send console commands and read text
replies. No mods, plugins, or datapacks involved.

### The wire format

Every packet, in both directions, looks like this (all integers are
**little-endian** int32):

```
[length][request id][type][body...][0x00][0x00]
   4B        4B       4B    N bytes   terminator + padding
```

`length` counts everything after itself (so `4 + 4 + N + 2`).

```python
class Rcon:
    SERVERDATA_AUTH = 3            # login packet type
    SERVERDATA_AUTH_RESPONSE = 2   # server's answer to login
    SERVERDATA_EXECCOMMAND = 2     # run a console command
    SERVERDATA_RESPONSE_VALUE = 0  # command output
```

Note the quirk inherited from Source: type `2` means *two different things*
depending on direction. We never need to disambiguate because we only match
on request ids.

### Connecting and authenticating

```python
def connect(self):
    self.sock = socket.create_connection((self.host, self.port), self.timeout)
    self.sock.settimeout(self.timeout)
    resp_id, _, _ = self._send(self.SERVERDATA_AUTH, self.password)
    if resp_id == -1:
        raise RconError("RCON authentication failed (bad password)")
```

The auth handshake is one packet out, one packet in. The server signals a
**bad password by echoing request id `-1`** instead of the id we sent —
that's the entire error mechanism, so we check exactly that.

`settimeout` matters: without it a dead server would block the bridge's
polling loop forever. With it, a stalled read raises `socket.timeout`
(an `OSError` subclass), which the bridge catches and treats as
"reconnect in 5 s".

### Sending a packet

```python
def _send(self, ptype: int, body: str):
    self._req_id += 1
    req_id = self._req_id
    payload = struct.pack("<ii", req_id, ptype) + body.encode("utf-8") + b"\x00\x00"
    self.sock.sendall(struct.pack("<i", len(payload)) + payload)
    return self._recv_packet()
```

- `struct.pack("<ii", ...)` = two little-endian int32s (`<` is the
  endianness flag).
- `b"\x00\x00"` = the null terminator of the body string plus the mandatory
  one-byte "pad" the protocol requires.
- Request ids just count up; we only use them for the auth `-1` check, since
  we operate strictly in lockstep (one command in flight at a time).

### Receiving a packet

```python
def _recv_packet(self):
    length = struct.unpack("<i", self._recv_exact(4))[0]
    data = self._recv_exact(length)
    resp_id, ptype = struct.unpack("<ii", data[:8])
    body = data[8:-2].decode("utf-8", errors="replace")
    return resp_id, ptype, body
```

`data[8:-2]` strips the two id/type ints from the front and the two null
bytes from the back — what's left is the human-readable reply
(e.g. `"Test passed"`).

```python
def _recv_exact(self, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = self.sock.recv(n - len(buf))
        if not chunk:
            raise RconError("connection closed by server")
        buf += chunk
    return buf
```

TCP is a byte stream, not a message stream — a single `recv()` may return a
partial packet, so we loop until we have exactly `n` bytes. An empty chunk
means the server closed the socket.

**Simplification we get away with:** long replies can be fragmented into
multiple RCON packets, and robust clients send a sentinel command to detect
the end. Our replies (`Test passed`, player lists) are tiny — always one
packet — so single-packet reads are fine here.

### Conveniences

```python
def __enter__(self): self.connect(); return self
def __exit__(self, *exc): self.close()
```

Context-manager support so short-lived uses read naturally:
`with Rcon(...) as r: r.command("list")`.

The `if __name__ == "__main__":` block makes the module double as a CLI —
`python3 rcon.py <host> <port> <password> [command...]` — which is how we
first verified connectivity over Tailscale.

---

## bridge/minecraft_led_bridge.py

The mirror loop: ask the server about one block once per second, push
changes to the Pico over USB serial.

### Configuration block

```python
RCON_HOST = "lat.tailafe6c1.ts.net"     # server, reachable over Tailscale
RCON_PORT = 25575
RCON_PASSWORD = "..."

LAMP_X, LAMP_Y, LAMP_Z = 0, 64, 0       # PLACEHOLDER — set after building the house
LAMP_BLOCK = "minecraft:redstone_lamp"  # or minecraft:copper_bulb

POLL_SECONDS = 1.0
RESEND_SECONDS = 5.0
```

`RESEND_SECONDS` exists for one reason: if you unplug/replug the Pico, its
fresh `main.py` doesn't know the lamp state. Rather than only sending on
*change*, the bridge re-sends the current state every 5 s, so a rebooted
Pico converges within seconds.

### The "sensor": one vanilla command

```python
def query_lamp(rcon):
    resp = rcon.command(
        f"execute if block {LAMP_X} {LAMP_Y} {LAMP_Z} {LAMP_BLOCK}[lit=true]"
    )
```

This is the heart of the whole project. `execute if block X Y Z
<block>[<state>]` is a vanilla command that tests whether the block at a
position matches, **including blockstate properties**. A redstone lamp
stores its on/off state as the `lit` property, so the server answers:

- `Test passed` → the block is a redstone lamp AND it is lit.
- `Test failed` → it isn't (unlit, or a different block entirely).
- `That position is not loaded` → nobody's near it and the chunk was never
  forceloaded.

Because `Test failed` is ambiguous, on failure we ask a second question —
same test but *without* `[lit=true]`:

```python
    if "failed" in resp:
        there = rcon.command(f"execute if block ... {LAMP_BLOCK}")
        if "passed" in there:
            return False     # lamp exists, just off
        print(f"warn: no {LAMP_BLOCK} at ...")
        return None          # wrong coords / lamp was broken — don't guess
```

Three-valued result: `True` (lit), `False` (unlit), `None` (can't tell).
On `None` we deliberately *don't* touch the LED — a misconfigured
coordinate shouldn't flicker the light off, it should warn and hold.

### Finding the Pico

```python
def find_pico_port():
    ports = glob.glob("/dev/cu.usbmodem*")
    return ports[0] if ports else None
```

MicroPython's USB CDC serial shows up as `/dev/cu.usbmodemNNN` on macOS.
The number depends on the physical port (we saw `101`), so we glob instead
of hardcoding. `cu.*` (call-up) rather than `tty.*` because `cu` devices
don't wait for a carrier-detect signal.

### The main loop as a state machine

```python
ser = None       # serial connection to Pico (None = not connected)
rcon = None      # RCON connection        (None = not connected)
last_state = None
last_sent = 0.0
```

Both connections are lazily (re)established inside the loop; either can die
independently and the loop heals it:

```python
while True:
    try:
        if rcon is None:
            rcon = Rcon(...); rcon.connect()

        state = query_lamp(rcon)

        if state is not None:
            changed = state != last_state
            if changed:
                print(f"lamp is now {'LIT' if state else 'UNLIT'}")
                last_state = state
            if changed or time.time() - last_sent > RESEND_SECONDS:
                # (re)open serial if needed, then:
                ser.write(b"1" if state else b"0")
```

The send condition — `changed or older-than-5s` — is what gives us both
instant reaction (≤1 poll interval) and self-healing sync (periodic resend).

### Error handling philosophy

```python
    except RconError as e:      # bad auth, closed socket
        ...; rcon = None; time.sleep(4)
    except OSError as e:        # network timeouts, unplugged serial port
        ...; ser = None; rcon = None; time.sleep(4)
```

Every failure path just tears down the affected connection, waits, and lets
the top of the loop rebuild it. There is no "fatal" state: the bridge is
designed to be left running while the server restarts, the Mac sleeps, or
the Pico gets replugged. (`4 + POLL_SECONDS = 5 s` retry cadence.)

### CLI flags

- `--once` — one query, print `LIT`/`UNLIT`/`UNKNOWN`, exit. Used for
  smoke-testing the RCON path without a Pico.
- `--no-serial` — run the poll loop and print state changes but never touch
  serial. Useful for testing the Minecraft side alone.

---

## pico/main.py

Runs on the Pico under MicroPython. A file named `main.py` in the board's
flash filesystem is executed automatically on every boot/reset — that's the
whole "install" story.

### Pins

```python
onboard = Pin("LED", Pin.OUT)
external = Pin(15, Pin.OUT)
```

On the Pico **W** boards the onboard LED is *not* on a normal GPIO — it's
wired through the wireless chip — so MicroPython gives it the symbolic name
`"LED"`. `Pin(15)` is GP15 (physical pin 20), reserved for a proper external
LED: GP15 → 330 Ω → LED → GND.

### Non-blocking input

```python
poller = select.poll()
poller.register(sys.stdin, select.POLLIN)
...
if poller.poll(200):
    ch = sys.stdin.read(1)
```

Commands arrive on USB serial, which MicroPython exposes as `sys.stdin`.
A bare `sys.stdin.read(1)` would block forever when the bridge is quiet, and
the heartbeat would never run. `select.poll` with a 200 ms timeout gives us
"read a byte if one is waiting, otherwise fall through 5×/second".

### Protocol handling

```python
if ch in ("0", "1"):
    state = ch == "1"
    last_rx = time.ticks_ms()
    onboard.value(state)
    external.value(state)
    print("ack", ch)
```

The protocol is deliberately a single byte with no framing — nothing to
parse, nothing to get out of sync. Any byte other than `0`/`1` (e.g. the
`\n` if a human tests from a terminal) is ignored. `print()` goes back out
over the same USB serial, giving the bridge (or a human in `mpremote repl`)
a round-trip acknowledgement.

### Timekeeping the MicroPython way

```python
last_rx = time.ticks_ms()
...
stale = time.ticks_diff(time.ticks_ms(), last_rx) > 10_000
```

`ticks_ms()` is a counter that wraps around (on RP2 after ~2^30 ms ≈ 12
days). Plain subtraction breaks at the wrap; `ticks_diff()` handles it
correctly. Rule of thumb on microcontrollers: never subtract tick values
directly.

### The heartbeat

```python
if state is None or stale:
    hb = not hb
    onboard.value(hb and (time.ticks_ms() % 1000) < 200)
    if state is None:
        external.value(0)
```

Status signalling without a display:

| Onboard LED | Meaning |
|---|---|
| short blink every second | alive, but no bridge data yet — or bridge silent >10 s (stale) |
| solid on / off | mirroring the in-game lamp |

The blink pattern is "on for the first 200 ms of every wall-clock second".
The external LED is forced off only when we have *never* received a state
(`state is None`); if the bridge merely went quiet, the external LED holds
the last known lamp state instead of going dark — the bridge's 5-second
resend will refresh it as soon as it returns.

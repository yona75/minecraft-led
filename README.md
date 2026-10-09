# Minecraft lamp → physical LED

Mirrors the on/off state of a redstone lamp in a Minecraft house onto a
physical LED driven by a Raspberry Pi Pico 2 W. Works with a 100% vanilla
server — it uses RCON (built into the server) to poll the lamp's
`lit` blockstate.

```
[Lever]--redstone-->[Redstone Lamp]     (in-game, vanilla)
                         |
        RCON poll ~1/s: "execute if block X Y Z minecraft:redstone_lamp[lit=true]"
                         |
        bridge/minecraft_led_bridge.py  (this Mac, reaches server over Tailscale)
                         |
        USB serial "1"/"0"  →  Pico 2 W main.py  →  onboard LED + GP15
```

## Components

- `bridge/rcon.py` — minimal RCON client (pure stdlib).
- `bridge/minecraft_led_bridge.py` — polls the server, mirrors state to the Pico.
- `pico/main.py` — MicroPython script on the Pico: reads `1`/`0` from USB
  serial, drives the onboard LED and GP15. Heartbeat-blinks while the bridge
  is not talking to it.

## Server side (already done)

- RCON enabled in `server.properties` on `lat.tailafe6c1.ts.net`
  (`minecraft-kiki-selin-1.26.2`, port 25575). Backup of the old config:
  `server.properties.bak-rcon`.

## Pico side (already done)

- MicroPython v1.29.0 flashed (via `picotool load`, NOT by copying the UF2 —
  the mass-storage copy wedges macOS in uninterruptible I/O).
- `pico/main.py` uploaded as `main.py`, runs on boot.
- Optional external LED: GP15 → 330 Ω resistor → LED anode; LED cathode → GND
  (any GND pin, e.g. pin 38; GP15 is physical pin 20).

## Once the house is built

1. In game, attach a lever to a redstone lamp. Press F3, look at the lamp,
   read "Targeted Block: X, Y, Z".
2. Keep its chunk loaded (as op): `/forceload add X Z`
3. Put the coordinates into `LAMP_X, LAMP_Y, LAMP_Z` in
   `bridge/minecraft_led_bridge.py` (and `LAMP_BLOCK` if you used a copper
   bulb instead).
4. Run the bridge:

   ```sh
   cd minecraft-led
   ./.venv/bin/python bridge/minecraft_led_bridge.py          # run forever
   ./.venv/bin/python bridge/minecraft_led_bridge.py --once   # single test query
   ```

Flip the lever in game → LED follows within ~1 second.

## Troubleshooting

- `UNKNOWN` / "That position is not loaded" → run the `/forceload` step.
- "no <block> at X Y Z" → coordinates point at the wrong block; re-check F3.
- Pico onboard LED blinking briefly every second → Pico is alive but the
  bridge isn't running/connected.
- If the Pico ever needs reflashing: `brew`-installed `picotool load -x <uf2>`
  with the board in BOOTSEL mode. Do not drag/copy the UF2 onto the RP2350
  drive on macOS.

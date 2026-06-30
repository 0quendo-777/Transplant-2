![Transplant 2 v0.1](assets/title.png)

**Transplant levels from Super Mario World (SNES) to Super Mario Advance 2 / Super Mario Advance 2: Super Mario World (GBA).**

Only Layer 1 (foreground tiles) and sprites are transplanted. Layer 2 (background graphics) is left untouched in the GBA ROM.

---

## Origins

This tool is a complete reimplementation from scratch based on the Super Mario Advance 2 ROM disassembly and hacking research. The original transplant tool by **Smallhacker** was decompiled and analyzed at:

**https://github.com/0quendo-777/transplant-tool-decomp**

The decompiled code was used as a reference to understand how SMW data maps to the SMA2 ROM structure, then reimplemented cleanly in Python without copying any of the original code. All addresses, offsets, data formats, and pointer tables were verified against the disassembly rather than assumed from the decomp.

---

## What It Does

Given an SMW ROM and an unmodified SMA2 ROM:
1. Reads all 512 SMW levels (indices `0x000`–`0x1FF`)
2. For each level found in the overworld pointer table (`0x2E000`+3, bank `0x10`+), extracts:
   - **Layer 1 header** (7 bytes — level length, mode, tilesets, palettes, timers)
   - **Layer 1 object data** (tile-encoded foreground objects)
   - **Sprite data** (all sprites in the level, sorted by global X coordinate)
3. Writes the L1 data to the GBA L1 data region (`0x080E6530–0x080F09D5`)
4. Writes the sprite data to the GBA sprite data region (`0x080FC02A–0x080FE744`)
5. Writes updated pointer tables to the GBA ROM pointing to the new data locations
6. Patches the secondary header table (`0x080F3D44`) with SMW entrance data

Layer 2 (BG graphics, backgrounds) is **never modified** — the original SMA2 L2 data is preserved.

---

## How to Use

```bat
python transplant2.py <smw_rom> <sma2_rom>
```

- `smw_rom` — SMW `.smc` / `.sfc` file
- `sma2_rom` — SMA2 `.gba` file (**modified in place** — make a backup first)

Example:
```bat
python transplant2.py smw.smc sma2.gba
```

Output shows free space remaining in the L1 and sprite data regions.

---

## ROM Tables Modified

| Table | GBA Address | Size | What |
|-------|------------|------|------|
| L1 pointer table | `0x080F22CC` | 512 × 4 bytes | Pointers to L1 data per sublevel |
| Sprite pointer table | `0x080F3314` | 512 × 4 bytes | Pointers to sprite data per sublevel |
| Secondary header table | `0x080F3D44` | 512 × 5 bytes | Entrance position, Layer 2 scroll, U/V flags |
| L2 BGID table | `0x080F3B38` | 521 bytes | Restored to original (preserved) |
| L2 tilemap pointer table | `0x080F2AF0` | 521 × 4 bytes | Restored to original (preserved) |

---

## Level Header Format (SMA2 Primary Header — 7 bytes)

```
Byte 0: BBBL LLLL   — BG palette | Level length (screens)
Byte 1: SSSM MMMM   — Scroll type | Level mode
Byte 2: mmmm ssss   — Music index | Sprite tileset
Byte 3: ttPP PFFF   — Timer digit | Sprite palette | FG palette
Byte 4: IIOC TTTT   — Item memory | Camera snap | Layer 1/2 tileset
Byte 5: bbbb cccc   — Back area color | Camera sensitivity
Byte 6: xxxx xxxx   — Unused (always 0x00)
```

Bytes 5 and 6 are GBA-specific. The code preserves the original SMA2 bytes 5–6 for every sublevel (read before any L1 data is written), then overwrites byte 5's high nibble with the BG color derived from SMW. This prevents GBA-specific scroll/camera behavior from being corrupted.

---

## Secondary Header Format (5 bytes, at `0x0F3D44`)

```
Byte 0: SSSS YYYY   — L2 scroll settings | Level entrance Y position
Byte 1: 33TT TXXX   — Layer 3 image | Entrance type | Level entrance X position
Byte 2: MMMM BBBB   — Midway entrance screen | Initial BG Y
Byte 3: ???? FFFF   — Unknown | Initial FG Y
Byte 4: IUVP PPPP   — Disable no-Yoshi intro | U-flag | V-flag | Entrance screen
```

---

## The Vertical Level Crash Fix

### What Was Happening

SMW has vertical levels (mode `0x0D`). When levels were transplanted from SMW into SMA2 and sprites were present, the game would crash instantly upon entering the level. The same transplanted levels worked fine on SNES hardware or SNES emulators.

### How the IUVP Flags Work (Discovered in the Disassembly)

The investigation traced through **Code.asm** at `0x0800F0E8` (the main level setup routine that parses the secondary header):

```asm
ldrb  r0,[r5]           ; header byte 4 loaded
mov   r0,0x10
and   r0,r1              ; mask U flag (bit 4, mask 0x10)
cmp   r0,0x0
beq   @@U_Clear
; U flag SET path:
ldr   r1,=0x03002340
ldr   r0,=0xFFF8
strh  r0,[r1,0x26]      ; store FFF8 to [0300439E] — signed Y offset

; later:
ldrb  r0,[r5]           ; header byte 4 loaded again
mov   r1,0x20
and   r0,r1              ; mask V flag (bit 5, mask 0x20)
cmp   r0,0x0
beq   @@V_Clear
; V flag SET path:
mov   r0,0x8
strh  r0,[r1,0x28]       ; store 0008 to [030043A0]

@@V_Clear:
strh  r0,[r1,0x28]       ; store 0 otherwise
```

From this, the flags map to:

| Flag | Bit | Mask | Effect when SET | Effect when CLEAR |
|------|-----|------|-----------------|-------------------|
| **U** | 4 | `0x10` | Writes `0xFFF8` to `0x0300439E` (signed Y offset used in level init) | Writes `0x0000` |
| **V** | 5 | `0x20` | Writes `0x0008` to `0x030043A0` (tells sprite Y-position logic it's a vertical level) | Writes `0x0000` |

The `V=1` path sets `0x0008` at `0x030043A0`. This value is read by the sprite Y-coordinate rendering routine. When V=0 but the level mode is `0x0D` (vertical), the sprite code misinterprets Y coordinates — every sprite's Y position is computed as if horizontal, producing out-of-bounds values that crash the GBA when sprites try to load.

The table `LevelMode_VertL2Flags` at `0x080D9604` (Data.asm) shows that mode `0x0D` has value `0x00`, so its bit 0 is clear and the vertical tilemap handler (`L1TVertLow_RAMPtrs`) is correctly selected. The problem is purely that SMA2's secondary header U/V bits need to be set for its own sprite runtime to work with vertical coordinates.

### The Fix

The level mode is extracted from the primary header during L1 loading. When building the secondary header byte 4 for mode `0x0D` levels, both U and V are forced to 1:

```python
# header2 byte 4 — IUVP flags (copied from SMW secondary header)
header2[local9][4] = header2smw[local9][3]

if loaded_levelmode == 0x0D:
    # Force U=1 and V=1 in byte 4 for SMA2's vertical level runtime.
    # SMA2 expects these bits set; SMW's values (typically 0) crash the GBA.
    # U = bit 4 (mask 0x10), V = bit 5 (mask 0x20)
    header2[local9][4] = (header2[local9][4] & ~0x30) | 0x30
```

The entrance screen number (`PPPPP`, bits 0–4) is preserved intact — only the U and V flags are overridden.

---

## Why L2 Is Not Transplanted

SMA2 levels use GBA-specific background graphics and tilemap layouts that are not bit-equivalent to SNES SMW data. Overwriting the L2 region risks corrupting BG tilesets that aren't present in SMW at all.

If you need BG changes, use the [SMA2 Layer 2 Editor](https://github.com/0quendo-777/SMA2-Layer-2-Editor) tool after running `transplant2.py`.

---

## Key Addresses (GBA)

| Name | GBA Address | ROM Offset | Purpose |
|------|------------|-----------|---------|
| L1 pointer table | `0x080F22CC` | `0x0F22CC` | 512 × 4 byte pointers |
| L2 pointer table | `0x080F2AF0` | `0x0F2AF0` | 521 × 4 byte pointers |
| Sprite pointer table | `0x080F3314` | `0x0F3314` | 512 × 4 byte pointers |
| BG ID table | `0x080F3B38` | `0x0F3B38` | 521 × 1 byte |
| Secondary header table | `0x080F3D44` | `0x0F3D44` | 512 × 5 bytes |
| L1 data region | `0x080E6530` | `0x0E6530` | Writable L1 + objects |
| Sprite data region | `0x080FC02A` | `0x0FC02A` | Writable sprite data |

---

## Research Sources

- **SMA2 Disassembly** — https://github.com/KarisaAdvynia/sma2-disasm
  The primary reference for all GBA address mappings, level loading routines, header formats, and RAM table structures. Every address and flag behavior was verified against this.
- **SMA2-DX** — https://github.com/kiliwily/SMA2-DX
  Full-featured ROM hack that documents many runtime behaviors through its source patches and bug fixes. Useful for understanding sprite handling edge cases.

---

## Credits

Developed by Oquendo. Based on research using the SMA2 disassembly and SMA2-DX.
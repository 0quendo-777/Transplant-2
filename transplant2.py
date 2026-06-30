# ============================================================================
# transplant2.py
#
# Reimplementation of the SMW → SMA2 level/sprite transplant tool.
# Ports Layer 1 foreground tiles and sprites from Super Mario World (SNES)
# into Super Mario Advance 2 (GBA), leaving Layer 2 (background) untouched.
#
# Credits: Oquendo — port, research, and all disassembly work.
# Based on the original transplant tool by Smallhacker.
# ============================================================================

import sys
import struct
import os
import argparse

# ============================================================================
# GLOBAL CONSTANTS
# ============================================================================

# Levels forced to be processed even if their bank pointer is invalid.
# These are SMW level IDs that exist outside the normal bank range ($10-$FF).
FORCE_LEVELS = [0xC5, 0xC7, 0x00, 0x100, 0x108, 0x104, 0xC8, 0x1C8]

# SMW pointer table base (bank 0x0C -> PC 0x60000)
SMW_L2_PTR_BASE = 0x60000


# ============================================================================
# LEVEL MAPPING TABLE
# ============================================================================
#
# This table documents which SMW source levels feed into each SMA2 destination
# sublevel. It is NOT used by the transplant loop itself -- the loop iterates
# all 512 possible level slots (0x000-0x1FF) and uses dynamic pointer detection
# (bank >= 0x10 check) to determine if a level exists in the SMW ROM.
#
# The map exists for reverse-lookup reference: given an SMW level ID, you can
# find which SMA2 sublevel it was transplanted into. The original decompiled
# tool had no such table because it processed levels 1:1 (same level number in
# SMW and SMA2). Since SMA2 has a different level numbering scheme than SMW,
# this mapping documents the relationship.
#
# Format: SMA2 sublevel ID -> [list of SMW level IDs that map to it]
#
LEVEL_MAP = {
    0x00: [0x000, 0x100],
    0x01: [0x101, 0x1F6, 0x1FC],
    0x02: [0x102, 0x1BE, 0x1FF],
    0x03: [0x103, 0x1FD],
    0x04: [0x104],
    0x05: [0x105, 0x1CB],
    0x06: [0x106, 0x1CA],
    0x07: [0x107, 0x1EA, 0x1F9, 0x1FB],
    0x08: [0x108],
    0x09: [0x109, 0x1F0, 0x1F1],
    0x0A: [0x10A, 0x1C2, 0x1F7],
    0x0B: [0x10B, 0x1C6],
    0x0D: [0x10D],
    0x0E: [0x10E],
    0x0F: [0x10F, 0x1BF],
    0x10: [0x110, 0x1EB, 0x1FE],
    0x11: [0x111, 0x1DE],
    0x12: [0x128],
    0x13: [0x113, 0x1BB],
    0x14: [0x114, 0x1D9, 0x1DA, 0x1DB, 0x1DC, 0x1DD],
    0x15: [0x115, 0x1E2, 0x1E3],
    0x16: [0x116, 0x1E4, 0x1E5],
    0x17: [0x117, 0x1C0, 0x1EC, 0x1ED, 0x1EE],
    0x18: [0x118, 0x1C3],
    0x19: [0x119, 0x1F5],
    0x1A: [0x11A, 0x1EF],
    0x1B: [0x11B, 0x1D8],
    0x1C: [0x11C, 0x1F2, 0x1F3, 0x1F4],
    0x1D: [0x11D, 0x1E6, 0x1E7, 0x1E8, 0x1E9, 0x1FA],
    0x1E: [0x11E],
    0x1F: [0x11F, 0x1C1, 0x1DF],
    0x20: [0x120],
    0x21: [0x121, 0x1D7],
    0x22: [0x122],
    0x23: [0x123, 0x1BC, 0x1F8],
    0x25: [0x125],
    0x26: [0x126],
    0x27: [0x127, 0x1E0, 0x1E1],
    0x28: [0x128],
    0x2A: [0x12A, 0x1C4, 0x1C5],
    0x2B: [0x12B],
    0x2C: [0x12C, 0x1C9],
    0x2D: [0x12D],
    0x30: [0x130, 0x1D5],
    0x32: [0x132],
    0x34: [0x134, 0x1D6],
    0x35: [0x135],
    0x36: [0x136],
    0xE2: [0x1FF],
    0xE3: [0x1BF],
    0xE4: [0x1C0],
    0xE5: [0x1DF],
    0xE6: [0x1C2],
    0xE7: [0x1C3],
    0xEA: [0x1C6],
    0xED: [0x1C9],
    0xEE: [0x1CA],
    0xEF: [0x1CB],
    0xF0: [0x1CC],
    0xF1: [0x1CD],
    0xF2: [0x1CE],
    0xF3: [0x1CF],
    0xF4: [0x1D0],
    0xF5: [0x1D1],
    0xF6: [0x1D2],
    0xF7: [0x1D3],
    0xF8: [0x1D4],
    0xFD: [0x1DB],
    0xFF: [0x1DC],
}

# Reverse lookup: SMW level ID -> SMA2 sublevel ID (built from LEVEL_MAP)
SMW_TO_SMA2 = {}
for _sma2, _smw_list in LEVEL_MAP.items():
    for _smw in _smw_list:
        SMW_TO_SMA2[_smw] = _sma2


# ============================================================================
# GBA ROM ADDRESSES
# ============================================================================
#
# Pointer tables (written after transplant):
#   L1 pointer table:    0x0F22CC  (4 bytes per entry, 0x200 entries)
#   Sprite pointer table: 0x0F3314 (4 bytes per entry, 0x200 entries)
#   BG layout table:     0x0F2AF0 (4 bytes per entry, 0x209 entries)
#   BG ID table:         0x0F3B38 (1 byte per entry, 0x209 entries)
#   Header2 table:       0x0F3D44 (5 bytes per entry, 0x200 entries)
#
# Data regions (written sequentially during transplant):
#   L1 data:      0x0E6530 - 0x0F09D5  (free space: 0xA4A5 bytes)
#   L2 data:     0x0F09D5 - 0x0F18C4  (NOT modified -- left as-is)
#   Sprite data: 0x0FC02A - 0x0FE744  (free space: 0x271A bytes)
#

# L1 stream pointers (destination in GBA ROM)
GBA_L1_PTR_BASE    = 0x0F22CC
# L1 data region (where level data actually gets written)
GBA_L1_DATA_BASE   = 0x0E6530
GBA_L1_DATA_END    = 0x0F09D5

# L2 data region (NOT modified -- preserved from original SMA2 ROM)
GBA_L2_DATA_BASE   = 0x0F09D5
GBA_L2_DATA_END     = 0x0F18C4

# Sprite data region (separate from L1)
GBA_SPRITE_DATA_BASE = 0x0FC02A
GBA_SPRITE_DATA_END  = 0x0FE744

# BG layout/ID tables (preserved from original SMA2 ROM unless explicitly set)
GBA_BG_LAYOUT_BASE = 0x0F2AF0
GBA_BG_LAYOUT_MAX  = 0x209   # 521 entries covering sublevels 000-208
GBA_BG_ID_BASE     = 0x0F3B38


# ============================================================================
# CLASSES
# ============================================================================

class TransplantContext:
    """Holds all state during a single transplant run."""

    def __init__(self):
        # Remaining freespace in each region (decremented as data is written)
        self.l1_freespace     = GBA_L1_DATA_END - GBA_L1_DATA_BASE   # 0xA4A5
        self.sprite_freespace = GBA_SPRITE_DATA_END - GBA_SPRITE_DATA_BASE  # 0x271A

        # Current level/sprite processing state
        self.totalsize = 0
        self.currentlevel    = 0
        self.currentlysprite = 0

        # L1 header fields (parsed from SMW, some overwritten with GBA values)
        self.bgcol       = 0
        self.bgpal       = 0
        self.cam         = 0
        self.fgpal       = 0
        self.itemmem     = 0
        self.levellength = 0
        self.levelmode   = 0
        self.music       = 0
        self.snap        = 0
        self.spriteheader = 0
        self.spritepal   = 0
        self.spriteset   = 0
        self.tileset     = 0
        self.time        = 0
        self.vscroll     = 0
        self.xbyte       = 0

        # Parsed block/sprite data for current level
        self.blocks = []

        # -------------------------------------------------------------------------
        # GBA-specific L1 header bytes 5 and 6.
        #
        # These are read from the ORIGINAL sma2_rom BEFORE any modifications.
        # They control GBA-specific scroll behaviour (sensitivity, xbit, xbyte)
        # that has no SMW equivalent and cannot be derived automatically.
        #
        # Byte 5 layout: [bgcol(4 bits) | sensitivity(2 bits) | xbit(2 bits)]
        #                bgcol       = high nibble (overwritten with SMW value)
        #                b5_lo       = low nibble  (preserved from GBA original)
        # Byte 6 layout: [xbyte (always 0x00 in vanilla SMA2)]
        # -------------------------------------------------------------------------
        self.gba_b5_lo = 0   # sensitivity + xbit (bits 3-0 of byte 5)
        self.gba_b6    = 0   # xbyte (byte 6)


class Block:
    """Represents a single level object (L1 block or sprite)."""

    def __init__(self):
        self.data     = [0] * 4   # raw bytes (3 or 4 depending on block type)
        self.props    = 0          # number of bytes to write
        self.sort_val = 0          # sort key for sprite ordering


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def read_snes_address(f):
    """Read a 3-byte SNES address (little-endian, bank byte last)."""
    b1 = f.read(1)[0]
    b2 = f.read(1)[0]
    b3 = f.read(1)[0]
    return b1 + (b2 << 8) + (b3 << 16)


def pc_address(snes_addr, header_offset):
    """Convert SNES address to PC file offset.

    SNES LoROM mapping:
      - lower 15 bits ($7FFF) -> direct mapped
      - bit 16-23 (bank byte) >> 1 -> added to result
      - +header_offset if ROM has 64-byte header
    """
    return (
        (snes_addr & 0x7FFF) +
        ((snes_addr & 0xFF0000) >> 1) +
        header_offset
    )


# ============================================================================
# LAYER 1 (LEVEL DATA)
# ============================================================================

def load_level(f, ctx):
    """Parse level data from SMW ROM file handle into ctx.blocks."""
    ctx.currentlysprite = 0
    ctx.totalsize = 0
    ctx.blocks.clear()

    # 5-byte L1 header
    header = struct.unpack('BBBBB', f.read(5))
    ctx.bgpal       = (header[0] >> 5) & 0x07
    ctx.levellength =  header[0]        & 0x1F
    ctx.levelmode   =  header[1]        & 0x1F
    ctx.bgcol       = (header[1] >> 5) & 0x07
    ctx.music       = (header[2] >> 4) & 0x07
    ctx.spriteset   =  header[2]        & 0x0F
    ctx.spritepal   = (header[3] >> 3) & 0x07
    ctx.fgpal       =  header[3]        & 0x07
    ctx.time        = (header[3] >> 6) & 0x03
    ctx.tileset     =  header[4]        & 0x0F
    ctx.vscroll     = (header[4] >> 4) & 0x03
    ctx.itemmem     = (header[4] >> 6) & 0x03
    ctx.snap        = 1 if ctx.levellength == 0 else 0
    ctx.cam         = 0

    # Remap SMW vscroll values to GBA equivalents
    if ctx.vscroll == 0 and ctx.levellength > 0:
        ctx.vscroll = 4
    elif ctx.vscroll == 2:
        ctx.vscroll = 6

    # Read level objects until 0xFF terminator
    while True:
        raw = f.read(1)
        if not raw:
            break
        b0 = raw[0]
        if b0 == 0xFF:
            break

        b1 = f.read(1)[0]
        b2 = f.read(1)[0]

        # Decode block type from first two bytes
        block_id = ((b0 & 0x60) >> 1) | (b1 >> 4)
        size = 3
        b3 = 0

        # 4-byte blocks: extended size or map16 tile with extra byte
        if block_id == 0x00 and b2 == 0x00:
            size, b3 = 4, f.read(1)[0]
        elif block_id in (0x22, 0x23):
            size, b3 = 4, f.read(1)[0]

        # Tile ID byte swap for specific tilesets (bittranspose)
        if block_id == 0x3F and ctx.tileset in (0x02, 0x06, 0x08):
            b2 = ((b2 & 0x0F) << 4) | ((b2 >> 4) & 0x0F)

        blk = Block()
        blk.data[0] = b0
        blk.data[1] = b1
        blk.data[2] = b2
        if size == 4:
            blk.data[3] = b3

        blk.props = size
        ctx.totalsize += size
        ctx.blocks.append(blk)


def save_level(f, ctx):
    """Write ctx.blocks as level data to GBA ROM.

    Layout: [7-byte header][block data][0xFF terminator]
    """
    # Header byte 0: levellength | bgpal
    f.write(struct.pack('B', ctx.levellength | (ctx.bgpal << 5)))

    # Header byte 1: levelmode | vscroll (high bit) -- vscroll low bits set below
    f.write(struct.pack('B', ctx.levelmode | (ctx.vscroll << 5)))

    # Header byte 2: spriteset | music
    f.write(struct.pack('B', ctx.spriteset | (ctx.music << 4)))

    # Header byte 3: fgpal | spritepal | time
    f.write(struct.pack('B', ctx.fgpal | (ctx.spritepal << 3) | (ctx.time << 6)))

    # Header byte 4: tileset | cam | snap | itemmem
    f.write(struct.pack('B', ctx.tileset | (ctx.cam << 4) | (ctx.snap << 5) | (ctx.itemmem << 6)))

    # Header byte 5: bgcol | gba_b5_lo (sensitivity + xbit from original GBA ROM)
    f.write(struct.pack('B', ctx.gba_b5_lo | (ctx.bgcol << 4)))

    # Header byte 6: xbyte (from original GBA ROM)
    f.write(struct.pack('B', ctx.gba_b6))

    # Block data
    for blk in ctx.blocks:
        for i in range(blk.props):
            f.write(struct.pack('B', blk.data[i]))

    # Terminator
    f.write(struct.pack('B', 0xFF))

    # Account for 7-byte header + block data + 1-byte terminator
    ctx.l1_freespace -= (ctx.totalsize + 8)


# ============================================================================
# SPRITES
# ============================================================================

def load_sprites(f, ctx):
    """Parse sprite data from SMW ROM file handle into ctx.blocks."""
    ctx.currentlysprite = 1
    ctx.totalsize = 0
    ctx.blocks.clear()

    ctx.spriteheader = f.read(1)[0]

    while True:
        raw = f.read(1)
        if not raw:
            break
        b0 = raw[0]
        if b0 == 0xFF:
            break

        b1 = f.read(1)[0]
        sprite_id = f.read(1)[0]

        # Compute sort key for sprite ordering
        sort_val = (
            ((b1 & 0x0F) << 4) |
            ((b1 & 0xF0) >> 4) |
            ((b0 & 0x02) << 7)
        )

        # Sprite ID remapping from SMW -> SMA2
        # Order matters! 0xC9/CA/CB must be checked BEFORE >= 0xCC catch-all.
        if sprite_id == 0x12:
            sprite_id = 0xC9
        elif sprite_id == 0x36:
            sprite_id = 0xCA
        elif sprite_id == 0x53:
            sprite_id = 0xCB
        elif sprite_id >= 0xCC:
            sprite_id += 3

        blk = Block()
        blk.data[0] = b0
        blk.data[1] = b1
        blk.data[2] = sprite_id
        blk.props   = 3
        blk.sort_val = sort_val

        ctx.totalsize += 3
        ctx.blocks.append(blk)


def sort_sprites(ctx):
    """Sort sprites by their computed sort key."""
    ctx.blocks.sort(key=lambda blk: blk.sort_val)


def save_sprites(f, ctx):
    """Write ctx.blocks as sprite data to GBA ROM.

    Layout: [1-byte header][sprite data][0xFF terminator]
    """
    f.write(struct.pack('B', ctx.spriteheader))

    for blk in ctx.blocks:
        f.write(struct.pack('BBB', blk.data[0], blk.data[1], blk.data[2]))

    f.write(struct.pack('B', 0xFF))

    # Account for 1-byte header + sprite data + 1-byte terminator
    ctx.sprite_freespace -= (ctx.totalsize + 2)


# ============================================================================
# TRANSPLANT MAIN LOOP
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Transplant SMW (SNES) levels -> SMA2 (GBA). L1 and sprites only.'
    )
    parser.add_argument('smw_rom',  help='Input SMW ROM (.smc/.sfc)')
    parser.add_argument('sma2_rom', help='Output SMA2 ROM (.gba, modified in place)')
    args = parser.parse_args()

    # -------------------------------------------------------------------------
    # Initialize context and working tables
    # -------------------------------------------------------------------------
    ctx = TransplantContext()

    # Levels forced to process regardless of pointer validity
    forcelevel = {i: (i in FORCE_LEVELS) for i in range(0x200)}

    # Output pointer tables (filled during transplant, written at end)
    pointer  = [0] * 0x200   # L1 data pointer per sublevel
    spointer = [0] * 0x200     # sprite data pointer per sublevel

    # BG layout/ID tables (None = leave original SMA2 value untouched)
    bg_layout_ptr = [None] * 0x209
    bg_id_table   = [None] * 0x209

    # SMW header2 data (read from SMW ROM, applied to SMA2)
    header2smw = [[0] * 4 for _ in range(0x200)]

    # Output header2 (SMA2 format: 5 bytes per level)
    header2 = [[0] * 5 for _ in range(0x200)]
    for i in range(0x200):
        header2[i][0] = 0x0F
        header2[i][4] = 0x80

    # -------------------------------------------------------------------------
    # Open both ROM files
    # -------------------------------------------------------------------------
    with open(args.smw_rom, 'rb') as f_in, open(args.sma2_rom, 'r+b') as f_out:

        # =====================================================================
        # STEP 1: Capture original SMA2 state before any modifications
        # =====================================================================

        # --- Read GBA L1 pointer table (to locate each sublevel's data) ---
        f_out.seek(GBA_L1_PTR_BASE)
        gba_l1_ptrs = [
            struct.unpack('<I', f_out.read(4))[0]
            for _ in range(0x200)
        ]

        # --- Read bytes 5 and 6 from each sublevel's original L1 header ---
        # These GBA-specific values (sensitivity, xbit, xbyte) are preserved
        # and re-applied during transplant, since they cannot be derived from SMW.
        gba_b5         = [0] * 0x200
        gba_b6         = [0] * 0x200
        gba_data_cache = [bytes(7)] * 0x200

        for i in range(0x200):
            pc = gba_l1_ptrs[i] & 0x00FFFFFF
            f_out.seek(pc)
            hdr = f_out.read(7)
            if len(hdr) >= 7:
                gba_b5[i]         = hdr[5]
                gba_b6[i]         = hdr[6]
                gba_data_cache[i] = hdr

        # --- Read BG tables (preserved from original SMA2 ROM) ---
        f_out.seek(GBA_BG_LAYOUT_BASE)
        gba_bg_layout_orig = [
            struct.unpack('<I', f_out.read(4))[0]
            for _ in range(0x209)
        ]

        f_out.seek(GBA_BG_ID_BASE)
        gba_bg_id_orig = [f_out.read(1)[0] for _ in range(0x209)]

        # =====================================================================
        # STEP 2: Read SMW ROM data
        # =====================================================================

        # Detect if ROM has a 64-byte header (some .smc dumps include it)
        f_in.seek(0, 2)
        smw_size = f_in.tell()
        header_offset = 0x200 if (smw_size % 0x8000 == 0x200) else 0

        # SMW header2 table at $2F000 (4 bytes per level, 0x200 entries)
        f_in.seek(0x2F000 + header_offset)
        for row in range(4):
            for col in range(0x200):
                header2smw[col][row] = f_in.read(1)[0]

        # =====================================================================
        # STEP 3: Set up write heads for each data region
        # =====================================================================

        # Write a fallback empty level first to reserve the base address as default.
        # This ensures any level without a valid pointer still has a valid address.
        f_out.seek(GBA_L1_DATA_BASE)
        default_l1_ptr = GBA_L1_DATA_BASE
        save_level(f_out, ctx)       # writes 8 bytes, deducts from l1_freespace
        l1_write_head = f_out.tell()

        # Same for sprites
        f_out.seek(GBA_SPRITE_DATA_BASE)
        default_sprite_ptr = GBA_SPRITE_DATA_BASE
        save_sprites(f_out, ctx)     # writes 2 bytes, deducts from sprite_freespace
        sprite_write_head = f_out.tell()

        # L2 is not modified in this implementation
        l2_write_head = GBA_L2_DATA_BASE

        # Pre-fill all pointer slots with fallback addresses
        for i in range(0x200):
            pointer[i]  = default_l1_ptr
            spointer[i] = default_sprite_ptr

        error_flag = False

        # =====================================================================
        # STEP 4: Main transplant loop -- process all 512 possible level slots
        # =====================================================================
        #
        # Unlike a static level_map approach, this loop uses the pointer table
        # embedded in the SMW ROM itself to discover which levels actually exist.
        #
        # For each slot 0x000-0x1FF:
        #   1. Read 3-byte SNES pointer from SMW's level pointer table
        #   2. If bank byte >= 0x10, the level exists -- read and transplant it
        #   3. Otherwise, leave the fallback (empty) level in place
        #
        for local9 in range(0x200):
            ctx.currentlevel = local9

            # Restore GBA-specific scroll settings for this sublevel
            ctx.gba_b5_lo = gba_b5[local9] & 0x0F   # sensitivity + xbit
            ctx.gba_b6    = gba_b6[local9]            # xbyte

            # ------------------------------------------------------------------
            # LAYER 1 -- read from SMW pointer table, write to GBA L1 region
            # ------------------------------------------------------------------
            # Level pointer table entry: 3 bytes at $2E000 + (level * 3)
            f_in.seek(0x2E000 + header_offset + (3 * local9))
            snes_addr = read_snes_address(f_in)
            bank = (snes_addr >> 16) & 0xFF

            if bank >= 0x10 or forcelevel[local9]:
                # Level exists -- read and transplant
                f_in.seek(pc_address(snes_addr, header_offset))
                load_level(f_in, ctx)

                needed_l1 = ctx.totalsize + 8   # header + data + terminator

                if needed_l1 <= ctx.l1_freespace:
                    f_out.seek(l1_write_head)
                    pointer[local9] = l1_write_head
                    save_level(f_out, ctx)
                    l1_write_head = f_out.tell()
                else:
                    print(f"  Error: Not enough space in L1 region for level {hex(local9)}. "
                          f"{ctx.l1_freespace} bytes remaining.")
                    error_flag = True

                # Remember GBA file position after L1 data to return here later
                l1_pos_after = f_out.tell()

                # ------------------------------------------------------------------
                # SPRITES -- read from SMW sprite pointer table, write to GBA sprite region
                # ------------------------------------------------------------------
                # Sprite pointer: 2 bytes at $2EC00 + (level << 1)
                f_in.seek(0x2EC00 + header_offset + (local9 << 1))
                sprite_ptr = struct.unpack('<H', f_in.read(2))[0]

                # Sprite SNES address: 2-byte pointer | high byte from $77100 + level
                f_in.seek(0x77100 + header_offset + local9)
                sprite_snes_addr = sprite_ptr | (f_in.read(1)[0] << 16)

                f_in.seek(pc_address(sprite_snes_addr, header_offset))
                load_sprites(f_in, ctx)
                sort_sprites(ctx)

                needed_sp = ctx.totalsize + 2   # header + data + terminator

                if needed_sp <= ctx.sprite_freespace:
                    f_out.seek(sprite_write_head)
                    spointer[local9] = sprite_write_head
                    save_sprites(f_out, ctx)
                    sprite_write_head = f_out.tell()
                else:
                    print(f"  Error: Not enough space in sprite region for level {hex(local9)}. "
                          f"{ctx.sprite_freespace} bytes remaining.")
                    error_flag = True

                # Return to position after L1 to write next level
                f_out.seek(l1_pos_after)

            # Always return to L1 write position (works for both valid and fallback levels)
            f_out.seek(l1_pos_after)

            # ------------------------------------------------------------------
            # HEADER2 -- per-level metadata derived from SMW header2 table
            # ------------------------------------------------------------------
            # Preserve levelmode for vertical level check below
            loaded_levelmode = ctx.levelmode

            header2[local9][0] = header2smw[local9][0]
            header2[local9][1] = header2smw[local9][1]

            # byte 2: drop the two unused SMW bits
            header2[local9][2] = header2smw[local9][2] & 0xF3
            # byte 3: re-encode from the bits we dropped above
            header2[local9][3] = (header2smw[local9][2] >> 2) & 0x03

            header2[local9][4] = header2smw[local9][3]

            # SMA2 mode 0x0D (vertical level): force U=1 and V=1 in IUVP byte.
            # SMW's U/V values crash on GBA because SMA2's sprite Y-coordinate
            # math depends on these flags being set. U = bit 4, V = bit 5.
            if loaded_levelmode == 0x0D:
                header2[local9][4] = (header2[local9][4] & ~0x30) | 0x30

            # Backgrounds (L2) are NOT modified -- original SMA2 values are
            # preserved because bg_layout_ptr and bg_id_table stay as None.

        # =====================================================================
        # STEP 5: Write all pointer tables back to the GBA ROM
        # =====================================================================

        # --- L1 pointer table (0xF22CC) ---
        f_out.seek(0xF22CC)
        for i in range(0x200):
            f_out.write(struct.pack('<I', pointer[i] | 0x08000000))

        # --- Sprite pointer table (0xF3314) ---
        f_out.seek(0xF3314)
        for i in range(0x200):
            f_out.write(struct.pack('<I', spointer[i] | 0x08000000))

        # --- Header2 table (0xF3D44) ---
        f_out.seek(0xF3D44)
        for row in range(5):
            for col in range(0x200):
                f_out.write(struct.pack('B', header2[col][row]))

        # --- BG layout pointer table (0x0F2AF0) ---
        # None entries -> restore original value from sma2_rom
        f_out.seek(GBA_BG_LAYOUT_BASE)
        for i in range(0x209):
            val = bg_layout_ptr[i] if bg_layout_ptr[i] is not None else gba_bg_layout_orig[i]
            if val < 0x08000000:
                val |= 0x08000000
            f_out.write(struct.pack('<I', val))

        # --- BG ID table (0x0F3B38) ---
        f_out.seek(GBA_BG_ID_BASE)
        for i in range(0x209):
            val = bg_id_table[i] if bg_id_table[i] is not None else gba_bg_id_orig[i]
            f_out.write(struct.pack('B', val))

        # =====================================================================
        # STEP 6: Report results
        # =====================================================================
        print(f"\nRemaining space:")
        print(f"  L1 region     (0x{GBA_L1_DATA_BASE:06X}-0x{GBA_L1_DATA_END:06X}): "
              f"{ctx.l1_freespace} free bytes of {GBA_L1_DATA_END - GBA_L1_DATA_BASE}")
        print(f"  Sprite region (0x{GBA_SPRITE_DATA_BASE:06X}-0x{GBA_SPRITE_DATA_END:06X}): "
              f"{ctx.sprite_freespace} free bytes of {GBA_SPRITE_DATA_END - GBA_SPRITE_DATA_BASE}")
        print(f"  L2 region     (0x{GBA_L2_DATA_BASE:06X}-0x{GBA_L2_DATA_END:06X}): "
              f"Preserved (original SMA2 data, not modified)")

        if error_flag:
            print("\nFinished with errors.")
            sys.exit(1)
        else:
            print("\nL1 and sprite transplant completed successfully!")


if __name__ == "__main__":
    main()
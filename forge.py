bl_info = {
    "name": "Blender Forge Bridge",
    "author": "Sopitive",
    "version": (0, 3, 15),  # bumped for name-stable label mapping
    "blender": (3, 0, 0),
    "location": "View3D > N-panel > Forge tab, Add (Shift+A) > Forge Objects, File > Export/Import > H2A Forge Objects",
    "description": "Spawn forge objects from a Props scene and export/import them into H2A forge object array via membridge.dll",
    "category": "3D View",
}

import bpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import (
    BoolProperty, StringProperty, PointerProperty, IntProperty, EnumProperty, CollectionProperty
)
from mathutils import Vector, Matrix
import os
import re
import struct
import string
import base64
from ctypes import (
    cdll,
    c_void_p, c_char_p, c_uint64, c_int,
    create_string_buffer,
)

# =============================================================================
# ENUMS
# =============================================================================

TEAM_ITEMS = [
    ("0", "Defender", "Team 0"),
    ("1", "Attacker", "Team 1"),
    ("2", "Team 3",   "Team 2"),
    ("3", "Team 4",   "Team 3"),
    ("4", "Team 5",   "Team 4"),
    ("5", "Team 6",   "Team 5"),
    ("6", "Team 7",   "Team 6"),
    ("7", "Team 8",   "Team 7"),
    ("8", "Neutral Team", "Neutral / no team"),
]

OBJECT_COLOR_ITEMS = [
    ("FF", "Team Color", "Uses team color"),
    ("0", "Red",        "Red"),
    ("1", "Blue",       "Blue"),
    ("2", "Gold",       "Gold"),
    ("3", "Green",      "Green"),
    ("4", "Purple",     "Purple"),
    ("5", "Sea Foam",   "Sea Foam"),
    ("6", "Orange",     "Orange"),
    ("7", "Cyan",       "Cyan"),
]

# ------------------------------
# Object Flags (packed byte @ OFF_OBJECT_FLAGS)
# Bits (right-to-left / LSB->MSB) per your description:
#   bits 0: unused
#   bit  1: NOT place-at-start (0 = place at start TRUE, 1 = FALSE)
#   bits 2-3: symmetry (00 none, 01 symmetric, 10 asymmetric, 11 both)
#   bit  4: unused
#   bit  5: game specific
#   bits 6-7: physics mode (00 normal, 01 fixed, 11 phased)
# ------------------------------

PHYSICS_MODE_ITEMS = [
    ("NORMAL", "Normal", "00 = normal"),
    ("FIXED",  "Fixed",  "01 = fixed"),
    ("PHASED", "Phased", "11 = phased"),
]

SYMMETRY_ITEMS = [
    ("NONE",       "None",       "00 = none"),
    ("SYMMETRIC",  "Symmetric",  "01 = symmetric"),
    ("ASYMMETRIC", "Asymmetric", "10 = asymmetric"),
    ("BOTH",       "Both",       "11 = both"),
]

BOOL_ITEMS = [
    ("0", "False", "False"),
    ("1", "True",  "True"),
]

# ------------------------------
# Passability (packed byte @ OFF_PASS_FLAGS)
# Bits (right-to-left):
#   bit0: players BLOCKED (inverted: 0=players allowed, 1=players not allowed)
#   bit1: land vehicles allowed
#   bit2: heavy vehicles allowed
#   bit3: flying vehicles allowed  (0x08)
#   bit4: projectiles allowed
# ------------------------------

ALLOW_BLOCK_ITEMS = [
    ("ALLOW", "Allow", "Allowed"),
    ("BLOCK", "Block", "Blocked"),
]

# ------------------------------
# Teleporter channel (byte @ OFF_TELE_CHAN)
# 0x00..0x19 = Alpha..Zulu, 0xFF = None
# ------------------------------

_TELE_NAMES = [
    "Alpha","Bravo","Charlie","Delta","Echo","Foxtrot","Golf","Hotel","India","Juliet","Kilo","Lima","Mike",
    "November","Oscar","Papa","Quebec","Romeo","Sierra","Tango","Uniform","Victor","Whiskey","X-ray","Yankee","Zulu"
]

TELEPORTER_CHANNEL_ITEMS = [("255", "(None)", "No teleporter channel")] + [
    (str(i), nm, f"Teleporter channel {nm}") for i, nm in enumerate(_TELE_NAMES)
]

# =============================================================================
# CONFIG
# =============================================================================

maxObjectCount   = 650
propSceneName    = "Props"
paletteRootName  = "Awash Palette"

ENTRY_STRIDE = 0x4C  # 76 bytes

OFF_POS = 0x06
OFF_FWD = 0x12
OFF_UP  = 0x1E

OFF_F32_A          = 0x2A
OFF_U16_FFFF       = 0x2E
OFF_U16_TYPECONST  = 0x30

OFF_PRE_FLAGS_BYTE = 0x3B

OFF_OBJECT_FLAGS  = 0x3C
OFF_CAN_DESPAWN   = 0x3D
OFF_TEAM_INDEX    = 0x3E
OFF_SPAWN_TIME    = 0x3F

OFF_OBJECT_COLOR  = 0x40
OFF_SPAWN_SEQ     = 0x41
OFF_TIMER_USER    = 0x42
OFF_SPAWN_CHAN    = 0x43

OFF_LABEL_1       = 0x44
OFF_LABEL_2       = 0x45
OFF_LABEL_3       = 0x46
OFF_LABEL_4       = 0x47
OFF_TELE_CHAN     = 0x48
OFF_PASS_FLAGS    = 0x49

OFF_TAIL_FLAG = ENTRY_STRIDE - 2

LABEL_BLOB_SIZE = 0x120A
LABEL_BLOB_BACK = 0x120A

# Label dropdown uses NAME identifiers (stable across refresh / gametypes)
LABEL_NONE_ID = "__NONE__"

# =============================================================================
# Object type mapping (TOP + SUB + PRE 0x3B)
# =============================================================================

OBJECT_TYPE_INFO = {
    "Teleporter, Sender":     (0x2A, 0x00, 0x0E),
    "Teleporter, Receiver":   (0x2A, 0x01, 0x0F),
    "Teleporter, Two Way":    (0x2A, 0x02, 0x0D),

    "Grass Plane":       (0x4D, 0x09, 0x00),
    "Block, 5x5, Flat":       (0x50, 0x1A, 0x00),
    "Block, 10x10, Flat":     (0x50, 0x1E, 0x00),
    "Pyramid":     (0x52, 0x06, 0x00),

    "Wall, Coliseum":         (0x54, 0x0B, 0x00),
    "Grid":                   (0x56, 0x00, 0x00),

    "Mongoose":                (0x1C, 0x00, 0x00),
    "Warthog":                (0x1D, 0x00, 0x00),
    "Scorpion":               (0x1E, 0x00, 0x00),
    "Respawn Point":          (0x34, 0x00, 0x10),
    "Initial Spawn Point":    (0x33, 0x00, 0x10),
}

# Reverse lookup: (top, sub, pre 0x3B) -> name
TYPEKEY_TO_NAME = {}
TYPEKEY_TO_NAME_LOOSE = {}  # optional: ignore pre3b
for nm, info in OBJECT_TYPE_INFO.items():
    if len(info) == 2:
        top, sub = info
        pre = 0
    else:
        top, sub, pre = info
    TYPEKEY_TO_NAME[(int(top)&0xFF, int(sub)&0xFF, int(pre)&0xFF)] = nm
    TYPEKEY_TO_NAME_LOOSE[(int(top)&0xFF, int(sub)&0xFF)] = nm

UNMAPPED_TEMPLATE = "__UNMAPPED__"

OBJECT_TYPE_ITEMS = [(k, k, "") for k in OBJECT_TYPE_INFO.keys()]

DEFAULT_OBJECT_TYPE = "Block, 5x5, Flat"
if DEFAULT_OBJECT_TYPE not in OBJECT_TYPE_INFO:
    DEFAULT_OBJECT_TYPE = next(iter(OBJECT_TYPE_INFO.keys()), "")

DEFAULT_PRE_FLAGS_BY_TYPE = {
    0x34: 0x00,    # Respawn Point
    0x33: 0x00,    # Initial Spawn Point
}

def _default_pre_flags_for_type(top_id: int) -> int:
    return int(DEFAULT_PRE_FLAGS_BY_TYPE.get(int(top_id) & 0xFF, 0)) & 0xFF

# =============================================================================
# Helpers
# =============================================================================

def _get_type_info(name: str):
    info = OBJECT_TYPE_INFO.get((name or "").strip(), None)
    if not info:
        return None
    if len(info) == 2:
        top_id, sub_id = info
        return (top_id, sub_id, 0x00)
    top_id, sub_id, pre3b = info
    return (top_id, sub_id, pre3b & 0xFF)

def _pack_u16(v: int) -> bytes:
    return struct.pack("<H", int(v) & 0xFFFF)

def _pack_f32(f: float) -> bytes:
    return struct.pack("<f", float(f))

def _write_float3_unaligned(buf: bytearray, off: int, v: Vector):
    buf[off+0:off+4]   = _pack_f32(v.x)
    buf[off+4:off+8]   = _pack_f32(v.y)
    buf[off+8:off+12]  = _pack_f32(v.z)

def _clamp_u8(v: int) -> int:
    try:
        v = int(v)
    except:
        v = 0
    if v < 0: v = 0
    if v > 255: v = 255
    return v

def _write_u8(buf: bytearray, off: int, v: int):
    buf[off] = _clamp_u8(v)

def _to_u8_twos_complement(v: int) -> int:
    try:
        v = int(v)
    except:
        v = 0
    return v & 0xFF

def _write_s8(buf: bytearray, off: int, v: int):
    buf[off] = _to_u8_twos_complement(v)

def _set_tail_flag(buf: bytearray, is_more_after: bool):
    buf[OFF_TAIL_FLAG:OFF_TAIL_FLAG+2] = (b"\x01\x00" if is_more_after else b"\x00\x00")

def _s8_to_u8(v: int) -> int:
    try:
        v = int(v)
    except:
        v = 0
    return v & 0xFF

def _clamp_s8_timer(v: int) -> int:
    try:
        s = int(v)
    except:
        s = 0
    if s < -127: s = -127
    if s > 127:  s = 127
    return s

def _parse_u8_auto(s) -> int:
    if isinstance(s, int):
        return s & 0xFF
    t = (str(s) or "").strip()
    if not t:
        return 0
    try:
        if t.lower().startswith("0x"):
            return int(t, 16) & 0xFF
        if any(ch in t.lower() for ch in "abcdef"):
            return int(t, 16) & 0xFF
        return int(t, 10) & 0xFF
    except:
        return 0

def _is_mostly_printable(s: str) -> bool:
    if not s:
        return False
    ok = sum((ch in string.printable and ch not in "\r\t") for ch in s)
    return ok >= max(3, int(len(s) * 0.85))

def parse_forge_labels_from_blob(blob: bytes):
    parts = blob.split(b"\x00")
    out = []
    for p in parts:
        if not p:
            continue
        try:
            s = p.decode("ascii", errors="ignore").strip()
        except:
            continue
        s = s.strip()
        if len(s) < 2:
            continue
        if _is_mostly_printable(s):
            out.append(s)

    seen = set()
    uniq = []
    for s in out:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq

# =============================================================================
# Label blob snapshot helpers (optional - keeps labels stable even if memory labels change)
# =============================================================================

def _b64_from_bytes(b: bytes) -> str:
    if not b:
        return ""
    return base64.b64encode(b).decode("ascii")

def _bytes_from_b64(s: str) -> bytes:
    try:
        if not s:
            return b""
        return base64.b64decode(s.encode("ascii"), validate=False)
    except:
        return b""

def _label_blob_addr_from_forge_array(forge_array_addr: int) -> int:
    return int(forge_array_addr) - int(LABEL_BLOB_BACK)

# =============================================================================
# Unmapped type tracking
# =============================================================================

UNMAPPED_KEY = "h2a_unmapped"           # bool-ish
UNMAPPED_TOP = "h2a_unmapped_top"       # int
UNMAPPED_SUB = "h2a_unmapped_sub"       # int
UNMAPPED_3B  = "h2a_unmapped_pre3b"     # int

def mark_unmapped(obj, top_id: int, sub_id: int, pre3b: int):
    if not obj:
        return
    obj[UNMAPPED_KEY] = True
    obj[UNMAPPED_TOP] = int(top_id) & 0xFF
    obj[UNMAPPED_SUB] = int(sub_id) & 0xFF
    obj[UNMAPPED_3B]  = int(pre3b) & 0xFF

def clear_unmapped(obj):
    if not obj:
        return
    if UNMAPPED_KEY in obj: del obj[UNMAPPED_KEY]
    if UNMAPPED_TOP in obj: del obj[UNMAPPED_TOP]
    if UNMAPPED_SUB in obj: del obj[UNMAPPED_SUB]
    if UNMAPPED_3B  in obj: del obj[UNMAPPED_3B]

def is_unmapped(obj) -> bool:
    try:
        return bool(obj and obj.get(UNMAPPED_KEY, False))
    except:
        return False

def get_export_type_triple(obj):
    """
    Returns (top_id, sub_id, pre3b) that should be used for export.
    - If object is marked unmapped -> use stored raw triple from import.
    - Else -> derive from template_name mapping.
    """
    if obj and is_unmapped(obj):
        try:
            top_id = int(obj.get(UNMAPPED_TOP, 0xFF)) & 0xFF
            sub_id = int(obj.get(UNMAPPED_SUB, 0x00)) & 0xFF
            pre3b  = int(obj.get(UNMAPPED_3B,  0x00)) & 0xFF
            return (top_id, sub_id, pre3b)
        except:
            pass

    try:
        info = _get_type_info(obj.h2a_forge.template_name)
        if not info:
            return None
        top_id, sub_id, pre3b_default = info
        return (int(top_id) & 0xFF, int(sub_id) & 0xFF, int(obj.h2a_forge.pre_flags_byte) & 0xFF)
    except:
        return None

def _is_scale_label_name(name: str) -> bool:
    return "scale" in (name or "").strip().lower()

# =============================================================================
# Import helpers (decode entry -> Blender object)
# =============================================================================

def _u8_from(b: bytes, off: int) -> int:
    return int(b[off]) & 0xFF

def _s8_from(b: bytes, off: int) -> int:
    v = int(b[off]) & 0xFF
    return v - 256 if v >= 128 else v

def _u16_from(b: bytes, off: int) -> int:
    return int.from_bytes(b[off:off+2], "little", signed=False) & 0xFFFF

def _f32_from(b: bytes, off: int) -> float:
    try:
        return struct.unpack_from("<f", b, off)[0]
    except:
        return 0.0

def _vec3_from(b: bytes, off: int) -> Vector:
    return Vector((_f32_from(b, off+0), _f32_from(b, off+4), _f32_from(b, off+8)))

def _entry_is_empty(b: bytes) -> bool:
    if not b or len(b) < 6:
        return True
    return (b[0] == 0xFF and b[1] == 0xFF and b[2] == 0xFF and b[3] == 0xFF and b[4] == 0xFF and b[5] == 0xFF)

def _entry_has_more_after(b: bytes) -> bool:
    if not b or len(b) < ENTRY_STRIDE:
        return False
    return b[OFF_TAIL_FLAG] == 0x01

def _build_reverse_type_maps():
    exact = {}
    by_pair = {}
    for nm, info in OBJECT_TYPE_INFO.items():
        try:
            if len(info) == 2:
                top_id, sub_id = info
                pre3b = 0
            else:
                top_id, sub_id, pre3b = info
            top_id &= 0xFF
            sub_id &= 0xFF
            pre3b &= 0xFF
            exact[(top_id, sub_id, pre3b)] = nm
            by_pair.setdefault((top_id, sub_id), []).append((pre3b, nm))
        except:
            pass
    return exact, by_pair

_TYPE_EXACT, _TYPE_BY_PAIR = _build_reverse_type_maps()

def _resolve_template_name_from_ids(top_id: int, sub_id: int, pre3b: int) -> str:
    top_id &= 0xFF
    sub_id &= 0xFF
    pre3b &= 0xFF

    nm = _TYPE_EXACT.get((top_id, sub_id, pre3b), None)
    if nm:
        return nm

    cands = _TYPE_BY_PAIR.get((top_id, sub_id), [])
    if len(cands) == 1:
        return cands[0][1]
    if cands:
        cands_sorted = sorted(cands, key=lambda t: t[0])
        return cands_sorted[0][1]
    return ""

def _ensure_import_collection(scene: bpy.types.Scene, name: str = "Imported Forge"):
    coll = bpy.data.collections.get(name, None)
    if not coll:
        coll = bpy.data.collections.new(name)
        scene.collection.children.link(coll)
    return coll

def _make_placeholder_mesh(name: str):
    mesh = bpy.data.meshes.new(name + "_mesh")
    verts = [
        (-0.5,-0.5,-0.5), (0.5,-0.5,-0.5), (0.5,0.5,-0.5), (-0.5,0.5,-0.5),
        (-0.5,-0.5, 0.5), (0.5,-0.5, 0.5), (0.5,0.5, 0.5), (-0.5,0.5, 0.5),
    ]
    faces = [
        (0,1,2,3),
        (4,5,6,7),
        (0,1,5,4),
        (1,2,6,5),
        (2,3,7,6),
        (3,0,4,7),
    ]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh

def _create_object_from_template_or_placeholder(context, template_name: str, top_id: int, sub_id: int, pre3b: int):
    if template_name and template_name in OBJECT_TYPE_INFO:
        try:
            return createForgeObject(context, template_name)
        except:
            pass

    coll = _ensure_import_collection(context.scene)
    mesh = _make_placeholder_mesh(f"Unknown_{top_id:02X}_{sub_id:02X}")
    obj = bpy.data.objects.new(f"Unknown_{top_id:02X}_{sub_id:02X}_{pre3b:02X}", mesh)
    coll.objects.link(obj)

    mark_as_forge_object(obj)
    obj.h2a_forge.template_name = DEFAULT_OBJECT_TYPE
    obj.h2a_forge.pre_flags_byte = int(pre3b) & 0xFF

    obj["h2a_import_top_id"] = int(top_id) & 0xFF
    obj["h2a_import_sub_id"] = int(sub_id) & 0xFF
    obj["h2a_import_pre3b"]  = int(pre3b) & 0xFF
    obj["h2a_import_unknown"] = True

    return obj

def _u8_color_to_enum(u: int) -> str:
    u = int(u) & 0xFF
    return "FF" if u == 0xFF else str(u)

def _tele_chan_to_enum(u: int) -> str:
    u = int(u) & 0xFF
    if u == 0xFF:
        return "255"
    if 0 <= u <= 25:
        return str(u)
    return "255"

# =============================================================================
# Label model + name-based mapping (stable across gametypes)
# =============================================================================

class H2AForgeLabelItem(PropertyGroup):
    name: StringProperty(name="Name", default="")
    index: IntProperty(name="Index", default=0, min=0)

def _find_label_index_by_name(context, label_name: str) -> int:
    """
    Returns current index for a label NAME in the current scene label table.
    Case-insensitive match.
    If not found or name is empty/None -> 0xFF.
    """
    nm = (label_name or "").strip()
    if not nm or nm == LABEL_NONE_ID:
        return 0xFF

    try:
        sp = context.scene.h2a_forge
        for it in sp.forge_labels:
            try:
                itnm = str(it.name or "").strip()
                if itnm and itnm.lower() == nm.lower():
                    return int(it.index) & 0xFF
            except:
                continue
    except:
        pass
    return 0xFF

def _find_label_name_by_index(context, idx_u8: int) -> str:
    if (int(idx_u8) & 0xFF) == 0xFF:
        return ""
    try:
        sp = context.scene.h2a_forge
        for it in sp.forge_labels:
            if (int(it.index) & 0xFF) == (int(idx_u8) & 0xFF):
                return str(it.name or "").strip()
    except:
        pass
    return ""

def _label_items_from_scene(context):
    # Enum identifiers are LABEL NAMES (stable) not indices.
    items = [(LABEL_NONE_ID, "(No Label)", "No Label (0xFF)")]
    if not context:
        return items

    try:
        sp = context.scene.h2a_forge
    except:
        return items

    temp = []
    for it in sp.forge_labels:
        try:
            idx = int(it.index) & 0xFF
            nm = str(it.name or "").strip()
        except:
            continue
        if idx == 0xFF:
            continue
        if not nm:
            continue
        temp.append((idx, nm))

    temp.sort(key=lambda t: (t[1].lower(), t[0]))

    for idx, nm in temp:
        # Identifier = name, so dropdown won't change when indices reorder.
        items.append((nm, nm, f"Forge Label '{nm}' (current index {idx})"))
    return items

def genForgeLabelEnumItems(self, context):
    return _label_items_from_scene(context)

def _label_enum_to_name(enum_str: str) -> str:
    s = (enum_str or "").strip()
    if not s or s == LABEL_NONE_ID:
        return ""
    return s

def _label_name_to_enum(label_name: str) -> str:
    nm = (label_name or "").strip()
    return LABEL_NONE_ID if not nm else nm

def _rebind_object_label_enums_from_names(context, obj):
    """
    After label refresh, re-apply enum selections from stored label_name_* so
    the dropdown doesn't "clobber" just because indices changed.
    """
    if not obj or not hasattr(obj, "h2a_forge"):
        return
    p = obj.h2a_forge
    for i in (1, 2, 3, 4):
        name_attr = f"label_name_{i}"
        enum_attr = f"label_enum_{i}"
        nm = (getattr(p, name_attr, "") or "").strip()
        target = _label_name_to_enum(nm)
        try:
            setattr(p, enum_attr, target)
        except:
            # If label name isn't present in the current label list,
            # fall back to none (keeps selection stable conceptually)
            try:
                setattr(p, enum_attr, LABEL_NONE_ID)
            except:
                pass

def _rebind_all_objects_after_label_refresh(context):
    try:
        for obj in context.scene.objects:
            if is_forge_object(obj) and hasattr(obj, "h2a_forge"):
                _rebind_object_label_enums_from_names(context, obj)
    except:
        pass

# =============================================================================
# Scaling logic
# =============================================================================

def recursive_330x(i, scale=100):
    for _ in range(i):
        scale += scale // 33 + scale // 228
    return scale

def spawnSeqToScale(spawnSequence, convention='47X', team='NONE'):
    one_percent_seq = -20 if convention == '330X' else -10
    if spawnSequence == one_percent_seq:
        return 0.01

    if convention == '1X':
        scale = 1
    elif convention == '33X':
        scale = 0.1 * spawnSequence
        if spawnSequence < -10:
            scale *= -2
            if spawnSequence > -81:
                scale += 8
            elif spawnSequence < -80:
                scale = 2 * scale - 8
        scale += 1
    elif convention == '71X':
        scale = 0.1 * spawnSequence
        if spawnSequence < -10:
            scale *= -4
            if spawnSequence > -81:
                scale += 6
            elif spawnSequence < -80:
                scale = 4 * scale - 90
        scale += 1
    elif convention == '47X':
        scale = spawnSequence
        lthn10 = spawnSequence < -10
        gthn71 = spawnSequence > -71
        gthn41 = spawnSequence > -41
        if lthn10:
            scale = 2 * (scale + 101)
            if gthn71:
                scale *= 3 if gthn41 else 2
        scale = 10 * scale + 100
        if lthn10:
            scale += 1000
            if gthn71:
                scale -= 1800 if gthn41 else 600
        scale *= 0.01
    elif convention == '330X':
        scale = 100
        i = spawnSequence
        if spawnSequence < 0:
            i *= 5
            scale += i
            if spawnSequence <= -20:
                i = spawnSequence + 201
                if spawnSequence == -20:
                    scale = 1
        if spawnSequence < -20 or spawnSequence > 0:
            if team == 'RED':
                scale = recursive_330x(i, scale=32732)
            else:
                scale = recursive_330x(i, scale=100)
        scale *= 0.01
    else:
        scale = 1.0
    return scale

COSMIC_DEFENDERS_TEAM_VALUE = 0

def _timer_to_scale_factor_330x(timer_s8: int, team_enum_value: int) -> float:
    s = int(timer_s8)
    if s < -128: s = -128
    if s > 127:  s = 127
    team_flag = 'RED' if int(team_enum_value) == COSMIC_DEFENDERS_TEAM_VALUE else 'NONE'
    return float(spawnSeqToScale(s, convention='330X', team=team_flag))

def _any_selected_label_is_scale(context, p) -> bool:
    for attr in ("label_enum_1", "label_enum_2", "label_enum_3", "label_enum_4"):
        nm = _label_enum_to_name(getattr(p, attr, LABEL_NONE_ID))
        if _is_scale_label_name(nm):
            return True
    return False

def apply_scale_preview_if_needed(context, obj):
    if not obj or not hasattr(obj, "h2a_forge"):
        return
    p = obj.h2a_forge

    if not _any_selected_label_is_scale(context, p):
        if obj.get("h2a_scaled_preview", False) and "h2a_base_scale" in obj:
            bs = obj["h2a_base_scale"]
            obj.scale = (bs[0], bs[1], bs[2])
            obj["h2a_scaled_preview"] = False
        return

    if "h2a_base_scale" not in obj:
        obj["h2a_base_scale"] = [float(obj.scale.x), float(obj.scale.y), float(obj.scale.z)]

    factor = _timer_to_scale_factor_330x(p.timer_user_data, int(p.team_enum))
    bs = obj["h2a_base_scale"]
    obj.scale = (bs[0] * factor, bs[1] * factor, bs[2] * factor)
    obj["h2a_scaled_preview"] = True

def _on_timer_user_data_update(self, context):
    apply_scale_preview_if_needed(context, context.object)

def _on_team_enum_update(self, context):
    apply_scale_preview_if_needed(context, context.object)

def _on_label_enum_update(self, context):
    """
    Update callback for label dropdowns:
    - Persist the selected LABEL NAME into label_name_1..4
    - Apply scale preview (since it depends on name "scale")
    """
    try:
        # Determine which PropertyGroup instance we are (object props)
        p = self
        # Keep label_name_* aligned with label_enum_*
        for i in (1, 2, 3, 4):
            enum_attr = f"label_enum_{i}"
            name_attr = f"label_name_{i}"
            try:
                sel = getattr(p, enum_attr, LABEL_NONE_ID)
            except:
                sel = LABEL_NONE_ID
            nm = _label_enum_to_name(sel)
            try:
                setattr(p, name_attr, nm)
            except:
                pass
        apply_scale_preview_if_needed(context, context.object)
    except:
        pass

COSMIC_FORCE_DEFENDERS_TEAM = True
COSMIC_RED_COLOR_VALUE = 1

def is_red_team_cosmic(p) -> bool:
    try:
        return _parse_u8_auto(p.object_color_enum) == COSMIC_RED_COLOR_VALUE
    except:
        return False

def resolve_team_byte(p) -> int:
    try:
        team = int(p.team_enum)
    except:
        team = 0

    if COSMIC_FORCE_DEFENDERS_TEAM and is_red_team_cosmic(p):
        return COSMIC_DEFENDERS_TEAM_VALUE
    return team

# =============================================================================
# Packed flag builders
# =============================================================================

def pack_object_flags(p) -> int:
    phys = (p.physics_mode_enum or "PHASED").upper()
    if phys == "NORMAL":
        phys_bits = 0
    elif phys == "FIXED":
        phys_bits = 1
    else:
        phys_bits = 3

    sym = (p.symmetry_enum or "BOTH").upper()
    if sym == "NONE":
        sym_bits = 0
    elif sym == "SYMMETRIC":
        sym_bits = 1
    elif sym == "ASYMMETRIC":
        sym_bits = 2
    else:
        sym_bits = 3

    try:
        gs = 1 if int(p.game_specific_enum) else 0
    except:
        gs = 0

    try:
        pas = 1 if int(p.place_at_start_enum) else 0
    except:
        pas = 1

    not_pas = 0 if pas else 1

    b = 0
    b |= (phys_bits & 0x3) << 6
    b |= (gs & 0x1) << 5
    b |= (sym_bits & 0x3) << 2
    b |= (not_pas & 0x1) << 1
    return b & 0xFF

def pack_passability_flags(p) -> int:
    b = 0
    if (p.pass_players_enum or "ALLOW") == "BLOCK":
        b |= 0x01
    if (p.pass_land_enum or "BLOCK") == "ALLOW":
        b |= 0x02
    if (p.pass_heavy_enum or "BLOCK") == "ALLOW":
        b |= 0x04
    if (p.pass_flying_enum or "BLOCK") == "ALLOW":
        b |= 0x08
    if (p.pass_projectiles_enum or "BLOCK") == "ALLOW":
        b |= 0x10
    return b & 0xFF

# =============================================================================
# membridge.dll wrapper
# =============================================================================

class MemBridge:
    def __init__(self):
        self.dll = None
        self.hproc = None
        self.dll_path = ""
        self._has_write_force = False
        self._has_set_total = False

    def _candidate_paths(self):
        blend_dir = bpy.path.abspath("//")
        if blend_dir:
            yield os.path.join(blend_dir, "membridge.dll")
        try:
            addon_dir = os.path.dirname(os.path.realpath(__file__))
            yield os.path.join(addon_dir, "membridge.dll")
        except:
            pass
        yield os.path.join(os.getcwd(), "membridge.dll")

    def load(self):
        if self.dll:
            return True

        dll_path = None
        tried = []
        for p in self._candidate_paths():
            tried.append(p)
            if os.path.exists(p):
                dll_path = p
                break

        if not dll_path:
            raise RuntimeError("membridge.dll not found. Tried:\n  " + "\n  ".join(tried))

        self.dll_path = dll_path
        self.dll = cdll.LoadLibrary(dll_path)

        self.dll.mb_open_process_by_name.argtypes = [c_char_p]
        self.dll.mb_open_process_by_name.restype = c_void_p

        self.dll.mb_close_handle.argtypes = [c_void_p]
        self.dll.mb_close_handle.restype = c_int

        self.dll.mb_read.argtypes = [c_void_p, c_uint64, c_void_p, c_int]
        self.dll.mb_read.restype = c_int

        try:
            self.dll.mb_write_force.argtypes = [c_void_p, c_uint64, c_void_p, c_int]
            self.dll.mb_write_force.restype = c_int
            self._has_write_force = True
        except Exception:
            self._has_write_force = False

        self.dll.mb_write.argtypes = [c_void_p, c_uint64, c_void_p, c_int]
        self.dll.mb_write.restype = c_int

        self.dll.mb_get_forge_object_array.argtypes = [c_void_p]
        self.dll.mb_get_forge_object_array.restype  = c_uint64

        self._has_set_total = False
        try:
            self.dll.mb_set_forge_object_total_exported.argtypes = [c_void_p, c_int]
            self.dll.mb_set_forge_object_total_exported.restype  = c_int
            self._has_set_total = True
        except Exception:
            self._has_set_total = False

        return True

    def open_process(self, exe_name: str):
        self.load()
        if self.hproc:
            return True

        h = self.dll.mb_open_process_by_name(exe_name.encode("ascii", errors="ignore"))
        if not h:
            raise RuntimeError(
                f"OpenProcess failed for '{exe_name}'.\n"
                f"Try running Blender with same elevation as the game."
            )
        self.hproc = h
        return True

    def close(self):
        if self.dll and self.hproc:
            try:
                self.dll.mb_close_handle(self.hproc)
            except:
                pass
        self.hproc = None

    def write(self, addr: int, data: bytes) -> bool:
        buf = create_string_buffer(data, len(data))
        if self._has_write_force:
            ok = self.dll.mb_write_force(self.hproc, c_uint64(addr), buf, c_int(len(data)))
        else:
            ok = self.dll.mb_write(self.hproc, c_uint64(addr), buf, c_int(len(data)))
        return ok == 1

    def read(self, addr: int, size: int) -> bytes:
        buf = create_string_buffer(size)
        ok = self.dll.mb_read(self.hproc, c_uint64(addr), buf, c_int(size))
        if ok != 1:
            return b""
        return buf.raw

    def get_forge_object_array(self) -> int:
        v = self.dll.mb_get_forge_object_array(self.hproc)
        return int(v) if v else 0

    def set_forge_object_total(self, exported_count: int) -> bool:
        if not self._has_set_total:
            return False
        try:
            rc = self.dll.mb_set_forge_object_total_exported(self.hproc, c_int(int(exported_count)))
            return rc == 1
        except:
            return False

g_mb = MemBridge()

# =============================================================================
# Props palette traversal + spawn
# =============================================================================

def get_props_scene():
    return bpy.data.scenes.get(propSceneName, None)

def get_palette_root_collection():
    return bpy.data.collections.get(paletteRootName, None)

iconDict = {}

def fillIconDict(collection):
    global iconDict
    for coll in collection.children:
        if len(coll.objects) > 0:
            ico = 'NONE'
            try:
                ico = coll.forge.icon
            except:
                pass
            if iconDict.get(coll, None) is None:
                iconDict[coll] = ico
        else:
            fillIconDict(coll)

def getCollectionEnums(collection, out_list):
    global iconDict
    for coll in collection.children:
        if len(coll.objects) > 0:
            out_list.append((coll.name, coll.name, "", iconDict.get(coll, 'NONE'), len(out_list)))
        else:
            getCollectionEnums(coll, out_list)
    return out_list

def genObjectTypesEnum(self, context):
    root = get_palette_root_collection()
    if not root:
        return []
    return getCollectionEnums(root, [])

def mark_as_forge_object(obj):
    obj["isForgeObject"] = True

def is_forge_object(obj) -> bool:
    return bool(obj and obj.get("isForgeObject", False))

def createForgeObject(context, leafCollectionName: str):
    props_scene = get_props_scene()
    if not props_scene:
        raise RuntimeError(f"Props scene '{propSceneName}' not found.")

    leaf = bpy.data.collections.get(leafCollectionName, None)
    if not leaf or len(leaf.objects) == 0:
        raise RuntimeError(f"Palette collection '{leafCollectionName}' not found or empty.")

    src = leaf.objects[0]

    new_data = src.data.copy() if src.data else None
    new_obj = bpy.data.objects.new(src.name, new_data)

    context.scene.collection.objects.link(new_obj)

    new_obj.location = context.scene.cursor.location
    new_obj.rotation_euler = (0.0, 0.0, 0.0)
    new_obj.scale = src.scale

    mark_as_forge_object(new_obj)

    if leafCollectionName in OBJECT_TYPE_INFO:
        new_obj.h2a_forge.template_name = leafCollectionName
    else:
        new_obj.h2a_forge.template_name = DEFAULT_OBJECT_TYPE

    info = OBJECT_TYPE_INFO.get(new_obj.h2a_forge.template_name, None)
    pre3b_default = 0
    if info:
        try:
            if len(info) >= 3:
                pre3b_default = int(info[2]) & 0xFF
            else:
                pre3b_default = 0
        except:
            pre3b_default = 0
    new_obj.h2a_forge.pre_flags_byte = pre3b_default

    try:
        clear_unmapped(new_obj)
    except:
        pass

    # Default labels to None (by name)
    try:
        new_obj.h2a_forge.label_name_1 = ""
        new_obj.h2a_forge.label_name_2 = ""
        new_obj.h2a_forge.label_name_3 = ""
        new_obj.h2a_forge.label_name_4 = ""
        new_obj.h2a_forge.label_enum_1 = LABEL_NONE_ID
        new_obj.h2a_forge.label_enum_2 = LABEL_NONE_ID
        new_obj.h2a_forge.label_enum_3 = LABEL_NONE_ID
        new_obj.h2a_forge.label_enum_4 = LABEL_NONE_ID
    except:
        pass

    bpy.ops.object.select_all(action='DESELECT')
    new_obj.select_set(True)
    context.view_layer.objects.active = new_obj
    return new_obj

class AddForgeObject(Operator):
    bl_idname = "h2a_forge.add_object"
    bl_label = "Forge Object"
    bl_property = "objectType"
    bl_options = {"REGISTER", "UNDO"}

    objectType: bpy.props.EnumProperty(name="Object Type", items=genObjectTypesEnum)

    def invoke(self, context, event):
        root = get_palette_root_collection()
        if not root:
            self.report({"ERROR"}, f"Palette root '{paletteRootName}' not found.")
            return {"CANCELLED"}
        fillIconDict(root)
        context.window_manager.invoke_search_popup(self)
        return {"FINISHED"}

    def execute(self, context):
        if context.scene.name == propSceneName:
            self.report({"ERROR"}, "You are in the Props scene. Switch to your working scene to place objects.")
            return {"CANCELLED"}
        try:
            createForgeObject(context, self.objectType)
            bpy.ops.ed.undo_push()
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

def addForgeObjectMenuItem(self, context):
    layout = self.layout
    layout.operator_context = 'INVOKE_DEFAULT'
    layout.operator(AddForgeObject.bl_idname, icon='ADD')

def _on_template_name_update(self, context):
    try:
        info = _get_type_info(self.template_name)
        if not info:
            return
        _, _, pre3b_default = info
        self.pre_flags_byte = int(pre3b_default) & 0xFF
        try:
            apply_scale_preview_if_needed(context, context.object)
        except:
            pass
    except:
        pass

# =============================================================================
# Properties / Sidebar Panel
# =============================================================================

class H2AForgeObjectProps(PropertyGroup):
    template_name: EnumProperty(
        name="Object Type",
        description="Forge object type (controls exported top/sub indices + default 0x3B)",
        items=OBJECT_TYPE_ITEMS,
        default=DEFAULT_OBJECT_TYPE,
        update=_on_template_name_update,
    )

    pre_flags_byte: IntProperty(
        name="Pre-Flags Byte (0x3B)",
        description="Byte at offset 0x3B (right before Object Flags). Some objects use this as subtype/behavior.",
        default=0,
        min=0,
        max=255,
    )

    physics_mode_enum: EnumProperty(
        name="Physics",
        description="Physics mode (packed into Object Flags byte)",
        items=PHYSICS_MODE_ITEMS,
        default="PHASED",
    )
    game_specific_enum: EnumProperty(
        name="Game Specific",
        description="Game-specific toggle (packed into Object Flags byte)",
        items=BOOL_ITEMS,
        default="1",
    )
    symmetry_enum: EnumProperty(
        name="Symmetry",
        description="Symmetry (packed into Object Flags byte)",
        items=SYMMETRY_ITEMS,
        default="BOTH",
    )
    place_at_start_enum: EnumProperty(
        name="Place At Start",
        description="Place-at-start (packed into Object Flags byte). True means bit1=0.",
        items=BOOL_ITEMS,
        default="1",
    )

    can_despawn: IntProperty(name="Can Despawn", default=0, min=0, max=255)

    team_enum: EnumProperty(
        name="Team",
        description="Forge team assignment",
        items=TEAM_ITEMS,
        default="8",
        update=_on_team_enum_update,
    )

    spawn_time: IntProperty(name="Spawn Time", default=0, min=0, max=255)

    object_color_enum: EnumProperty(
        name="Object Color",
        description="Forge object color override",
        items=OBJECT_COLOR_ITEMS,
        default="FF",
    )

    spawn_sequence: IntProperty(
        name="Spawn Sequence",
        default=0,
        min=-128,
        max=127,
    )

    timer_user_data: IntProperty(
        name="Timer/User (Scale)",
        default=0,
        min=-127,
        max=127,
        update=_on_timer_user_data_update,
    )

    spawn_channel: IntProperty(name="Spawn Channel", default=0xFF, min=0, max=255)

    # Persisted label NAMEs (stable across label refresh / gametypes)
    label_name_1: StringProperty(name="LabelName1", default="")
    label_name_2: StringProperty(name="LabelName2", default="")
    label_name_3: StringProperty(name="LabelName3", default="")
    label_name_4: StringProperty(name="LabelName4", default="")

    # Dropdowns are NAME identifiers (not indices)
    label_enum_1: EnumProperty(name="Label 1", items=genForgeLabelEnumItems, update=_on_label_enum_update)
    label_enum_2: EnumProperty(name="Label 2", items=genForgeLabelEnumItems, update=_on_label_enum_update)
    label_enum_3: EnumProperty(name="Label 3", items=genForgeLabelEnumItems, update=_on_label_enum_update)
    label_enum_4: EnumProperty(name="Label 4", items=genForgeLabelEnumItems, update=_on_label_enum_update)

    teleporter_channel_enum: EnumProperty(
        name="Teleporter Channel",
        description="Teleporter channel byte (Alpha..Zulu, None=0xFF)",
        items=TELEPORTER_CHANNEL_ITEMS,
        default="255",
    )

    pass_players_enum: EnumProperty(name="Players", items=ALLOW_BLOCK_ITEMS, default="ALLOW")
    pass_flying_enum: EnumProperty(name="Flying Vehicles", items=ALLOW_BLOCK_ITEMS, default="BLOCK")
    pass_heavy_enum: EnumProperty(name="Heavy Vehicles", items=ALLOW_BLOCK_ITEMS, default="BLOCK")
    pass_land_enum: EnumProperty(name="Land Vehicles", items=ALLOW_BLOCK_ITEMS, default="BLOCK")
    pass_projectiles_enum: EnumProperty(name="Projectiles", items=ALLOW_BLOCK_ITEMS, default="BLOCK")

class H2AForgeSceneProps(PropertyGroup):
    target_exe: StringProperty(name="Target EXE", default="MCC-Win64-Shipping.exe")
    confirm_overwrite: BoolProperty(name="I understand this overwrites forge memory", default=False)
    forge_labels: CollectionProperty(type=H2AForgeLabelItem)

    # Optional: snapshot of labels blob captured on import (lets you restore exact label table if desired)
    imported_label_blob_b64: StringProperty(name="Imported Label Blob (b64)", default="")
    imported_label_blob_size: IntProperty(name="Imported Label Blob Size", default=0, min=0)

    # Import options
    import_clear_existing: BoolProperty(
        name="Clear Existing Imported",
        default=True,
        description="Delete existing forge objects in the scene before importing from memory",
    )
    import_limit: IntProperty(
        name="Import Max",
        default=maxObjectCount,
        min=1,
        max=maxObjectCount,
        description="Maximum number of slots to scan while importing",
    )

class VIEW3D_PT_h2a_forge_sidebar(Panel):
    bl_label = "H2A Forge"
    bl_idname = "VIEW3D_PT_h2a_forge_sidebar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Forge"

    def draw(self, context):
        layout = self.layout
        sp = context.scene.h2a_forge

        layout.prop(sp, "target_exe")
        layout.prop(sp, "confirm_overwrite")

        row = layout.row(align=True)
        row.operator("h2a_forge.export_memory", icon="EXPORT")
        row.operator("h2a_forge.import_memory", icon="IMPORT")

        row2 = layout.row(align=True)
        row2.operator("h2a_forge.refresh_labels", icon="FILE_REFRESH")

        layout.separator()
        boxi = layout.box()
        boxi.label(text="Import Options")
        boxi.prop(sp, "import_clear_existing")
        boxi.prop(sp, "import_limit")

        layout.separator()
        o = context.active_object
        if o and is_forge_object(o):
            layout.label(text="Selected Forge Object")
            layout.prop(o.h2a_forge, "template_name")

            box = layout.box()
            box.label(text="Mapped Fields")
            col = box.column(align=True)

            col.prop(o.h2a_forge, "pre_flags_byte")

            col.separator()
            col.label(text="Object Flags")
            col.prop(o.h2a_forge, "physics_mode_enum")
            col.prop(o.h2a_forge, "game_specific_enum")
            col.prop(o.h2a_forge, "symmetry_enum")
            col.prop(o.h2a_forge, "place_at_start_enum")

            col.separator()
            col.prop(o.h2a_forge, "can_despawn")
            col.prop(o.h2a_forge, "team_enum")
            col.prop(o.h2a_forge, "spawn_time")
            col.prop(o.h2a_forge, "object_color_enum")
            col.prop(o.h2a_forge, "spawn_sequence")
            col.prop(o.h2a_forge, "timer_user_data")
            col.prop(o.h2a_forge, "spawn_channel")

            col.separator()
            col.label(text="Labels (Name-mapped)")
            col.prop(o.h2a_forge, "label_enum_1")
            col.prop(o.h2a_forge, "label_enum_2")
            col.prop(o.h2a_forge, "label_enum_3")
            col.prop(o.h2a_forge, "label_enum_4")

            col.separator()
            col.prop(o.h2a_forge, "teleporter_channel_enum")

            col.separator()
            col.label(text="Passability")
            col.prop(o.h2a_forge, "pass_players_enum")
            col.prop(o.h2a_forge, "pass_flying_enum")
            col.prop(o.h2a_forge, "pass_heavy_enum")
            col.prop(o.h2a_forge, "pass_land_enum")
            col.prop(o.h2a_forge, "pass_projectiles_enum")

# =============================================================================
# Import/Export entry application
# =============================================================================

def _apply_entry_to_object(context, obj: bpy.types.Object, entry: bytes, slot_index: int = -1, *args):
    """
    Apply one 0x4C forge entry (memory) onto a Blender object.

    IMPORTANT:
      - Labels are now stored on the object by NAME (label_name_1..4).
      - Dropdown enums use NAME identifiers; refresh will not clobber.
    """
    from mathutils import Vector, Matrix

    try:
        slot_index_i = int(slot_index)
    except:
        slot_index_i = -1

    def _u8(off):
        return entry[off] if (entry and len(entry) > off) else 0

    def _s8(off):
        v = _u8(off)
        return v - 256 if v >= 128 else v

    def _f32(off):
        try:
            return struct.unpack_from("<f", entry, off)[0]
        except:
            return 0.0

    def _v3(off):
        return Vector((_f32(off + 0), _f32(off + 4), _f32(off + 8)))

    top_id = _u8(0x00)
    sub_id = _u8(OFF_U16_TYPECONST)
    pre3b  = _u8(OFF_PRE_FLAGS_BYTE)

    pos = _v3(OFF_POS)
    fwd = _v3(OFF_FWD)
    up  = _v3(OFF_UP)

    x = Vector(fwd)
    z = Vector(up)

    if x.length < 1e-8:
        x = Vector((1.0, 0.0, 0.0))
    else:
        x.normalize()

    if z.length < 1e-8:
        z = Vector((0.0, 0.0, 1.0))
    else:
        z.normalize()

    y = z.cross(x)
    if y.length < 1e-8:
        alt = Vector((0.0, 1.0, 0.0)) if abs(x.dot(Vector((0, 1, 0)))) < 0.99 else Vector((0.0, 0.0, 1.0))
        z = alt
        z.normalize()
        y = z.cross(x)

    if y.length < 1e-8:
        y = Vector((0.0, 1.0, 0.0))
    else:
        y.normalize()

    z = x.cross(y)
    if z.length < 1e-8:
        z = Vector((0.0, 0.0, 1.0))
    else:
        z.normalize()

    mw = Matrix((
        (x.x, y.x, z.x, pos.x),
        (x.y, y.y, z.y, pos.y),
        (x.z, y.z, z.z, pos.z),
        (0.0, 0.0, 0.0, 1.0),
    ))
    obj.matrix_world = mw

    name = None
    try:
        key = (int(top_id) & 0xFF, int(sub_id) & 0xFF, int(pre3b) & 0xFF)
        name = TYPEKEY_TO_NAME.get(key)
        if not name:
            name = TYPEKEY_TO_NAME_LOOSE.get((int(top_id) & 0xFF, int(sub_id) & 0xFF))
    except:
        name = None

    if name:
        obj.h2a_forge.template_name = name
        obj.h2a_forge.pre_flags_byte = int(pre3b) & 0xFF
        try:
            clear_unmapped(obj)
        except:
            pass
    else:
        obj.h2a_forge.template_name = DEFAULT_OBJECT_TYPE
        obj.h2a_forge.pre_flags_byte = int(pre3b) & 0xFF
        try:
            mark_unmapped(obj, top_id, sub_id, pre3b)
        except:
            pass
        try:
            obj.name = f"UNMAPPED_{top_id:02X}_{sub_id:02X}_{pre3b:02X}"
        except:
            pass

    p = obj.h2a_forge
    p.pre_flags_byte = int(pre3b) & 0xFF

    flags = _u8(OFF_OBJECT_FLAGS)
    phys_bits = (flags >> 6) & 0x3
    gs_bit    = (flags >> 5) & 0x1
    sym_bits  = (flags >> 2) & 0x3
    not_pas   = (flags >> 1) & 0x1

    p.physics_mode_enum   = "NORMAL" if phys_bits == 0 else ("FIXED" if phys_bits == 1 else "PHASED")
    p.game_specific_enum  = "1" if gs_bit else "0"
    p.symmetry_enum       = "NONE" if sym_bits == 0 else ("SYMMETRIC" if sym_bits == 1 else ("ASYMMETRIC" if sym_bits == 2 else "BOTH"))
    p.place_at_start_enum = "1" if (not_pas == 0) else "0"

    p.can_despawn = _u8(OFF_CAN_DESPAWN)

    team_u = _u8(OFF_TEAM_INDEX)
    if team_u > 8:
        team_u = 8
    p.team_enum = str(int(team_u))

    p.spawn_time = _u8(OFF_SPAWN_TIME)

    col_u = _u8(OFF_OBJECT_COLOR)
    if col_u == 0xFF:
        p.object_color_enum = "FF"
    elif 0 <= col_u <= 7:
        p.object_color_enum = str(int(col_u))
    else:
        p.object_color_enum = "FF"

    p.spawn_sequence  = int(_s8(OFF_SPAWN_SEQ))
    p.timer_user_data = int(_s8(OFF_TIMER_USER))

    p.spawn_channel = _u8(OFF_SPAWN_CHAN)

    # -------------------------
    # LABELS: memory stores indices; we map to NAMES and persist.
    # -------------------------
    li1 = _u8(OFF_LABEL_1)
    li2 = _u8(OFF_LABEL_2)
    li3 = _u8(OFF_LABEL_3)
    li4 = _u8(OFF_LABEL_4)

    n1 = _find_label_name_by_index(context, li1)
    n2 = _find_label_name_by_index(context, li2)
    n3 = _find_label_name_by_index(context, li3)
    n4 = _find_label_name_by_index(context, li4)

    p.label_name_1 = n1
    p.label_name_2 = n2
    p.label_name_3 = n3
    p.label_name_4 = n4

    # Set enums by NAME (stable)
    try: p.label_enum_1 = _label_name_to_enum(n1)
    except: p.label_enum_1 = LABEL_NONE_ID
    try: p.label_enum_2 = _label_name_to_enum(n2)
    except: p.label_enum_2 = LABEL_NONE_ID
    try: p.label_enum_3 = _label_name_to_enum(n3)
    except: p.label_enum_3 = LABEL_NONE_ID
    try: p.label_enum_4 = _label_name_to_enum(n4)
    except: p.label_enum_4 = LABEL_NONE_ID

    tele_u = _u8(OFF_TELE_CHAN)
    if tele_u == 0xFF:
        p.teleporter_channel_enum = "255"
    elif 0 <= tele_u <= 25:
        p.teleporter_channel_enum = str(int(tele_u))
    else:
        p.teleporter_channel_enum = "255"

    pass_b = _u8(OFF_PASS_FLAGS)
    p.pass_players_enum     = "BLOCK" if (pass_b & 0x01) else "ALLOW"
    p.pass_land_enum        = "ALLOW" if (pass_b & 0x02) else "BLOCK"
    p.pass_heavy_enum       = "ALLOW" if (pass_b & 0x04) else "BLOCK"
    p.pass_flying_enum      = "ALLOW" if (pass_b & 0x08) else "BLOCK"
    p.pass_projectiles_enum = "ALLOW" if (pass_b & 0x10) else "BLOCK"

    try:
        apply_scale_preview_if_needed(context, obj)
    except:
        pass

    if slot_index_i >= 0:
        obj["h2a_forge_slot"] = int(slot_index_i)

def _read_all_entries_from_memory(context, base_addr: int, limit: int):
    entries = []
    for i in range(int(limit)):
        raw = g_mb.read(base_addr + i * ENTRY_STRIDE, ENTRY_STRIDE)
        if not raw or len(raw) != ENTRY_STRIDE:
            break
        if _entry_is_empty(raw):
            if i == 0:
                break
            if not _entry_has_more_after(raw):
                break
            continue
        entries.append((i, raw))
        if not _entry_has_more_after(raw):
            break
    return entries

# =============================================================================
# RAW TEMPLATES
# =============================================================================

def _hex_to_bytes(s: str) -> bytes:
    s = s.strip().replace("\n", " ").replace("\t", " ")
    s = re.sub(r"[^0-9A-Fa-f]", " ", s)
    parts = [p for p in s.split(" ") if p]
    return bytes(int(p, 16) for p in parts)

def _ensure_len(name: str, b: bytes, expected: int) -> bytes:
    if len(b) == expected:
        return b
    if len(b) > expected:
        return b[:expected]
    return b + bytes([0] * (expected - len(b)))

EMPTY_SLOT_HEX = """
FF FF FF FF FF FF
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00
FF FF
00 00 00 00 00 00 00 00 00 00 00 00 00 00
08 00 FF 00 00 00
FF FF FF FF
00 00 00 00
"""
EMPTY_SLOT_BYTES = _ensure_len("EMPTY_SLOT_BYTES", _hex_to_bytes(EMPTY_SLOT_HEX), ENTRY_STRIDE)

def _init_entry_for_type(top_id: int, sub_id: int, pre3b: int) -> bytearray:
    blob = bytearray(EMPTY_SLOT_BYTES)
    blob[0] = int(top_id) & 0xFF
    blob[1] = 0x00
    blob[2:6] = b"\xFF\xFF\xFF\xFF"
    blob[OFF_F32_A:OFF_F32_A+4] = _pack_f32(1.0)
    blob[OFF_U16_FFFF:OFF_U16_FFFF+2] = _pack_u16(0xFFFF)

    blob[OFF_U16_TYPECONST:OFF_U16_TYPECONST+2] = _pack_u16(int(sub_id) & 0xFF)
    blob[OFF_PRE_FLAGS_BYTE] = int(pre3b) & 0xFF

    _set_tail_flag(blob, False)
    return blob

# =============================================================================
# Export core
# =============================================================================

def build_entry_bytes(context, obj: bpy.types.Object):
    """
    Build one 0x4C entry from a Blender object.

    LABELS:
      - UI stores label NAMEs (stable).
      - Export resolves those NAMEs to the CURRENT label table indices.
      - If a name is missing in current table -> writes 0xFF (no label).
    """
    if not obj or not hasattr(obj, "h2a_forge"):
        return None

    triple = get_export_type_triple(obj)
    if not triple:
        return None

    top_id, sub_id, pre3b = triple
    blob = _init_entry_for_type(top_id, sub_id, int(pre3b) & 0xFF)

    m = obj.matrix_world
    fwd = Vector(m.col[0].xyz).normalized()
    up  = Vector(m.col[2].xyz).normalized()
    pos = Vector(m.col[3].xyz)

    _write_float3_unaligned(blob, OFF_POS, pos)
    _write_float3_unaligned(blob, OFF_FWD, fwd)
    _write_float3_unaligned(blob, OFF_UP,  up)

    p = obj.h2a_forge

    _write_u8(blob, OFF_PRE_FLAGS_BYTE, int(p.pre_flags_byte) & 0xFF)

    _write_u8(blob, OFF_OBJECT_FLAGS,  pack_object_flags(p))
    _write_u8(blob, OFF_CAN_DESPAWN,   p.can_despawn)
    _write_u8(blob, OFF_TEAM_INDEX,    resolve_team_byte(p))
    _write_u8(blob, OFF_SPAWN_TIME,    p.spawn_time)

    _write_u8(blob, OFF_OBJECT_COLOR,  _parse_u8_auto(p.object_color_enum))
    _write_s8(blob, OFF_SPAWN_SEQ,     p.spawn_sequence)

    timer_s8 = _clamp_s8_timer(p.timer_user_data)
    _write_u8(blob, OFF_TIMER_USER, _s8_to_u8(timer_s8))

    _write_u8(blob, OFF_SPAWN_CHAN, p.spawn_channel)

    # Resolve label indices by NAME
    n1 = (p.label_name_1 or _label_enum_to_name(getattr(p, "label_enum_1", LABEL_NONE_ID))).strip()
    n2 = (p.label_name_2 or _label_enum_to_name(getattr(p, "label_enum_2", LABEL_NONE_ID))).strip()
    n3 = (p.label_name_3 or _label_enum_to_name(getattr(p, "label_enum_3", LABEL_NONE_ID))).strip()
    n4 = (p.label_name_4 or _label_enum_to_name(getattr(p, "label_enum_4", LABEL_NONE_ID))).strip()

    i1 = _find_label_index_by_name(context, n1)
    i2 = _find_label_index_by_name(context, n2)
    i3 = _find_label_index_by_name(context, n3)
    i4 = _find_label_index_by_name(context, n4)

    _write_u8(blob, OFF_LABEL_1, i1)
    _write_u8(blob, OFF_LABEL_2, i2)
    _write_u8(blob, OFF_LABEL_3, i3)
    _write_u8(blob, OFF_LABEL_4, i4)

    _write_u8(blob, OFF_TELE_CHAN,  _clamp_u8(int(getattr(p, "teleporter_channel_enum", "255"))))
    _write_u8(blob, OFF_PASS_FLAGS, pack_passability_flags(p))

    return bytes(blob)

def gather_forge_objects_in_scene(context):
    return [obj for obj in context.scene.objects if is_forge_object(obj)]

# =============================================================================
# Operators
# =============================================================================

class H2AForgeRefreshLabels(Operator):
    bl_idname = "h2a_forge.refresh_labels"
    bl_label = "Refresh Forge Labels"
    bl_options = {"REGISTER"}

    def execute(self, context):
        sp = context.scene.h2a_forge

        try:
            g_mb.open_process(sp.target_exe)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        try:
            base_addr = g_mb.get_forge_object_array()
            if not base_addr:
                self.report({"ERROR"}, "mb_get_forge_object_array returned 0 (cannot resolve forge array base).")
                return {"CANCELLED"}

            label_addr = base_addr - LABEL_BLOB_BACK
            blob = g_mb.read(label_addr, LABEL_BLOB_SIZE)
            if not blob or len(blob) != LABEL_BLOB_SIZE:
                self.report({"ERROR"}, f"Failed to read labels blob at 0x{label_addr:X} size 0x{LABEL_BLOB_SIZE:X}")
                return {"CANCELLED"}

            labels = parse_forge_labels_from_blob(blob)
            if not labels:
                self.report({"ERROR"}, "Parsed 0 labels from blob (parser may need adjustment).")
                return {"CANCELLED"}

            sp.forge_labels.clear()

            # NOTE: we do not store a special 0xFF entry here; the enum always provides "(No Label)" separately.
            for i, nm in enumerate(labels):
                item = sp.forge_labels.add()
                item.name = nm
                item.index = i

            # IMPORTANT: keep dropdowns stable by re-binding from stored label_name_*.
            _rebind_all_objects_after_label_refresh(context)

            self.report({"INFO"}, f"Loaded {len(labels)} forge labels from 0x{label_addr:X} (name-mapped).")
            return {"FINISHED"}

        finally:
            g_mb.close()

class H2AForgeExportMemory(Operator):
    bl_idname = "h2a_forge.export_memory"
    bl_label = "Export H2A Forge Objects (Memory)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        sp = context.scene.h2a_forge

        if not sp.confirm_overwrite:
            self.report({"ERROR"}, "Enable 'I understand this overwrites forge memory' first.")
            return {"CANCELLED"}

        try:
            g_mb.open_process(sp.target_exe)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        base_addr = 0
        written = 0
        skipped = 0
        try:
            base_addr = g_mb.get_forge_object_array()
            if base_addr == 0:
                self.report({"ERROR"}, "mb_get_forge_object_array returned 0 (pointer chain failed).")
                return {"CANCELLED"}

            objs = gather_forge_objects_in_scene(context)

            entries = []
            skipped = 0
            for obj in objs:
                if len(entries) >= maxObjectCount:
                    break
                entry = build_entry_bytes(context, obj)
                if entry is None:
                    skipped += 1
                    continue
                entries.append(entry)

            written = len(entries)

            for i in range(maxObjectCount):
                if i < written:
                    b = bytearray(entries[i])
                    _set_tail_flag(b, is_more_after=(i < (written - 1)))
                else:
                    b = bytearray(EMPTY_SLOT_BYTES)
                    _set_tail_flag(b, is_more_after=False)

                addr = base_addr + i * ENTRY_STRIDE
                if not g_mb.write(addr, bytes(b)):
                    self.report({"ERROR"}, f"Write failed at slot {i} addr=0x{addr:X} (dll='{g_mb.dll_path}')")
                    return {"CANCELLED"}

            if g_mb._has_set_total:
                ok = g_mb.set_forge_object_total(written)
                if not ok:
                    self.report({"WARNING"}, "Exported objects, but failed to update object total count (mb_set_forge_object_total_exported failed).")

        finally:
            g_mb.close()

        self.report({"INFO"}, f"Exported {written} objects (skipped {skipped}) and padded to {maxObjectCount}. base=0x{base_addr:X}")
        return {"FINISHED"}

class H2AForgeImportMemory(Operator):
    bl_idname = "h2a_forge.import_memory"
    bl_label = "Import H2A Forge Objects (Memory)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sp = context.scene.h2a_forge

        try:
            g_mb.open_process(sp.target_exe)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        imported = 0
        unknown = 0

        try:
            base_addr = g_mb.get_forge_object_array()
            if base_addr == 0:
                self.report({"ERROR"}, "mb_get_forge_object_array returned 0 (pointer chain failed).")
                return {"CANCELLED"}

            # Capture label blob snapshot (optional)
            try:
                label_addr = _label_blob_addr_from_forge_array(base_addr)
                blob = g_mb.read(label_addr, LABEL_BLOB_SIZE)
                if blob and len(blob) == LABEL_BLOB_SIZE:
                    sp.imported_label_blob_b64 = _b64_from_bytes(blob)
                    sp.imported_label_blob_size = len(blob)
            except:
                pass

            if sp.import_clear_existing:
                to_del = [o for o in context.scene.objects if is_forge_object(o)]
                for o in to_del:
                    try:
                        bpy.data.objects.remove(o, do_unlink=True)
                    except:
                        pass

            pairs = _read_all_entries_from_memory(context, base_addr, sp.import_limit)

            for slot_idx, entry in pairs:
                top_id = _u8_from(entry, 0x00)
                sub_id = _u16_from(entry, OFF_U16_TYPECONST) & 0xFF
                pre3b  = _u8_from(entry, OFF_PRE_FLAGS_BYTE)

                template_name = _resolve_template_name_from_ids(top_id, sub_id, pre3b)

                obj = _create_object_from_template_or_placeholder(context, template_name, top_id, sub_id, pre3b)
                _apply_entry_to_object(context, obj, entry, slot_idx)

                obj["h2a_import_slot"] = int(slot_idx)
                if not template_name or obj.get("h2a_import_unknown", False):
                    unknown += 1
                imported += 1

            self.report({"INFO"}, f"Imported {imported} objects ({unknown} modelless). base=0x{base_addr:X}")
            return {"FINISHED"}

        finally:
            g_mb.close()

def export_menu_draw(self, context):
    self.layout.operator(H2AForgeExportMemory.bl_idname, text="H2A Forge Objects (Memory)")

def import_menu_draw(self, context):
    self.layout.operator(H2AForgeImportMemory.bl_idname, text="H2A Forge Objects (Memory)")

# =============================================================================
# Register
# =============================================================================

reg_classes = [
    H2AForgeLabelItem,
    H2AForgeObjectProps,
    H2AForgeSceneProps,
    AddForgeObject,
    VIEW3D_PT_h2a_forge_sidebar,
    H2AForgeRefreshLabels,
    H2AForgeExportMemory,
    H2AForgeImportMemory,
]

def register():
    for cls in reg_classes:
        bpy.utils.register_class(cls)

    bpy.types.Object.h2a_forge = PointerProperty(type=H2AForgeObjectProps)
    bpy.types.Scene.h2a_forge  = PointerProperty(type=H2AForgeSceneProps)

    bpy.types.VIEW3D_MT_add.append(addForgeObjectMenuItem)
    bpy.types.TOPBAR_MT_file_export.append(export_menu_draw)
    bpy.types.TOPBAR_MT_file_import.append(import_menu_draw)

def unregister():
    try:
        bpy.types.VIEW3D_MT_add.remove(addForgeObjectMenuItem)
    except:
        pass
    try:
        bpy.types.TOPBAR_MT_file_export.remove(export_menu_draw)
    except:
        pass
    try:
        bpy.types.TOPBAR_MT_file_import.remove(import_menu_draw)
    except:
        pass

    try:
        del bpy.types.Object.h2a_forge
    except:
        pass
    try:
        del bpy.types.Scene.h2a_forge
    except:
        pass

    for cls in reversed(reg_classes):
        try:
            bpy.utils.unregister_class(cls)
        except:
            pass

if __name__ == "__main__":
    register()

bl_info = {
    "name": "Blender Forge Bridge",
    "author": "Sopitive",
    "version": (0, 3, 15),  # split into modules (ui + memory)
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

# --- load "memory.py" from Blender Text blocks as a real module ---
import sys, types, bpy

def _load_textblock_module(module_name: str, text_name: str):
    """
    Create/refresh a Python module from a Blender Text datablock.
    This lets you split code into multiple Text blocks inside the .blend.
    """
    txt = bpy.data.texts.get(text_name)
    if not txt:
        raise ImportError(f"Missing Text block '{text_name}' (needed for module '{module_name}')")

    src = txt.as_string()

    mod = sys.modules.get(module_name)
    if mod is None:
        mod = types.ModuleType(module_name)
        mod.__file__ = f"<blender-text:{text_name}>"
        sys.modules[module_name] = mod

    # Re-exec so edits apply without restarting Blender
    mod.__dict__.clear()
    mod.__dict__.update({
        "__name__": module_name,
        "__file__": f"<blender-text:{text_name}>",
        "__package__": "",  # not a package
        "bpy": bpy,         # convenience (optional)
    })
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod

mem = _load_textblock_module("h2a_forge_memory", "memory.py")
# ---------------------------------------------------------------



# =============================================================================
# UI-SIDE PROPERTY GROUPS
# =============================================================================

class H2AForgeObjectProps(PropertyGroup):
    template_name: EnumProperty(
        name="Object Type",
        description="Forge object type (controls exported top/sub indices + default 0x3B)",
        items=mem.OBJECT_TYPE_ITEMS,
        default=mem.DEFAULT_OBJECT_TYPE,
        update=mem._on_template_name_update,
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
        items=mem.PHYSICS_MODE_ITEMS,
        default="PHASED",
    )
    game_specific_enum: EnumProperty(
        name="Game Specific",
        description="Game-specific toggle (packed into Object Flags byte)",
        items=mem.BOOL_ITEMS,
        default="1",
    )
    symmetry_enum: EnumProperty(
        name="Symmetry",
        description="Symmetry (packed into Object Flags byte)",
        items=mem.SYMMETRY_ITEMS,
        default="BOTH",
    )
    place_at_start_enum: EnumProperty(
        name="Place At Start",
        description="Place-at-start (packed into Object Flags byte). True means bit1=0.",
        items=mem.BOOL_ITEMS,
        default="1",
    )

    can_despawn: IntProperty(name="Can Despawn", default=0, min=0, max=255)

    team_enum: EnumProperty(
        name="Team",
        description="Forge team assignment",
        items=mem.TEAM_ITEMS,
        default="8",
        update=mem._on_team_enum_update,
    )

    spawn_time: IntProperty(name="Spawn Time", default=0, min=0, max=255)

    object_color_enum: EnumProperty(
        name="Object Color",
        description="Forge object color override",
        items=mem.OBJECT_COLOR_ITEMS,
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
        update=mem._on_timer_user_data_update,
    )

    spawn_channel: IntProperty(name="Spawn Channel", default=0xFF, min=0, max=255)

    # Persisted label NAMEs (stable across label refresh / gametypes)
    label_name_1: StringProperty(name="LabelName1", default="")
    label_name_2: StringProperty(name="LabelName2", default="")
    label_name_3: StringProperty(name="LabelName3", default="")
    label_name_4: StringProperty(name="LabelName4", default="")

    # Dropdowns are NAME identifiers (not indices)
    label_enum_1: EnumProperty(name="Label 1", items=mem.genForgeLabelEnumItems, update=mem._on_label_enum_update)
    label_enum_2: EnumProperty(name="Label 2", items=mem.genForgeLabelEnumItems, update=mem._on_label_enum_update)
    label_enum_3: EnumProperty(name="Label 3", items=mem.genForgeLabelEnumItems, update=mem._on_label_enum_update)
    label_enum_4: EnumProperty(name="Label 4", items=mem.genForgeLabelEnumItems, update=mem._on_label_enum_update)

    teleporter_channel_enum: EnumProperty(
        name="Teleporter Channel",
        description="Teleporter channel byte (Alpha..Zulu, None=0xFF)",
        items=mem.TELEPORTER_CHANNEL_ITEMS,
        default="255",
    )

    pass_players_enum: EnumProperty(name="Players", items=mem.ALLOW_BLOCK_ITEMS, default="ALLOW")
    pass_flying_enum: EnumProperty(name="Flying Vehicles", items=mem.ALLOW_BLOCK_ITEMS, default="BLOCK")
    pass_heavy_enum: EnumProperty(name="Heavy Vehicles", items=mem.ALLOW_BLOCK_ITEMS, default="BLOCK")
    pass_land_enum: EnumProperty(name="Land Vehicles", items=mem.ALLOW_BLOCK_ITEMS, default="BLOCK")
    pass_projectiles_enum: EnumProperty(name="Projectiles", items=mem.ALLOW_BLOCK_ITEMS, default="BLOCK")


class H2AForgeSceneProps(PropertyGroup):
    target_exe: StringProperty(name="Target EXE", default="MCC-Win64-Shipping.exe")
    confirm_overwrite: BoolProperty(name="I understand this overwrites forge memory", default=False)

    forge_labels: CollectionProperty(type=mem.H2AForgeLabelItem)

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
        default=mem.maxObjectCount,
        min=1,
        max=mem.maxObjectCount,
        description="Maximum number of slots to scan while importing",
    )


# =============================================================================
# UI: Add Menu Operator (Shift+A search popup)
# =============================================================================

class AddForgeObject(Operator):
    bl_idname = "h2a_forge.add_object"
    bl_label = "Forge Object"
    bl_property = "objectType"
    bl_options = {"REGISTER", "UNDO"}

    objectType: bpy.props.EnumProperty(name="Object Type", items=mem.genObjectTypesEnum)

    def invoke(self, context, event):
        root = mem.get_palette_root_collection()
        if not root:
            self.report({"ERROR"}, f"Palette root '{mem.paletteRootName}' not found.")
            return {"CANCELLED"}
        mem.fillIconDict(root)
        context.window_manager.invoke_search_popup(self)
        return {"FINISHED"}

    def execute(self, context):
        if context.scene.name == mem.propSceneName:
            self.report({"ERROR"}, "You are in the Props scene. Switch to your working scene to place objects.")
            return {"CANCELLED"}
        try:
            mem.createForgeObject(context, self.objectType)
            bpy.ops.ed.undo_push()
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


def addForgeObjectMenuItem(self, context):
    layout = self.layout
    layout.operator_context = 'INVOKE_DEFAULT'
    layout.operator(AddForgeObject.bl_idname, icon='ADD')


# =============================================================================
# UI: Sidebar Panel
# =============================================================================

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
        row.operator(mem.H2AForgeExportMemory.bl_idname, icon="EXPORT")
        row.operator(mem.H2AForgeImportMemory.bl_idname, icon="IMPORT")

        row2 = layout.row(align=True)
        row2.operator(mem.H2AForgeRefreshLabels.bl_idname, icon="FILE_REFRESH")

        layout.separator()
        boxi = layout.box()
        boxi.label(text="Import Options")
        boxi.prop(sp, "import_clear_existing")
        boxi.prop(sp, "import_limit")

        layout.separator()
        o = context.active_object
        if o and mem.is_forge_object(o):
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
# File menu items
# =============================================================================

def export_menu_draw(self, context):
    self.layout.operator(mem.H2AForgeExportMemory.bl_idname, text="H2A Forge Objects (Memory)")

def import_menu_draw(self, context):
    self.layout.operator(mem.H2AForgeImportMemory.bl_idname, text="H2A Forge Objects (Memory)")


# =============================================================================
# Register
# =============================================================================

UI_CLASSES = [
    H2AForgeObjectProps,
    H2AForgeSceneProps,
    AddForgeObject,
    VIEW3D_PT_h2a_forge_sidebar,
]

def register():
    # Register memory-side classes first (label item + ops)
    for cls in mem.MEMORY_CLASSES:
        bpy.utils.register_class(cls)

    # Register UI classes
    for cls in UI_CLASSES:
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

    # Unregister UI classes
    for cls in reversed(UI_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except:
            pass

    # Unregister memory classes
    for cls in reversed(mem.MEMORY_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except:
            pass

if __name__ == "__main__":
    register()

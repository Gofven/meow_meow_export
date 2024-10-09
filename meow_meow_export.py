import os
import shutil
from pathlib import Path

# PhotoshopAPI dependencies
import glob
import psapi
import numpy as np
import imageio.v3 as iio

# Substance 3D Painter modules
from substance_painter.layerstack import LayerNode, GroupLayerNode, TextureSet
import substance_painter.ui
import substance_painter.export
import substance_painter.project
import substance_painter.textureset

# PySide module to build custom UI
from PySide6 import QtWidgets, QtGui
from substance_painter.exception import ProjectError

plugin_widgets = []
node_visibility = dict()

# Note: name defaults to node.uid()
def export_textures(node: LayerNode,
                    export_path: Path,
                    map_type: str = "documentMap",
                    map_name: str = "baseColor",
                    name: str = None):
    # Verify if a project is open before trying to export something
    if not substance_painter.project.is_open():
        return

    # Set the active stack to selected Layer
    stack = node.get_stack()
    substance_painter.textureset.set_active_stack(stack)

    # Build the configuration
    export_config = {
        "exportShaderParams": False,
        "exportPath": str(export_path),
        "defaultExportPreset": "2d_view",
        "exportPresets": [
            {"name": "2d_view",
             "maps": [
                 {
                     "fileName": str(node.uid()) if name is None else name,
                     "channels": [
                         {
                             "destChannel": "R",
                             "srcChannel": "R",
                             "srcMapType": map_type,
                             "srcMapName": map_name,
                         },
                         {
                             "destChannel": "G",
                             "srcChannel": "G",
                             "srcMapType": map_type,
                             "srcMapName": map_name,
                         },
                         {
                             "destChannel": "B",
                             "srcChannel": "B",
                             "srcMapType": map_type,
                             "srcMapName": map_name,
                         },
                         {
                             "destChannel": "A",
                             "srcChannel": "A",
                             "srcMapType": map_type,
                             "srcMapName": map_name,
                         },
                     ],
                     "parameters": {
                         "fileFormat": "png",
                         "bitDepth": "8",
                         "dithering": False,
                         "paddingAlgorithm": "transparent",
                         "dilationDistance": 16,
                     }
                 }
             ],
             }
        ],
        "exportList": [
            {
                "rootPath": str(stack),
            }
        ]
    }

    # Actual export operation:
    export_result = substance_painter.export.export_project_textures(export_config)

    # In case of error, display a human-readable message:
    if export_result.status != substance_painter.export.ExportStatus.Success:
        print(export_result.message)


# TODO Add support for group layers, aswell as opacity
def generate_psds(export_path: Path, cache_path: Path, delete_on_success: bool = False):
    for texture_set in substance_painter.textureset.all_texture_sets():
        layered_file = psapi.LayeredFile_8bit(psapi.enum.ColorMode.rgb, 4096, 4096)  # Create file

        for stack in texture_set.all_stacks():
            stack_root_nodes = substance_painter.layerstack.get_root_layer_nodes(stack)

            def loop_nodes(nodes, group: psapi.GroupLayer_8bit = None):
                for node in nodes:
                    if isinstance(node, substance_painter.layerstack.GroupLayerNode):
                        if get_psapi_blending_mode(node):
                            group_layer = psapi.GroupLayer_8bit(layer_name=node.get_name(),
                                                                blend_mode=get_psapi_blending_mode(node))
                            if not group:
                                layered_file.add_layer(group_layer)
                                print(layered_file.layers)

                            else:
                                group.add_layer(layered_file=layered_file,
                                                layer=group_layer)

                            loop_nodes(node.sub_layers(), group=group_layer)

                    elif isinstance(node, substance_painter.layerstack.LayerNode):
                        if get_psapi_blending_mode(node):
                            for im_path in glob.glob(os.path.join(cache_path, f'{str(node.uid())}.png')):
                                image = iio.imread(im_path)

                                data = np.zeros((image.shape[2], image.shape[0], image.shape[1]), np.uint8)
                                data[0] = image[:, :, 0]
                                data[1] = image[:, :, 1]
                                data[2] = image[:, :, 2]
                                data[3] = image[:, :, 3]

                                layer = psapi.ImageLayer_8bit(data,
                                                              blend_mode=get_psapi_blending_mode(node),
                                                              layer_name=node.get_name(),
                                                              height=4096,
                                                              width=4096)

                                if not group:
                                    layered_file.add_layer(layer)

                                else:
                                    group.add_layer(layered_file=layered_file,
                                                    layer=layer)

            loop_nodes(stack_root_nodes)
        print(layered_file.layers)
        layered_file.compression = psapi.enum.Compression.rle
        layered_file.write(Path(os.path.join(str(export_path), f"{texture_set}.psd")))

    # Delete cache on completion
    if delete_on_success:
        shutil.rmtree(str(cache_path))


# Performs a method on every node
def perform(func_layer: exec,
            func_group: exec = None,
            func_layer_args: list = None,
            func_layer_kwargs: dict = None,
            func_group_args: list = None,
            func_group_kwargs: dict = None
            ):
    func_layer_args = func_layer_args or []
    func_layer_kwargs = func_layer_kwargs or {}
    func_group_args = func_group_args or []
    func_group_kwargs = func_group_kwargs or {}

    for texture_set in substance_painter.textureset.all_texture_sets():
        for stack in texture_set.all_stacks():
            stack_root_nodes = substance_painter.layerstack.get_root_layer_nodes(stack)

            def loop_nodes(nodes):
                for node in nodes:
                    if isinstance(node, substance_painter.layerstack.GroupLayerNode):
                        if func_group:
                            func_group(node, *func_group_args, **func_group_kwargs)

                        loop_nodes(node.sub_layers())

                    elif isinstance(node, substance_painter.layerstack.LayerNode):
                        func_layer(node, *func_layer_args, **func_layer_kwargs)

            loop_nodes(stack_root_nodes)


def get_psapi_blending_mode(node: LayerNode):
    blending_mode = node.get_blending_mode(channel=substance_painter.layerstack.ChannelType.BaseColor)
    if blending_mode and not str(blending_mode).split('.')[1].startswith("NormalMap"):
        return getattr(psapi.enum.BlendMode,
                       str(node.get_blending_mode(substance_painter.layerstack.ChannelType.BaseColor)
                           ).lower().split('.')[1])

    return None



# Save dict containing visibility info for each object
def save_state(node):
    node_visibility[str(node.uid())] = node.is_visible()


# Toggle node transparency
def set_visibility(node, visible: bool):
    if not node.is_visible() == visible:
        node.set_visible(visible)


# Reset node transparency
def reset_visibility(node):
    if is_visible := node_visibility.get(str(node.uid())):
        node.set_visible(is_visible)


# Export node
# Note: Extra path referring to png's that are different from baseColor
def export(node: substance_painter.layerstack.LayerNode, export_path: Path, extra_path: Path):
    layer_number = list(node_visibility).index(str(node.uid())) + 1
    total_layers = len(node_visibility)

    set_visibility(node, True)
    substance_painter.logging.log(substance_painter.logging.INFO,
                                  channel="Meow Meow Export",
                                  message=f"Exporting layer {layer_number} of {total_layers} "
                                          f"({node.get_texture_set().name()}/{node.get_name()})...")

    if str(node.get_blending_mode(substance_painter.layerstack.ChannelType.BaseColor)
           ).split('.')[1].startswith("NormalMap"):
        export_textures(node, export_path=extra_path, map_name='normal', name=node.get_name())

    else:
        export_textures(node, export_path=export_path)

    set_visibility(node, False)


def generate_textures():
    # Check if active project is loaded
    try:
        export_path = substance_painter.project.file_path()
    except ProjectError as e:
        substance_painter.logging.log(substance_painter.logging.ERROR, channel="Meow Meow Export", message=str(e))
        return

    export_path = Path(os.path.join(os.path.dirname(export_path), "meow_meow_export"))  # Root export path (for psd)
    cache_path = export_path.joinpath(".cache")  # Path for exported pngs

    # Create folder if it doesn't exist
    cache_path.mkdir(parents=True, exist_ok=True)

    perform(save_state)  # Save visibility info

    perform(set_visibility, func_layer_kwargs=dict(visible=False))  # Hide all layer nodes
    perform(export, func_layer_kwargs=dict(export_path=cache_path, extra_path=export_path))
    perform(reset_visibility)
    generate_psds(delete_on_success=True, export_path=export_path, cache_path=cache_path)


def start_plugin():
    # Create a text widget for a menu
    Action = QtGui.QAction(text="Meow Meow Export")
    Action.triggered.connect(generate_textures)

    # Add this widget to the existing File menu of the application
    substance_painter.ui.add_action(
        substance_painter.ui.ApplicationMenu.File,
        Action)

    # Store the widget for proper cleanup later when stopping the plugin
    plugin_widgets.append(Action)


def close_plugin():
    # Remove all widgets that have been added to the UI
    for widget in plugin_widgets:
        substance_painter.ui.delete_ui_element(widget)

    plugin_widgets.clear()


if __name__ == "__main__":
    start_plugin()

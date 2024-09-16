import os
import shutil
from pathlib import Path

# PhotoshopAPI dependencies
import glob
import psapi
import numpy as np
import imageio.v3 as iio

# Substance 3D Painter modules
import substance_painter.ui
import substance_painter.export
import substance_painter.project
import substance_painter.textureset

# PySide module to build custom UI
from PySide2 import QtWidgets
from substance_painter.exception import ProjectError

plugin_widgets = []

class LayerData:
    texture_set_name: str
    uid: int
    layer_name: str
    visible: bool

    def __init__(self,
                 texture_set_name: str,
                 uid: int,
                 layer_name: str,
                 blending_mode: substance_painter.layerstack.BlendingMode,
                 visible: bool,
                 export_root_path: Path):
        self.texture_set_name = texture_set_name
        self.uid = uid
        self.layer_name = layer_name
        self.blending_mode = blending_mode
        self.visible = visible
        self.export_root_path = export_root_path

    def __dict__(self):
        return dict(texture_set=self.texture_set_name, uid=self.uid, layer=self.layer_name, visible=self.visible)

    @property
    def export_path(self) -> str:
        return os.path.join(self.export_root_path, self.texture_set_name)

    @property
    def file_path(self) -> str:
        return os.path.join(self.export_path, f"{self.layer_name}.png")

    @property
    def layer_node(self) -> substance_painter.layerstack.LayerNode:
        for texture_set in substance_painter.textureset.all_texture_sets():
            for stack in texture_set.all_stacks():
                stack_root_nodes = substance_painter.layerstack.get_root_layer_nodes(stack)

                for layer in stack_root_nodes:
                    if isinstance(layer, substance_painter.layerstack.LayerNode) and layer.uid() == self.uid:
                        return layer

    @property
    def psapi_blending_mode(self):
        return getattr(psapi.enum.BlendMode,
                       str(self.blending_mode).lower().split('.')[1]) if self.blending_mode else None

    # Focus visibility on one texture in the active TextureSet
    def focus_visibility(self):
        for stack in self.layer_node.get_texture_set().all_stacks():
            stack_root_nodes = substance_painter.layerstack.get_root_layer_nodes(stack)

            for layer in stack_root_nodes:
                if isinstance(layer, substance_painter.layerstack.LayerNode):
                    layer.set_visible(layer.uid() == self.uid)


staged_data: dict[str: LayerData] = dict()


def export_textures(layer_data: LayerData):
    # Verify if a project is open before trying to export something
    if not substance_painter.project.is_open():
        return

    # Set the active stack to selected Layer
    stack = substance_painter.layerstack.Stack.from_name(texture_set_name=layer_data.texture_set_name)
    substance_painter.textureset.set_active_stack(stack)

    layer_data.focus_visibility()

    Path(layer_data.export_path).mkdir(parents=True, exist_ok=True)

    # Build the configuration
    export_config = {
        "exportShaderParams": False,
        "exportPath": str(layer_data.export_path),
        "defaultExportPreset": "2d_view",
        "exportPresets": [
            {"name": "2d_view",
             "maps": [
                 {
                     "fileName": layer_data.layer_name,
                     "channels": [
                         {
                             "destChannel": "R",
                             "srcChannel": "R",
                             "srcMapType": "documentMap",
                             "srcMapName": "baseColor"
                         },
                         {
                             "destChannel": "G",
                             "srcChannel": "G",
                             "srcMapType": "documentMap",
                             "srcMapName": "baseColor"
                         },
                         {
                             "destChannel": "B",
                             "srcChannel": "B",
                             "srcMapType": "documentMap",
                             "srcMapName": "baseColor"
                         },
                         {
                             "destChannel": "A",
                             "srcChannel": "A",
                             "srcMapType": "documentMap",
                             "srcMapName": "baseColor"
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
def generate_psds(export_path: Path, delete_on_success: bool = False):
    for texture_set, layers in staged_data.items():
        layered_file = psapi.LayeredFile_8bit(psapi.enum.ColorMode.rgb, 4096, 4096)

        for layer_data in layers:
            for im_path in glob.glob(layer_data.file_path):
                image = iio.imread(im_path)

                data = np.zeros((image.shape[2], image.shape[0], image.shape[1]), np.uint8)
                data[0] = image[:, :, 0]
                data[1] = image[:, :, 1]
                data[2] = image[:, :, 2]
                data[3] = image[:, :, 3]

                layer = psapi.ImageLayer_8bit(data,
                                              blend_mode=layer_data.psapi_blending_mode,
                                              layer_name=os.path.basename(im_path).split('.')[0],
                                              height=4096,
                                              width=4096)
                layered_file.add_layer(layer)

        layered_file.write(Path(os.path.join(str(export_path), f"{texture_set}.psd")))

        if delete_on_success:
            shutil.rmtree(os.path.join(str(export_path), texture_set))


# Sets all layers visibility back to it's initial values
def reset_visibility():
    for layers in staged_data.values():
        for layer_data in layers:
            layer_data.layer_node.set_visible(layer_data.visible)


# Stores layers in a list including certain previous states
def save_state(export_path: Path):
    for texture_set in substance_painter.textureset.all_texture_sets():
        for stack in texture_set.all_stacks():
            stack_root_nodes = substance_painter.layerstack.get_root_layer_nodes(stack)
            print(stack.all_channels())

            for layer in stack_root_nodes:
                if not staged_data.get(texture_set.name()):
                    staged_data[texture_set.name()] = list()

                blending_mode = layer.get_blending_mode(channel=substance_painter.layerstack.ChannelType.BaseColor)

                staged_data[texture_set.name()].append(LayerData(texture_set.name(),
                                                                 layer.uid(),
                                                                 layer.get_name(),
                                                                 blending_mode,
                                                                 True,
                                                                 export_root_path=export_path))


def generate_textures():
    try:
        export_path = substance_painter.project.file_path()
    except ProjectError as e:
        substance_painter.logging.log(substance_painter.logging.ERROR, channel="Meow Meow Export", message=str(e))
        return

    export_path = Path(os.path.join(os.path.dirname(export_path), "meow_meow_export"))

    # Create folder if it doesn't exist
    export_path.mkdir(parents=True, exist_ok=True)

    save_state(export_path=export_path)

    total_layers = sum([len(x) for x in staged_data.values()])

    layer = 1
    for i, layers in enumerate(staged_data.values()):
        for j, layer_data in enumerate(layers):
            substance_painter.logging.log(substance_painter.logging.INFO,
                                          channel="Meow Meow Export",
                                          message=f"Exporting layer {layer} of {total_layers} "
                                                  f"({layer_data.texture_set_name}/{layer_data.layer_name})...")
            export_textures(layer_data=layer_data)
            layer += 1

    reset_visibility()
    generate_psds(delete_on_success=True, export_path=export_path)


def start_plugin():
    # Create a text widget for a menu
    Action = QtWidgets.QAction("Meow Meow Export", triggered=generate_textures)

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

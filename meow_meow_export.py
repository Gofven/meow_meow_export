import inspect
import os
import shutil
from pathlib import Path

# PhotoshopAPI dependencies
import glob
import photoshopapi as psapi
import numpy as np
import imageio.v3 as iio
from PySide6.QtGui import Qt, QFont
from PySide6.QtWidgets import QMainWindow, QPushButton, QDialog, QMenu, QCheckBox, QVBoxLayout, QWidget, QFileDialog, \
    QSpinBox, QLabel

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
                    name: str = None,
                    dilation_distance: int = 16):
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
                         "dilationDistance": dilation_distance,
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
def generate_psds(export_path: Path, cache_path: Path, delete_on_success: bool = False, filters: list[int] = None):
    for i, texture_set in enumerate(substance_painter.textureset.all_texture_sets()):
        if filters and filters[i] == 0:
            continue

        layered_file = psapi.LayeredFile_8bit(psapi.enum.ColorMode.rgb, 4096, 4096)  # Create file

        for stack in texture_set.all_stacks():
            stack_root_nodes = substance_painter.layerstack.get_root_layer_nodes(stack)

            def loop_nodes(nodes, group: psapi.GroupLayer_8bit = None):
                for node in nodes:
                    print(f"Adding node {node.get_name()}{f' for group {group.name}' if group else ''} to the PSD!")
                    if isinstance(node, substance_painter.layerstack.GroupLayerNode):
                        if get_psapi_blending_mode(node):
                            group_layer = psapi.GroupLayer_8bit(layer_name=node.get_name(),
                                                                blend_mode=get_psapi_blending_mode(node))
                            if not group:
                                layered_file.add_layer(group_layer)

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
                                                              width=4096,
                                                              pos_x=2048,
                                                              pos_y=2048)

                                if not group:
                                    layered_file.add_layer(layer)

                                else:
                                    group.add_layer(layered_file=layered_file,
                                                    layer=layer)

            loop_nodes(stack_root_nodes)
        layered_file.compression = psapi.enum.Compression.rle

        if layered_file.layers.count == 0:
            print(f"No layers for textureset {texture_set.name()}, skipping...")
            continue

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
            func_group_kwargs: dict = None,
            filters: list[int] = None):
    func_layer_args = func_layer_args or []
    func_layer_kwargs = func_layer_kwargs or {}
    func_group_args = func_group_args or []
    func_group_kwargs = func_group_kwargs or {}

    for i, texture_set in enumerate(substance_painter.textureset.all_texture_sets()):
        if filters and filters[i] == 0:  # Skip if filter is set to 0 for textureset
            continue

        for stack in texture_set.all_stacks():
            stack_root_nodes = substance_painter.layerstack.get_root_layer_nodes(stack)

            def loop_nodes(nodes):
                for node in nodes:
                    if isinstance(node, substance_painter.layerstack.GroupLayerNode):
                        if not filters or ((filters and filters[i] == 2) or node_visibility.get(str(node.uid()))):
                            if func_group:
                                func_group(node, *func_group_args, **func_group_kwargs)

                            loop_nodes(node.sub_layers())

                    elif isinstance(node, substance_painter.layerstack.LayerNode):
                        if not filters or ((filters and filters[i] == 2) or node_visibility.get(str(node.uid()))):
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
def export(node: substance_painter.layerstack.LayerNode, export_path: Path, extra_path: Path, dilation_distance: int):
    layer_number = list(node_visibility).index(str(node.uid())) + 1
    total_layers = len(node_visibility)

    set_visibility(node, True)
    substance_painter.logging.log(substance_painter.logging.INFO,
                                  channel="Meow Meow Export",
                                  message=f"Exporting layer {layer_number} of {total_layers} "
                                          f"({node.get_texture_set().name()}/{node.get_name()})...")

    if str(node.get_blending_mode(substance_painter.layerstack.ChannelType.BaseColor)
           ).split('.')[1].startswith("NormalMap"):
        export_textures(node,
                        export_path=extra_path,
                        map_name='normal',
                        name=node.get_name(),
                        dilation_distance=dilation_distance)

    else:
        export_textures(node, export_path=export_path, dilation_distance=dilation_distance)

    set_visibility(node, False)


class MainWindow(QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Q stuff
        self.setWindowTitle("Meow Meow Export!")
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Folder picker
        try:
            self.export_path = substance_painter.project.file_path()
        except ProjectError as e:
            substance_painter.logging.log(substance_painter.logging.ERROR, channel="Meow Meow Export", message=str(e))
            return

        # Export variables
        self.dilation_distance = 16
        self.textureset_options: list[int] = [2 for i in substance_painter.textureset.all_texture_sets()]

        export_button = QPushButton("Select Export Location")
        export_button.setStyleSheet("margin-top: 10px;")
        export_button.clicked.connect(lambda checked=False, default_export_path=os.path.split(self.export_path)[0]:
                                      self.pick_folder(default_export_path=default_export_path))

        label_checkbox = QLabel("Select TextureSet(s) to export (choose between 3 options)")
        label_checkbox.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(label_checkbox)

        # TextureSet picker
        for i, texture_set in enumerate(substance_painter.textureset.all_texture_sets()):
            check_box = QCheckBox(text=f"{texture_set.name()} (Set to: Include all)")
            check_box.setTristate(True)
            check_box.setCheckState(Qt.CheckState.Checked)
            check_box.stateChanged.connect(lambda state, name=texture_set.name(), pos=i, checkbox=check_box:
                                           self.set_textureset_state(name, state, pos, checkbox))

            check_box.setStyleSheet(
                "QCheckBox::indeterminate"
                "{"
                "background-color : #4f4016;"
                "}"
                "QCheckBox::checked"
                "{"
                "background-color : #1b4f16;"
                "}"
            )

            layout.addWidget(check_box)

        # Dilation distance setter
        label_dilation = QLabel("Set dilation distance")
        label_dilation.setStyleSheet("font-size: 16px; font-weight: 700; margin-top: 10px")
        layout.addWidget(label_dilation)

        dilation_distance_box = QSpinBox()
        dilation_distance_box.setRange(0, 20000)
        dilation_distance_box.setSingleStep(1)
        dilation_distance_box.setValue(16)
        dilation_distance_box.valueChanged.connect(self.dilation_distance_selector)
        layout.addWidget(dilation_distance_box)

        # Add export button to window
        layout.addWidget(export_button)

        # MeowMeowExport button
        meow_button = QPushButton("Export now Meow!")
        meow_button.clicked.connect(self.generate_textures)
        layout.addWidget(meow_button)

        self.setCentralWidget(container)

    def dilation_distance_selector(self, value: int):
        print(value)
        self.dilation_distance = value

    def pick_folder(self, default_export_path: str):
        export_folder_dialog = QFileDialog.getExistingDirectory(
            caption="Select export folder",
            dir=default_export_path,
            options=QFileDialog.Option.ShowDirsOnly
        )

        if export_folder_dialog:
            print("Chosen folder:", export_folder_dialog)
            self.export_path = export_folder_dialog

    def set_textureset_state(self, name: str, state: int, pos: int, checkbox: QCheckBox):
        print(name, state)
        self.textureset_options[pos] = state

        if state == 0:
            checkbox.setText(f"{name} (Set to: Ignore all)")

        elif state == 1:
            checkbox.setText(f"{name} (Set to: Ignore invisible layers)")

        else:
            checkbox.setText(f"{name} (Set to: Include all)")

    def generate_textures(self):
        # Check if active project is loaded
        try:
            export_path = self.export_path
        except ProjectError as e:
            substance_painter.logging.log(substance_painter.logging.ERROR, channel="Meow Meow Export", message=str(e))
            return

        export_path = Path(os.path.join(os.path.dirname(export_path), "meow_meow_export"))  # Root export path (for psd)
        cache_path = export_path.joinpath(".cache")  # Path for exported pngs

        # Create folder if it doesn't exist
        cache_path.mkdir(parents=True, exist_ok=True)

        perform(save_state)  # Save visibility info

        perform(set_visibility, func_layer_kwargs=dict(visible=False))  # Hide all layer nodes
        perform(export, func_layer_kwargs=dict(export_path=cache_path,
                                               extra_path=export_path,
                                               dilation_distance=self.dilation_distance), filters=self.textureset_options)
        perform(reset_visibility)
        generate_psds(delete_on_success=True,
                      export_path=export_path,
                      cache_path=cache_path,
                      filters=self.textureset_options)


def show_main_window():
    window = MainWindow(substance_painter.ui.get_main_window())
    window.show()


def start_plugin():
    # Create a text widget for a menu
    Action = QtGui.QAction(text="Meow Meow Export")
    # Action.triggered.connect(generate_textures)
    Action.triggered.connect(show_main_window)

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

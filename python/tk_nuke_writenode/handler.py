# Copyright (c) 2013 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import sgtk
import nuke
import os
import re
from .create_dialog import WriteNodePanel

# standard toolkit logger
logger = sgtk.platform.get_logger(__name__)

# Hidden knob on the ShotGrid write node that holds the path that was
# rendered the last time (local or farm).
LAST_RENDERED_KNOB = "lastRenderedPath"


class NukeWriteNodeHandler(object):
    """
    Main application
    """

    def __init__(self):
        self.app = sgtk.platform.current_bundle()
        self.sg = self.app.shotgun

    def render_local(self, node):
        """Render the specified node.
        Will create paths and render

        Args:
            node (attribute): node to render
        """

        # Set paths for node
        prepared_write = self.__prepare_write(node)

        # If paths are set, render
        if prepared_write:
            node.knob("Render").execute()

        # If paths hasn't been set, let user know something went wrong
        else:
            nuke.message("Something went wrong.")

    def render_farm(self, node):
        """Submit the node to render on farm.
        Will create paths and submit

        Args:
            node (attribute): node to submit to farm
        """

        # Set parameters for node before rendering
        prepared_write = self.__prepare_write(node)
        if prepared_write:

            # Using https://github.com/gillesvink/NukeDeadlineSubmission
            import deadline_submission

            # Submit node for rendering on farm
            submit = deadline_submission.DeadlineSubmission().submit(node)

            # If submitted, increment save to not touch script while rendering
            if submit:
                self.__increment_save()
        else:
            nuke.message("Something went wrong.")

    def create_writenode(self):
        """This function will use the Write Node create panel
        and set up the node correctly.

        It will check if the node already exists, and if so, it will show
        the user the already existing node.
        """

        # Get all write nodes to check if the created name exists
        write_nodes = self.get_all_write_nodes()

        # Create initial list to add all names to
        write_names = []
        for node in write_nodes:
            node = nuke.toNode(node)

            # Append name to list
            write_names.append(node["output"].value())

        # Get all options possible for write nodes
        write_node_settings = self.__get_write_node_options()

        # Get default names
        main_category_name = self.app.get_setting("main_category_name")
        main_write_name = self.app.get_setting("main_write_name")
        default_category = self.app.get_setting("default_category")

        # Give variables to write node panel
        write_node_data = WriteNodePanel(
            default_category,
            write_node_settings,
            main_category_name,
            main_write_name,
        )

        # Set panel minimum width and height
        write_node_data.setMinimumSize(200, 190)

        # Open panel, and if user proceeded, continue
        if write_node_data.showModalDialog():

            # Get output name
            output_name = write_node_data.output_knob.value()

            # Validate name
            if not len(output_name) > 0:
                nuke.message("No name specified, please specify a name.")
                return

            # If name already exists, show message and go to node
            if output_name in write_names:
                nuke.message("Write node %s already existing." % output_name)
                self.go_to_write_node(output_name)
                return

            # Set regex for output name validation
            regex = re.compile(r"[a-zA-Z0-9]*$")

            # Validate name
            if not regex.match(output_name):
                nuke.message(
                    "Name contains illegal characters. Please only use letters "
                    "and numbers. \n[a-zA-Z0-9]"
                )
                return

            # Get category name
            category = write_node_data.category_knob.value()

            # If category is another than main category, but has the main write
            # name in it, it is not allowed because only one "main" node is
            # allowed to exist, in the "main" category
            if category != main_category_name:
                if output_name == main_write_name:
                    nuke.message(
                        "Name %s only allowed on %s category."
                        % (output_name, main_category_name)
                    )
                    return

            write_data = write_node_data.data_knob.value()

            self.__create_write(
                write_node_settings, category, output_name, write_data
            )

    def knob_changed(self, node, knob):
        """Function called whenever any knob changes on
        the ShotGrid write node

        Args:
            node (attribute): node to process
            knob (attribute): knob that has changed
        """

        if knob.name() == "dataType":
            # Get the settings the node has to be set to
            configuration = self.__get_node_settings(node)

            # Get internal node settings
            settings = configuration.get("settings")

            # Open to edit internal node
            with node:
                # Get node attribute
                write_node = nuke.toNode("Write1")

                # Set file type
                write_node["file_type"].setValue(
                    configuration.get("file_type")
                )

                # Set all knob settings
                for knob, setting in settings.items():

                    try:
                        write_node[knob].setValue(setting)

                    except Exception as e:
                        logger.debug(
                            "Could not apply %s to the knob %s, because %s"
                            % (setting, knob, str(e))
                        )

            logger.debug("Updated node settings")

    def read_from_selected(self):
        """Create read node from the selected node"""

        try:
            # Select current selected node
            node = nuke.selectedNode()

            # Create read from write with specified node
            self.read_from_write(node)

        # If something went wrong, e.g no node selected, let user know
        except Exception as error:
            nuke.message(str(error))

    def read_from_write(self, node):
        """Create read node from node.

        Will add a read node with the last render of this node underneath
        it. The path that was actually rendered is stored on the node when
        rendering, because the script (and with it the write path) is
        versioned up after a farm submission.

        Args:
            node (attribute): node to create read node from
        """

        # Make sure we are in nuke root level
        with nuke.root():

            current_path = node["file"].value()
            last_rendered_path = self.get_last_rendered_path(node)

            if not current_path and not last_rendered_path:
                nuke.message(
                    "This write node has not rendered yet, please render"
                    " before creating a read from this write node."
                )
                return

            # Paths to try, most likely first. Older versions are only
            # scanned when nothing else is found (nodes rendered before
            # the rendered path was stored on the node).
            candidates = []
            for path in (last_rendered_path, current_path):
                if path and path not in candidates:
                    candidates.append(path)

            result = None
            for path in candidates:
                result = self.__find_render(node, path)
                if result:
                    break

            if not result and current_path:
                result = self.__find_previous_version_render(
                    node, current_path
                )

            if not result:
                nuke.message(
                    "Could not find any rendered frames for %s.\n\n"
                    "Last rendered path:\n%s"
                    % (node.name(), last_rendered_path or current_path)
                )
                return

            render_path, frames = result

            # Create read node
            read_node = nuke.createNode("Read")

            # Set path
            read_node["file"].fromUserText(render_path)

            # Set colorspace
            read_node["colorspace"].setValue(self.get_colorspace(node))

            # Set parameters
            start_frame = int(min(frames))
            last_frame = int(max(frames))

            read_node["first"].setValue(start_frame)
            read_node["origfirst"].setValue(start_frame)
            read_node["last"].setValue(last_frame)
            read_node["origlast"].setValue(last_frame)

            # Set position
            read_node["xpos"].setValue(node.xpos())
            read_node["ypos"].setValue(node.ypos() + 50)

    def __find_render(self, node, render_path):
        """Look for a rendered frame sequence for a work render path.
        If the render has been published, the published path is used.

        Args:
            node (attribute): ShotGrid write node
            render_path (str): work render path (with frame spec)

        Returns:
            tuple: (path, frame list) or None if nothing was found
        """
        paths = []
        try:
            if self.__is_published(render_path):
                paths.append(self.__get_published_path(node, render_path))
        except Exception as e:
            logger.debug(
                "Could not check publish for %s: %s" % (render_path, str(e))
            )
        paths.append(render_path)

        for path in paths:
            path = path.replace(os.sep, "/")
            directory = os.path.dirname(path)
            if not os.path.isdir(directory):
                continue

            for sequence_path, frames in self.__get_frame_sequences(
                directory
            ):
                if sequence_path.replace(os.sep, "/") == path:
                    return path, frames

        return None

    def __find_previous_version_render(self, node, render_path):
        """Fallback: walk back through older versions of the render path
        and return the newest one that has frames on disk.

        Args:
            node (attribute): ShotGrid write node
            render_path (str): current work render path

        Returns:
            tuple: (path, frame list) or None if nothing was found
        """
        try:
            render_template = self.get_node_render_template(node)
            fields = render_template.get_fields(render_path)
        except Exception as e:
            logger.debug("Could not get fields for %s: %s" % (render_path, e))
            return None

        version = fields.get("version")
        if not version:
            return None

        # Limit the search, every version may do a ShotGrid query
        oldest_version = max(version - 25, 0)
        for previous_version in range(version - 1, oldest_version, -1):
            fields["version"] = previous_version
            path = render_template.apply_fields(fields).replace(os.sep, "/")
            result = self.__find_render(node, path)
            if result:
                return result

        return None

    def get_last_rendered_path(self, node):
        """Get the path this node rendered to the last time.

        Args:
            node (attribute): ShotGrid write node

        Returns:
            str: last rendered path, empty string if unknown
        """
        knob = node.knobs().get(LAST_RENDERED_KNOB)
        if knob is None:
            return ""
        return knob.value()

    @staticmethod
    def __store_rendered_path(node, render_path):
        """Store the path that is being rendered on the node, so the read
        can still be created after the script is versioned up.

        Args:
            node (attribute): ShotGrid write node
            render_path (str): path being rendered
        """
        knob = node.knobs().get(LAST_RENDERED_KNOB)
        if knob is None:
            knob = nuke.String_Knob(
                LAST_RENDERED_KNOB, "last rendered path"
            )
            knob.setFlag(nuke.INVISIBLE)
            node.addKnob(knob)
        knob.setValue(render_path)

    def convert_placeholder_nodes(self):
        """Search existing placeholder nodes, and creates write nodes
        accordingly with the correct settings.

        This function can be used in templates to set write node types,
        and create correct write nodes while loading script for first time"""

        # Filter all nodes to scan for ModifyMetaData nodes
        for placeholder_node in nuke.allNodes("ModifyMetaData"):

            # If placeholder node starts with ShotGridWriteNodePlaceholder, we
            # know this is the node we want to replace with a
            # correct write node
            if not placeholder_node.name().startswith(
                "ShotGridWriteNodePlaceholder"
            ):
                continue

            try:
                self.__convert_placeholder(placeholder_node)
            except Exception as e:
                logger.warning(
                    "Could not convert %s: %s"
                    % (placeholder_node.name(), str(e))
                )

    def __convert_placeholder(self, placeholder_node):
        """Replace one placeholder node with a ShotGrid write node, keeping
        its position and all connections (input and downstream nodes).

        Args:
            placeholder_node (attribute): ModifyMetaData placeholder node

        Returns:
            attribute: created write node
        """
        # Get write node settings
        write_node_settings = self.__get_write_node_options()

        # Get provided data from metadata node. Read the knob directly so
        # it also works when the placeholder has no (valid) input.
        metadata = {}
        for key, value in re.findall(
            r"\{set\s+(\S+)\s+\"?([^}\"]*)\"?\}",
            placeholder_node["metadata"].toScript(),
        ):
            metadata[key] = value.strip()
        if not metadata.get("category"):
            metadata = placeholder_node.metadata()
        category = metadata.get("category")
        output_name = metadata.get("output")
        data_type = self.__resolve_data_type(
            category, metadata.get("data_type")
        )

        # Get position and connections to replace node
        placeholder_xpos = placeholder_node.xpos()
        placeholder_ypos = placeholder_node.ypos()
        placeholder_input = placeholder_node.input(0)

        downstream = []
        for dependent in placeholder_node.dependent(
            nuke.INPUTS | nuke.HIDDEN_INPUTS, forceEvaluate=False
        ):
            for index in range(dependent.inputs()):
                if dependent.input(index) == placeholder_node:
                    downstream.append((dependent, index))

        # Deselect everything, so the new node is not auto-connected
        for node in nuke.selectedNodes():
            node["selected"].setValue(False)

        # Delete the old node
        nuke.delete(placeholder_node)

        # Create write node
        write_node = self.__create_write(
            write_node_settings, category, output_name, data_type
        )

        # Restore position and connections
        write_node["xpos"].setValue(placeholder_xpos)
        write_node["ypos"].setValue(placeholder_ypos)
        write_node.setInput(0, placeholder_input)
        for dependent, index in downstream:
            dependent.setInput(index, write_node)

        return write_node

    def add_callbacks(self):
        """Adds callbacks on script load"""
        nuke.addOnScriptLoad(self.convert_placeholder_nodes, nodeClass="Root")
        nuke.addOnScriptLoad(self.sync_write_nodes, nodeClass="Root")
        nuke.addOnScriptSave(self.update_all_paths, nodeClass="Root")
        nuke.addKnobChanged(self._on_controls_changed, nodeClass="NoOp")

    def remove_callbacks(self):
        """Removes callbacks on destroy"""
        nuke.removeOnScriptLoad(
            self.convert_placeholder_nodes, nodeClass="Root"
        )
        nuke.removeOnScriptLoad(self.sync_write_nodes, nodeClass="Root")
        nuke.removeOnScriptSave(self.update_all_paths, nodeClass="Root")
        nuke.removeKnobChanged(self._on_controls_changed, nodeClass="NoOp")

    def _on_controls_changed(self):
        """knobChanged callback for the sgWriteControls NoOp inside every
        ShotGrid write node. Applies the new profile and path when the
        data type is changed."""
        try:
            controls = nuke.thisNode()
            if controls.name() != "sgWriteControls":
                return
            if nuke.thisKnob().name() != "dataType":
                return
            parent_name = controls.fullName().rsplit(".", 1)[0]
            parent = nuke.toNode(parent_name) if "." in controls.fullName() else None
            if parent is None or "isShotGridWriteNode" not in parent.knobs():
                return
            self.knob_changed(parent, nuke.thisKnob())
            self.update_path(parent)
        except Exception as e:
            logger.debug("Write node knobChanged failed: %s" % str(e))

    def update_path(self, node):
        """Calculate the render path for a ShotGrid write node and set it on
        the internal Write, so artists see the correct path straight away.

        Args:
            node (attribute): ShotGrid write node

        Returns:
            bool: True if the path could be set
        """
        try:
            configuration = self.__get_node_settings(node)
            if not configuration:
                return False
            render_path = self.__calculate_path(node, configuration)
        except Exception as e:
            # Script not saved in a ShotGrid work area yet, etc.
            logger.debug(
                "Could not calculate path for %s: %s" % (node.name(), str(e))
            )
            return False

        with node:
            write_node = nuke.toNode("Write1")
            if write_node["file"].value() != render_path:
                write_node["file"].setValue(render_path)
        return True

    def update_all_paths(self):
        """Set the render path on every ShotGrid write node in the script."""
        for node_name in self.get_all_write_nodes():
            node = nuke.toNode(node_name)
            if node:
                self.update_path(node)

    def sync_write_nodes(self):
        """Bring every ShotGrid write node in the script in line with the
        current pipeline configuration.

        - Renamed write node profiles (setting ``renamed_write_nodes``) are
          mapped to their new name.
        - The data menu is refreshed with the profiles currently configured.
        - The settings of the profile (compression, datatype, ...) are applied
          to the internal Write node, so artists always see what will be
          rendered. The channels knob is left alone, as artists may change it.
        """
        write_node_settings = self.__get_write_node_options()

        for node_name in self.get_all_write_nodes():
            node = nuke.toNode(node_name)
            if not node:
                continue

            try:
                category = node["category"].value()
                data_types = write_node_settings.get(category)
                if not data_types:
                    logger.warning(
                        "Write node %s uses unknown category %s, skipping."
                        % (node_name, category)
                    )
                    continue

                data_type = self.__resolve_data_type(
                    category, node["dataType"].value()
                )

                # Refresh the available options and set the (new) name
                node["dataType"].setValues(data_types)
                if data_type in data_types:
                    node["dataType"].setValue(data_type)
                else:
                    logger.warning(
                        "Write node %s uses unknown data type %s."
                        % (node_name, data_type)
                    )
                    continue

                configuration = self.__get_node_settings(node)
                if not configuration:
                    continue

                tile_color = configuration.get("tile_color")
                if tile_color is not None:
                    node["tile_color"].setValue(tile_color)

                with node:
                    write_node = nuke.toNode("Write1")
                    write_node["file_type"].setValue(
                        configuration.get("file_type")
                    )
                    for knob, setting in configuration.get(
                        "settings", {}
                    ).items():
                        if knob == "channels":
                            continue
                        try:
                            write_node[knob].setValue(setting)
                        except Exception as e:
                            logger.debug(
                                "Could not apply %s to the knob %s, because %s"
                                % (setting, knob, str(e))
                            )

                self.update_path(node)

                logger.debug("Synced write node %s" % node_name)

            except Exception as e:
                logger.warning(
                    "Could not sync write node %s: %s" % (node_name, str(e))
                )

    def __resolve_data_type(self, category, data_type):
        """Map an old (renamed) write node profile name to its current name.

        Args:
            category (str): category of the write node
            data_type (str): profile name stored on the node

        Returns:
            str: current profile name
        """
        renamed = self.app.get_setting("renamed_write_nodes") or {}

        # Allow a per-category mapping ({category: {old: new}}) as well as
        # a global one ({old: new})
        category_map = renamed.get(category)
        if isinstance(category_map, dict) and data_type in category_map:
            return category_map[data_type]

        new_name = renamed.get(data_type)
        if isinstance(new_name, str):
            return new_name

        return data_type

    def update_read_nodes(self):
        """Updates all read nodes to use published path instead
        of work path
        """
        # Get all write nodes, to retrieve rendered file paths
        write_nodes = self.get_all_write_nodes()

        # Build dictionary containing both write node and file path
        image_sequences = {}

        # Iterate trough all write nodes
        for write_node in write_nodes:

            # We only got a name, so we need to get the attributes
            write_node = nuke.toNode(write_node)

            # Get render path
            render_path = write_node["file"].value()

            # Append write node to the rendered path
            image_sequences[render_path] = write_node

            # Also map the last rendered path, the current path is already
            # versioned up after a farm submission
            last_rendered_path = self.get_last_rendered_path(write_node)
            if last_rendered_path:
                image_sequences[last_rendered_path] = write_node

        # Filter for all read nodes
        all_nodes = nuke.allNodes("Read")

        # Iterate trough all read nodes
        for node in all_nodes:

            # If path in read node is in the directory we just created
            # set publish path
            read_path = node["file"].value()
            if read_path in image_sequences.keys():

                # Calculate publish path
                write_node = image_sequences.get(read_path)
                published_path = self.__get_published_path(
                    write_node, read_path
                )

                # Set publish path to read node
                node["file"].setValue(published_path)

    @staticmethod
    def get_all_write_nodes():
        """Get all write nodes in list

        Returns:
            list: write nodes in current script
        """

        # Find all groups in script
        all_nodes = nuke.allNodes("Group")

        # Create list to add nodes to
        write_nodes = []

        # Iterate trough all group nodes
        for node in all_nodes:
            # In the write nodes, we have a special knob
            # to help identify this group as a write node
            # If the group has the node "isShotGridWriteNode" we
            # know this is a ShotGrid write node
            if node.knob("isShotGridWriteNode"):

                # If it is a ShotGrid write node, add it to the list
                write_nodes.append(node.name())

        return write_nodes

    @staticmethod
    def go_to_write_node(output_name):
        """Will move the DAG towards the write node using
        the specified output_name

        Args:
            output_name (_type_): _description_
        """
        # Filter all nodes to search for group
        all_nodes = nuke.allNodes("Group")
        for node in all_nodes:

            # If write node has "isShotGridWriteNode" knob, it
            # is indeed a ShotGrid writenode
            if node["isShotGridWriteNode"]:

                # If the node has the specified output_name, we
                # know this is the node we are search for
                if node["output"].value() == output_name:

                    # Position DAG to position of node
                    nuke.zoom(3, [node.xpos(), node.ypos()])

    def get_node_render_template(self, node):
        """Get  render template used by the specified node

        Args:
            node (attribute): node to get render template used

        Returns:
            attribute: render template from templates.yml
        """

        # Get configuration for node
        configuration = self.__get_node_settings(node)

        # Get render template
        render_template = configuration.get("render_template")

        # Find template in templates.yml
        render_template = self.app.get_template_by_name(render_template)

        return render_template

    def get_node_publish_template(self, node):
        """Get publish template used by the specified node

        Args:
            node (attribute): node to get publish template used

        Returns:
            attribute: publish template from templates.yml
        """
        # Get configuration for node
        configuration = self.__get_node_settings(node)

        # Get publish template
        publish_template = configuration.get("publish_template")

        # Find template in templates.yml
        publish_template = self.app.get_template_by_name(publish_template)

        return publish_template

    def get_published_status(self, node):
        """This function will check on ShotGrid if there is a publish with
        exactly the same name on the project.

        Args:
            node (attribute): node to retrieve publish status

        Returns:
            bool: If there is a publish existing it will return
            "True", otherwise return a "False" value
        """
        return self.__is_published(node["file"].value())

    def __is_published(self, render_path):
        """Check on ShotGrid if there is a publish on the project with the
        same file name as the render path.

        Args:
            render_path (str): render path to check

        Returns:
            bool: True if a publish with this name exists
        """
        file_name = os.path.basename(render_path)

        current_engine = sgtk.platform.current_engine()
        project_id = current_engine.context.project["id"]

        filters = [
            ["project", "is", {"type": "Project", "id": project_id}],
            ["code", "is", file_name],
        ]

        return self.sg.find_one("PublishedFile", filters) is not None

    @staticmethod
    def get_colorspace(node):
        """Get colorspace node is rendering

        Args:
            node (attribute): node to get colorspace

        Returns:
            str: colorspace
        """
        # Open node to get the write node values
        with node:
            write_node = nuke.toNode("Write1")

            # Get colorspace knob value
            colorspace = write_node["colorspace"].value()

            return colorspace

    def __create_write(
        self, write_node_settings, category, output_name, data_type
    ):
        """Create write node using specified settings

        Args:
            write_node_settings (dict): containing all parameters to setup node
            category (str): category user has chosen to setup node
            output_name (str): output name to render
            data_type (str): datatype to use

        Returns:
            attribute: created write node
        """

        # Create write node
        created_write = nuke.createNode("sgWrite")

        # Set output knob value to use specified output_name
        created_write["output"].setValue(output_name)

        # Get all categories and add to knob
        categories = []
        for key, value in write_node_settings.items():
            categories.append(key)

        created_write["category"].setValues(categories)

        # Set category user specified
        created_write["category"].setValue(category)

        # Get all datatypes from pipeline settings
        data_types = write_node_settings.get(category)
        created_write["dataType"].setValues(data_types)

        # Set datatype knob to use datatype user specified
        created_write["dataType"].setValue(data_type)

        # Get the settings the node has to be set to
        configuration = self.__get_node_settings(created_write)
        created_write["tile_color"].setValue(configuration.get("tile_color"))

        # Get internal node settings
        settings = configuration.get("settings")

        # Open to edit internal node
        with created_write:
            # Get node attribute
            write_node = nuke.toNode("Write1")

            # Set file type
            write_node["file_type"].setValue(configuration.get("file_type"))

            # Set all knob settings
            for knob, setting in settings.items():

                try:
                    write_node[knob].setValue(setting)

                except Exception as e:
                    logger.debug(
                        "Could not apply %s to the knob %s, because %s"
                        % (setting, knob, str(e))
                    )

        self.update_path(created_write)

        return created_write

    def __get_write_node_options(self):
        """This function will build a dictionary containing
        the category name and write node names

        Returns:
            dict: category name and write node names

            For example: {
            "main": ["exr (dwaa 16bit)"],
            "prerender": [
                "exr (dwaa 16bit)",
                "exr (zip 16bit)",
                "exr (zip 32bit)",
            ],
            "mattepainting": ["tiff (deflate 16 bit)"],
        }
        """

        # Get categories from settings
        categories = self.app.get_setting("categories")

        # Create initial dictionary to add settings to
        write_node_settings = {}
        for category in categories:

            # Get category name
            category_name = category.get("category_name")

            # Get write node names
            write_nodes = category.get("write_nodes")
            write_node_names = []
            for write_node in write_nodes:
                write_node_name = write_node.get("name")
                write_node_names.append(write_node_name)

            # Add list with names to category
            write_node_settings[category_name] = write_node_names

        return write_node_settings

    def __get_node_settings(self, node):
        """This function will go trough the dictionary to get the correct
        configuration dictionary matching the settings of the node

        Args:
            node (attribute): node to setup

        Returns:
            dict: containing all settings to set write node

            For example: {
            "name": "exr (dwaa 16bit)",
            "file_type": "exr",
            "render_template": "nuke_shot_render_work",
            "publish_template": "nuke_shot_render_pub",
            "tile_color": 2365546239,
            "settings": {
                "colorspace": "scene_linear",
                "datatype": "16 bit half",
                "channels": "rgba",
                "compression": "DWAA",
            },
        }
        """

        # Get required information to get settings
        write_category = node["category"].value()
        data_type = self.__resolve_data_type(
            write_category, node["dataType"].value()
        )

        categories = self.app.get_setting("categories")

        # Iterate trough all categories to find our category
        for category in categories:

            # If category name matches our name, it is the category
            if category.get("category_name") == write_category:

                # Search trough all possible write nodes
                for write_node in category.get("write_nodes"):

                    # If write node matches our data type, we need
                    # these settings
                    if write_node.get("name") == data_type:

                        return write_node

    def __calculate_path(self, node, configuration):
        """Calculate write path using template provided in configuration

        Args:
            node (attribute): node to calculate path
            configuration (dict): configuration containing template

        Returns:
            str: file path for rendering
        """

        # Get render template from settings
        render_template = configuration.get("render_template")

        # Search for render template in templates.yml
        render_template = self.app.get_template_by_name(render_template)

        # Get script template
        script_template = self.app.get_template("template_script_work")

        # Get values for fields
        current_file = nuke.root().name()

        # Get fields already set by script path
        fields = script_template.get_fields(current_file)

        fields["SEQ"] = "FORMAT: %d"
        fields["output"] = node["output"].value()

        # Calculate path
        render_path = render_template.apply_fields(fields).replace(os.sep, "/")

        return render_path

    def __prepare_write(self, node):
        """Set all parameters when rendering.
        Will calculate paths and set them

        Args:
            node (attribute): node to process

        Returns:
            bool: returns True if processing is completed, False if failed
        """

        # Get node settings for selected node
        configuration = self.__get_node_settings(node)
        if configuration:
            # Get render path
            render_path = self.__calculate_path(node, configuration)
            settings = configuration.get("settings")

            # Now we have all the parameters necessary, lets set them
            with node:

                write_node = nuke.toNode("Write1")
                write_node["file"].setValue(render_path)
                for knob, setting in settings.items():

                    # Prevent to change the channels knob
                    if not knob == "channels":
                        write_node[knob].setValue(setting)

            # Remember what is rendered, the path changes when the
            # script is versioned up after a farm submission
            self.__store_rendered_path(node, render_path)

            # Make sure directory exists
            render_directory = os.path.dirname(render_path)

            # If directory doesn't exist, create it
            if not os.path.isdir(render_directory):
                os.makedirs(render_directory)

            return True

        else:
            nuke.message(
                "Could not find configuration for node %s"
                % node["name"].value()
            )
            return False

    def __increment_save(self):
        """Increment save the current script"""

        # Get script template
        script_template = self.app.get_template("template_script_work")
        script_file = nuke.root().name()

        # Get fields
        fields = script_template.get_fields(script_file)

        # Increment version number
        fields["version"] = fields["version"] + 1

        # Calculate path
        new_script_file = script_template.apply_fields(fields).replace(
            os.sep, "/"
        )

        # Save script with incremented path
        nuke.scriptSaveAs(new_script_file)

    def __get_published_path(self, node, path):
        """Calculate path for published render path

        Args:
            node (attribute): node to calculate path for
            path (str): file path set in write node

        Returns:
            str: path used for publishing
        """
        # Get render template and get fields from it
        render_template = self.get_node_render_template(node)
        render_fields = render_template.get_fields(path)

        # Get publish template
        publish_template = self.get_node_publish_template(node)

        # Calculate path with fields from render path
        publish_path = publish_template.apply_fields(render_fields).replace(
            os.sep, "/"
        )

        return publish_path

    @staticmethod
    def __get_frame_sequences(folder, extensions=None, frame_spec=None):
        """Copied from the publisher app, and customized to return
        file sequences with frame lists instead of filenames

        Args:
            folder (str): folder to scan for frame sequences
            extensions (str, optional): extension to search for. Defaults
            to None (all).
            frame_spec (str, optional): if required another frame spec
            can be used
            for returning. Defaults to None (%04d).

        Returns:
            list: containing all frame sequences in specified folder
        """

        FRAME_REGEX = re.compile(r"(.*)([._-])(\d+)\.([^.]+)$", re.IGNORECASE)

        # list of already processed file names
        processed_names = {}

        # examine the files in the folder
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)

            if os.path.isdir(file_path):
                # ignore subfolders
                continue

            # see if there is a frame number
            frame_pattern_match = re.search(FRAME_REGEX, filename)

            if not frame_pattern_match:
                # no frame number detected. carry on.
                continue

            prefix = frame_pattern_match.group(1)
            frame_sep = frame_pattern_match.group(2)
            frame_str = frame_pattern_match.group(3)
            extension = frame_pattern_match.group(4) or ""

            # filename without a frame number.
            file_no_frame = "%s.%s" % (prefix, extension)

            if file_no_frame in processed_names:
                # already processed this sequence. add the framenumber to the list, later we can use this to
                # determine the framerange
                processed_names[file_no_frame]["frame_list"].append(frame_str)
                continue

            if extensions and extension not in extensions:
                # not one of the extensions supplied
                continue

            # make sure we maintain the same padding
            if not frame_spec:
                padding = len(frame_str)
                frame_spec = "%%0%dd" % (padding,)

            seq_filename = "%s%s%s" % (prefix, frame_sep, frame_spec)

            if extension:
                seq_filename = "%s.%s" % (seq_filename, extension)

            # build the path in the same folder
            seq_path = os.path.join(folder, seq_filename)

            # remember each seq path identified and a list of files matching the
            # seq pattern
            processed_names[file_no_frame] = {
                "sequence_path": seq_path,
                "frame_list": [frame_str],
            }

        # build the final list of sequence paths to return
        frame_sequences = []
        for file_no_frame in processed_names:
            seq_info = processed_names[file_no_frame]
            seq_path = seq_info["sequence_path"]

            frame_sequences.append((seq_path, seq_info["frame_list"]))

        return frame_sequences

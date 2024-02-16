# Copyright (c) 2013 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

"""
Tank Write Node for Nuke

"""

import os
import nuke
import tank
from tank import TankError


class NukeWriteNode(tank.platform.Application):
    def init_app(self):
        """
        Called as the application is being initialized
        """
        # import module and create handler
        tk_nuke_writenode = self.import_module("tk_nuke_writenode")
        self.__write_node_handler = tk_nuke_writenode.TankWriteNodeHandler(self)

        # patch handler onto nuke module for access in WriteNode knobs
        nuke._shotgun_write_node_handler = self.__write_node_handler
        # and for backwards compatibility!
        nuke._tank_write_node_handler = self.__write_node_handler

        # add WriteNodes to nuke menu
        self.__add_write_node_commands()

        # add callbacks:
        self.__write_node_handler.add_callbacks()

    @property
    def context_change_allowed(self):
        """
        Specifies that context changes are allowed.
        """
        return True

    def destroy_app(self):
        """
        Called when the app is unloaded/destroyed
        """
        self.log_debug("Destroying tk-nuke-writenode app")

        # remove any callbacks that were registered by the handler:
        self.__write_node_handler.remove_callbacks()

        # clean up the nuke module:
        if hasattr(nuke, "_shotgun_write_node_handler"):
            del nuke._shotgun_write_node_handler
        if hasattr(nuke, "_tank_write_node_handler"):
            del nuke._tank_write_node_handler

    def post_context_change(self, old_context, new_context):
        """
        Handles refreshing the render paths of all Shotgun write nodes
        after a context change has been completed.

        :param old_context: The sgtk.context.Context being switched from.
        :param new_context: The sgtk.context.Context being switched to.
        """

        self.__write_node_handler.populate_profiles_from_settings()
        self.__write_node_handler.populate_script_template()
        self.__add_write_node_commands(new_context)

        # now the writenode handler settings have been updated we can update the paths of all existing PTR writenodes
        for node in self.get_write_nodes():
            # Although there are nuke callbacks to handle setting up the new node; on automatic context change
            # these are triggered before the engine changes context, so we must manually call it here.
            # this will force the path to reset and the profiles to be rebuilt.
            self.__write_node_handler.setup_new_node(node)

    def process_placeholder_nodes(self):
        """
        Convert any placeholder nodes to TK Write Nodes
        """
        self.__write_node_handler.process_placeholder_nodes()

    # interface for other apps to query write node info:
    #

    # access general information:
    def get_write_nodes(self):
        """
        Return list of all write nodes
        """
        return self.__write_node_handler.get_nodes()

    def get_node_name(self, node):
        """
        Return the name for the specified node
        """
        return self.__write_node_handler.get_node_name(node)

    def get_node_profile_name(self, node):
        """
        Return the name of the profile the specified node
        is using
        """
        return self.__write_node_handler.get_node_profile_name(node)

    def get_node_tank_type(self, node):
        """
        Return the tank type for the specified node

        Note: Legacy version with old 'Tank Type' name - use
        get_node_published_file_type instead!
        """
        return self.__write_node_handler.get_node_tank_type(node)

    def get_node_published_file_type(self, node):
        """
        Return the published file type for the specified node
        """
        return self.__write_node_handler.get_node_tank_type(node)

    def is_node_render_path_locked(self, node):
        """
        Determine if the render path for the specified node
        is locked.  The path will become locked if the cached
        version of the path no longer matches the computed
        path (using the appropriate render template).  This
        can happen if the file is moved on disk or if the template
        is changed.
        """
        return self.__write_node_handler.render_path_is_locked(node)

    # access full-res render information:
    def get_node_render_path(self, node):
        """
        Return the render path for the specified node
        """
        return self.__write_node_handler.compute_render_path(node)

    def get_node_render_files(self, node):
        """
        Return the list of rendered files for the node
        """
        return self.__write_node_handler.get_files_on_disk(node)

    def get_node_render_template(self, node):
        """
        Return the render template for the specified node
        """
        return self.__write_node_handler.get_render_template(node)

    def get_node_publish_template(self, node):
        """
        Return the publish template for the specified node
        """
        return self.__write_node_handler.get_publish_template(node)

    # access proxy-res render information:
    def get_node_proxy_render_path(self, node):
        """
        Return the render path for the specified node
        """
        return self.__write_node_handler.compute_proxy_path(node)

    def get_node_proxy_render_files(self, node):
        """
        Return the list of rendered files for the node
        """
        return self.__write_node_handler.get_proxy_files_on_disk(node)

    def get_node_proxy_render_template(self, node):
        """
        Return the render template for the specified node
        """
        return self.__write_node_handler.get_proxy_render_template(node)

    def get_node_proxy_publish_template(self, node):
        """
        Return the publish template for the specified node
        """
        return self.__write_node_handler.get_proxy_publish_template(node)

    # useful utility functions:
    def generate_node_thumbnail(self, node):
        """
        Generate a thumnail for the specified node
        """
        return self.__write_node_handler.generate_thumbnail(node)

    def reset_node_render_path(self, node):
        """
        Reset the render path of the specified node.  This
        will force the render path to be updated based on
        the current script path and configuration.

        Note, this should really never be needed now that the
        path is reset automatically when the user changes something.
        """
        self.__write_node_handler.reset_render_path(node)

    def convert_to_write_nodes(self, show_warning=False):
        """
        Convert all Shotgun write nodes found in the current Script to regular
        Nuke Write nodes.  Additional toolkit information will be stored on
        additional user knobs named 'tk_*'

        :param show_warning: Optional bool that sets whether a warning box should be displayed to the user;
         defaults to False.
        :param create_folders: Optional bool that sets whether the operation will create the required output folders;
         defaults to False
        """

        # By default we want to convert the write nodes, unless the warning is shown and the user chooses to abort.
        continue_with_convert = True

        if show_warning:
            # defer importing the QT module so the app doesn't require QT unless running this method with the warning.
            from sgtk.platform.qt import QtGui

            res = QtGui.QMessageBox.question(
                None,
                "Convert All PTR Write Nodes?",
                "This will convert all Flow Production Tracking write nodes to standard "
                "write nodes."
                "\nOK to proceed?",
                QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
            )

            if res != QtGui.QMessageBox.Yes:
                # User chose to abort the operation, we should not convert the write nodes
                continue_with_convert = False

        if continue_with_convert:
            self.__write_node_handler.convert_sg_to_nuke_write_nodes()

    def convert_from_write_nodes(self, show_warning=False):
        """
        Convert all regular Nuke Write nodes that have previously been converted
        from Flow Production Tracking Write nodes, back into Flow Production Tracking Write nodes.

        :param show_warning: Optional bool that sets whether a warning box should be displayed to the user;
         defaults to False.
        """

        # By default we want to convert the write nodes, unless the warning is shown and the user chooses to abort.
        continue_with_convert = True

        if show_warning:
            # defer importing the QT module so the app doesn't require QT unless running this method with the warning.
            from sgtk.platform.qt import QtGui

            res = QtGui.QMessageBox.question(
                None,
                "Convert All Write Nodes?",
                "This will convert any Flow Production Tracking Write Nodes that have "
                "been converted "
                "into standard write nodes back to their original form."
                "\nOK to proceed?",
                QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
            )

            if res != QtGui.QMessageBox.Yes:
                # User chose to abort the operation, we should not convert the write nodes
                continue_with_convert = False

        if continue_with_convert:
            self.__write_node_handler.convert_nuke_to_sg_write_nodes()

    def create_new_write_node(self, profile_name):
        """
        Creates a Shotgun write node using the provided profile_name.
        """
        self.__write_node_handler.create_new_node(profile_name)

    # Private methods
    #
    def __add_write_node_commands(self, context=None):
        """
        Creates write node menu entries for all write node configurations
        and the convert to and from Shotgun write node actions if configured to do so.
        """
        context = context or self.context

        write_node_icon = os.path.join(self.disk_location, "resources", "tk2_write.png")

        for profile_name in self.__write_node_handler.profile_names:
            # add to toolbar menu
            cb_fn = lambda pn=profile_name: self.__write_node_handler.create_new_node(
                pn
            )
            self.engine.register_command(
                "%s [Flow Production Tracking]" % profile_name,
                cb_fn,
                dict(
                    type="node",
                    icon=write_node_icon,
                    context=context,
                ),
            )

        # Show the convert actions in the Menu if configured to do so
        if self.get_setting("show_convert_actions"):

            # We only want to show the convert methods if there are no promoted knobs,
            # as these aren't supported when converting back
            # todo: We should check the settings and then scan the scene to see if any PTR write nodes use promoted knobs
            write_nodes = self.get_setting("write_nodes")
            promoted_knob_write_nodes = next(
                (a_node for a_node in write_nodes if a_node["promote_write_knobs"]),
                None,
            )

            if not promoted_knob_write_nodes:
                # no presets use promoted knobs so we are OK to register the menus.

                convert_to_write_nodes_action = lambda: self.convert_to_write_nodes(
                    show_warning=True
                )
                convert_from_write_nodes_action = lambda: self.convert_from_write_nodes(
                    show_warning=True
                )

                self.engine.register_command(
                    "Convert PTR Write Nodes to Write Nodes...",
                    convert_to_write_nodes_action,
                    {
                        "type": "context_menu",
                        "icon": os.path.join(self.disk_location, "icon_256.png"),
                    },
                )
                self.engine.register_command(
                    "Convert Write Nodes back to PTR format...",
                    convert_from_write_nodes_action,
                    {
                        "type": "context_menu",
                        "icon": os.path.join(self.disk_location, "icon_256.png"),
                    },
                )
            else:
                self.log_debug(
                    "Convert menu options were disabled as "
                    "promoted knobs were detected in the app settings."
                )

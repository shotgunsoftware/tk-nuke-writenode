"""
Copyright (c) 2013 Shotgun Software, Inc
----------------------------------------------------

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

        tk_nuke_writenode = self.import_module("tk_nuke_writenode")
        self.write_node_handler = tk_nuke_writenode.TankWriteNodeHandler(self)

        """
        tk_nuke_writenode = self.import_module("tk_nuke_writenode")
        
        # park this module with Nuke so that the snapshot history UI
        # pane code can find it later on.
        nuke._tk_nuke_publish = tk_nuke_publish

        # validate template_work and template_publish have the same extension
        _, work_ext = os.path.splitext(self.get_template("template_work").definition)
        _, pub_ext = os.path.splitext(self.get_template("template_publish").definition)

        if work_ext != pub_ext:
            # disable app
            self.log_error("'template_work' and 'template_publish' have different file extensions.")
            return

        # create handlers for our various commands
        self.write_node_handler = tk_nuke_publish.TankWriteNodeHandler(self)
        # immediately attach it to the nuke API so that the gizmos can reach it
        nuke._tank_write_node_handler = self.write_node_handler
        self.snapshot_handler = tk_nuke_publish.TankSnapshotHandler(self,
                                                                    self.write_node_handler)
        self.publish_handler = tk_nuke_publish.TankPublishHandler(self,
                                                                  self.snapshot_handler,
                                                                  self.write_node_handler)

        # add stuff to main menu
        self.engine.register_command("Snapshot As...", self.snapshot_handler.snapshot_as)
        self.engine.register_command("Snapshot", self.snapshot_handler.snapshot)
        self.engine.register_command("Publish...", self.publish_handler.publish)
        self.engine.register_command("Version up Work file...", self.snapshot_handler.manual_version_up)

         # custom panes
        self.engine.register_command("Tank Snapshot History",
                                     tk_nuke_publish.snapshot_history.create_new_panel,
                                     {"type": "custom_pane",
                                      "panel_id": tk_nuke_publish.snapshot_history.PANEL_UNIQUE_ID})
        """
        self.__add_write_nodes()

    def destroy_app(self):
        self.log_debug("Destroying tk-nuke-writenode")

    def __generate_create_node_callback_fn(self, name, render_templ, publish_templ, file_type, file_settings):
        """
        Helper
        Creates a callback function for the tank write node
        """
        cb_fn = (lambda n=name, rt=render_templ, pt=publish_templ, ft=file_type, ts=file_settings:
                 self.write_node_handler.create_new_node(n, rt, pt, ft, ts))
        return cb_fn

    def __add_write_nodes(self):
        """
        Creates write node menu entries for all write node configurations
        """
        write_node_icon = os.path.join(self.disk_location, "resources", "tk2_write.png")

        for x in self.get_setting("write_nodes", []):
            # each write node has a couple of entries
            name = x.get("name", "unknown")
            file_type = x.get("file_type")
            file_settings = x.get("settings", {})
            if not isinstance(file_settings, dict):
                raise TankError("Configuration Error: Write node contains invalid settings. "
                                     "Settings must be a dictionary. Current config: %s" % x)

            rts = x.get("render_template")
            if rts is None:
                raise TankError("Configuration Error: Write node has no render_template: %s" % x)

            pts = x.get("publish_template")
            if pts is None:
                raise TankError("Configuration Error: Write node has no publish_template: %s" % x)

            render_template = self.get_template_by_name(rts)
            if render_template is None:
                raise TankError("Configuration Error: Could not find render template: %s" % x)

            publish_template = self.get_template_by_name(pts)
            if publish_template is None:
                raise TankError("Configuration Error: Could not find publish template: %s" % x)

            # make sure that all required fields exist in the templates
            for x in ["version", "name"]:
                if x not in render_template.keys.keys():
                    raise TankError("Configuration Error: The required field '%s' is missing"
                                         "from the template %s" % (x, render_template))
                if x not in publish_template.keys.keys():
                    raise TankError("Configuration Error: The required field '%s' is missing"
                                         "from the template %s" % (x, publish_template))

            # add stuff to toolbar menu
            cb_fn = self.__generate_create_node_callback_fn(name,
                                                            render_template,
                                                            publish_template,
                                                            file_type,
                                                            file_settings)
            self.engine.register_command("Tank Write: %s" % name,
                                          cb_fn,
                                          {"type": "node", "icon": write_node_icon})


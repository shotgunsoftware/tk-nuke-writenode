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

        # import module and get handler
        tk_nuke_writenode = self.import_module("tk_nuke_writenode")
        self.write_node_handler = tk_nuke_writenode.TankWriteNodeHandler(self)

        # patch handler onto nuke module for access in WriteNode knobs
        nuke._tank_write_node_handler = self.write_node_handler

        # add WriteNodes to nuke menu
        self.__add_write_nodes()

    def destroy_app(self):
        self.log_debug("Destroying tk-nuke-writenode")


    # interface for other apps to query write node info:
    #
    
    def get_write_nodes(self):
        """
        Return list of all write nodes
        """
        return self.write_node_handler.get_nodes()
    
    def get_node_name(self, node):
        """
        Return the name for the specified node
        """
        return self.write_node_handler.get_node_name(node)

    def get_node_profile_name(self, node):
        """
        Return the name of the profile the specified node
        is using
        """
        return self.write_node_handler.get_node_profile_name(node)
    
    def get_node_render_files(self, node):
        """
        Return the list of rendered files for the node
        """
        return self.write_node_handler.get_files_on_disk(node)
    
    def get_node_render_template(self, node):
        """
        Return the render template for the specified node
        """
        return self.write_node_handler.get_render_template(node)
    
    def get_node_publish_template(self, node):
        """
        Return the publish template for the specified node
        """
        return self.write_node_handler.get_publish_template(node)
    
    def get_node_tank_type(self, node):
        """
        Return the tank type for the specified node
        
        Note: Legacy version with old 'Tank Type' name - use
        get_node_published_file_type instead!
        """
        return self.write_node_handler.get_node_tank_type(node)

    def get_node_published_file_type(self, node):
        """
        Return the published file type for the specified node
        """
        return self.write_node_handler.get_node_tank_type(node)
    
    def get_node_render_path(self, node):
        """
        Return the render path for the specified node
        """
        return self.write_node_handler.compute_path(node)
    
    def generate_node_thumbnail(self, node):
        """
        Generate a thumnail for the specified node
        """
        return self.write_node_handler.generate_thumbnail(node)
    
    def reset_node_render_path(self, node):
        """
        Reset the render path of the specified node.  This
        will force the render path to be updated based on
        the current script path and configuraton
        """
        self.write_node_handler.reset_render_path(node)
        
    def is_node_render_path_locked(self, node):
        """
        Determine if the render path for the specified node
        is locked.  The path will become locked if the cached
        version of the path no longer matches the computed
        path (using the appropriate render template).  This
        can happen if the file is moved on disk or if the template
        is changed.
        """
        return self.write_node_handler.render_path_is_locked(node)

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

            # add to toolbar menu
            cb_fn = (lambda n=name, rt=render_template, pt=publish_template, ft=file_type, ts=file_settings:
                        self.write_node_handler.create_new_node(n, rt, pt, ft, ts))
            self.engine.register_command("Shotgun Write: %s" % name, cb_fn, {"type": "node", "icon": write_node_icon})


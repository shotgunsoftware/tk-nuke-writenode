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
        for node in self.get_write_nodes():
            self.reset_node_render_path(node)

        self.__write_node_handler.populate_profiles_from_settings()
        self.__write_node_handler.populate_script_template()
        self.__add_write_node_commands(new_context)
        
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
        the current script path and configuraton.
        
        Note, this should really never be needed now that the
        path is reset automatically when the user changes something.
        """
        self.__write_node_handler.reset_render_path(node)

    def convert_to_write_nodes(self):
        """
        Convert all Shotgun write nodes found in the current Script to regular
        Nuke Write nodes.  Additional toolkit information will be stored on 
        additional user knobs named 'tk_*'
        """
        self.__write_node_handler.convert_sg_to_nuke_write_nodes()

    def convert_from_write_nodes(self):
        """
        Convert all regular Nuke Write nodes that have previously been converted
        from Shotgun Write nodes, back into Shotgun Write nodes.
        """
        self.__write_node_handler.convert_nuke_to_sg_write_nodes()
        
    # Private methods
    #
    def __add_write_node_commands(self, context=None):
        """
        Creates write node menu entries for all write node configurations
        """
        context = context or self.context

        write_node_icon = os.path.join(self.disk_location, "resources", "tk2_write.png")

        for profile_name in self.__write_node_handler.profile_names:
            # add to toolbar menu
            cb_fn = lambda pn=profile_name: self.__write_node_handler.create_new_node(pn)
            self.engine.register_command(
                "%s [Shotgun]" % profile_name,
                cb_fn, 
                dict(
                    type="node",
                    icon=write_node_icon,
                    context=context,
                )
            )
            
            


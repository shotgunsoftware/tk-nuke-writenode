# Copyright (c) 2013 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.

import os
import sys
import tempfile
import pickle
import datetime
import base64
import re

import nuke
import nukescripts

import tank
from tank import TankError
from tank.platform import constants

# Special exception raised when the work file cannot be resolved.
class TkComputePathError(TankError):
    pass

class TankWriteNodeHandler(object):
    """
    Handles requests and processing from a tank write node.
    """

    SG_WRITE_NODE_CLASS = "WriteTank"
    SG_WRITE_DEFAULT_NAME = "ShotgunWrite"
    WRITE_NODE_NAME = "Write1"

    OUTPUT_KNOB_NAME = "tank_channel"
    USE_NAME_AS_OUTPUT_KNOB_NAME = "tk_use_name_as_channel"

    ################################################################################################
    # Construction

    def __init__(self, app):
        """
        Construction
        """
        self._app = app
        self._script_template = self._app.get_template("template_script_work")
        
        # cache the profiles:
        self._promoted_knobs = {}
        self._profile_names = []
        self._profiles = {}
        
        self.__currently_rendering_nodes = set()
        self.__node_computed_path_settings_cache = {}
        self.__path_preview_cache = {}
        # flags to track when the render and proxy paths are being updated.
        self.__is_updating_render_path = False
        self.__is_updating_proxy_path = False

        self.populate_profiles_from_settings()
            
    ################################################################################################
    # Properties
            
    @property
    def profile_names(self):
        """
        return the list of available profile names
        """
        return self._profile_names
            
    ################################################################################################
    # Public methods

    def populate_profiles_from_settings(self):
        """
        Sources profile definitions from the current app settings.
        """
        self._profiles = {}
        self._profile_names = []

        for profile in self._app.get_setting("write_nodes", []):
            name = profile["name"]
            if name in self._profiles:
                self._app.log_warning("Configuration contains multiple Write Node profiles called '%s'!  Only the "
                                      "first will be available" % name)                
                continue
            
            self._profile_names.append(name)
            self._profiles[name] = profile

    def populate_script_template(self):
        """
        Sources the current context's work file template from the parent app.
        """
        self._script_template = self._app.get_template("template_script_work")
            
    def get_nodes(self):
        """
        Returns a list of tank write nodes
        """
        if nuke.exists("root"):
            return nuke.allNodes(group=nuke.root(), 
                                 filter=TankWriteNodeHandler.SG_WRITE_NODE_CLASS, 
                                 recurseGroups = True)
        else:
            return []
            
    def get_node_name(self, node):
        """
        Return the name for the specified node
        """
        return node.name()

    def get_node_profile_name(self, node):
        """
        Return the name of the profile the specified node is using
        """
        return node.knob("profile_name").value()
    
    def get_node_tank_type(self, node):
        """
        Return the tank type for the specified node
        """
        settings = self.__get_node_profile_settings(node)
        if settings:
            return settings["tank_type"]
        
    def get_render_template(self, node):
        """
        helper function. Returns the associated render template obj for a node
        """
        return self.__get_render_template(node)

    def get_publish_template(self, node):
        """
        helper function. Returns the associated pub template obj for a node
        """
        return self.__get_publish_template(node)

    def get_proxy_render_template(self, node):
        """
        helper function. Returns the associated render proxy template obj for a node.
        If this hasn't been defined then it falls back to the regular render template.
        """
        return self.__get_render_template(node, is_proxy=True, fallback_to_render=True)

    def get_proxy_publish_template(self, node):
        """
        helper function. Returns the associated pub template obj for a node
        """
        return (self.__get_publish_template(node, True)
                or self.__get_publish_template(node, False))

    def compute_render_path(self, node):
        """
        Public method to compute and return the render path
        """
        return self.__compute_render_path(node, is_proxy=False)

    def compute_proxy_path(self, node):
        """
        Public method to compute and return the proxy render path
        """
        return self.__compute_render_path(node, is_proxy=True)

    def get_files_on_disk(self, node):
        """
        Called from render publisher & UI (via exists_on_disk)
        Returns the files on disk associated with this node
        """
        return self.__get_files_on_disk(node, False)

    def get_proxy_files_on_disk(self, node):
        """
        Called from render publisher & UI (via exists_on_disk)
        Returns the files on disk associated with this node
        """
        return self.__get_files_on_disk(node, True)

    def render_path_is_locked(self, node):
        """
        Return True if the render path is currently locked because something unexpected
        has changed.  When the render path is locked, the cached version will always be
        used until it has been reset by an intentional user change/edit.
        """
        # calculate the path:
        render_path = ""
        try:
            render_path = self.__compute_render_path(node)
        except:
            return True
        
        # get the cached path:
        cached_path = self.__get_render_path(node)
        
        return self.__is_render_path_locked(node, render_path, cached_path)

    def reset_render_path(self, node):
        """
        Reset the render path of the specified node.  This
        will force the render path to be updated based on
        the current script path and configuraton
        """
        is_proxy = node.proxy()
        self.__update_render_path(node, force_reset=True, is_proxy=is_proxy)     
        self.__update_render_path(node, force_reset=True, is_proxy=(not is_proxy))

    def create_new_node(self, profile_name):
        """
        Creates a new write node

        :returns: a node object.
        """
        curr_filename = self.__get_current_script_path()
        if not curr_filename:
            nuke.message("Please save the file first!")
            return

        # make sure that the file is a proper tank work path
        if not self._script_template.validate(curr_filename):
            nuke.message("This file is not a Shotgun work file. Please use Shotgun Save-As in order "
                         "to save the file as a valid work file.")
            return

        # new node please!
        node = nuke.createNode(TankWriteNodeHandler.SG_WRITE_NODE_CLASS)

        # rename to our new default name:
        existing_node_names = [n.name() for n in nuke.allNodes()]
        postfix = 1
        while True:
            new_name = "%s%d" % (TankWriteNodeHandler.SG_WRITE_DEFAULT_NAME, postfix)
            if new_name not in existing_node_names:
                node.knob("name").setValue(new_name)
                break
            else:
                postfix += 1

        self._app.log_debug("Created Shotgun Write Node %s" % node.name())

        # set the profile:
        self.__set_profile(node, profile_name, reset_all_settings=True)

        return node

    def process_placeholder_nodes(self):
        """
        Convert any placeholder nodes to TK Write Nodes
        """
        self._app.log_debug("Looking for placeholder nodes to process...")
        
        node_found = False
        for n in nuke.allNodes("ModifyMetaData"):
            if not n.name().startswith("ShotgunWriteNodePlaceholder"):
                continue

            self._app.log_debug("Found ShotgunWriteNodePlaceholder node: %s" % n)
            metadata = n.metadata()
            profile_name = metadata.get("name")
            output_name = metadata.get("output") or metadata.get("channel") # for backwards compatibility 

            # Make sure the profile is valid:
            if profile_name not in self._profiles:
                self._app.log_warning("Unknown write node profile in file, skipping: %s" % profile_name)
                continue

            # try and ensure we're connected to the tree after we delete the nodes
            if not node_found:
                node_found = True
                try:
                    n.dependencies()[0].setSelected(True)
                except:
                    pass

            # create the node:
            new_node = self.create_new_node(profile_name)

            # set the output:
            self.__set_output(new_node, output_name)
            
            # And remove the original metadata
            nuke.delete(n)

    def generate_thumbnail(self, node):
        """
        generates a thumbnail in a temp location and returns the path to it.
        It is the responsibility of the caller to delete this thumbnail afterwards.
        The thumbnail will be in png format.

        Returns None if no thumbnail could be generated
        """
        # get thumbnail node

        th_node = node.node("create_thumbnail")
        if th_node is None:
            # write gizmo that does not have the create thumbnail node
            return None
        th_node.knob("disable").setValue(False)
        
        png_path = tempfile.NamedTemporaryFile(suffix=".png", prefix="tanktmp", delete=False).name

        # set render output - make sure to use a path with slashes on all OSes
        th_node.knob("file").setValue(png_path.replace(os.path.sep, "/"))
        th_node.knob("proxy").setValue(png_path.replace(os.path.sep, "/"))

        # and finally render!
        try:
            # pick mid frame
            current_in = nuke.root()["first_frame"].value()
            current_out = nuke.root()["last_frame"].value()
            frame_to_render = (current_out - current_in) / 2 + current_in
            frame_to_render = int(frame_to_render)
            render_node_name = "%s.create_thumbnail" % node.name()
            # and do it - always render the first view we find.
            first_view = nuke.views()[0]
            nuke.execute(render_node_name, 
                         start=frame_to_render, 
                         end=frame_to_render, 
                         incr=1, 
                         views=[first_view])
        except Exception, e:
            self._app.log_warning("Thumbnail could not be generated: %s" % e)
            # remove the temp file
            try:
                os.remove(png_path)
            except:
                pass
            png_path = None
        finally:
            # reset paths
            th_node.knob("file").setValue("")
            th_node.knob("proxy").setValue("")
            th_node.knob("disable").setValue(True)

        return png_path

    def add_callbacks(self):
        """
        Add callbacks to watch for certain events:
        """
        # add callback to check for placeholder nodes
        nuke.addOnScriptLoad(self.process_placeholder_nodes, nodeClass="Root")

        # script save callback used to reset paths whenever
        # a script is saved as a new name
        nuke.addOnScriptSave(self.__on_script_save)
        
        # user create callback that gets executed whenever a Shotgun Write Node
        # is created by the user
        nuke.addOnUserCreate(self.__on_user_create, nodeClass=TankWriteNodeHandler.SG_WRITE_NODE_CLASS)

        # set up all existing nodes:
        for n in self.get_nodes():
            self.__setup_new_node(n)
        
    def remove_callbacks(self):
        """
        Removed previously added callbacks
        """
        nuke.removeOnScriptLoad(self.process_placeholder_nodes, nodeClass="Root")
        nuke.removeOnScriptSave(self.__on_script_save)
        nuke.removeOnUserCreate(self.__on_user_create, nodeClass=TankWriteNodeHandler.SG_WRITE_NODE_CLASS)

    def convert_sg_to_nuke_write_nodes(self):
        """
        Utility function to convert all Shotgun Write nodes to regular
        Nuke Write nodes.
        
        # Example use:
        import sgtk
        eng = sgtk.platform.current_engine()
        app = eng.apps["tk-nuke-writenode"]
        # Convert Shotgun write nodes to Nuke write nodes:
        app.convert_to_write_nodes()

        :param create_folders: When set to true, it will create the folders on disk for the render and proxy paths.
         Defaults to false.
        """
        # clear current selection:
        nukescripts.clear_selection_recursive()
        
        # get write nodes:
        sg_write_nodes = self.get_nodes()
        for sg_wn in sg_write_nodes:
        
            # set as selected:
            sg_wn.setSelected(True)
            node_name = sg_wn.name()
            node_pos = (sg_wn.xpos(), sg_wn.ypos())
            
            # create new regular Write node:
            new_wn = nuke.createNode("Write")
            new_wn.setSelected(False)
        
            # copy across file & proxy knobs (if we've defined a proxy template):
            new_wn["file"].setValue(sg_wn["cached_path"].evaluate())
            if sg_wn["proxy_render_template"].value():
                new_wn["proxy"].setValue(sg_wn["tk_cached_proxy_path"].evaluate())
            else:
                new_wn["proxy"].setValue("")

            # make sure file_type is set properly:
            int_wn = sg_wn.node(TankWriteNodeHandler.WRITE_NODE_NAME)
            new_wn["file_type"].setValue(int_wn["file_type"].value())
        
            # copy across any knob values from the internal write node.
            for knob_name, knob in int_wn.knobs().iteritems():
                # skip knobs we don't want to copy:
                if knob_name in ["file_type", "file", "proxy", "beforeRender", "afterRender", 
                              "name", "xpos", "ypos"]:
                    continue
                
                if knob_name in new_wn.knobs():
                    try:
                        new_wn[knob_name].setValue(knob.value())
                    except TypeError:
                        # ignore type errors:
                        pass

            # Set the nuke write node to have create directories ticked on by default
            # As toolkit hasn't created the output folder at this point.
            new_wn["create_directories"].setValue(True)
        
            # copy across select knob values from the Shotgun Write node:
            for knob_name in ["tile_color", "postage_stamp", "label"]:
                new_wn[knob_name].setValue(sg_wn[knob_name].value())
        
            # Store Toolkit specific information on write node
            # so that we can reverse this process later

            # profile
            knob = nuke.String_Knob("tk_profile_name")
            knob.setValue(sg_wn["profile_name"].value())
            new_wn.addKnob(knob)
            
            # output
            knob = nuke.String_Knob("tk_output")
            knob.setValue(sg_wn[TankWriteNodeHandler.OUTPUT_KNOB_NAME].value())
            new_wn.addKnob(knob)
            
            # use node name for output
            knob = nuke.Boolean_Knob(TankWriteNodeHandler.USE_NAME_AS_OUTPUT_KNOB_NAME)
            knob.setValue(sg_wn[TankWriteNodeHandler.USE_NAME_AS_OUTPUT_KNOB_NAME].value())
            new_wn.addKnob(knob)
        
            # templates
            knob = nuke.String_Knob("tk_render_template")
            knob.setValue(sg_wn["render_template"].value())
            new_wn.addKnob(knob)
            
            knob = nuke.String_Knob("tk_publish_template")
            knob.setValue(sg_wn["publish_template"].value())
            new_wn.addKnob(knob)
            
            knob = nuke.String_Knob("tk_proxy_render_template")
            knob.setValue(sg_wn["proxy_render_template"].value())
            new_wn.addKnob(knob)
            
            knob = nuke.String_Knob("tk_proxy_publish_template")
            knob.setValue(sg_wn["proxy_publish_template"].value())
            new_wn.addKnob(knob)
        
            # delete original node:
            nuke.delete(sg_wn)
        
            # rename new node:
            new_wn.setName(node_name)
            new_wn.setXpos(node_pos[0])
            new_wn.setYpos(node_pos[1])
            
    def convert_nuke_to_sg_write_nodes(self):
        """
        Utility function to convert all Nuke Write nodes to Shotgun
        Write nodes (only converts Write nodes that were previously
        Shotgun Write nodes)

        # Example use:
        import sgtk
        eng = sgtk.platform.current_engine()
        app = eng.apps["tk-nuke-writenode"]
        # Convert previously converted Nuke write nodes back to 
        # Shotgun write nodes:
        app.convert_from_write_nodes()
        """
        # clear current selection:
        nukescripts.clear_selection_recursive()
        
        # get write nodes:
        write_nodes = nuke.allNodes(group=nuke.root(), filter="Write", recurseGroups = True)
        for wn in write_nodes:
        
            # look for additional toolkit knobs:
            profile_knob = wn.knob("tk_profile_name")
            output_knob = wn.knob("tk_output")
            use_name_as_output_knob = wn.knob(TankWriteNodeHandler.USE_NAME_AS_OUTPUT_KNOB_NAME)
            render_template_knob = wn.knob("tk_render_template")
            publish_template_knob = wn.knob("tk_publish_template")
            proxy_render_template_knob = wn.knob("tk_proxy_render_template")
            proxy_publish_template_knob = wn.knob("tk_proxy_publish_template")
        
            if (not profile_knob
                or not output_knob
                or not use_name_as_output_knob
                or not render_template_knob
                or not publish_template_knob
                or not proxy_render_template_knob
                or not proxy_publish_template_knob):
                # can't convert to a Shotgun Write Node as we have missing parameters!
                continue

            # set as selected:
            wn.setSelected(True)
            node_name = wn.name()
            node_pos = (wn.xpos(), wn.ypos())
            
            # create new Shotgun Write node:
            new_sg_wn = nuke.createNode(TankWriteNodeHandler.SG_WRITE_NODE_CLASS)
            new_sg_wn.setSelected(False)

            # copy across file & proxy knobs as well as all cached templates:
            new_sg_wn["cached_path"].setValue(wn["file"].value())
            new_sg_wn["tk_cached_proxy_path"].setValue(wn["proxy"].value())
            new_sg_wn["render_template"].setValue(render_template_knob.value())
            new_sg_wn["publish_template"].setValue(publish_template_knob.value())
            new_sg_wn["proxy_render_template"].setValue(proxy_render_template_knob.value())
            new_sg_wn["proxy_publish_template"].setValue(proxy_publish_template_knob.value())
            
            # set the profile & output - this will cause the paths to be reset:
            # Note, we don't call the method __set_profile() as we don't want to
            # run all the normal logic that runs as part of switching the profile.
            # Instead we want this node to be rebuilt as close as possible to the
            # original before it was converted to a regular Nuke write node.
            profile_name = profile_knob.value()
            new_sg_wn["profile_name"].setValue(profile_name)
            new_sg_wn["tk_profile_list"].setValue(profile_name)
            new_sg_wn[TankWriteNodeHandler.OUTPUT_KNOB_NAME].setValue(output_knob.value())
            new_sg_wn[TankWriteNodeHandler.USE_NAME_AS_OUTPUT_KNOB_NAME].setValue(use_name_as_output_knob.value())

            # make sure file_type is set properly:
            int_wn = new_sg_wn.node(TankWriteNodeHandler.WRITE_NODE_NAME)
            int_wn["file_type"].setValue(wn["file_type"].value())

            # copy across and knob values from the internal write node.
            for knob_name, knob in wn.knobs().iteritems():
                # skip knobs we don't want to copy:
                if knob_name in ["file_type", "file", "proxy", "beforeRender", "afterRender", 
                              "name", "xpos", "ypos", "disable", "tile_color", "postage_stamp",
                              "label"]:
                    continue
                
                if knob_name in int_wn.knobs():
                    try:
                        int_wn[knob_name].setValue(knob.value())
                    except TypeError:
                        # ignore type errors:
                        pass
        
            # explicitly copy some settings to the new Shotgun Write Node instead:
            for knob_name in ["disable", "tile_color", "postage_stamp"]:
                new_sg_wn[knob_name].setValue(wn[knob_name].value())
                
            # delete original node:
            nuke.delete(wn)
        
            # rename new node:
            new_sg_wn.setName(node_name)
            new_sg_wn.setXpos(node_pos[0])
            new_sg_wn.setYpos(node_pos[1])       


    ################################################################################################
    # Public methods called from gizmo - although these are public, they should 
    # be considered as private and not used directly!

    def on_knob_changed_gizmo_callback(self):
        """
        Called when the value of a knob on a Shotgun Write Node is changed
        """
        self.__on_knob_changed()

    def on_node_created_gizmo_callback(self):
        """
        Called when an instance of a Shotgun Write Node is created.  This can
        be when the node is created for the first time or when it is loaded
        or imported/pasted from an existing script.
        """
        # NOTE: Future self or other person: every time we touch this method to
        # try to fix one of the PythonObject ValueErrors that Nuke occasionally
        # raises on file open, it breaks something for someone. Most recently, it
        # was farm setups for a few clients. It's best if we just leave this
        # alone from now on, unless we someday have a better understanding of
        # what's going on and the consequences of changing the on_node_created
        # behavior.
        self.__setup_new_node(nuke.thisNode())

    def on_compute_path_gizmo_callback(self):
        """
        Callback executed when nuke requests the location of the std output to be computed on the internal Write
        node.  Returns a path on disk. This will return the path in a form that Nuke likes (eg. with slashes). 
        
        It also updates the preview fields on the node. and the UI
        """
        # the ShotgunWrite node is the current node's parent:
        node = nuke.thisParent()
        if not node:
            return
        
        # don't do anything until the node is fully constructed!
        if not self.__is_node_fully_constructed(node):
            return

        # return the render path but don't reset it:
        path = self.__update_render_path(node, is_proxy=False)
        return path

    def on_compute_proxy_path_gizmo_callback(self):
        """
        Callback executed when nuke requests the location of the std output to be computed on the internal Write
        node.  Returns a path on disk. This will return the path in a form that Nuke likes (eg. with slashes). 
        """
        # the ShotgunWrite node is the current node's parent:
        node = nuke.thisParent()
        if not node:
            return
        
        # don't do anything until the node is fully constructed!
        if not self.__is_node_fully_constructed(node):
            return

        # return the render path but don't reset it:
        path = self.__update_render_path(node, is_proxy=True)
        return path
    
    def on_show_in_fs_gizmo_callback(self):
        """
        Shows the location of the node in the file system.
        This is a callback which is executed when the show in fs
        button is pressed on the nuke write node.
        """
        node = nuke.thisNode()
        if not node:
            return
        
        render_dir = None

        # first, try to just use the current cached path:
        is_proxy = node.proxy()
        render_path = self.__get_render_path(node, is_proxy)
        if render_path:
            # the above method returns nuke style slashes, so ensure these
            # are pointing correctly
            render_path = render_path.replace("/", os.path.sep)
            
            dir_name = os.path.dirname(render_path)
            if os.path.exists(dir_name):
                render_dir = dir_name
                
        if not render_dir:
            # render directory doesn't exist so try using location
            # of rendered frames instead:
            try:
                files = self.get_files_on_disk(node)
                if len(files) == 0:
                    nuke.message("There are no %srenders for this node yet!\n"
                             "When you render, the files will be written to "
                             "the following location:\n\n%s" 
                             % (("proxy " if is_proxy else ""), render_path))
                else:
                    render_dir = os.path.dirname(files[0])
            except Exception, e:
                nuke.message("Unable to jump to file system:\n\n%s" % e)                
        
        # if we have a valid render path then show it:      
        if render_dir:
            system = sys.platform

            # run the app
            if system == "linux2":
                cmd = "xdg-open \"%s\"" % render_dir
            elif system == "darwin":
                cmd = "open '%s'" % render_dir
            elif system == "win32":
                cmd = "cmd.exe /C start \"Folder\" \"%s\"" % render_dir
            else:
                raise Exception("Platform '%s' is not supported." % system)

            self._app.log_debug("Executing command '%s'" % cmd)
            exit_code = os.system(cmd)
            if exit_code != 0:
                nuke.message("Failed to launch '%s'!" % cmd)

    def on_reset_render_path_gizmo_callback(self):
        """
        Callback from the gizmo whenever the reset path button is pressed.
        """
        node = nuke.thisNode()
        if not node:
            return
        
        self.reset_render_path(node)
    
    def on_copy_path_to_clipboard_gizmo_callback(self):
        """
        Callback from the gizmo whenever the 'Copy path to clipboard' button
        is pressed.
        """
        node = nuke.thisNode()
        
        # get the path depending if in full or proxy mode:
        is_proxy = node.proxy()
        render_path = self.__get_render_path(node, is_proxy)
        
        # use Qt to copy the path to the clipboard:
        from sgtk.platform.qt import QtGui
        QtGui.QApplication.clipboard().setText(render_path)
    
    def on_before_render_gizmo_callback(self):
        """
        Callback from nuke whenever a tank write node is about to be rendered.
        """        
        # the current node is the internal 'Write1' Write node:
        node = nuke.thisNode()
        if not node:
            return

        views = node.knob("views").value().split()

        if len(views) < 2:
            # check if proxy render or not
            if nuke.root()["proxy"].value():
                # proxy mode
                out_file = node.knob("proxy").evaluate()
            else:
                out_file = node.knob("file").evaluate()

            out_dir = os.path.dirname(out_file)
            self._app.ensure_folder_exists(out_dir)
            
        else:
            # stereo or odd number of views...
            for view in views:
                if nuke.root()["proxy"].value():
                    # proxy mode
                    out_file = node.knob("proxy").evaluate(view=view)
                else:
                    out_file = node.knob("file").evaluate(view=view)

                out_dir = os.path.dirname(out_file)
                self._app.ensure_folder_exists(out_dir)
                
        # add group/parent to list of currently rendering nodes:
        grp = nuke.thisGroup()
        if grp:
            self.__currently_rendering_nodes.add(grp)

        # Run any beforeRender code that the user added in the node's Python
        # tab manually.
        cmd = grp.knob("tk_before_render").value()

        if cmd:
            try:
                exec(cmd)
            except Exception:
                self._app.log_error("The Write node's beforeRender setting failed "
                                    "to execute!")
                raise
    def on_after_render_gizmo_callback(self):
        """
        Callback from nuke whenever a tank write node has finished being rendered
        """        
        # the current node is the internal 'Write1' Write node:
        node = nuke.thisNode()
        if not node:
            return
        
        # remove parent/group from list of currently rendering nodes:
        grp = nuke.thisGroup()
        if grp and grp in self.__currently_rendering_nodes:
            self.__currently_rendering_nodes.remove(grp)

        # Run any afterRender code that the user added in the node's Python
        # tab manually.
        cmd = grp.knob("tk_after_render").value()

        if cmd:
            try:
                exec(cmd)
            except Exception:
                self._app.log_error("The Write node's afterRender setting failed "
                                    "to execute!")
                raise

    ################################################################################################
    # Private methods

    def __get_node_profile_settings(self, node):
        """
        Find the profile settings for the specified node
        """
        profile_name = self.get_node_profile_name(node)
        if profile_name:
            return self._profiles.get(profile_name)

    def __get_template(self, node, name):
        """
        Get the named template for the specified node.
        """
        template_name = None
        
        # get the template from the nodes profile settings:
        settings = self.__get_node_profile_settings(node)
        if settings:
            template_name = settings[name]
            if template_name:
                # update the cached setting:
                self.__update_knob_value(node, name, template_name)
        else:
            # the profile probably doesn't exist any more so
            # try to use the cached version
            template_name = node.knob(name).value()
            
        return self._app.get_template_by_name(template_name)
    
    def __get_render_template(self, node, is_proxy=False, fallback_to_render=False):
        """
        Get a specific render template for the current profile
        
        :param is_proxy:            Specifies which of the two
        :param fallback_to_render:  If true and proxy template is null then the
                                    render template will be returned instead.
        """
        if is_proxy:
            template = self.__get_template(node, "proxy_render_template")
            if template or not fallback_to_render:
                return template

        return self.__get_template(node, "render_template") 
    
    def __get_publish_template(self, node, is_proxy=False):
        """
        Get a specific publish template for the current profile
        """
        if is_proxy:
            return self.__get_template(node, "proxy_publish_template")
        else:
            return self.__get_template(node, "publish_template")
    
    def __is_output_used(self, node):
        """
        Determine if output key is used in either the render or the proxy render
        templates
        """
        render_template = self.__get_render_template(node, is_proxy=False)
        proxy_render_template = self.__get_render_template(node, is_proxy=True)
        
        for template in [render_template, proxy_render_template]:
            if not template:
                continue
            # check for output key and also channel for backwards compatibility!
            if "output" in template.keys or "channel" in template.keys:
                return True
            
        return False
    
    def __update_knob_value(self, node, name, new_value):
        """
        Update the value for the specified knob on the specified node
        but only if it is different to the current value to avoid 
        unneccesarily invalidating the cache
        """
        current_value = node.knob(name).value()
        if new_value != current_value: 
            node.knob(name).setValue(new_value)
    
    def __update_output_knobs(self, node):
        """
        Update output knob visibility depending if output is a key
        in the render template or not
        """
        output_knob = node.knob(TankWriteNodeHandler.OUTPUT_KNOB_NAME)
        name_as_output_knob = node.knob(TankWriteNodeHandler.USE_NAME_AS_OUTPUT_KNOB_NAME)
        
        output_is_used = self.__is_output_used(node)
        name_as_output = name_as_output_knob.value() 
        
        output_knob.setEnabled(output_is_used and not name_as_output)
        output_knob.setVisible(output_is_used)
        name_as_output_knob.setVisible(output_is_used)    
    
    def __update_path_preview(self, node, is_proxy):
        """
        Updates the path preview fields on the tank write node.
        """
        # first set up the node label
        # this will be displayed on the node in the graph
        # useful to tell what type of node it is
        pn = node.knob("profile_name").value()
        label = "Shotgun Write %s" % pn
        self.__update_knob_value(node, "label", label)

        # get the render path:
        path = self.__get_render_path(node, is_proxy)

        # calculate the parts:
        context_path = local_path = file_name = ""

        # check to see if we have cached the various pieces for this node:
        cache_key = (path, self._app.context)
        cached_path_preview = self.__path_preview_cache.get(cache_key)
        if cached_path_preview:
            context_path = cached_path_preview["context_path"]
            local_path = cached_path_preview["local_path"]
            file_name = cached_path_preview["file_name"]
        else:
            # normalize the path for os platform
            norm_path = path.replace("/", os.sep)
    
            # get the file name
            file_name = os.path.basename(norm_path)
            render_dir = os.path.dirname(norm_path)
    
            # now get the context path
            context_path = None
            for x in self._app.context.entity_locations:
                if render_dir.startswith(x):
                    context_path = x
    
            if context_path:
                # found a context path!
                # chop off this bit from the normalized path
                local_path = render_dir[len(context_path):]
                # drop start slash
                if local_path.startswith(os.sep):
                    local_path = local_path[len(os.sep):]
                # e.g. for path   /mnt/proj/shotXYZ/renders/v003/hello.%04d.exr
                # context_path:   /mnt/proj/shotXYZ
                # local_path:     renders/v003
            else:
                # skip the local path
                context_path = render_dir
                local_path = ""
                # e.g. for path   /mnt/proj/shotXYZ/renders/v003/hello.%04d.exr
                # context_path:   /mnt/proj/shotXYZ/renders/v003
                # local_path:
    
            self.__path_preview_cache[cache_key] = {"context_path":context_path, 
                                                    "local_path":local_path, 
                                                    "file_name":file_name}
    
        # update the preview knobs - note, not sure why but
        # under certain circumstances the property editor doesn't
        # update correctly - hiding and showing the knob seems to
        # fix this though without any noticeable side effect       
        def set_path_knob(name, value):
            k = node.knob(name)
            if k.value() != value:
                k.setValue(value)
            k.setVisible(False)
            k.setVisible(True)
        
        set_path_knob("path_context", context_path)
        set_path_knob("path_local", local_path)
        set_path_knob("path_filename", file_name)
        

    def __apply_cached_file_format_settings(self, node):
        """
        Apply the file_type and settings that have been cached on the node to the internal
        Write node.  This mechanism is used when the settings can't be retrieved from the
        profile for some reason.
        
        :param node:    The Shotgun write node to retrieve and apply the settings on
        """
        file_type = node["tk_file_type"].value()
        if not file_type:
            return
        
        file_settings_str = node["tk_file_type_settings"].value()
        file_settings = {}
        try:
            # file_settings_str is a pickled dictionary so convert it back to a dictionary:
            file_settings = pickle.loads(file_settings_str) or {}
        except Exception, e:
            self._app.log_warning("Failed to extract cached file settings from node '%s' - %s" 
                              % node.name(), e)
        
        # update the node:
        self.__populate_format_settings(node, file_type, file_settings)        
        

    def __set_profile(self, node, profile_name, reset_all_settings=False):
        """
        Set the current profile for the specified node.
        
        :param node:                The Shotgun Write node to set the profile on
        :param profile_name:        The name of the profile to set on the node
        :param reset_all_settings:  If true then all settings from the profile will be reset on the node.  If 
                                    false, only those that _aren't_ propagated up to the Shotgun Write node will 
                                    be reset.  For example, if colorspace has been set in the profile and force
                                    is False then the knob won't get reset to the value from the profile.
        """
        # can't change the profile if this isn't a valid profile:
        if profile_name not in self._profiles:
            # at the very least, try to restore the file format settings from the cached values:
            self.__apply_cached_file_format_settings(node)
            return

        # get the profile details:
        profile = self._profiles.get(profile_name)
        if not profile:
            # this shouldn't really every happen!
            self._app.log_warning("Failed to find a write node profile called '%s' for node '%s'!" 
                                  % profile_name, node.name())
            # at the very least, try to restore the file format settings from the cached values:
            self.__apply_cached_file_format_settings(node)            
            return

        self._app.log_debug("Changing the profile for node '%s' to: %s" % (node.name(), profile_name))

        # keep track of the old profile name:
        old_profile_name = node.knob("profile_name").value()
        
        # pull settings from profile:
        render_template = self._app.get_template_by_name(profile["render_template"])
        publish_template = self._app.get_template_by_name(profile["publish_template"])
        proxy_render_template = self._app.get_template_by_name(profile["proxy_render_template"])
        proxy_publish_template = self._app.get_template_by_name(profile["proxy_publish_template"])
        file_type = profile["file_type"]
        file_settings = profile["settings"]
        tile_color = profile["tile_color"]
        promote_write_knobs = profile.get("promote_write_knobs", [])

        # Make sure any invalid entries are removed from the profile list:
        list_profiles = node.knob("tk_profile_list").values()
        if list_profiles != self._profile_names:
            node.knob("tk_profile_list").setValues(self._profile_names)

        # update both the list and the cached value for profile name:
        self.__update_knob_value(node, "profile_name", profile_name)
        self.__update_knob_value(node, "tk_profile_list", profile_name)
        
        # set the format
        self.__populate_format_settings(
            node,
            file_type,
            file_settings,
            reset_all_settings,
            promote_write_knobs,
        )
        
        # cache the type and settings on the root node so that 
        # they get serialized with the script:
        self.__update_knob_value(node, "tk_file_type", file_type)
        self.__update_knob_value(node, "tk_file_type_settings", pickle.dumps(file_settings))

        # Hide the promoted knobs that might exist from the previously
        # active profile.
        for promoted_knob in self._promoted_knobs.get(node, []):
            promoted_knob.setFlag(nuke.INVISIBLE)

        self._promoted_knobs[node] = []
        write_node = node.node(TankWriteNodeHandler.WRITE_NODE_NAME)

        # We'll use link knobs to tie our top-level knob to the write node's
        # knob that we want to promote.
        for i, knob_name in enumerate(promote_write_knobs):
            target_knob = write_node.knob(knob_name)
            if not target_knob:
                self._app.log_warning("Knob %s does not exist and will not be promoted." % knob_name)
                continue

            link_name = "_promoted_" + str(i)

            # We have 20 link knobs stashed away to use.  If we overflow that
            # then we will simply create a new link knob and deal with the
            # fact that it will end up in a "User" tab in the UI. The reason
            # that we store a gaggle of link knobs on the gizmo is that it's
            # the only way to present the promoted knobs in the write node's
            # primary tab.  Adding knobs after the node exists results in them
            # being shoved into a "User" tab all by themselves, which is lame.
            if i > 19:
                link_knob = nuke.Link_Knob(link_name)
            else:
                # We have to pull the link knobs from the knobs dict rather than
                # by name, otherwise we'll get the link target and not the link
                # itself if this is a link that was previously used.
                link_knob = node.knobs()[link_name]

            link_knob.setLink(target_knob.fullyQualifiedName())
            label = target_knob.label() or knob_name
            link_knob.setLabel(label)
            link_knob.clearFlag(nuke.INVISIBLE)
            self._promoted_knobs[node].append(link_knob)

        # Adding knobs might have caused us to jump tabs, so we will set
        # back to the first tab.
        if len(promote_write_knobs) > 19:
            node.setTab(0)

        # write the template name to the node so that we know it later
        self.__update_knob_value(node, "render_template", render_template.name)
        self.__update_knob_value(node, "publish_template", publish_template.name)
        self.__update_knob_value(node, "proxy_render_template", 
                                 proxy_render_template.name if proxy_render_template else "")
        self.__update_knob_value(node, "proxy_publish_template", 
                                 proxy_publish_template.name if proxy_publish_template else "")

        # If a node's tile_color was defined in the profile then set it:
        if not tile_color or len(tile_color) != 3:
            if tile_color:
                # don't have exactly three values for RGB so log a warning:
                self._app.log_warning(("The tile_color setting for profile '%s' must contain 3 values (RGB) - this "
                                    "setting will be ignored!") % profile_name)
            
            # reset tile_color knob value back to default:
            default_value = int(node["tile_color"].defaultValue())
            self.__update_knob_value(node, "tile_color", default_value)
        else:
            # build packed RGB
            # (Red << 24) + (Green << 16) + (Blue << 8)
            packed_rgb = 0
            for element in tile_color:
                packed_rgb = (packed_rgb + min(max(element, 0), 255)) << 8 
        
            self.__update_knob_value(node, "tile_color", packed_rgb)

        # Reset the render path but only if the named profile has changed - this will only
        # be the case if the user has changed the profile through the UI so this will avoid
        # the node automatically updating without the user's knowledge.
        if profile_name != old_profile_name:
            self.reset_render_path(node)

    def __populate_initial_output_name(self, template, node):
        """
        Create a suitable output name for a node based on it's profile and
        the other nodes that already exist in the scene.
        """
        if node.knob(TankWriteNodeHandler.OUTPUT_KNOB_NAME).value():
            # don't want to modify the current value if there is one
            return
        
        # first, check that output is actually used in the template and determine 
        # the default value and if the key is optional.
        # (check for the 'channel' key as well for backwards compatibility)
        have_output_key = False
        output_default = None
        output_is_optional = True
        for key_name in ["output", "channel"]:
            key = template.keys.get(key_name)
            if key:
                have_output_key = True
                if output_default is None:
                    output_default = key.default
                if output_is_optional:
                    output_is_optional = template.is_optional(key_name)                
        if not have_output_key:
            # Nothing to do!
            return
        
        if output_default is None:
            # no default name - use hard coded built in
            output_default = "output"
        
        # get the output names for all other nodes that are using the same profile
        used_output_names = set()
        node_profile = self.get_node_profile_name(node)
        for n in self.get_nodes():
            if n != node and self.get_node_profile_name(n) == node_profile:
                used_output_names.add(n.knob(TankWriteNodeHandler.OUTPUT_KNOB_NAME).value())

        # handle if output is optional:
        if output_is_optional and "" not in used_output_names:
            # default should be an empty string:
            output_default = ""

        # now ensure output name is unique:
        postfix = 1
        output_name = output_default
        while output_name in used_output_names:
            output_name = "%s%d" % (output_default, postfix)
            postfix += 1
        
        # finally, set the output name on the knob:
        node.knob(TankWriteNodeHandler.OUTPUT_KNOB_NAME).setValue(output_name)

    def __populate_format_settings(
        self, node, file_type, file_settings, reset_all_settings=False, promoted_write_knobs=None
    ):
        """
        Controls the file format of the write node
        
        :param node:                    The Shotgun Write node to set the profile on
        :param file_type:               The file type to set on the internal Write node
        :param file_settings:           A dictionary of settings to set on the internal Write node
        :param reset_all_settings:      Determines if all settings should be set on the internal Write 
                                        node (True) or just those that aren't propagated to the Shotgun
                                        Write node (False) 
        :param promoted_write_knobs:    A list of knob names that have been promoted from the
                                        encapsulated write node. In the case where reset_all_settings
                                        is false, these knobs are treated as user-controlled knobs
                                        and will not be reset to their preset value.
        """
        # get the embedded write node
        write_node = node.node(TankWriteNodeHandler.WRITE_NODE_NAME)
        promoted_write_knobs = promoted_write_knobs or []
        
        # set the file_type
        write_node.knob("file_type").setValue(file_type)
        
        # and read it back to check that the value is what we expect
        if write_node.knob("file_type").value() != file_type:
            self._app.log_error("Shotgun write node configuration refers to an invalid file "
                                "format '%s'! Reverting to auto-detect mode instead." % file_type)
            write_node.knob("file_type").setValue("  ")
            return

        # get a list of the settings we shouldn't update:
        knobs_to_skip = []
        if not reset_all_settings:
            # Skip setting any knobs on the internal Write node that are represented by knobs on the 
            # containing Shotgun Write node.  These knobs are typically only set at first creation 
            # time or when the profile is changed as the artist is then free to change them.
            for knob_name in node.knobs():
                knob = node.knob(knob_name)

                if knob.node() == write_node:
                    knobs_to_skip.append(knob_name)

            knobs_to_skip.extend(promoted_write_knobs)

        # now apply file format settings
        for setting_name, setting_value in file_settings.iteritems():
            if setting_name in knobs_to_skip:
                # skip this setting:
                continue
            
            knob = write_node.knob(setting_name)
            if knob is None:
                self._app.log_error("%s is not a valid setting for file format %s. It will be ignored." 
                                    % (setting_name, file_type))
                continue

            knob.setValue(setting_value)
            if knob.value() != setting_value:
                self._app.log_error("Could not set %s file format setting %s to '%s'. Instead the value was set to '%s'" 
                                    % (file_type, setting_name, setting_value, knob.value()))

        # If we're not resetting everything, then we need to try and
        # make sure that the settings that the user made to the internal
        # write knobs are retained. The reason for this is that promoted
        # write knobs are handled by pre-defined link knobs, which are
        # left unlinked in the gizmo itself. This means that their values
        # are not properly written to the .nk file on save, and will
        # revert to default settings on load. On save of the .nk file, we
        # store a sanitized and serialized chunk of .nk script representing
        # all non-default knob values in a hidden knob "tk_write_node_settings".
        # Right here, we are deserializing that data and reapplying it to
        # the internal write node.
        if not reset_all_settings:
            tcl_settings = node.knob("tk_write_node_settings").value()

            if tcl_settings:
                knob_settings = pickle.loads(str(base64.b64decode(tcl_settings)))
                # We're going to filter out everything that isn't one of our
                # promoted write node knobs. This will allow us to make sure
                # that those knobs are set to the correct value, regardless
                # of what the profile settings above have done.
                filtered_settings = []

                # Example data after splitting:
                #
                # ['',
                #  'file /some/path/to/an/image.exr',
                #  'proxy /some/path/to/an/image.exr',
                #  'file_type exr',
                #  'datatype "32 bit float"',
                #  'beforeRender "<beforeRender callback script>"',
                #  'afterRender "<afterRender callback script>"']
                for setting in re.split(r"\n", knob_settings):
                    # We match the name of the knob, which is everything up to
                    # the first space character. From the example data above,
                    # that would be something like "datatype".
                    match = re.match(r"(\S+)\s.*", setting)
                    if match:
                        if match.group(1) in promoted_write_knobs:
                            self._app.log_debug(
                                "Found promoted write node knob setting: %s" % setting
                            )
                            filtered_settings.append(setting)

                self._app.log_debug(
                    "Promoted write node knob settings to be applied: %s" % filtered_settings
                )
                write_node.readKnobs("\n".join(filtered_settings))
                self.reset_render_path(node)

    def __set_output(self, node, output_name):
        """
        Set the output on the specified node from user interaction.
        """
        self._app.log_debug("Changing the output for node '%s' to: %s" % (node.name(), output_name))
        
        # update output knob:
        self.__update_knob_value(node, TankWriteNodeHandler.OUTPUT_KNOB_NAME, output_name)
        
        # reset the render path:
        self.reset_render_path(node)

    def __wrap_text(self, t, line_length):
        """
        Wrap text to the line_length number of characters where possible
        splitting on words
        """
        lines = []
        this_line = ""
        for part in t.split(" "):
            if len(part) >= line_length:
                if this_line:
                    lines.append(this_line)
                    this_line = ""
                lines.append(part)
                this_line_len = 0
            else:
                this_line = " ".join([this_line, part]) if this_line else part
                if len(this_line) >= line_length:
                    lines.append(this_line)
                    this_line = ""
        if this_line:
            lines.append(this_line)
        return lines

    def __update_render_path(self, node, force_reset=False, is_proxy=False):
        """
        Update the render path and the various feedback knobs based on the current
        context and other node settings.
        
        :param node:        The Shotgun Write node to update the path for
        :param force_reset: Force the path to be reset regardless of any cached
                            values
        :param is_proxy:    If True then update the proxy render path, otherwise
                            just update the normal render path.
        :returns:           The updated render path
        """
        try:
            # get the cached path without evaluating:
            cached_path = (node.knob("tk_cached_proxy_path").toScript() if is_proxy
                                    else node.knob("cached_path").toScript())

            if node in self.__currently_rendering_nodes:
                # when rendering we don't want to re-evaluate the paths as doing
                # so can cause problems!  Specifically, I found that accessing
                # width, height or format on a node can cause the evaluation
                # of the internal Write node file/proxy to not be evaluated!!
                return cached_path
            
            # it seems that querying certain things (e.g. node.width()) will sometimes cause the render 
            # and proxy paths to be re-evaluated causing this function to be called recursively which
            # can break things!  In case that happens we use some flags to track it so that the path
            # only gets updated once.
            if is_proxy:
                if self.__is_updating_proxy_path:
                    return cached_path
                else:
                    self.__is_updating_proxy_path = True
            else:
                if self.__is_updating_render_path:
                    return cached_path
                else:
                    self.__is_updating_render_path = True
    
            # get the current script path:
            script_path = self.__get_current_script_path()
                
            reset_path_button_visible = False
            path_warning = ""
            render_path = None
            cache_entry = None
            try:
                # gather the render settings to use when computing the path:
                render_template, width, height, output_name = self.__gather_render_settings(node, is_proxy)
                
                # experimental settings cache to avoid re-computing the path if nothing has changed...
                cache_item = self.__node_computed_path_settings_cache.get((node, is_proxy), (None, "", ""))
                old_cache_entry, compute_path_error, render_path = cache_item
                cache_entry = {
                    "ctx":self._app.context,
                    "width":width,
                    "height":height,
                    "output":output_name,
                    "script_path":script_path
                }
                
                if (not force_reset) and old_cache_entry and cache_entry == old_cache_entry:
                    # nothing of relevance has changed since the last time the path was changed!
                    # if there was previously an error then raise it so that it gets reported properly:
                    if compute_path_error:
                        raise TkComputePathError(compute_path_error)
                else:
                    # compute the render path:
                    render_path = self.__compute_render_path_from(node, render_template, width, height, output_name)
                    
            except TkComputePathError, e:
                # update cache:
                self.__node_computed_path_settings_cache[(node, is_proxy)] = (cache_entry, str(e), "")
                
                # render path could not be computed for some reason - display warning
                # to the user in the property editor:
                path_warning += "<br>".join(self.__wrap_text(
                        "The render path is currently frozen because Toolkit could not "
                        "determine a valid path!  This was due to the following problem:", 60)) + "<br>"
                path_warning += "<br>"
                path_warning += ("&nbsp;&nbsp;&nbsp;" 
                                + " <br>&nbsp;&nbsp;&nbsp;".join(self.__wrap_text(str(e), 57)) 
                                + " <br>")
                
                if cached_path:
                    # have a previously cached path so we can at least still render:
                    path_warning += "<br>"
                    path_warning += "<br>".join(self.__wrap_text(
                        "You can still render to the frozen path but you won't be able to "
                        "publish this node!", 60))
                
                render_path = cached_path
            else:
                # update cache:
                self.__node_computed_path_settings_cache[(node, is_proxy)] = (cache_entry, "", render_path)
                
                path_is_locked = False
                if not force_reset:
                    # if we force-reset the path then it will never be locked, otherwise we need to test
                    # to see if it is locked.  A path is considered locked if the render path differs
                    # from the cached path ignoring certain dynamic fields (e.g. width, height).
                    path_is_locked = self.__is_render_path_locked(node, render_path, cached_path, is_proxy)
                
                if path_is_locked:
                    # render path was not what we expected!
                    path_warning += "<br>".join(self.__wrap_text(
                        "The path does not match the current Shotgun Work Area.  You can "
                        "still render but you will not be able to publish this node.", 60)) + "<br>"
                    path_warning += "<br>"
                    path_warning += "<br>".join(self.__wrap_text(
                        "The path will be automatically reset next time you version-up, publish "
                        "or click 'Reset Path'.", 60))
                    
                    reset_path_button_visible = True
                    render_path = cached_path
                
                if not path_is_locked or not cached_path:
                    self.__update_knob_value(node, "tk_cached_proxy_path" if is_proxy else "cached_path", render_path)
                    
                # Also update the 'last known script' to be the current script
                # this mechanism is used to determine if the script is being saved
                # as a new file or as the same file in the onScriptSave callback
                last_known_script_knob = node.knob("tk_last_known_script")
                if force_reset or not last_known_script_knob.value():
                    last_known_script_knob.setValue(script_path)
    
            # Note that this method can get called to update the proxy render path when the node 
            # isn't in proxy mode!  Because we only want to update the UI to represent the 'actual'
            # state then we check for that here:  
            if is_proxy == node.proxy():
                
                # update warning displayed to the user:
                if path_warning:
                    path_warning = "<i style='color:orange'><b><br>Warning</b><br>%s</i><br>" % path_warning
                    self.__update_knob_value(node, "path_warning", path_warning)
                    node.knob("path_warning").setVisible(True)
                else:
                    self.__update_knob_value(node, "path_warning", "")
                    node.knob("path_warning").setVisible(False)
                node.knob("reset_path").setVisible(reset_path_button_visible)
        
                # show/hide proxy mode label depending if we're currently 
                # rendering in proxy mode:
                node.knob("tk_render_mode").setVisible(is_proxy)
                
                # update the render warning label if needed:
                render_warning = ""
                if is_proxy:
                    full_render_path = self.__get_render_path(node, False)
                    if full_render_path == render_path:
                        render_warning = ("The full & proxy resolution render paths are currently the same.  "
                                          "Rendering in proxy mode will overwrite any previously rendered "
                                          "full-res frames!")
                if render_warning:
                    self.__update_knob_value(node, "tk_render_warning", 
                                             "<i style='color:orange'><b>Warning</b> <br>%s<i><br>" 
                                             % "<br>".join(self.__wrap_text(render_warning, 60)))
                    node.knob("tk_render_warning").setVisible(True)
                else:
                    self.__update_knob_value(node, "tk_render_warning", "")
                    node.knob("tk_render_warning").setVisible(False)
                
                # update output knobs:
                self.__update_output_knobs(node)
    
                # finally, update preview:
                self.__update_path_preview(node, is_proxy)
    
            return render_path           

        finally:
            # make sure we reset the update flag
            if is_proxy:
                self.__is_updating_proxy_path = False
            else:
                self.__is_updating_render_path = False
        
    def __get_render_path(self, node, is_proxy=False):
        """
        Return the currently cached path for the specified node.  This will calculate the path
        if it's not previously been cached.
        """
        path = ""

        # get the cached path to return:
        if is_proxy:
            path = node.knob("tk_cached_proxy_path").toScript()
        else:
            path = node.knob("cached_path").toScript()
            
        if not path:
            # never been cached so compute instead:                
            try:
                path = self.__compute_render_path(node, is_proxy)
            except TkComputePathError:
                    # ignore
                    pass
            
        return path

    def __get_files_on_disk(self, node, is_proxy=False):
        """
        Called from render publisher & UI (via exists_on_disk)
        Returns the files on disk associated with this node
        """
        file_name = self.__get_render_path(node, is_proxy)
        template = self.__get_render_template(node, is_proxy, fallback_to_render=True)

        if not template.validate(file_name):
            raise Exception("Could not resolve the files on disk for node %s."
                            "The path '%s' is not recognized by Shotgun!" % (node.name(), file_name))

        fields = template.get_fields(file_name)
       
        # make sure we don't look for any eye - %V or SEQ - %04d stuff
        frames = self._app.tank.paths_from_template(template, fields, ["SEQ", "eye"])
        
        return frames

    def __calculate_proxy_dimensions(self, node):
        """
        Calculate the proxy dimensions for the specified node.
        
        Note, there must be an easier way to do this - have emailed support! - also
        this currently doesn't work if there is an upstream reformat node set to
        anything other than a format (e.g. scale, box)!
        """
        if not nuke.exists("root"):
            return
        root = nuke.root()
        
        # calculate scale and offset to apply for proxy    
        scale_x = scale_y = 1.0
        offset_x = offset_y = 0.0
    
        proxy_type = root.knob("proxy_type").value()
        if proxy_type == "scale":
            # simple scale factor:
            scale_x = scale_y = root.knob("proxy_scale").value()
        elif proxy_type == "format":
            # Need to calculate scale and offset required to map the proxy format to the root format
    
            # root format:
            root_format = root.format()
            root_w = root_format.width()
            root_h = root_format.height()
            root_aspect = root_format.pixelAspect()    
    
            # proxy format
            proxy_format = root.knob("proxy_format").value()
            proxy_w  = proxy_format.width()
            proxy_h  = proxy_format.height()
            proxy_aspect = proxy_format.pixelAspect()
        
            # calculate scales and offsets required:
            scale_x = float(proxy_w)/float(root_w)
            scale_y = scale_x * (proxy_aspect/root_aspect)
    
            offset_x = 0.0 # this always seems to be 0.0...
            offset_y = (((proxy_h/scale_y) - root_h) * scale_y)/2.0
        else:
            # unexpected type!
            pass
    
        # calculate the scaled format for the node:
        scaled_format = node.format().scaled(scale_x,scale_y,offset_x,offset_y)
                
        #print ("sx:", scale_x, "sy:", scale_y, "tx:", offset_x, "ty:", offset_y, 
        #        "w:", scaled_format.width(), "h:", scaled_format.height())
        return (scaled_format.width(), scaled_format.height())
        

    def __gather_render_settings(self, node, is_proxy=False):
        """
        Gather the render template, width, height and output name required
        to compute the render path for the specified node.
        
        :param node:         The current Shotgun Write node
        :param is_proxy:     If True then compute the proxy path, otherwise compute the standard render path
        :returns:            Tuple containing (render template, width, height, output name)
        """
        render_template = self.__get_render_template(node, is_proxy)
        width = height = 0
        output_name = ""
        
        if is_proxy:
            if not render_template:
                # we don't have a proxy template so fall back to render template.
                # there will be a warning in the UI for this
                #
                # Note: to retain backwards compatibility, if no proxy template has
                # been specified then the full-res dimensions will be used instead
                # of the proxy dimensions.
                return self.__gather_render_settings(node, False)
            
            # width & height are set to the proxy dimensions:
            width, height = self.__calculate_proxy_dimensions(node)
        else:
            # width & height are set to the node's dimensions:
            width, height = node.width(), node.height()
        
        if render_template:
            # check for 'channel' for backwards compatibility
            if "output" in render_template.keys or "channel" in render_template.keys:
                output_name = node.knob(TankWriteNodeHandler.OUTPUT_KNOB_NAME).value()
            
        return (render_template, width, height, output_name)


    def __compute_render_path(self, node, is_proxy=False):
        """
        Computes the render path for a node.

        :param node:         The current Shotgun Write node
        :param is_proxy:     If True then compute the proxy path, otherwise compute the standard render path
        :returns:            The computed render path        
        """
        
        # gather the render settings to use:
        render_template, width, height, output_name = self.__gather_render_settings(node, is_proxy)

        # compute the render path:
        return self.__compute_render_path_from(node, render_template, width, height, output_name)

    def __compute_render_path_from(self, node, render_template, width, height, output_name):
        """
        Computes the render path for a node using the specified settings

        :param node:               The current Shotgun Write node
        :param render_template:    The render template to use to construct the render path
        :param width:              The width of the rendered images
        :param height:             The height of the rendered images
        :param output_name:        The toolkit output name specified by the user for this node
        :returns:                  The computed render path        
        """

        # make sure we have a valid template:
        if not render_template:
            raise TkComputePathError("Unable to determine the render template to use!")
        
        # get the current script path:
        curr_filename = self.__get_current_script_path()

        # create fields dict with all the metadata
        #
        
        # extract the work fields from the script path using the work_file template:
        fields = {}
        if curr_filename and self._script_template and self._script_template.validate(curr_filename):
            fields = self._script_template.get_fields(curr_filename)
        if not fields:
            raise TkComputePathError("The current script is not a Shotgun Work File!")

        # Force use of %d format for nuke renders:
        fields["SEQ"] = "FORMAT: %d"
        
        # use %V - full view printout as default for the eye field
        fields["eye"] = "%V"

        # add in width & height:
        fields["width"] = width
        fields["height"] = height

        # add in date values for YYYY, MM, DD
        today = datetime.date.today()
        fields["YYYY"] = today.year
        fields["MM"] = today.month
        fields["DD"] = today.day

        # validate the output name - be backwards compatible with 'channel' as well
        for key_name in ["output", "channel"]:
            if key_name in fields:
                del(fields[key_name])
            
            if key_name in render_template.keys:
                if not output_name:
                    if not render_template.is_optional(key_name):
                        raise TkComputePathError("A valid output name is required by this profile for the '%s' field!"
                                                 % key_name)
                else:
                    if not render_template.keys[key_name].validate(output_name):                
                        raise TkComputePathError("The output name '%s' contains illegal characters!" % output_name)
                    fields[key_name] = output_name            
         
        # update with additional fields from the context:       
        fields.update(self._app.context.as_template_fields(render_template))

        # generate the render path:
        path = ""
        try:
            path = render_template.apply_fields(fields)
        except TankError, e:
            raise TkComputePathError(str(e))
        
        # make slahes uniform:
        path = path.replace(os.path.sep, "/")

        return path        

    def __is_render_path_locked(self, node, render_path, cached_path, is_proxy=False):
        """
        Return True if the render path is currently locked because something unexpected
        has changed.  When the render path is locked, the cached version will always be
        used until it has been reset by an intentional user change/edit.
        
        The path is locked if a new path generated with the previous template fields
        would be different to the cached path ignoring the width & height fields. 
        """
        # get the render template:
        render_template = self.__get_render_template(node, is_proxy, fallback_to_render=True)
        if not render_template:
            return True        
        
        path_is_locked = False
        if cached_path:
            # Need to determine if something unexpected has changed in the file path that 
            # we care about. To do this, we need to:
            # - Extract previous fields from cached path - if this fails then it tells us 
            #   that a static part of the template has changed
            # - Compare previous fields with new fields - this will tell us if a field we 
            #   care about has changed (we can ignore width, height differences).
            prev_fields = {}
            try:
                prev_fields = render_template.get_fields(cached_path)
            except TankError:
                # failed to extract or apply fields so something changed!
                path_is_locked = True
            else:
                # get the new fields from the render path and compare:
                new_fields = render_template.get_fields(render_path)
                
                path_is_locked = (len(new_fields) != len(prev_fields))
                if not path_is_locked:
                    for name, value in new_fields.iteritems():
                        if name not in prev_fields:
                            path_is_locked = True
                            break
                        
                        if name in ["width", "height", "YYYY", "MM", "DD"]:
                            # ignore these as they are free to change!
                            continue
                        elif prev_fields[name] != value:
                            path_is_locked = True
                            break
                        
        return path_is_locked     
                
    def __setup_new_node(self, node):
        """
        Setup a node when it's created (either directly or as a result of loading a script).
        This allows us to dynamically populate the profile list.

        This method will re-process the node and reapply settings in case it has
        been previously processed.

        .. note:: There are edge cases in Nuke where a node has already been previously
                  set up but for another context - this can happen as a consequence of
                  bugs in the automatic context switching. It is therefore not safe to
                  assume that setting up of these nodes only needs to happen once -
                  it needs to happen whenever the toolkit write node configuration
                  changes.
        
        :param node:    The Shotgun Write Node to set up
        """
        # check that this node is actually a Gizmo.  It might not be if 
        # it was created/loaded when the Gizmo wasn't available!
        if not isinstance(node, nuke.Gizmo):
            return
        
        self._app.log_debug("Setting up new node...")

        # reset the construction flag to ensure that
        # the node is toggled into its incomplete state
        # this will disable certain callbacks from firing.
        self.__set_final_construction_flag(node, False)

        # populate the profiles list as this isn't stored with the file and is
        # dynamic based on the user's configuration
        profile_names = list(self._profile_names)
        current_profile_name = self.get_node_profile_name(node)
        if current_profile_name and current_profile_name not in self._profiles:
            # profile no longer exists but we need to handle this so add it
            # to the list:
            current_profile_name = "%s [Not Found]" % current_profile_name
            profile_names.insert(0, current_profile_name)
            
        list_profiles = node.knob("tk_profile_list").values()
        if list_profiles != profile_names:
            node.knob("tk_profile_list").setValues(profile_names)
        
        reset_all_profile_settings = False
        if not current_profile_name:
            # default to first profile:
            current_profile_name = node.knob("tk_profile_list").value()
            # and as this node has never had a profile set, lets make
            # sure we reset all settings 
            reset_all_profile_settings = True 
        
        # ensure that the correct entry is selected from the list:
        self.__update_knob_value(node, "tk_profile_list", current_profile_name)
        # and make sure the node is up-to-date with the profile:
        self.__set_profile(node, current_profile_name, reset_all_settings=reset_all_profile_settings)
        
        # ensure that the disable value properly propogates to the internal write node:
        write_node = node.node(TankWriteNodeHandler.WRITE_NODE_NAME)
        write_node["disable"].setValue(node["disable"].value())

        # Ensure that the output name matches the node name if
        # that option is enabled on the node. This is primarily
        # going to handle the situation where a node with "use name as
        # output name" enabled is copied and pasted. When it is
        # pasted the node will get a new name to avoid a collision
        # and we need to make sure we update the output name to
        # match that new name.
        if node.knob(TankWriteNodeHandler.USE_NAME_AS_OUTPUT_KNOB_NAME).value():
            # force output name to be the node name:
            new_output_name = node.knob("name").value()
            self.__set_output(node, new_output_name)
        
        # now that the node is constructed, we can process
        # knob changes correctly.
        self.__set_final_construction_flag(node, True)

    def __set_final_construction_flag(self, node, status):
        """
        Controls the flag that indicates that a node has been
        finalized.

        :param node: nuke node object
        :param status: boolean flag to indicating finalized state.
        """
        if status:
            node.knob("tk_is_fully_constructed").setValue(True)
            node.knob("tk_is_fully_constructed").setEnabled(False)
        else:
            node.knob("tk_is_fully_constructed").setEnabled(True)
            node.knob("tk_is_fully_constructed").setValue(False)

    def __is_node_fully_constructed(self, node):
        """
        The tk_is_fully_constructed knob is set to True after the onCreate callback has completed.  This
        mechanism allows the code to ignore other callbacks that may fail because things aren't set
        up correctly (e.g. knobChanged calls for default values when loading a script).
        """
        if not node.knob("tk_is_fully_constructed"):
            return False
        
        return node.knob("tk_is_fully_constructed").value()
    
    def __on_knob_changed(self):
        """
        Callback that gets called whenever the value for a knob on a Shotgun Write
        node is set.
        
        Note, this gets called numerous times when a script is loaded as well as when
        a knob is changed via the user/script
        """
        node = nuke.thisNode()
        knob = nuke.thisKnob()
        grp = nuke.thisGroup()
        
        if not self.__is_node_fully_constructed(node):
            # knobChanged will be called during script load for all knobs with non-default 
            # values.  We want to ignore these implicit changes so we make use of a knob to
            # keep track of the node creation.  If the node isn't fully created we ignore
            # all knob changes
            #print "Ignoring change to %s.%s value = %s" % (node.name(), knob.name(), knob.value())
            return
        
        if knob.name() == "tk_profile_list":
            # change the profile for the specified node:
            new_profile_name = knob.value()
            self.__set_profile(node, new_profile_name, reset_all_settings=True)
            
        elif knob.name() == TankWriteNodeHandler.OUTPUT_KNOB_NAME:
            # internal cached output has been changed!
            new_output_name = knob.value()
            if node.knob(TankWriteNodeHandler.USE_NAME_AS_OUTPUT_KNOB_NAME).value():
                # force output name to be the node name:
                new_output_name = node.knob("name").value()
            self.__set_output(node, new_output_name)
            
        elif knob.name() == "name":
            # node name has changed:
            if node.knob(TankWriteNodeHandler.USE_NAME_AS_OUTPUT_KNOB_NAME).value():
                # set the output to the node name:
                self.__set_output(node, knob.value())
                
        elif knob.name() == TankWriteNodeHandler.USE_NAME_AS_OUTPUT_KNOB_NAME:
            # checkbox controlling if the name should be used as the output has been toggled
            name_as_output = knob.value()
            node.knob(TankWriteNodeHandler.OUTPUT_KNOB_NAME).setEnabled(not name_as_output)
            if name_as_output:
                # update output to reflect the node name:
                self.__set_output(node, node.knob("name").value())
                
        else:
            # Propogate changes to certain knobs from the gizmo/group to the
            # encapsulated Write node.
            #
            # The normal mechanism of linking these knobs can't be used because the
            # knob already exists as part of the base node (it's not added by the gizmo)
            knobs_to_propogate = ["disable"]
            
            # check if the value for this knob should be propogated:
            knob_name = knob.name()
            if knob_name in knobs_to_propogate:
                # find the enclosed write node:
                write_node = grp.node(TankWriteNodeHandler.WRITE_NODE_NAME)
                if not write_node:
                    return
            
                # propogate the value:
                self._app.log_debug("Propogating value for '%s.%s' to '%s.%s.%s'" 
                                    % (grp.name(), knob_name, grp.name(), write_node.name(), knob_name))
                
                write_node.knob(knob_name).setValue(nuke.thisKnob().value())

    def __get_current_script_path(self):
        """
        Get the current script path (if the current script has been saved).  This will
        use the nuke.scriptName() call if available (Nuke 8+ ?) otherwise it will fall
        back to the slightly less safe nuke.root().name() - this will result in an
        internal error (not a catchable exception) if the root object doesn't yet exist
        (e.g. whilst the file is being loaded).
        
        :returns:   The current Nuke script path or None if the script hasn't been
                    saved yet.  The path will have os-correct slashes
        """
        script_path = None
        if hasattr(nuke, "scriptName"):
            # scriptName method is new for Nuke 8
            try:
                script_path = nuke.scriptName()
            except:
                # script has never been saved!
                script_path = None
        else:
            # check nuke.root - note that this isn't safe to do if
            # the root node hasn't been created yet!
            if nuke.exists("root"):
                script_path = nuke.root().name()
                if script_path == "Root":
                    script_path = None
            
        if script_path:
            # convert to os-style slashes:
            script_path = script_path.replace("/", os.path.sep)
            
        return script_path
                

    def __on_script_save(self):
        """
        Called when the script is saved.
        
        Iterates over the Shotgun write nodes in the scene.  If the script is being saved as
        a new file then it resets all render paths before saving
        """
        save_file_path = self.__get_current_script_path()
        if not save_file_path:
            # script has never been saved as anything!
            return
        
        for n in self.get_nodes():
            # check to see if the script is being saved to a new file or the same file:
            knob = n.knob("tk_last_known_script")
            if not knob:
                continue
             
            last_known_path = knob.value()
            if last_known_path:
                # correct slashes for compare:
                last_known_path = last_known_path.replace("/", os.path.sep)
                
            if last_known_path != save_file_path:
                # we're saving to a new file so reset the render path:
                try:
                    self.reset_render_path(n)
                except:
                    # don't want any exceptions to stop the save!
                    pass

            # For each of our nodes, we need to keep a record of any non-default
            # knob values on the encapsulated write node. We will need this when
            # this file is re-opened, as the dynamically-linked, "promoted" write
            # knobs do not save to the .nk file properly, and so their values are
            # lost on load. We sanitize and serialize the .nk script data that the
            # writeKnobs() method gives us, and then store that in a hidden knob
            # tk_write_node_settings for use when repopulating the file_type
            # settings on load.
            write_node = n.node(TankWriteNodeHandler.WRITE_NODE_NAME)
            nk_data = write_node.writeKnobs(
                nuke.WRITE_NON_DEFAULT_ONLY | nuke.TO_SCRIPT | nuke.TO_VALUE
            )
            knob_changes = pickle.dumps(nk_data)
            self.__update_knob_value(
                n,
                "tk_write_node_settings",
                unicode(base64.b64encode(knob_changes)),
            )
                
    def __on_user_create(self):
        """
        Called when the user creates a Shotgun Write node.  Not called when loading
        or pasting a script.
        """
        node = nuke.thisNode()
        
        # check that this node is actually a Gizmo.  It might not be if 
        # it was created/loaded when the Gizmo wasn't available!
        if not isinstance(node, nuke.Gizmo):
            # it's not so we can't do anything!
            return
        
        # setup the new node:
        self.__setup_new_node(node)
        
        # populate the initial output name based on the render template:
        render_template = self.get_render_template(node)
        self.__populate_initial_output_name(render_template, node)







        
        
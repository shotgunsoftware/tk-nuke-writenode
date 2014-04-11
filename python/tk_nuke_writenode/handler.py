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

    ################################################################################################
    # Construction

    def __init__(self, app):
        """
        Construction
        """
        self._app = app
        self._script_template = self._app.get_template("template_script_work")
        
        # cache the profiles:
        self._profile_names = []
        self._profiles = {}
        for profile in self._app.get_setting("write_nodes", []):
            name = profile["name"]
            if name in self._profiles:
                self._app.log_warning("Configuration contains multiple Write Node profiles called '%s'!  Only the "
                                      "first will be available" % name)                
                continue
            
            self._profile_names.append(name)
            self._profiles[name] = profile
            
        self.__currently_rendering_nodes = set()
        self.__node_computed_path_settings_cache = {}
        self.__path_preview_cache = {}
        
        self.__enable_path_evaluation = True
            
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
            
    def get_nodes(self):
        """
        Returns a list of tank write nodes
        """
        return nuke.allNodes(group=nuke.root(), filter=TankWriteNodeHandler.SG_WRITE_NODE_CLASS, recurseGroups = True)
            
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
        self.__update_render_path(node, force_reset = True, is_proxy = is_proxy)     
        self.__update_render_path(node, force_reset = True, is_proxy = (not is_proxy))

    def create_new_node(self, profile_name):
        """
        Creates a new write node

        :returns: a node object.
        """
        if nuke.root().name() == "Root":
            # must snapshot first!
            nuke.message("Please save the file first!")
            return

        # make sure that the file is a proper tank work path
        curr_filename = nuke.root().name().replace("/", os.path.sep)
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
        self.__set_profile(node, profile_name)

        return node

    def process_placeholder_nodes(self):
        """
        Convert any placeholder nodes to TK Write Nodes
        """
        self._app.log_debug("Looking for placeholder nodes to process...")
        
        node_found = False
        for n in nuke.allNodes("ModifyMetaData"):
            if not n.name().startswith('ShotgunWriteNodePlaceholder'):
                continue

            self._app.log_debug("Found ShotgunWriteNodePlaceholder node: %s" % n)
            metadata = n.metadata()
            profile_name = metadata.get('name')

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

            # set the channel:            
            self.__set_channel(new_node, metadata.get('channel'))
            
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
        th_node.knob('disable').setValue(False)
        
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
            th_node.knob('disable').setValue(True)

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

        # set up all existing nodes:
        for n in self.get_nodes():
            self.__setup_new_node(n)
        
    def remove_callbacks(self):
        """
        Removed previously added callbacks
        """
        nuke.removeOnScriptLoad(self.process_placeholder_nodes, nodeClass="Root")
        nuke.removeOnScriptSave(self.__on_script_save)

    def convert_sg_to_nuke_write_nodes(self):
        """
        Utility function to convert all Shotgun Write nodes to regular
        Nuke Write nodes.
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
        
            # copy across file & proxy knobs:
            new_wn["file"].setValue(sg_wn["cached_path"].evaluate())
            new_wn["proxy"].setValue(sg_wn["tk_cached_proxy_path"].evaluate())

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
        
            # copy across select knob values from the Shotgun Write node:
            for knob_name in ["tile_color", "postage_stamp", "label"]:
                new_wn[knob_name].setValue(sg_wn[knob_name].value())
        
            # Store Toolkit specific information on write node
            # so that we can reverse this process later

            # profile
            knob = nuke.String_Knob("tk_profile_name")
            knob.setValue(sg_wn["profile_name"].value())
            new_wn.addKnob(knob)
            
            # channel
            knob = nuke.String_Knob("tk_channel")
            knob.setValue(sg_wn["tank_channel"].value())
            new_wn.addKnob(knob)
            
            # use node name for channel
            knob = nuke.Boolean_Knob("tk_use_name_as_channel")
            knob.setValue(sg_wn["tk_use_name_as_channel"].value())
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
        """
        # clear current selection:
        nukescripts.clear_selection_recursive()
        
        # get write nodes:
        write_nodes = nuke.allNodes(group=nuke.root(), filter="Write", recurseGroups = True)
        for wn in write_nodes:
        
            # look for additional toolkit knobs:
            profile_knob = wn.knob("tk_profile_name")
            channel_knob = wn.knob("tk_channel")
            use_name_as_channel_knob = wn.knob("tk_use_name_as_channel")
            render_template_knob = wn.knob("tk_render_template")
            publish_template_knob = wn.knob("tk_publish_template")
            proxy_render_template_knob = wn.knob("tk_proxy_render_template")
            proxy_publish_template_knob = wn.knob("tk_proxy_publish_template")
        
            if (not profile_knob
                or not channel_knob
                or not use_name_as_channel_knob
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
            
            # set the profile & channel - this will cause the paths to be reset:
            new_sg_wn["profile_name"].setValue(profile_knob.value())
            new_sg_wn["tank_channel"].setValue(channel_knob.value())
            new_sg_wn["tk_use_name_as_channel"].setValue(use_name_as_channel_knob.value())

            # make sure file_type is set properly:
            int_wn = new_sg_wn.node(TankWriteNodeHandler.WRITE_NODE_NAME)
            int_wn["file_type"].setValue(wn["file_type"].value())
#        
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

            # run this one last time to ensure the profile list is constructed correctly:
            self.__setup_new_node(new_sg_wn)

    def toggle_all_path_evaluation(self, enable_evaluation):
        """
        Toggle path evaluation for all Shotgun Write nodes.
        
        :param enable_evaluation:   If False, path evaluation will be blocked for all Write nodes.  If
                                    True then it will be unblocked.
        """
        if enable_evaluation == self.__enable_path_evaluation:
            # nothing to do!
            return

        if not enable_evaluation:
            # disabling so first make sure that all paths are up to date:
            for n in self.get_nodes():
                self.__update_render_path(n, False, False)
                self.__update_render_path(n, False, True)
        
        # toggle evaluation:
        self.__enable_path_evaluation = enable_evaluation
        
        if enable_evaluation:
            # update all paths as evaluation was previously disabled!
            for n in self.get_nodes():
                self.__update_render_path(n, True, False)
                self.__update_render_path(n, True, True)


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
        Called when a new instance of a Shotgun Write Node is created
        """
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
                cmd = 'xdg-open "%s"' % render_dir
            elif system == "darwin":
                cmd = "open '%s'" % render_dir
            elif system == "win32":
                cmd = 'cmd.exe /C start "Folder" "%s"' % render_dir
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
            if nuke.root()['proxy'].value():
                # proxy mode
                out_file = node.knob("proxy").evaluate()
            else:
                out_file = node.knob("file").evaluate()

            out_dir = os.path.dirname(out_file)
            self._app.ensure_folder_exists(out_dir)
            
        else:
            # stereo or odd number of views...
            for view in views:
                if nuke.root()['proxy'].value():
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
    
    def __is_channel_used(self, node):
        """
        Determine if channel is used in either the render or the proxy render
        templates
        """
        render_template = self.__get_render_template(node, is_proxy=False)
        proxy_render_template = self.__get_render_template(node, is_proxy=True)
        
        for template in [render_template, proxy_render_template]:
            if not template:
                continue
            if "channel" in template.keys:
                return True
            
        return False
    
    def __get_channel_for_node(self, node):
        """
        returns the channel for a tank write node.
        May return None if no value has been defined.
        """
        if self.__is_channel_used(node):
            return node.knob("tank_channel") or None
        else:
            return None
    
    def __update_knob_value(self, node, name, new_value):
        """
        Update the value for the specified knob on the specified node
        but only if it is different to the current value to avoid 
        unneccesarily invalidating the cache
        """
        current_value = node.knob(name).value()
        if new_value != current_value: 
            node.knob(name).setValue(new_value)
    
    def __update_channel_knobs(self, node):
        """
        Update channel knob visibility depending if channel is a key
        in the render template or not
        """
        channel_knob = node.knob("tank_channel")
        name_as_channel_knob = node.knob("tk_use_name_as_channel")
        
        channel_is_used = self.__is_channel_used(node)
        name_as_channel = name_as_channel_knob.value() 
        
        channel_knob.setEnabled(channel_is_used and not name_as_channel)
        channel_knob.setVisible(channel_is_used)
        name_as_channel_knob.setVisible(channel_is_used)    
    
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
        

    def __set_profile(self, node, profile_name):
        """
        Set the current profile for the specified node 
        """
        # can't change the profile if this isn't a valid profile:
        if profile_name not in self._profiles:
            return

        # check if the new profile is different to the old profile:
        current_profile = node.knob("profile_name").value()
        if profile_name == current_profile:
            return

        self._app.log_debug("Changing the profile for node '%s' to: %s" % (node.name(), profile_name))

        # get the profile details:
        profile = self._profiles[profile_name]
        render_template = self._app.get_template_by_name(profile["render_template"])
        publish_template = self._app.get_template_by_name(profile["publish_template"])
        proxy_render_template = self._app.get_template_by_name(profile["proxy_render_template"])
        proxy_publish_template = self._app.get_template_by_name(profile["proxy_publish_template"])
        file_type = profile["file_type"]
        file_settings = profile["settings"]

        # Make sure any invalid entries are removed from the profile list:
        list_profiles = node.knob("tk_profile_list").values()
        if list_profiles != self._profile_names:
            node.knob("tk_profile_list").setValues(self._profile_names)

        # update both the list and the cached value for profile name:
        self.__update_knob_value(node, "profile_name", profile_name)
        self.__update_knob_value(node, "tk_profile_list", profile_name)
        
        # set the format
        self.__populate_format_settings(node, file_type, file_settings)

        # auto-populate channel name based on template
        self.__populate_initial_channel_name(render_template, node)

        # write the template name to the node so that we know it later
        self.__update_knob_value(node, "render_template", render_template.name)
        self.__update_knob_value(node, "publish_template", publish_template.name)
        self.__update_knob_value(node, "proxy_render_template", 
                                 proxy_render_template.name if proxy_render_template else "")
        self.__update_knob_value(node, "proxy_publish_template", 
                                 proxy_publish_template.name if proxy_publish_template else "")

        # reset the render path:
        self.reset_render_path(node)

    def __populate_initial_channel_name(self, template, node):
        """
        Create a suitable channel name for a node based on it's profile and
        the other nodes that already exist in the scene.
        """
        if node.knob("tank_channel").value():
            # don't want to modify the current value if there is one
            return
        
        # first, check that channel is actually used in the 
        # template!
        channel_key = template.keys.get("channel")
        if not channel_key:
            # Nothing to do!
            return

        # try to get default channel name from template
        channel_name_base = channel_key.default
        if channel_name_base is None:
            # no default name - use hard coded built in
            channel_name_base = "output"
        
        # get the channels for all other nodes that are using the same profile
        used_channel_names = set()
        node_profile = self.get_node_profile_name(node)
        for n in self.get_nodes():
            if n != node and self.get_node_profile_name(n) == node_profile:
                used_channel_names.add(n.knob("tank_channel").value())

        # handle if channel is optional:
        if template.is_optional("channel") and "" not in used_channel_names:
            channel_name_base = ""

        # now ensure channel name is unique:
        postfix = 1
        channel_name = channel_name_base
        while channel_name in used_channel_names:
            channel_name = "%s%d" % (channel_name_base, postfix)
            postfix += 1
        
        # finally, set the channel name:
        node.knob("tank_channel").setValue(channel_name)

    def __populate_format_settings(self, node, file_type, file_settings):
        """
        Controls the file format of the write node
        """
        # get the embedded write node
        write_node = node.node(TankWriteNodeHandler.WRITE_NODE_NAME)
        
        # set the file_type
        write_node.knob("file_type").setValue(file_type)
        
        # and read it back to check that the value is what we expect
        if write_node.knob("file_type").value() != file_type:
            self._app.log_error("Shotgun write node configuration refers to an invalid file "
                                "format '%s'! Reverting to auto-detect mode instead." % file_type)
            write_node.knob("file_type").setValue("  ")
            return

        # now apply file format settings
        for setting_name, setting_value in file_settings.iteritems():
            knob = write_node.knob(setting_name)
            if knob is None:
                self._app.log_error("%s is not a valid setting for file format %s. It will be ignored." 
                                    % (setting_name, file_type))
                continue

            knob.setValue(setting_value)
            if knob.value() != setting_value:
                self._app.log_error("Could not set %s file format setting %s to '%s'. Instead the value was set to '%s'" 
                                    % (file_type, setting_name, setting_value, knob.value()))

    def __set_channel(self, node, channel_name):
        """
        Set the channel on the specified node from user interaction.
        """
        self._app.log_debug("Changing the channel for node '%s' to: %s" % (node.name(), channel_name))
        
        # update channel knob:
        self.__update_knob_value(node, "tank_channel", channel_name)
        
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
        Update the render path and the various feedback knobs
        """
        # get the cached path without evaluating:
        cached_path = (node.knob("tk_cached_proxy_path").toScript() if is_proxy
                                else node.knob("cached_path").toScript())

        if not self.__enable_path_evaluation or node in self.__currently_rendering_nodes:
            # when rendering we don't want to re-evaluate the paths as doing
            # so can cause problems!  Specifically, I found that accessing
            # width, height or format on a node can cause the evaluation
            # of the internal Write node file/proxy to not be evaluated!!
            if not self.__enable_path_evaluation:
                # this will get printed a lot but it is only for debug purposes so that should be fine!
                self._app.log_debug("Path evaluation is currently disabled - this may result in the render & "
                                    "proxy render paths becoming out of sync with the current context and "
                                    "other settings.")
            return cached_path
            
        reset_path_button_visible = False
        path_warning = ""
        render_path = None
        try:
            # gather the render settings to use when computing the path:
            render_template, width, height, channel_name = self.__gather_render_settings(node, is_proxy)
            
            # experimental settings cache to avoid re-computing the path
            # if nothing has changed...
            old_cache_entry = self.__node_computed_path_settings_cache.get((node, is_proxy))
            new_cache_entry = {
                "ctx":self._app.context,
                "width":width,
                "height":height,
                "channel":channel_name,
                "script_path":nuke.root().name()
            }
            
            if (not force_reset) and old_cache_entry and new_cache_entry == old_cache_entry:
                # nothing of relevance has changed since the last time the path was
                # computed so just return the cached path:
                render_path = cached_path
            else:
                # update cache:
                self.__node_computed_path_settings_cache[(node, is_proxy)] = new_cache_entry
            
                # compute the render path:
                render_path = self.__compute_render_path_from(node, render_template, width, height, channel_name)
                
        except TkComputePathError, e:
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
            path_is_locked = False
            if not force_reset:
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
                last_known_script_knob.setValue(nuke.root().name())

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
            
            # update channel knobs:
            self.__update_channel_knobs(node)

            # finally, update preview:
            self.__update_path_preview(node, is_proxy)

        return render_path           
        
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
        root = nuke.root()
        if not root:
            return
    
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
        Gather the render template, width, height and channel name required
        to compute the render path for the specified node.
        
        :param node:         The current Shotgun Write node
        :param is_proxy:     If True then compute the proxy path, otherwise compute the standard render path
        :returns:            Tuple containing (render template, width, height, channel name)
        """
        render_template = self.__get_render_template(node, is_proxy)
        width = height = 0
        channel_name = ""
        
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
        
        if "channel" in render_template.keys:
            channel_name = node.knob("tank_channel").value()
            
        return (render_template, width, height, channel_name)


    def __compute_render_path(self, node, is_proxy=False):
        """
        Computes the render path for a node.

        :param node:         The current Shotgun Write node
        :param is_proxy:     If True then compute the proxy path, otherwise compute the standard render path
        :returns:            The computed render path        
        """
        
        # gather the render settings to use:
        render_template, width, height, channel_name = self.__gather_render_settings(node, is_proxy)

        # compute the render path:
        return self.__compute_render_path_from(node, render_template, width, height, channel_name)

    def __compute_render_path_from(self, node, render_template, width, height, channel_name):
        """
        Computes the render path for a node using the specified settings

        :param node:               The current Shotgun Write node
        :param render_template:    The render template to use to construct the render path
        :param width:              The width of the rendered images
        :param height:             The height of the rendered images
        :param channel_name:       The toolkit channel name specified by the user for this node
        :returns:                  The computed render path        
        """

        # make sure we have a valid template:
        if not render_template:
            raise TkComputePathError("Unable to determine the render template to use!")
        
        # make sure we have a valid nuke root node: 
        root_node = nuke.root()
        if not root_node:
            return ""

        # create fields dict with all the metadata
        #
        
        # extract the work fields from the script path using the work_file template:
        fields = {}
        curr_filename = root_node.name().replace("/", os.path.sep)
        if self._script_template and self._script_template.validate(curr_filename):
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

        # validate the channel name
        if "channel" in fields:
            del(fields["channel"])
        if "channel" in render_template.keys:
            if not channel_name:
                if not render_template.is_optional("channel"):
                    raise TkComputePathError("A valid channel is required by this profile!")
            else:
                if not render_template.keys["channel"].validate(channel_name):                
                    raise TkComputePathError("The channel name '%s' contains illegal characters!" % channel_name)
                fields["channel"] = channel_name
         
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
                        
                        if name in ["width", "height"]:
                            # ignore these as they are free to change!
                            continue
                        elif prev_fields[name] != value:
                            path_is_locked = True
                            break
                        
        return path_is_locked     
                
    def __setup_new_node(self, node):
        """
        Setup a node when it's created (either directly or as a result of loading a script).  This
        allows us to dynamically populate the profile list.
        """
        self._app.log_debug("Setting up new node...")
        
        # populate the profiles list as this isn't stored with the file:
        profile_names = list(self._profile_names)
        current_profile_name = self.get_node_profile_name(node)
        if current_profile_name and current_profile_name not in self._profiles:
            # profile no longer exists but we need to handle this:
            current_profile_name = "%s [Invalid]" % current_profile_name
            profile_names.insert(0, current_profile_name)
        node.knob("tk_profile_list").setValues(profile_names)
        
        if current_profile_name:
            # ensure that the correct entry is selected from the list:
            self.__update_knob_value(node, "tk_profile_list", current_profile_name)
        
        # ensure that the disable value properly propogates to the internal write node:
        write_node = node.node(TankWriteNodeHandler.WRITE_NODE_NAME)
        write_node["disable"].setValue(node["disable"].value()) 
        
        # now that the node is constructed, we can process knob changes
        # correctly.
        node.knob("tk_is_fully_constructed").setValue(True)
        node.knob("tk_is_fully_constructed").setEnabled(False)
    
    def __is_node_fully_constructed(self, node):
        """
        The tk_is_fully_constructed knob is set to True after the onCreate callback has completed.  This
        mechanism allows the code to ignore other callbacks that may fail because things aren't set
        up correctly (e.g. knobChanged calls for default values when loading a script).
        """
        try:
            return node.knob("tk_is_fully_constructed").value()            
        except ValueError:
            # it seems that nuke sometimes calls callbacks before it's finished setting
            # up a node enough to be accessed - this catches that error and ignores it
            return False
    
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
            self.__set_profile(node, new_profile_name)
            
        elif knob.name() == "tank_channel":
            # internal cached channel has been changed!
            new_channel_name = knob.value()
            if node.knob("tk_use_name_as_channel").value():
                # force channel name to be the node name:
                new_channel_name = node.knob("name").value()
            self.__set_channel(node, new_channel_name)
            
        elif knob.name() == "name":
            # node name has changed:
            if node.knob("tk_use_name_as_channel").value():
                # set the channel to the node name:
                self.__set_channel(node, knob.value())
                
        elif knob.name() == "tk_use_name_as_channel":
            # checkbox controlling if the name should be used as the channel has been toggled
            name_as_channel = knob.value()
            node.knob("tank_channel").setEnabled(not name_as_channel)
            if name_as_channel:
                # update channel to reflect the node name:
                self.__set_channel(node, node.knob("name").value())
                
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

    def __on_script_save(self):
        """
        Called when the script is saved.
        
        Iterates over the Shotgun write nodes in the scene.  If the script is being saved as
        a new file then it resets all render paths before saving
        """
        save_file_path = nuke.root().name()
        if save_file_path == "Root":
            # don't think this should ever be the case!
            return        
        
        for n in self.get_nodes():
            # check to see if the script is being saved to a new file or the same file: 
            last_known_path = n.knob("tk_last_known_script").value()
            if last_known_path != save_file_path:
                # we're saving to a new file so reset the render path:
                try:
                    self.reset_render_path(n)
                except:
                    # don't want any exceptions to stop the save!
                    pass


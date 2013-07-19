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

import tank
from tank import TankError
from tank.platform import constants

# Special exception raised when the work file cannot be resolved.
class TankWorkFileError(TankError):
    pass

class TankWriteNodeHandler(object):
    """
    Handles requests and processing from a tank write node.
    """

    def __init__(self, app):
        self._app = app
        self._script_template = self._app.get_template("template_script_work")

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
        settings = self._get_node_profile_settings(node)
        if settings:
            return settings["tank_type"]

    def _get_current_script_fields(self):        
        """
        Extract fields from the current script using the script
        template
        """
        curr_filename = nuke.root().name().replace("/", os.path.sep)
        
        work_fields = {}
        if self._script_template and self._script_template.validate(curr_filename):
            work_fields = self._script_template.get_fields(curr_filename)
                
        return work_fields

    def _get_node_profile_settings(self, node):
        """
        Find the profile settings for the specified node
        """
        profile_name = self.get_node_profile_name(node)
        if profile_name:
            app_settings = self._app.get_setting("write_nodes", {})
            for profile in app_settings:
                if profile["name"] == profile_name:
                    return profile

    def _get_template(self, node, name):
        """
        Get the named template for the specified node.
        """
        template_name = None
        
        # get the template from the nodes profile settings:
        settings = self._get_node_profile_settings(node)
        if settings:
            template_name = settings[name]
            if template_name:
                # update the cached setting:
                node.knob(name).setValue(template_name)
        else:
            # the profile probably doesn't exist any more so
            # try to use the cached version
            template_name = node.knob(name).value()
            
        return self._app.get_template_by_name(template_name)
        
    def get_render_template(self, node):
        """
        helper function. Returns the associated render template obj for a node
        """
        return self._get_template(node, "render_template")

    def get_publish_template(self, node):
        """
        helper function. Returns the associated pub template obj for a node
        """
        return self._get_template(node, "publish_template")
    
    def _update_path_preview(self, node, path):
        """
        Updates the path preview fields on the tank write node.
        """

        # first set up the node label
        # this will be displayed on the node in the graph
        # useful to tell what type of node it is
        pn = node.knob("profile_name").value()
        label = "Shotgun Write %s" % pn
        node.knob("label").setValue(label)

        # now try to set the nuke node name - fail gracefully
        work_file_fields = self._get_current_script_fields()
        if work_file_fields:
            chan_name = node.knob("tank_channel").evaluate()
            
            # preview: myscene output3 v032
            # alt:     myscene v032
            node_name = "%s " % work_file_fields.get("name")
            if chan_name != "":
                node_name += "%s " % chan_name
            node_name += "v%03d" % work_file_fields.get("version")
            node.knob("name").setValue(node_name)
        
        # normalize the path for os platform
        norm_path = path.replace("/", os.sep)

        # get the file name
        filename = os.path.basename(norm_path)
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

        pn = node.knob("path_context")
        pn.setValue(context_path)
        pn = node.knob("path_local")
        pn.setValue(local_path)
        pn = node.knob("path_filename")
        pn.setValue(filename)

    def __node_type_exists(self, render_template):
        """
        Returns True if there is already a node in the scene with the given render template.
        """
        for n in nuke.allNodes("WriteTank"):
            if self.get_render_template(n) == render_template:
                return True
        return False

    def __populate_channel_name(self, template, node):
        """
        Create a suitable channel name for a node
        """
        # look at all other nodes to determine the channel
        # name so that we don't produce a duplicate and
        # to check if we are the first write for a given
        # profile
        channel_names = []
        for n in nuke.allNodes("WriteTank"):
            ch_knob = n.knob("tank_channel")
            channel_names.append(ch_knob.evaluate())

        channel_knob = node.knob("tank_channel")

        # need to know if channel is optional to default the first value
        def is_optional(tmpl, key):
            required_keys = tmpl.missing_keys({}, skip_defaults=False)
            return not key in required_keys

        # try to get default channel name from template
        nk = template.keys.get("channel")
        if nk is None:
            # disable channel if it is not in the template.
            channel_name_base = ''
            channel_knob.setEnabled(False)
            channel_knob.setVisible(False)
        else:
            channel_name_base = nk.default
            if channel_name_base is None:
                # no default name - use hard coded built in
                channel_name_base = "output"
            if is_optional(template, "channel"):
                if not self.__node_type_exists(template):
                    # first optional node gets an empty channel name
                    channel_name_base = ''
                else:
                    # not the first, pretend there is at least one default named node
                    channel_names.append(channel_name_base)

        # look at other nodes to ensure uniqueness
        counter = 0
        channel_name = channel_name_base
        while channel_name in channel_names:
            counter += 1
            if counter > 1:
                channel_name = "%s%d" % (channel_name_base, counter)
            else:
                channel_name = channel_name_base

        channel_knob.setValue(channel_name)

    def __populate_format_settings(self, node, file_type, file_settings):
        """
        Controls the file format of the write node
        """
        # get the embedded write node
        write_node = node.node("Write1")
        # set the file_type
        write_node.knob("file_type").setValue(file_type)
        # now have to read it back and check that the value is what we
        # expect. Cheers Nuke.
        if write_node.knob("file_type").value() != file_type:
            self._app.log_error("Shotgun write node configuration refers to an invalid file "
                                "format '%s'! Will revert to auto-detect mode." % file_type)
            write_node.knob("file_type").setValue("  ")
            return

        # now apply file format settings
        for x in file_settings:
            knob = write_node.knob(x)
            val_to_set = file_settings[x]
            if knob is None:
                self._app.log_error("Invalid setting for file format %s - %s: %s. This "
                                    "will be ignored." % (file_type, x, val_to_set))
            else:
                knob.setValue(val_to_set)
                val = knob.value()
                if val != val_to_set:
                    self._app.log_error("Could not set %s file format setting %s: '%s'. Instead "
                                        "the value was set to '%s'" % (file_type, x, val_to_set, val))

    def create_new_node(self, name, render_template, pub_template, file_type, file_settings):
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
        
        # if the render template does not contain channel then only one node
        # for that template is allowed
        if "channel" not in render_template.keys:
            if self.__node_type_exists(render_template):
                nuke.message("Only one node of this type is allowed.  Add channel "
                        "to the Shotgun template for this node type to change that.")
                return

        # new node please!
        node = nuke.createNode("WriteTank")

        self._app.log_debug("Created Shotgun Write Node %s" % node.name())

        # auto-populate channel name based on template
        self.__populate_channel_name(render_template, node)

        # write the template name to the node so that we know it later
        node.knob("render_template").setValue(render_template.name)
        node.knob("publish_template").setValue(pub_template.name)
        node.knob("profile_name").setValue(name)

        # set the format
        self.__populate_format_settings(node, file_type, file_settings)

        # calculate the preview path
        self._update_path_preview(node, self.compute_path(node))

        return node

    def compute_path(self, node, force_compute=False):
        """
        Computes the path for a node.

        :param node: current write node
        :param force_compute: always re-compute the path ignoring any cached_path
        :returns: a path based on the node settings
        """
        # note! Nuke has got pretty good error handling so don't try to catch any exceptions.

        if not force_compute:
            # if there is a cached path then just return that:
            cached_path = node.knob("cached_path").toScript()
            if cached_path:
                return cached_path
        
        work_file_fields = self._get_current_script_fields()
        if not work_file_fields:
            raise TankWorkFileError("Not a Shotgun Work File!")
        
        # get the template
        template = self.get_render_template(node)

        # create fields dict with all the metadata
        fields = {}
        fields["width"] = node.width()
        fields["height"] = node.height()
        fields["name"] = work_file_fields.get("name")
        fields["version"] = work_file_fields["version"]
        fields["SEQ"] = "FORMAT: %d"
        # use %V - full view printout as default for the eye field
        fields["eye"] = "%V"

        # now validate the channel name
        chan_name = self.get_channel_from_node(node)
        if (chan_name is not None) and ("channel" in template.keys) and (not template.keys["channel"].validate(chan_name)):
            raise TankError("Channel name '%s' contains illegal characters!" % chan_name)
        if chan_name is not None:
            fields["channel"] = chan_name

        fields.update(self._app.context.as_template_fields(template))

        # get a path from tank
        render_path = template.apply_fields(fields)
        
        # make slahes uniform:
        render_path = render_path.replace(os.path.sep, "/")

        return render_path

    def on_compute_path_gizmo_callback(self, node):
        """
        Callback executed when nuke requests the location of the std output to be computed.
        returns a path on disk. This will return the path in a form that Nuke likes
        (eg. with slashes). 
        
        It also updates the preview fields on the node. and the UI
        """
        
        # get the cached path without evaluating (so it should be the value that originally set):
        cached_path = node.knob("cached_path").toScript()
        
        # compute path:
        reset_path_button_visible = False
        path_warning = ""
        
        try:
            render_path = self.compute_path(node, True)
        except TankWorkFileError:
            # work file could not be resolved.
            # this probably means that someone has moved this nuke
            # file to a location outside of tank (for rendering?)
            # in that case just keep the old file paths.
            render_path = cached_path
            
            # turn on the warning about location
            path_warning = ("<i style='color:orange'>"
                       "Path is currently frozen because the Nuke file has <br>"
                       "been moved outside the area of the file system that <br>"
                       "Shotgun recognizes. <br>"
                       "You can still render this node, but you cannot make <br>"
                       "any changes to it.<br>"
                       "</i>")
        else:
            if not cached_path:
                # cache the new render path
                node.knob("cached_path").setValue(render_path)
                #node.knob("path_warning").setValue("")
            elif render_path != cached_path:
                # render path does not match the cached path - the template has probably changed!
                path_warning = ("<i style='color:orange'>"
                                "The path does not match the current Shotgun Work Area.  You<br>"
                                "can still render but you will not be able to publish this node.<br>"
                                "<br>"
                                "The path will be automatically reset next time you version-up,<br>"
                                "publish or click 'Reset Path'.<br>"
                                "</i>")
                
                reset_path_button_visible = True
                render_path = cached_path            
            
        # update preview:
        self._update_path_preview(node, render_path)
        
        node.knob("reset_path").setVisible(reset_path_button_visible)
        node.knob("path_warning").setValue(path_warning)
        node.knob("path_warning").setVisible(bool(path_warning))
        
        return render_path
    
    def reset_render_path(self, node):
        """
        Reset the render path of the specified node.  This
        will force the render path to be updated based on
        the current script path and configuraton
        """        
        node.knob("cached_path").setValue("")
        # callback to refresh cached path and update node:
        self.on_compute_path_gizmo_callback(node)   

    def render_path_is_locked(self, node):
        calculated_path = ""
        try:
            calculated_path = self.compute_path(node, True)
        except:
            return True
        return self.compute_path(node, False) != calculated_path

    def generate_thumbnail(self, node):
        """
        generates a thumbnail in a temp location and returns the path to it.
        It is the responsibility of the caller to delete this thumbnail afterwards.
        The thumbnail will be in png format.

        Returns None if no thumbnail could be generated
        """
        # get thumbnail node

        th_node = node.node("create_thumbnail")
        th_node.knob('disable').setValue(False)
        if th_node is None:
            # write gizmo that does not have the create thumbnail node
            return None

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

    def show_in_fs(self, node):
        """
        Shows the location of the node in the file system.
        This is a callback which is executed when the show in fs
        button is pressed on the nuke write node.
        """
        render_dir = None

        # first, try to just use the current cached path:
        render_path = self.compute_path(node)
        if render_path:
            dir_name = os.path.dirname(render_path)
            if os.path.exists(dir_name):
                render_dir = dir_name
                
        if not render_dir:
            # render directory doesn't exist so try using location
            # of rendered frames instead:
            try:
                files = self.get_files_on_disk(node)
                if len(files) == 0:
                    nuke.message("There are no renders for this node yet!\n"
                             "When you render, the files will be written to "
                             "the following location:\n\n%s" % render_path)
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
                self.log_error("Failed to launch '%s'!" % cmd)

    def get_files_on_disk(self, node):
        """
        Called from render publisher & UI (via exists_on_disk)
        Returns the files on disk associated with this node
        """
        file_name = self.compute_path(node)
        template = self.get_render_template(node)

        if not template.validate(file_name):
            raise Exception("Could not resolve the files on disk for node %s."
                            "The path '%s' is not recognized by Shotgun!" % (node.name(), file_name))

        fields = template.get_fields(file_name)
       
        # make sure we don't look for any eye - %V or SEQ - %04d stuff
        frames = self._app.tank.paths_from_template(template, fields, ["SEQ", "eye"])

        return frames

    def exists_on_disk(self, node):
        """
        Called from UI only atm - ImageBrowser
        returns true if this node has been rendered to disk
        """
        return (len(self.get_files_on_disk(node)) > 0)

    def get_channel_from_node(self, node):
        """
        returns the channel for a tank write node.
        May return None if no value has been defined.
        """
        channel_knob = node.knob("tank_channel")
        return channel_knob.value() or None

    def get_nodes(self):
        """
        Returns a list of tank write nodes
        """
        return nuke.allNodes("WriteTank")


    def on_before_render(self, node):
        """
        callback from nuke whenever a tank write node is about to be rendered.
        note that the node parameter represents the write node inside of the gizmo.
        """

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

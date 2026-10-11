from __future__ import annotations

import nuke
import sgtk


class WriteNodeHook(sgtk.get_hook_baseclass()):
    """Callbacks for tk write nodes."""

    @property
    def handler(self):
        """Return the TankWriteNodeHandler instance of tk-nuke-writenode app."""
        return self.parent.handler

    def post_create_new_node(self, node: nuke.Node, profile_name: str) -> None:
        """Callback triggered only after handler created a new write node.

        This is triggered after `.post_profile_set` is called.
        """

    def post_profile_changed(
        self, node: nuke.Node, old_profile_name: str, profile_name: str
    ) -> None:
        """Callback triggered only when the write node's profile was changed.

        This is triggered before `.post_profile_set` is called.
        """

    def post_profile_set(self, node: nuke.Node, profile_name: str) -> None:
        """Callback when node's profile was set, regardless of whether it changed.

        This is triggered after `.post_profile_changed` is called.
        """

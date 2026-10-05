from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import nuke
import sgtk
from fileseq import findSequenceOnDisk  # fileseq is part of sgtk dependencies

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable


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

    def create_read_nodes(self, node: nuke.Node) -> None:
        """Callback to create read nodes associated with the tk write node."""
        prefix = (
            f"{node.fullName()}[{self.handler.get_node_profile_name(node)}] "
            "Failed to compute"
        )
        new_nodes = []
        try:
            with node.parent(), self.suppress(msg=(f"{prefix} work render path")):
                # Work render
                work_path: str = self.handler.compute_render_path(node)
                read_node = self.create_read(work_path)
                read_node.setName(f"Read_{node.name()}_Work")
                new_nodes.append(read_node)

                # Publish render
                with self.suppress(msg=(f"{prefix} publish path")):
                    publish_path = self.compute_publish_path(node, work_path=work_path)
                    read_node = self.create_read(publish_path)
                    read_node.setName(f"Read_{node.name()}_Publish")
                    new_nodes.append(read_node)
        finally:
            self.layout_as_row(node, new_nodes)

    @contextlib.contextmanager
    def suppress(
        self,
        *exceptions: type[Exception],
        level: int = logging.DEBUG,
        msg: str = "",
        **kwargs: object,
    ) -> Generator[None, None, None]:
        """Context manager to suppress exceptions and log a message if they occur.

        Similar to `.contextlib.suppress`, but accepts optional ``level`` for explicit
        logging level for given, fully formatted ``msg``, along with any ``kwargs``
        passed-through to the `.logging.Logger.log` call.
        """
        exceptions = exceptions or (Exception,)
        try:
            yield
        except exceptions:
            if msg:
                self.logger.log(level, msg, **kwargs)

    def layout_as_row(
        self,
        node: nuke.Node,
        nodes_to_layout: Iterable[nuke.Node],
        x_gap: int = 150,
        y_offset: int = 100,
    ) -> Generator[list[nuke.Node], None, None]:
        """Context manager to layout nodes as a row relative to a given node.

        By default, layout the nodes below the given node left-to-right.

        Positive ``y_offset`` will place row below the given node, while negative will
        place it above. Positive ``x_gap`` will space nodes left-to-right, while
        negative will space them right-to-left.
        """
        valid_nodes = [n for n in nodes_to_layout if n.parent() == node.parent()]
        y_pos = node.ypos() + y_offset

        x_span = abs(x_gap) * (len(valid_nodes) - 1)
        start_x = node.xpos() + (x_span // 2)

        for multiplier, new_node in enumerate(valid_nodes):
            new_node.setXYpos(start_x - (multiplier * x_gap), y_pos)

    def create_read(self, path: str) -> nuke.Node:
        """Create a Nuke Read node from the given full path."""
        read_node = nuke.createNode("Read")
        read_path = path
        with self.suppress(level=logging.WARNING, msg=f"No {path!r} found"):
            seq = findSequenceOnDisk(path, strictPadding=True, preserve_padding=True)
            read_path = f"{path} {seq.frameRange()}"
        read_node["file"].fromUserText(read_path)
        return read_node

    def compute_publish_path(
        self,
        node: nuke.Node,
        publish_template: sgtk.TemplatePath | None = None,
        work_path: str | None = None,
        work_template: sgtk.TemplatePath | None = None,
    ) -> str | None:
        """Compute the publish path based on the work path and templates."""
        publish_template = publish_template or self.handler.get_publish_template(node)
        work_path = work_path or self.handler.compute_work_path(node)
        work_template = work_template or self.handler.get_render_template(node)

        publish_fields: dict = work_template.get_fields(work_path)
        return publish_template.apply_fields(publish_fields)

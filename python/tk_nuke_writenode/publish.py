"""
Copyright (c) 2013 Shotgun Software, Inc
----------------------------------------------------

Publish support for Tank WriteNodes

"""

from .handler import TankWriteNodeHandler

class WriteNodePublishable(object):
    """
    Encapsulate a write node for publish
    """
    def __init__(self, handler, node):
        self._write_node_handler = handler 
        self._node = node
        
    def publish(self, target_path):
        """
        do publish!
        """
        pass
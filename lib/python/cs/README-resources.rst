Resourcing related classes and functions.
=========================================

NestingOpenClosedMixin
----------------------

This is a mixin class for objects with multiple open/close users.
After the last .close, the object's .shutdown method is called.
This also presented the context manager interface to allow open/close thus::

  with obj:
    do stuff while open

@notclosed
----------

Decorator for object methods which must not be called after the object is closed.

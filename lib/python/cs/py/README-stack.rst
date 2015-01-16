Convenience functions for the python execution stack.
-----------------------------------------------------

I find the supplied python traceback facilities quite awkward.
These functions provide convenient facilities.

Presented:

* Frame, a nametuple for a stack frome with a nice __str__.

* frames(), returning the current stack as a list of Frames.

* caller(), returning the Frame of the caller's caller.

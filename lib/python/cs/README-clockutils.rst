An implementation of PEP0418 with the "Choosing the clock from a list of constraints" get_clock() and get_clocks() functions.
=============================================================================================================================

PEP 418 (https://www.python.org/dev/peps/pep-0418/) generated much discussion.

Most particpants wanted a call to get a high resolution monotonic clock, which many platforms offer, and much of the discussion surrounded what guarrentees Python should offer for the clock it returned.

I was of the opinion that:

* the many different clocks available have varying features and that the user should be able to inspect them when handed a clock

* the proposed time.monotonic() et al should be offered as default policies for convenience

* however, the proposed time.monotonic() call and friends represented policy; the user calling it is handed an arbitrary clock with some guarrentees; the user has no facility to implement policy themselves. Therefore I proposed two calls: get_clock() and get_clocks() for requesting or enumerating clocks with desired features from those available on the platform.

This cs.clockutils package implements get_clock() and get_clocks() and provides example implementations of the "policy" calls such as monotonic().

References:
-----------

PEP418
  https://www.python.org/dev/peps/pep-0418/
My core two posts in the discussion outlining my proposal
  http://www.mail-archive.com/python-dev@python.org/msg66174.html
  http://www.mail-archive.com/python-dev@python.org/msg66179.html

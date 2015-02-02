Later: queue functions for execution later in priority and time order.
======================================================================

I use Later objects for convenient queuing of functions whose execution occurs later in a priority order with capacity constraints.

Why not futures? I already had this, I prefer its naming scheme and interface, and futures did not seem to support prioritising execution.

Use is simple enough: create a Later instance and typically queue functions with the .defer() method::

  L = Later(4)      # a Later with a parallelism of 4
  ...
  LF = L.defer(func, *args, **kwargs)
  ...
  x = LF()          # collect result

The .defer method and its sublings return a LateFunction, which is a subclass of cs.asynchron.Asynchron. As such it is a callable, so to collect the result you just call the LateFunction.

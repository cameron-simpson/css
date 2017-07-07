X(): low level debug function.
==============================

X() is my function for low level ad hoc debug messages.
It takes a message and optional format arguments for use with `%`.
It is presented here in its own module for reuse.

It normally writes directly to `sys.stderr` but accepts an optional keyword argument `file` to specify a different filelike object.

Its behaviour may be tweaked with the globals `X_logger` or `X_via_tty`.
If `file` is not None, write to it unconditionally.
Otherwise, if X_logger then log a warning to that logger.
Otherwise, if X_via_tty then open /dev/tty and write the message to it.
Otherwise, write the message to sys.stderr.

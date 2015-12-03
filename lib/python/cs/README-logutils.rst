Logging convenience routines.
-----------------------------

The logging package is very useful, but a little painful to use. This package provides low impact logging setup and some extremely useful if unconventional context hooks for logging.

The logging verbosity output format has different defaults based on whether an output log file is a tty and whether the environment variable $DEBUG is set, and to what.

Some examples:
--------------

Program initialisation::

  from cs.logutils import setup_logging

  def main(argv):
    cmd = os.path.basename(argv.pop(0))
    setup_logging(cmd)

Basic logging from anywhere::

  from cs.logutils import info, warning, error
  [...]
  def some_function(...):
    [...]
    error("nastiness found! bad value=%r", bad_value)

Context for log messages and exception strings::

  from cs.logutils import Pfx
  [...]
  def func1(foo):
    [...]
    with Pfx("func1(%s)", foo.name):
      [...]
      warning("badness!")   # emits "func1(fooname): badness!"
  [...]
  def loadfile(filename):
    with Pfx(filename):
      lineno = 0
      for line in open(filename):
        lineno += 1
        with Pfx("%d", lineno):
          [...]
          bah = something from the line ...
          func1(bah)        # emits "filename: lineno: func1(fooname): badness!"
                            # if the warning triggers

This keeps log lines short and provides context in reported errors.

#!/usr/bin/python
#
# Convenience routines for logging.
#       - Cameron Simpson <cs@cskk.id.au> 29aug2009
#

r'''
Logging convenience routines.

The logging package is very useful, but a little painful to use.
This package provides low impact logging setup and some extremely
useful if unconventional context hooks for logging.

The default logging verbosity output format has different defaults
based on whether an output log file is a tty
and whether the environment variable `$DEBUG` is set, and to what.

On terminals warnings and errors get ANSI colouring.

A mode is available that uses `cs.upd` for certain log levels.

Log messages dispatched via `warning` and friends from this module
are automatically prefixed with the current `cs.pfx` prefix string,
providing automatic message context.

Some examples:
--------------

Program initialisation:

    from cs.logutils import setup_logging

    def main(argv):
        cmd = os.path.basename(argv.pop(0))
        setup_logging(cmd)

Basic logging from anywhere:

    from cs.logutils import info, warning, error
    [...]
    def some_function(...):
        [...]
        error("nastiness found! bad value=%r", bad_value)
'''

from __future__ import with_statement
import codecs
from contextlib import contextmanager
try:
  import importlib
except ImportError:
  importlib = None
import logging
from logging import Formatter, StreamHandler
import os
import os.path
from pprint import pformat
import stat
import sys
from threading import Lock
import time
import traceback
from types import SimpleNamespace as NS
from cs.ansi_colour import colourise, env_no_color
from cs.context import stackattrs
from cs.deco import fmtdoc, logging_wrapper
from cs.lex import is_dotted_identifier
import cs.pfx
from cs.pfx import Pfx, XP
from cs.py.func import funccite

__version__ = '20230212-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.ansi_colour>=20200729',
        'cs.context>=stackable_state',
        'cs.deco',
        'cs.lex',
        'cs.pfx',
        'cs.py.func',
        'cs.upd',
    ],
}

DEFAULT_BASE_FORMAT = '%(asctime)s %(levelname)s %(message)s'
DEFAULT_PFX_FORMAT = '%(asctime)s %(levelname)s %(pfx)s: %(message)s'
DEFAULT_PFX_FORMAT_TTY = '%(pfx)s: %(message)s'

# High level action tracking, above INFO and below WARNING.
TRACK = logging.INFO + 5

# Quiet messaging, below TRACK and above the rest.
QUIET = TRACK - 1

# Special status line tracking, above INFO and below TRACK and WARNING
STATUS = QUIET - 1

# Special verbose value, below INFO but above DEBUG.
VERBOSE = logging.INFO - 1

# check the hierarchy
assert (
    logging.DEBUG < VERBOSE < logging.INFO < STATUS < QUIET < TRACK <
    logging.WARNING
)

loginfo = None
D_mode = False

def ifdebug():
  ''' Test the `loginfo.level` against `logging.DEBUG`.
  '''
  global loginfo  # pylint: disable=global-statement
  if loginfo is None:
    loginfo = setup_logging()
  return loginfo.level <= logging.DEBUG

class LoggingState(NS):
  ''' A logging setup arranged for conventional UNIX command line use.
  '''

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  # pylint: disable=too-many-arguments,redefined-builtin
  def __init__(
      self,
      cmd=None,
      main_log=None,
      format=None,
      level=None,
      flags=None,
      upd_mode=None,
      ansi_mode=None,
      trace_mode=None,
      verbose=None,
      supplant_root_logger=False,
  ):
    ''' Prepare the `LoggingState` for conventional UNIX command
        line error messaging.

        Amongst other things, the default logger now includes
        the `cs.pfx` prefix in the message.

        This function runs in two modes:
        - if logging has not been set up, it sets up a root logger
        - if the root logger already has handlers,
          monkey patch the first handler's formatter to prefix the `cs.pfx` state

        Parameters:
        * `cmd`: program name, default from `basename(sys.argv[0])`.
          Side-effect: sets `cs.pfx.cmd` to this value.
        * `main_log`: default logging system.
          If `None`, the main log will go to `sys.stderr`;
          if `main_log` is a string, is it used as a filename to
          open in append mode;
          otherwise main_log should be a stream suitable
          for use with `logging.StreamHandler()`.
          The resulting log handler is added to the `logging` root logger.
        * `format`: the message format for `main_log`.
          If `None`, use `DEFAULT_PFX_FORMAT_TTY`
          when `main_log` is a tty or FIFO,
          otherwise `DEFAULT_PFX_FORMAT`.
        * `level`: `main_log` logging level.
          If `None`, infer a level from the environment
          using `infer_logging_level()`.
        * `flags`: a string containing debugging flags separated by commas.
          If `None`, infer the flags from the environment using
          `infer_logging_level()`.
          The following flags have meaning:
          `D`: set cs.logutils.D_mode to True;
          `TDUMP`: attach a signal handler to SIGHUP to do a thread stack dump;
          `TRACE`: enable various noisy tracing facilities;
          `UPD`, `NOUPD`: set the default for `upd_mode` to True or False respectively.
        * `upd_mode`: a Boolean to activate cs.upd as the `main_log` method;
          if `None`, set it to `True` if `flags` contains 'UPD',
          otherwise to `False` if `flags` contains 'NOUPD',
          otherwise set it from `main_log.isatty()`.
          A true value causes the root logger to use `cs.upd` for logging.
        * `ansi_mode`: if `None`,
          set it from `main_log.isatty() and not cs.colourise.env_no_color()`,
          which thus honours the `$NO_COLOR` environment variable
          (see https://no-color.org/ for the convention).
          A true value causes the root logger to colour certain logging levels
          using ANSI terminal sequences (currently only if `cs.upd` is used).
        * `trace_mode`: if `None`, set it according to the presence of
          'TRACE' in flags. Otherwise if `trace_mode` is true, set the
          global `loginfo.trace_level` to `loginfo.level`; otherwise it defaults
          to `logging.DEBUG`.
        * `verbose`: if `None`, then if stderr is a tty then the log
          level is `INFO` otherwise `WARNING`. Otherwise, if `verbose` is
          true then the log level is `INFO` otherwise `WARNING`.
    '''
    global D_mode, loginfo  # pylint: disable=global-statement

    # infer logging modes, these are the initial defaults
    inferred = infer_logging_level(verbose=verbose)
    if level is None:
      level = inferred.level
    if flags is None:
      flags = inferred.flags

    if cmd is None:
      cmd = os.path.basename(sys.argv[0])
    cs.pfx.cmd = cmd

    if main_log is None:
      main_log = sys.stderr
    elif isinstance(main_log, str):
      # pylint: disable=consider-using-with
      main_log = open(main_log, "a", encoding='utf-8')

    # determine some attributes of main_log
    try:
      fd = main_log.fileno()
    except (AttributeError, IOError):
      is_fifo = False
      ##is_reg = False                        # unused
      is_tty = False
    else:
      st = os.fstat(fd)
      is_fifo = stat.S_ISFIFO(st.st_mode)
      ##is_reg = stat.S_ISREG(st.st_mode)     # unused
      is_tty = stat.S_ISCHR(st.st_mode)

    if getattr(main_log, 'encoding', None) is None:
      main_log = codecs.getwriter("utf-8")(main_log)

    if trace_mode is None:
      trace_mode = 'TRACE' in flags

    if 'D' in flags:
      D_mode = True

    if upd_mode is None:
      if 'UPD' in flags:
        upd_mode = True
      elif 'NOUPD' in flags:
        upd_mode = False
      else:
        upd_mode = is_tty

    if ansi_mode is None:
      ansi_mode = is_tty and not env_no_color()

    if format is None:
      if is_tty or is_fifo:
        format = DEFAULT_PFX_FORMAT_TTY
      else:
        format = DEFAULT_PFX_FORMAT

    upd_ = None
    if upd_mode:
      from cs.upd import Upd  # pylint: disable=import-outside-toplevel
      upd_ = Upd()

    if trace_mode:
      # enable tracing in the thread that called setup_logging
      Pfx._state.trace = info
      trace_level = level
    else:
      trace_level = logging.DEBUG

    NS.__init__(
        self,
        main_log=main_log,
        level=level,
        verbose=verbose,
        trace_level=trace_level,
        flags=flags,
        cmd=cmd,
        upd=upd_,
        upd_mode=upd_mode,
        ansi_mode=ansi_mode,
        format=format,
        supplant_root_logger=supplant_root_logger,
    )

  def apply(self):
    ''' Apply this `LoggingState` to the current logging setup.
    '''
    global loginfo
    root_logger = logging.getLogger()
    if root_logger.handlers:
      # The logging system is already set up.
      # Just monkey patch the leading handler's formatter.
      PfxFormatter.patch_formatter(root_logger.handlers[0].formatter)
    else:
      # Set up a handler etc.
      main_handler = logging.StreamHandler(self.main_log)
      if self.upd_mode:
        main_handler = UpdHandler(
            self.main_log, ansi_mode=self.ansi_mode, over_handler=main_handler
        )
        self.upd = main_handler.upd
      root_logger.setLevel(self.level)
      if loginfo is None:
        # only do this the first time
        # TODO: fix this clumsy hack, some kind of stackable state?
        main_handler.setFormatter(PfxFormatter(format))
        if self.supplant_root_logger:
          root_logger.handlers.pop(0)
        root_logger.addHandler(main_handler)

    if 'TDUMP' in self.flags:
      # do a thread dump to the main_log on SIGHUP
      # pylint: disable=import-outside-toplevel
      import signal
      from cs.debug import thread_dump

      # pylint: disable=unused-argument
      def handler(sig, frame):
        thread_dump(None, self.main_log)

      signal.signal(signal.SIGHUP, handler)

def setup_logging(**kw):
  ''' Prepare a `LoggingState` and return it.
      It is also available as the global `cs.logutils.loginfo`.
  '''
  global loginfo
  loginfo = LoggingState(**kw)
  loginfo.apply()
  return loginfo

class PfxFormatter(Formatter):
  ''' A Formatter subclass that has access to the program's `cmd` and `Pfx` state.
  '''

  @fmtdoc
  def __init__(self, fmt=None, datefmt=None, cmd=None):
    ''' Initialise the `PfxFormatter`.

        Parameters:
        * `fmt`: format template,
          default from `DEFAULT_PFX_FORMAT` `{DEFAULT_PFX_FORMAT!r}`.
          Passed through to `Formatter.__init__`.
        * `datefmt`:
          Passed through to `Formatter.__init__`.
        * `cmd`: the "command prefix" made available to format strings.
          If not set, `cs.pfx.cmd` is presented.
    '''
    if fmt is None:
      fmt = DEFAULT_PFX_FORMAT
    self.cmd = cmd
    Formatter.__init__(self, fmt=fmt, datefmt=datefmt)

  def format(self, record):
    ''' Set `record.cmd` and `record.pfx`
        to the global `cmd` and `Pfx` context prefix respectively,
        then call `Formatter.format`.
    '''
    record.cmd = self.cmd if self.cmd else cs.pfx.cmd
    record.pfx = Pfx._state.prefix
    try:
      s = Formatter.format(self, record)
    except TypeError as e:
      XP(
          "cs.logutils: PfxFormatter.format: record=%r, self=%s: %s", record,
          self, e
      )
      raise
    record.message = s
    return s

  @staticmethod
  def patch_formatter(formatter):
    ''' Monkey patch an existing `Formatter` instance
        with a `format` method which prepends the current `Pfx` prefix.
    '''
    if isinstance(formatter, PfxFormatter):
      return
    try:
      getattr(formatter, 'PfxFormatter__monkey_patched')
    except AttributeError:
      old_format = formatter.format

      def new_format(record):
        ''' Call the former `formatter.format` method
            and prepend the current `Pfx` prefix to the start.
        '''
        ##msg0 = record.msg
        ##args0 = record.args
        cur_pfx = Pfx._state.prefix
        if not cur_pfx:
          return old_format(record)
        if not isinstance(record.args, tuple):
          # TODO: dict support
          return old_format(record)
        if record.args:
          new_msg = '%s' + str(record.msg)
          new_args = (cur_pfx + cs.pfx.DEFAULT_SEPARATOR,) + tuple(record.args)
        else:
          new_msg = cur_pfx + cs.pfx.DEFAULT_SEPARATOR + str(record.msg)
          new_args = record.args
        try:
          with stackattrs(record, msg=new_msg, args=new_args):
            return old_format(record)
        except Exception as e:  # pylint: disable=broad-except
          # unsupported in some way, fall back to the original
          # and lose the prefix
          return old_format(record)

      formatter.format = new_format
      formatter.PfxFormatter__monkey_patched = True

# pylint: disable=too-many-branches,too-many-statements,redefined-outer-name
def infer_logging_level(env_debug=None, environ=None, verbose=None):
  ''' Infer a logging level from the `env_debug`, which by default
      comes from the environment variable `$DEBUG`.

      Usually default to `logging.WARNING`, but if `sys.stderr` is a terminal,
      default to `logging.INFO`.

      Parse the environment variable `$DEBUG` as a comma separated
      list of flags.

      Examine the in sequence flags to affect the logging level:
      * numeric < 1: `logging.WARNING`
      * numeric >= 1 and < 2: `logging.INFO`
      * numeric >= 2: `logging.DEBUG`
      * `"DEBUG"`: `logging.DEBUG`
      * `"STATUS"`: `STATUS`
      * `"INFO"`: `logging.INFO`
      * `"TRACK"`: `TRACK`
      * `"WARNING"`: `logging.WARNING`
      * `"ERROR"`: `logging.ERROR`

      Return an object with the following attributes:
      * `.level`: A logging level.
      * `.flags`: All the words from `$DEBUG` as separated by commas and uppercased.
  '''
  if env_debug is None:
    if environ is None:
      environ = os.environ
    env_debug = environ.get('DEBUG', '')
  if verbose is None:
    if sys.stderr.isatty():
      level = TRACK
    else:
      level = logging.WARNING
  elif verbose:
    level = logging.VERBOSE
  else:
    level = logging.WARNING
  flags = []
  for flag in env_debug.split(','):
    flag = flag.strip()
    if not flag:
      continue
    if flag.isdigit():
      flag_level = int(flag)
      if flag_level < 1:
        level = logging.WARNING
      elif flag_level >= 2:
        level = logging.DEBUG
      else:
        level = logging.INFO
    elif flag[0].islower() and is_dotted_identifier(flag):
      # modulename - now honoured by cs.debug, not this
      pass
    elif ':' in flag:
      # module:funcname - now honoured by cs.debug, not this
      pass
    else:
      uc_flag = flag.upper()
      flags.append(uc_flag)
      if uc_flag == 'DEBUG':
        level = logging.DEBUG
      elif uc_flag == 'STATUS':
        level = STATUS
      elif uc_flag == 'INFO':
        level = logging.INFO
      elif uc_flag == 'TRACK':
        level = TRACK
      # pylint: disable=consider-using-in
      elif uc_flag == 'WARN' or uc_flag == 'WARNING':
        level = logging.WARNING
      elif uc_flag == 'ERROR':
        level = logging.ERROR
  return NS(
      level=level,
      flags=flags,
  )

def D(msg, *args):
  ''' Print formatted debug string straight to `sys.stderr` if
      `D_mode` is true, bypassing the logging modules entirely.
      A quick'n'dirty debug tool.
  '''
  # pylint: disable=global-statement
  global D_mode
  if D_mode:
    XP(msg, *args)

# pylint: disable=too-many-arguments,redefined-builtin
def add_logfile(
    filename,
    logger=None,
    mode='a',
    encoding=None,
    delay=False,
    format=None,
    no_prefix=False
):
  ''' Add a `FileHandler` logging to the specified `filename`;
      return the chosen logger and the new handler.

      Parameters:
      * `logger`: if supplied and not `None`, add the `FileHandler` to that
        `Logger`, otherwise to the root Logger. If `logger` is a string, call
        `logging.getLogger(logger)` to obtain the logger.
      * `mode`, `encoding` and `delay`: passed to the `FileHandler`
        initialiser.
      * `format`: used to override the handler's default format.
      * `no_prefix`: if true, do not put the `Pfx` context onto the front of the message.
  '''
  if logger is None:
    logger = logging.getLogger()
  elif isinstance(logger, str):
    logger = logging.getLogger(logger)
  handler = logging.FileHandler(filename, mode, encoding, delay)
  if no_prefix:
    if format is None:
      format = DEFAULT_BASE_FORMAT
    formatter = Formatter(format)
  else:
    formatter = PfxFormatter(format)
  handler.setFormatter(formatter)
  logger.addHandler(handler)
  return logger, handler

logTo = add_logfile

@contextmanager
def with_log(filename, **kw):
  ''' Context manager to add a `Logger` to the output logs temporarily.
  '''
  logger, handler = add_logfile(filename, **kw)
  try:
    yield logger, handler
  finally:
    logger.removeHandler(handler)

class NullHandler(logging.Handler):
  ''' A `Handler` which discards its requests.
  '''

  def emit(self, record):
    ''' Discard the log record.
    '''

__logExLock = Lock()

def logException(exc_type, exc_value, exc_tb):
  ''' Replacement for `sys.excepthook` that reports via the `cs.logutils`
      logging wrappers.
  '''
  with __logExLock:
    curhook = sys.excepthook
    sys.excepthook = sys.__excepthook__
    exception("EXCEPTION: %s:%s" % (exc_type, exc_value))
    for line in traceback.format_exception(exc_type, exc_value, exc_tb):
      exception("EXCEPTION> " + line)
    sys.excepthook = curhook

# Logger public functions
def exception(msg, *args, **kwargs):
  ''' Emit an exception log with the current `Pfx` prefix.
  '''
  Pfx._state.cur.exception(msg, *args, **kwargs)

@logging_wrapper
def log(level, msg, *args, **kwargs):
  ''' Emit a log at the specified level with the current `Pfx` prefix.
  '''
  Pfx._state.cur.log(level, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def debug(msg, *args, **kwargs):
  ''' Emit a log at `logging.DEBUG` level with the current `Pfx` prefix.
  '''
  log(logging.DEBUG, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def info(msg, *args, **kwargs):
  ''' Emit a log at `logging.INFO` level with the current `Pfx` prefix.
  '''
  log(logging.INFO, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def status(msg, *args, **kwargs):
  ''' Emit a log at `STATUS` level with the current `Pfx` prefix.
  '''
  log(STATUS, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def track(msg, *args, **kwargs):
  ''' Emit a log at `TRACK` level with the current `Pfx` prefix.
  '''
  log(TRACK, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def quiet(msg, *args, **kwargs):
  ''' Emit a log at `QUIET` level with the current `Pfx` prefix.
  '''
  log(QUIET, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def verbose(msg, *args, **kwargs):
  ''' Emit a log at `VERBOSE` level with the current `Pfx` prefix.
  '''
  log(VERBOSE, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def ifverbose(is_verbose, msg, *args, **kwargs):
  ''' Conditionally log a message.

      If `is_verbose` is `None`, log at `VERBOSE` level and rely on the logging setup.
      Otherwise, if `is_verbose` is true, log at `INFO` level.
  '''
  if is_verbose is None:
    # emit at VERBOSE level, use the logging handler levels to emit or not
    verbose(msg, *args, **kwargs)
  elif is_verbose:
    # emit at INFO level
    info(msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def warning(msg, *args, **kwargs):
  ''' Emit a log at `logging.WARNING` level with the current `Pfx` prefix.
  '''
  log(logging.WARNING, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def error(msg, *args, **kwargs):
  ''' Emit a log at `logging.ERROR` level with the current `Pfx` prefix.
  '''
  log(logging.ERROR, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def critical(msg, *args, **kwargs):
  ''' Emit a log at `logging.CRITICAL` level with the current `Pfx` prefix.
  '''
  log(logging.CRITICAL, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def trace(msg, *args, **kwargs):
  ''' Emit a log message at `loginfo.trace_level` with the current `Pfx` prefix.
  '''
  log(loginfo.trace_level, msg, *args, **kwargs)

@logging_wrapper(stacklevel_increment=1)
def upd(msg, *args, **kwargs):
  ''' If we're using an `UpdHandler`,
      update the status line otherwise write an info message.

      Note that this calls `Upd.out` directly with `msg%args`
      and thus does not include the current `Pfx` prefix.
      You may well want to use the `status()` function instead.
  '''
  _upd = loginfo.upd
  if _upd:
    _upd.out(msg, *args)
  else:
    info(msg, *args, **kwargs)

# pylint: disable=too-many-instance-attributes
class LogTime(object):
  ''' LogTime is a context manager that logs the elapsed time of the enclosed
      code. After the run, the field .elapsed contains the elapsed time in
      seconds.
  '''

  def __init__(self, tag, *args, **kwargs):
    ''' Set up a LogTime.

        Parameters:
        * `tag`: label included at the start of the log entry
        * `args`: optional array; if not empty `args` is applied to
          `tag` with `%`
        * `level`: keyword argument specifying a log level for a
          default log entry, default `logging.INFO`
        * `threshold`: keyword argument specifying minimum time to
          cause a log, default None (no minimum)
        * `warning_level`: keyword argument specifying the log level
          for a warning log entry, default `logging.WARNING`
        * `warning_threshold`: keyword argument specifying a time
          which raises the log level to `warning_level`
    '''
    threshold = kwargs.pop('threshold', 1.0)
    level = kwargs.pop('level', logging.INFO)
    warning_threshold = kwargs.pop('warning_threshold', None)
    warning_level = kwargs.pop('warning_level', logging.WARNING)
    self.tag = tag
    self.tag_args = args
    self.threshold = threshold
    self.level = level
    self.warning_threshold = warning_threshold
    self.warning_level = warning_level
    self.start = None
    self.end = None
    self.elapsed = None

  def __enter__(self):
    self.start = time.time()
    return self

  def __exit__(self, *_):
    now = self.end = time.time()
    elapsed = self.elapsed = now - self.start
    if self.threshold is not None and elapsed >= self.threshold:
      level = self.level
      if self.warning_threshold is not None and elapsed >= self.warning_threshold:
        level = self.warning_level
      tag = self.tag
      if self.tag_args:
        tag = tag % self.tag_args
      log(level, "%s: ELAPSED %5.3fs" % (tag, elapsed))
    return False

class UpdHandler(StreamHandler):
  ''' A `StreamHandler` subclass whose `.emit` method
      uses a `cs.upd.Upd` for transcription.
  '''

  def __init__(
      self, strm=None, upd_level=None, ansi_mode=None, over_handler=None
  ):
    ''' Initialise the `UpdHandler`.

        Parameters:
        * `strm`: the output stream, default `sys.stderr`.
        * `upd_level`: the magic logging level which updates the status line
          via `Upd`. Default: `STATUS`.
        * `ansi_mode`: if `None`, set from `strm.isatty()`.
          A true value causes the handler to colour certain logging levels
          using ANSI terminal sequences.
    '''
    from cs.upd import Upd  # pylint: disable=import-outside-toplevel
    if strm is None:
      strm = sys.stderr
    if upd_level is None:
      upd_level = STATUS
    if ansi_mode is None:
      ansi_mode = strm.isatty()
    StreamHandler.__init__(self, strm)
    self.upd = Upd(strm)
    self.upd_level = upd_level
    self.ansi_mode = ansi_mode
    self.over_handler = over_handler
    self.__lock = Lock()

  def emit(self, logrec):
    ''' Emit a `LogRecord` `logrec`.

        For the log level `self.upd_level` update the status line.
        For other levels write a distinct line
        to the output stream, possibly colourised.
    '''
    upd = self.upd
    if logrec.levelno == self.upd_level:
      line = self.format(logrec)
      with self.__lock:
        upd.out(line)
    else:
      if self.ansi_mode:
        if logrec.levelno >= logging.ERROR:
          logrec.msg = colourise(logrec.msg, 'red')
        elif logrec.levelno >= logging.WARNING:
          logrec.msg = colourise(logrec.msg, 'yellow')
      line = self.format(logrec)
      with self.__lock:
        if upd.disabled:
          self.over_handler.emit(logrec)
        else:
          upd.nl(line)

  def flush(self):
    ''' Flush the update status.
    '''
    return self.upd.flush()

if __name__ == '__main__':

  @logging_wrapper
  def test_warning(msg, *a, **kw):
    'test function for warning'
    warning(msg, *a, **kw)

  setup_logging(
      sys.argv[0],
      format='%(pfx)s: from %(funcName)s:%(filename)s:%(lineno)d: %(message)s'
  )
  test_warning("test warning")

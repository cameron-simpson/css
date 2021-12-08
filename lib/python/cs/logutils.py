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
from cs.deco import fmtdoc, logging_wrapper
from cs.lex import is_dotted_identifier
import cs.pfx
from cs.pfx import Pfx, XP
from cs.py.func import funccite
from cs.upd import Upd

__version__ = '20210721-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.ansi_colour>=20200729', 'cs.deco', 'cs.lex', 'cs.pfx',
        'cs.py.func', 'cs.upd'
    ],
}

DEFAULT_BASE_FORMAT = '%(asctime)s %(levelname)s %(message)s'
DEFAULT_PFX_FORMAT = '%(asctime)s %(levelname)s %(pfx)s: %(message)s'
DEFAULT_PFX_FORMAT_TTY = '%(pfx)s: %(message)s'

# High level action tracking, above INFO and below WARNING.
TRACK = logging.INFO + 5

# Special status line tracking, above INFO and below TRACK and WARNING
STATUS = TRACK - 1

# Special verbose value, below INFO but above DEBUG.
VERBOSE = logging.INFO - 1

# check the hierarchy
assert logging.DEBUG < VERBOSE < logging.INFO < STATUS < TRACK < logging.WARNING

loginfo = None
D_mode = False

def ifdebug():
  ''' Test the `loginfo.level` against `logging.DEBUG`.
  '''
  global loginfo  # pylint: disable=global-statement
  if loginfo is None:
    loginfo = setup_logging()
  return loginfo.level <= logging.DEBUG

# pylint: disable=too-many-branches,too-many-statements,too-many-locals
# pylint: disable=too-many-arguments,redefined-builtin
def setup_logging(
    cmd_name=None,
    main_log=None,
    format=None,
    level=None,
    flags=None,
    upd_mode=None,
    ansi_mode=None,
    trace_mode=None,
    module_names=None,
    function_names=None,
    verbose=None,
    supplant_root_logger=False,
):
  ''' Arrange basic logging setup for conventional UNIX command
      line error messaging; return an object with informative attributes.
      That object is also available as the global `cs.logutils.loginfo`.

      Parameters:
      * `cmd_name`: program name, default from `basename(sys.argv[0])`.
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
  if module_names is None:
    module_names = inferred.module_names
  if function_names is None:
    function_names = inferred.function_names

  if cmd_name is None:
    cmd_name = os.path.basename(sys.argv[0])
  cs.pfx.cmd = cmd_name

  if main_log is None:
    main_log = sys.stderr
  elif isinstance(main_log, str):
    main_log = open(main_log, "a")

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

  if 'TDUMP' in flags:
    # do a thread dump to the main_log on SIGHUP
    # pylint: disable=import-outside-toplevel
    import signal
    import cs.debug as cs_debug

    # pylint: disable=unused-argument
    def handler(sig, frame):
      cs_debug.thread_dump(None, main_log)

    signal.signal(signal.SIGHUP, handler)

  main_handler = logging.StreamHandler(main_log)
  upd_ = Upd()
  if upd_mode:
    main_handler = UpdHandler(
        main_log, ansi_mode=ansi_mode, over_handler=main_handler
    )
    upd_ = main_handler.upd

  root_logger = logging.getLogger()
  root_logger.setLevel(level)
  if loginfo is None:
    # only do this the first time
    # TODO: fix this clumsy hack, some kind of stackable state?
    main_handler.setFormatter(PfxFormatter(format))
    if supplant_root_logger:
      root_logger.handlers.pop(0)
    root_logger.addHandler(main_handler)

  if trace_mode:
    # enable tracing in the thread that called setup_logging
    Pfx._state.trace = info
    trace_level = level
  else:
    trace_level = logging.DEBUG

  if module_names or function_names:
    if importlib is None:
      warning(
          "setup_logging: no importlib (python<2.7?),"
          " ignoring module_names=%r/function_names=%r", module_names,
          function_names
      )
    else:
      for module_name in module_names:
        try:
          M = importlib.import_module(module_name)
        except ImportError:
          warning("setup_logging: cannot import %r", module_name)
        else:
          M.DEBUG = True
      for module_name, func_name in function_names:
        try:
          M = importlib.import_module(module_name)
        except ImportError:
          warning("setup_logging: cannot import %r", module_name)
          continue
        F = M
        for funcpart in func_name.split('.'):
          M = F
          try:
            F = M.getattr(funcpart)
          except AttributeError:
            F = None
            break
        if F is None:
          warning("no %s.%s() found", module_name, func_name)
        else:
          setattr(M, funcpart, _ftrace(F))

  loginfo = NS(
      logger=root_logger,
      level=level,
      verbose=verbose,
      trace_level=trace_level,
      flags=flags,
      module_names=module_names,
      function_names=function_names,
      cmd=cmd_name,
      upd=upd_,
      upd_mode=upd_mode,
      ansi_mode=ansi_mode,
      format=format,
  )

  return loginfo

def ftrace(func):
  ''' Decorator to trace a function if `__module__.DEBUG` is true.
  '''
  M = func.__module__

  def func_wrap(*a, **kw):
    do_debug = M.__dict__.get('DEBUG', False)
    wrapper = _ftrace(func) if do_debug else func
    return wrapper(*a, **kw)

  return func_wrap

def _ftrace(func):
  ''' Decorator to trace the call and return of a function.
  '''
  fname = '.'.join((func.__module__, funccite(func)))

  def traced_func(*a, **kw):
    citation = "%s(*%s, **%s)" % (
        fname, pformat(a, depth=1), pformat(kw, depth=2)
    )
    XP("CALL %s", citation)
    try:
      result = func(*a, **kw)
    except Exception as e:
      XP("EXCEPTION from %s: %s %s", citation, type(e), e)
      raise
    else:
      XP("RESULT from %s: %r", citation, result)
      return result

  return traced_func

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
      * `.module_names`: Module names to be debugged.
      * `.function_names`: Functions to be traced in the form *module_name*`.`*func_name*.
  '''
  if env_debug is None:
    if environ is None:
      environ = os.environ
    env_debug = os.environ.get('DEBUG', '')
  level = TRACK
  if verbose is None:
    if not sys.stderr.isatty():
      level = logging.WARNING
  elif verbose:
    level = logging.VERBOSE
  else:
    level = logging.WARNING
  flags = [F.upper() for F in env_debug.split(',') if len(F)]
  module_names = []
  function_names = []
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
      # modulename
      module_names.append(flag)
    elif ':' in flag:
      # module:funcname
      module_name, func_name = flag.split(':', 1)
      if is_dotted_identifier(module_name) and is_dotted_identifier(func_name):
        function_names.append((module_name, func_name))
    else:
      uc_flag = flag.upper()
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
      module_names=module_names,
      function_names=function_names
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

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
and whether the environment variable $DEBUG is set, and to what.

On terminals warnings and errors get ANSI colouring.

A mode is available that uses cs.upd.

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
import time
from threading import Lock
import traceback
from cs.ansi_colour import colourise
from cs.lex import is_dotted_identifier
from cs.obj import O
from cs.pfx import Pfx, XP
from cs.py.func import funccite
from cs.upd import upd_for

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.ansi_colour',
        'cs.lex',
        'cs.obj',
        'cs.pfx',
        'cs.py.func',
        'cs.upd'
    ],
}

DEFAULT_BASE_FORMAT = '%(asctime)s %(levelname)s %(message)s'
DEFAULT_PFX_FORMAT = '%(asctime)s %(levelname)s %(pfx)s: %(message)s'
DEFAULT_PFX_FORMAT_TTY = '%(pfx)s: %(message)s'

loginfo = O(upd_mode=None)
logging_level = logging.INFO
trace_level = logging.DEBUG
D_mode = False

def ifdebug():
  ''' Test the `logging_level` against `logging.DEBUG`.
  '''
  global logging_level
  return logging_level <= logging.DEBUG

def setup_logging(
    cmd_name=None,
    main_log=None, format=None, level=None,
    flags=None, upd_mode=None, ansi_mode=None, trace_mode=None,
    module_names=None, function_names=None, verbose=None
):
  ''' Arrange basic logging setup for conventional UNIX command
      line error messaging; return an object with informative attributes.

      Parameters:
      * `cmd_name`: program name, default from `basename(sys.argv[0])`.
        Side-effect: sets `cs.pfx.cmd` to this value.
      * `main_log`: default logging system.
        If None, the main log will go to sys.stderr;
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
        If None, infer a level from the environment
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
        otherwise set it to `False` (was from main_log.isatty()).
        A true value causes the root logger to use `cs.upd` for logging.
      * `ansi_mode`: if `None`, set it from `main_log.isatty()`.
        A true value causes the root logger to colour certain logging levels
        using ANSI terminal sequences (currently only if `cs.upd` is used).
      * `trace_mode`: if `None`, set it according to the presence of
        'TRACE' in flags. Otherwise if `trace_mode` is true, set the
        global `trace_level` to `logging_level`; otherwise it defaults
        to `logging.DEBUG`.
      * `verbose`: if `None`, then if stderr is a tty then the log
        level is `INFO` otherwise `WARNING`. Otherwise, if `verbose` is
        true then the log level is `INFO` otherwise `WARNING`.
  '''
  global logging_level, trace_level, D_mode, loginfo
  import cs.pfx

  # infer logging modes, these are the initial defaults
  inferred = infer_logging_level(verbose=verbose)
  if level is None:
    level = inferred.level
  loginfo.level = level
  if flags is None:
    flags = inferred.flags
  loginfo.flags = flags
  if module_names is None:
    module_names = inferred.module_names
  loginfo.module_names = module_names
  if function_names is None:
    function_names = inferred.function_names
  loginfo.function_names = function_names

  if cmd_name is None:
    cmd_name = os.path.basename(sys.argv[0])
  cs.pfx.cmd = cmd_name
  loginfo.cmd = cmd_name

  if main_log is None:
    main_log = sys.stderr
  elif isinstance(main_log, str):
    main_log = open(main_log, "a")
  loginfo.main_log_file = main_log

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

  if main_log.encoding is None:
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
      ##upd_mode = is_tty
      upd_mode = False
  loginfo.upd_mode = upd_mode

  if ansi_mode is None:
    ansi_mode = is_tty
  loginfo.ansi_mode = ansi_mode

  if format is None:
    if is_tty or is_fifo:
      format = DEFAULT_PFX_FORMAT_TTY
    else:
      format = DEFAULT_PFX_FORMAT
  loginfo.format = format

  if 'TDUMP' in flags:
    # do a thread dump to the main_log on SIGHUP
    import signal
    import cs.debug
    def handler(sig, frame):
      cs.debug.thread_dump(None, main_log)
    signal.signal(signal.SIGHUP, handler)

  if upd_mode:
    main_handler = UpdHandler(main_log, logging_level, ansi_mode=ansi_mode)
    loginfo.upd = main_handler.upd
  else:
    loginfo.upd = None
    main_handler = logging.StreamHandler(main_log)

  root_logger = logging.getLogger()
  root_logger.setLevel(level)
  main_handler.setFormatter(PfxFormatter(format))
  root_logger.addHandler(main_handler)

  logging_level = level
  if trace_mode:
    # enable tracing in the thread that called setup_logging
    Pfx._state.trace = info
    trace_level = logging_level

  if module_names or function_names:
    if importlib is None:
      warning(
          "setup_logging: no importlib (python<2.7?),"
          " ignoring module_names=%r/function_names=%r",
          module_names, function_names)
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

  return loginfo

def ftrace(func):
  ''' Decorator to trace a function if __module__.DEBUG is true.
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
  fname = '.'.join( (func.__module__, funccite(func)) )
  def traced_func(*a, **kw):
    citation = "%s(*%s, **%s)" % (fname, pformat(a, depth=1), pformat(kw, depth=2))
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
  ''' A Formatter subclass that has access to the program's cmd and Pfx state.
  '''

  def __init__(self, fmt=None, datefmt=None, cmd=None):
    ''' Initialise the PfxFormatter.

        `fmt` and `datefmt` are passed to Formatter.
        If `fmt` is None, DEFAULT_PFX_FORMAT is used.
        If `cmd` is not None, the message is prefixed with the string `cmd`.
    '''
    self.cmd = cmd
    Formatter.__init__(self, fmt=fmt, datefmt=datefmt)

  def format(self, record):
    ''' Set .cmd and .pfx to the global cmd and Pfx context prefix
        respectively, then call Formatter.format.
    '''
    import cs.pfx
    record.cmd = self.cmd if self.cmd else cs.pfx.cmd
    record.pfx = Pfx._state.prefix
    try:
      s = Formatter.format(self, record)
    except TypeError as e:
      XP("cs.logutils: PfxFormatter.format: record=%r, self=%s: %s", record, self, e)
      raise
    record.message = s
    return s

def infer_logging_level(env_debug=None, environ=None, verbose=None):
  ''' Infer a logging level from the `env_debug`, which by default
      comes from the environment variable `$DEBUG`.

      Usually default to logging.WARNING, but if sys.stderr is a terminal,
      default to logging.INFO.

      Parse the environment variable $DEBUG as a comma separated
      list of flags.

      Examine the in sequence flags to affect the logging level:
      * `numeric < 1`: `logging.WARNING`
      * `numeric >= 1 and < 2`: `logging.INFO`
      * `numeric >= 2`: `logging.DEBUG`
      * `"DEBUG"`: `logging.DEBUG`
      * `"INFO"`: ` logging.INFO`
      * `"WARNING"`: `logging.WARNING`
      * `"ERROR"`: `logging.ERROR`

      Return an object with the following attributes:
      * `.level`: A logging level.
      * `.flags`: All the words from $DEBUG as separated by commas and uppercased.
      * `.module_names`: Module names to be debugged.
      * `.function_names`: Functions to be traced in the for "module_name.func_name()".
  '''
  if env_debug is None:
    if environ is None:
      environ = os.environ
    env_debug = os.environ.get('DEBUG', '')
  level = logging.WARNING
  if verbose is None:
    if sys.stderr.isatty():
      level = logging.INFO
  elif verbose:
    level = logging.INFO
  else:
    level = logging.WARNING
  flags = [ F.upper() for F in env_debug.split(',') if len(F) ]
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
        function_names.append( (module_name, func_name) )
    else:
      uc_flag = flag.upper()
      if uc_flag == 'DEBUG':
        level = logging.DEBUG
      elif uc_flag == 'INFO':
        level = logging.INFO
      elif uc_flag == 'WARN' or uc_flag == 'WARNING':
        level = logging.WARNING
      elif uc_flag == 'ERROR':
        level = logging.ERROR
  return O(level=level, flags=flags, module_names=module_names, function_names=function_names)

def D(msg, *args):
  ''' Print formatted debug string straight to sys.stderr if D_mode is true,
      bypassing the logging modules entirely.
      A quick'n'dirty debug tool.
  '''
  global D_mode
  if D_mode:
    XP(msg, *args)

def status(msg, *args, **kwargs):
  ''' Write a message to the terminal's status line.

      Parameters:
      * `msg`: message string
      * `args`: if not empty, the message is %-formatted with `args`
      * `file`: optional keyword argument specifying the output file.
        Default: `sys.stderr`.

      Hack: if there is no status line use the xterm title bar sequence :-(
  '''
  if args:
    msg = msg % args
  f = kwargs.pop('file', None)
  if kwargs:
    raise ValueError("unexpected keyword arguments: %r" % (kwargs,))
  if f is None:
    f = sys.stderr
  try:
    has_ansi_status = f.has_ansi_status
  except AttributeError:
    try:
      import curses
    except ImportError:
      has_ansi_status = None
    else:
      curses.setupterm()
      has_status = curses.tigetflag('hs')
      if has_status == -1:
        warning(
            'status: curses.tigetflag(hs): not a Boolean capability, presuming false')
        has_ansi_status = None
      elif has_status > 0:
        has_ansi_status = (
            curses.tigetstr('to_status_line'),
            curses.tigetstr('from_status_line')
        )
      else:
        warning('status: hs=%s, presuming false', has_status)
        has_ansi_status = None
    f.has_ansi_status = has_ansi_status
  if has_ansi_status:
    msg = has_ansi_status[0] + msg + has_ansi_status[1]
  else:
    msg = '\033]0;' + msg + '\007'
  f.write(msg)
  f.flush()

def add_logfile(filename, logger=None, mode='a',
                encoding=None, delay=False, format=None, no_prefix=False):
  ''' Add a FileHandler logging to the specified `filename`;
      return the chosen logger and the new handler.

      Parameters:
      * `logger`: if supplied and not None, add the FileHandler to that
        Logger, otherwise to the root Logger. If `logger` is a string, call
        `logging.getLogger(logger)` to obtain the logger.
      * `mode`, `encoding` and `delay`: passed to the logging.FileHandler
        initialiser.
      * `format`: used to override the handler's default format.
      * `no_prefix`: if true, do not put the Pfx context onto the front of the message.
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
  ''' Context manager to add a Logger to the output logs temporarily.
  '''
  logger, handler = add_logfile(filename, **kw)
  try:
    yield logger, handler
  finally:
    logger.removeHandler(handler)

class NullHandler(logging.Handler):
  ''' A Handler which discards its requests.
  '''
  def emit(self, record):
    ''' Discard the log record.
    '''
    pass

__logExLock = Lock()
def logException(exc_type, exc_value, exc_tb):
  ''' Replacement for sys.excepthook that reports via the cs.logutils
      logging wrappers.
  '''
  with __logExLock:
    curhook = sys.excepthook
    sys.excepthook = sys.__excepthook__
    exception("EXCEPTION: %s:%s" % (exc_type, exc_value))
    for line in traceback.format_exception(exc_type, exc_value, exc_tb):
      exception("EXCEPTION> " + line)
    sys.excepthook = curhook

def OBSOLETE(func):
  ''' Decorator for obsolete functions.

      Use:

          @OBSOLETE
          def f(...):

      This emits a warning log message before calling the decorated function.
  '''
  def wrapped(*args, **kwargs):
    ''' Wrap `func` to emit an "OBSOLETE" warning before calling `func`.
    '''
    frame = traceback.extract_stack(None, 2)[0]
    warning(
        "OBSOLETE call to %s:%d %s(), called from %s:%d %s",
        func.__code__.co_filename, func.__code__.co_firstlineno,
        func.__name__, frame[0], frame[1], frame[2]
    )
    return func(*args, **kwargs)
  return wrapped

# Logger public functions
def exception(msg, *args):
  ''' Emit an exception log with the current Pfx prefix.
  '''
  Pfx._state.cur.exception(msg, *args)
def log(level, msg, *args, **kwargs):
  ''' Emit a log at the specified `level` with the current Pfx prefix.
  '''
  Pfx._state.cur.log(level, msg, *args, **kwargs)
def debug(msg, *args, **kwargs):
  ''' Emit a log at `logging.DEBUG` `level` with the current Pfx prefix.
  '''
  log(logging.DEBUG, msg, *args, **kwargs)
def info(msg, *args, **kwargs):
  ''' Emit a log at `logging.INFO` `level` with the current Pfx prefix.
  '''
  log(logging.INFO, msg, *args, **kwargs)
def warning(msg, *args, **kwargs):
  ''' Emit a log at `logging.WARNING` `level` with the current Pfx prefix.
  '''
  log(logging.WARNING, msg, *args, **kwargs)
def error(msg, *args, **kwargs):
  ''' Emit a log at `logging.ERROR` `level` with the current Pfx prefix.
  '''
  log(logging.ERROR, msg, *args, **kwargs)
def critical(msg, *args, **kwargs):
  ''' Emit a log at `logging.CRITICAL` `level` with the current Pfx prefix.
  '''
  log(logging.CRITICAL, msg, *args, **kwargs)
def trace(msg, *args, **kwargs):
  ''' Emit a log message at `trace_level` with the current Pfx prefix.
  '''
  log(trace_level, msg, *args, **kwargs)

def upd(msg, *args):
  ''' If we're using an UpdHandler,
      update the status line otherwise write an info message.
  '''
  _upd = loginfo.upd
  if _upd:
    _upd.out(msg, *args)
  else:
    info(msg, *args)

class LogTime(object):
  ''' LogTime is a content manager that logs the elapsed time of the enclosed
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
  def __enter__(self):
    self.start = time.time()
    return self
  def __exit__(self, exc_type, exc_value, traceback):
    now = time.time()
    elapsed = now - self.start
    if self.threshold is not None and elapsed >= self.threshold:
      level = self.level
      if self.warning_threshold is not None and elapsed >= self.warning_threshold:
        level = self.warning_level
      tag = self.tag
      if self.tag_args:
        tag = tag % self.tag_args
      log(level, "%s: ELAPSED %5.3fs" % (tag, elapsed))
    self.elapsed = elapsed
    return False

class UpdHandler(StreamHandler):
  ''' A `StreamHandler` subclass whose `.emit` method
      uses a `cs.upd.Upd` for transcription.
  '''

  def __init__(self, strm=None, nl_level=None, ansi_mode=None):
    ''' Initialise the UpdHandler.

        Parameters:
        * `strm`: the output stream, default `sys.stderr`.
        * `nl_level`: the logging level at which conventional line-of-text
          output is written; log messages of a lower level go via the
          update-the-current-line method.
          Default: `logging.WARNING`.
        * `ansi_mode`: if `None`, set from `strm.isatty()`.
          A true value causes the handler to colour certain logging levels
          using ANSI terminal sequences.
    '''
    if strm is None:
      strm = sys.stderr
    if nl_level is None:
      nl_level = logging.INFO
    if ansi_mode is None:
      ansi_mode = strm.isatty()
    StreamHandler.__init__(self, strm)
    self.upd = upd_for(strm)
    self.nl_level = nl_level
    self.__ansi_mode = ansi_mode
    self.__lock = Lock()

  def emit(self, logrec):
    ''' Emit a LogRecord `logrec`.

        For log levels at or above `self.nl_level` write a distinct line
        to the output stream, possible colourised.

        For log levels below `self.nl_level` update the status text.
    '''
    if logrec.levelno >= self.nl_level:
      if self.__ansi_mode:
        if logrec.levelno >= logging.ERROR:
          logrec.msg = colourise(logrec.msg, 'red')
        elif logrec.levelno >= logging.WARN:
          logrec.msg = colourise(logrec.msg, 'yellow')
      with self.__lock:
        self.upd.without(StreamHandler.emit, self, logrec)
    else:
      with self.__lock:
        self.upd.out(logrec.getMessage())

  def upd(self, msg, *a):
    ''' Update the status text.
    '''
    if a:
      msg = msg % a
    with self.__lock:
      self.upd.out(msg)

  def flush(self):
    ''' Flush the update status.
    '''
    return self.upd.flush()

if __name__ == '__main__':
  setup_logging(sys.argv[0])

#!/usr/bin/python
#
# Convenience routines for logging.
#       - Cameron Simpson <cs@zip.com.au> 29aug2009
#

from __future__ import with_statement

DISTINFO = {
    'description': "Logging convenience routines.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': ['cs.ansi_colour', 'cs.lex', 'cs.obj', 'cs.py.func', 'cs.py3', 'cs.upd'],
}

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
import threading
from threading import Lock
import traceback
from cs.ansi_colour import colourise
from cs.lex import is_dotted_identifier
from cs.obj import O, O_str
from cs.py.func import funccite
from cs.py3 import unicode, StringTypes, ustr
from cs.upd import Upd

cmd = __file__

DEFAULT_BASE_FORMAT = '%(asctime)s %(levelname)s %(message)s'
DEFAULT_PFX_FORMAT = '%(cmd)s: %(asctime)s %(levelname)s %(pfx)s: %(message)s'
DEFAULT_PFX_FORMAT_TTY = '%(cmd)s: %(pfx)s: %(message)s'

loginfo = O(upd_mode=False)
logging_level = logging.INFO
trace_level = logging.DEBUG
D_mode = False

def ifdebug():
  global logging_level
  return logging_level <= logging.DEBUG

def setup_logging(cmd_name=None, main_log=None, format=None, level=None, flags=None, upd_mode=None, ansi_mode=None, trace_mode=None, module_names=None, function_names=None):
  ''' Arrange basic logging setup for conventional UNIX command line error messaging; return an object with informative attributes.
      Sets cs.logging.cmd to `cmd_name`; default from sys.argv[0].
      If `main_log` is None, the main log will go to sys.stderr; if
      `main_log` is a string, is it used as a filename to open in append
      mode; otherwise main_log should be a stream suitable for use
      with logging.StreamHandler().
      if `format` is None, use DEFAULT_PFX_FORMAT_TTY when main_log is a tty
      or FIFO, otherwise DEFAULT_PFX_FORMAT.
      If `level` is None, infer a level from the environment using
      infer_logging_level().
      If `flags` is None, infer the flags from the environment using
      infer_logging_level().
      If `upd_mode` is None, set it to True if flags contains 'UPD',
      otherwise to False if flags contains 'NOUPD', otherwise set
      it from main_log.isatty().
      A true value causes the root logger to use cs.upd for logging.
      If `ansi_mode` is None, set it from main_log.isatty().
      A true value causes the root logger to colour certain logging levels
      using ANSI terminal sequences (currently only if cs.upd is used).
      If `trace_mode` is None, set it according to the presence of
      'TRACE' in flags.
      If trace_mode is true, set the global trace_level to logging_level;
      otherwise it defaults to logging.DEBUG.
  '''
  global cmd, logging_level, trace_level, D_mode, loginfo

  # infer logging modes, these are the initial defaults
  inferred = infer_logging_level()
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
  cmd = cmd_name
  loginfo.cmd = cmd

  if main_log is None:
    main_log = sys.stderr
  elif type(main_log) is str:
    main_log = open(main_log, "a")
  loginfo.main_log_file = main_log

  # determine some attributes of main_log
  try:
    fd = main_log.fileno()
  except (AttributeError, IOError):
    is_fifo = False
    is_reg = False
    is_tty = False
  else:
    st = os.fstat(fd)
    is_fifo = stat.S_ISFIFO(st.st_mode)
    is_reg = stat.S_ISREG(st.st_mode)
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
      upd_mode = is_tty
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
    def handler(sig, fr):
      cs.debug.thread_dump(None, main_log)
    signal.signal(signal.SIGHUP, handler)

  if upd_mode:
    main_handler = UpdHandler(main_log, None, ansi_mode=ansi_mode)
    loginfo.upd = main_handler.upd
    # enable tracing in the thread that called setup_logging
    if 'TRACE' in flags:
      Pfx._state.trace = True
  else:
    main_handler = logging.StreamHandler(main_log)

  rootLogger = logging.getLogger()
  rootLogger.setLevel(level)
  main_handler.setFormatter(PfxFormatter(format))
  rootLogger.addHandler(main_handler)

  logging_level = level
  if trace_mode:
    trace_level = logging_level

  if module_names or function_names:
    if importlib is None:
      warning("setup_logging: no importlib (python<2.7?), ignoring module_names=%r/function_names=%r", module_names, function_names)
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
    if do_debug:
      func = _ftrace(func)
    return func(*a, **kw)
  return func_wrap

def _ftrace(func):
  ''' Decorator to trace the call and return of a function.
  '''
  fname = '.'.join( (func.__module__, funccite(func)) )
  def traced_func(*a, **kw):
    citation = "%s(*%s, **%s)" % (fname, pformat(a, depth=1), pformat(kw, depth=2))
    X("CALL %s", citation)
    try:
      result = func(*a, **kw)
    except Exception as e:
      X("EXCEPTION from %s: %s %s", citation, type(e), e)
      raise
    else:
      X("RESULT from %s: %r", citation, result)
      return result
  return traced_func

class PfxFormatter(Formatter):
  ''' A Formatter subclass that has access to the program's cmd and Pfx state.
  '''

  def __init__(self, fmt=None, datefmt=None, cmd=None, context_level=None):
    ''' Initialise the PfxFormatter.
        `fmt` and `datefmt` are passed to Formatter.
        If `fmt` is None, DEFAULT_PFX_FORMAT is used.
        If `cmd` is not None, the message is prefixed with the string `cmd`.
        If `context_level` is None, records with .level < context_level will not have the Pfx state inserted at the front of the message.
    '''
    self.cmd = cmd
    self.context_level = context_level
    Formatter.__init__(self, fmt=fmt, datefmt=datefmt)

  def format(self, record):
    ''' Set .cmd and .pfx to the global cmd and Pfx context prefix respectively, then call Formatter.format.
    '''
    record.cmd = self.cmd if self.cmd else globals()['cmd']
    record.pfx = Pfx._state.prefix
    try:
      fmts = Formatter.format(self, record)
    except TypeError as e:
      X("cs.logutils.format: record=%r, self=%s: %s", record, self, e)
      X("record=%s", record.__dict__)
      X("self=%s", self.__dict__)
      raise
    message_parts = []
    if self.context_level is None or record.level >= self.context_level:
      message_parts.append(self.formatTime(record))
      message_parts.append(record.pfx)
    message_parts.append(record.message)
    record.message = ': '.join(message_parts)
    return record.message

def infer_logging_level(env_debug=None, environ=None):
  ''' Infer a logging level from the `env_debug`, which by default comes from the environment variable $DEBUG.
      Usually default to logging.WARNING, but if sys.stderr is a terminal,
      default to logging.INFO.
      Parse the environment variable $DEBUG as a comma separated
      list of flags.
      Examine the in sequence flags to affect the logging level:
        numeric < 1 => logging.WARNING
        numeric >= 1 and < 2: logging.INFO
        numeric >= 2 => logging.DEBUG
        "DEBUG" => logging.DEBUG
        "TRACE" => Pfx issues log.info calls
        "INFO"  => logging.INFO
        "WARNING" => logging.WARNING
        "ERROR" => logging.ERROR
      Return an object with the following attributes:
        .level  A logging level.
        .flags  All the words from $DEBUG as separated by commas and uppercased.
        .module_names
                Module names to be debugged.
        .function_names
                Functions to be traced in the for "module_name.func_name()".
  '''
  if env_debug is None:
    if environ is None:
      environ = os.environ
    env_debug = os.environ.get('DEBUG', '')
  level = logging.WARNING
  if sys.stderr.isatty():
    level = logging.INFO
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
      if flag == 'DEBUG':
        level = logging.DEBUG
      elif flag == 'INFO':
        level = logging.INFO
      elif flag == 'WARN' or flag == 'WARNING':
        level = logging.WARNING
      elif flag == 'ERROR':
        level = logging.ERROR
  return O(level=level, flags=flags, module_names=module_names, function_names=function_names)

def D(msg, *args):
  ''' Print formatted debug string straight to sys.stderr if D_mode is true,
      bypassing the logging modules entirely.
      A quick'n'dirty debug tool.
  '''
  global D_mode
  if D_mode:
    X(msg, *args)

def DP(msg, *args):
  ''' Print formatted debug string straight to sys.stderr if D_mode is true,
      bypassing the logging modules entirely.
      A quick'n'dirty debug tool.
      Differs from D() by including the prefix() context.
  '''
  global D_mode
  if D_mode:
    XP(msg, *args)

# set to true to log as a warning
X_via_log = False
# set to true to write direct to /dev/tty
X_via_tty = False

def X(msg, *args, **kwargs):
  ''' Unconditionally write the message `msg` to sys.stderr.
      If `args` is not empty, format `msg` using %-expansion with `args`.
  '''
  if X_via_log:
    # NB: ignores any kwargs
    msg = str(msg)
    if args:
      msg = msg % args
    warning(msg)
  elif X_via_tty:
    # NB: ignores any kwargs
    msg = str(msg)
    if args:
      msg = msg % args
    with open('/dev/tty', 'w') as fp:
      fp.write(msg)
      fp.write('\n')
      fp.flush()
  else:
    file = kwargs.pop('file', None)
    if file is None:
      file = sys.stderr
    return nl(msg, *args, file=file)

def XP(msg, *args, **kwargs):
  ''' Variation on X() which prefixes the message with the currrent Pfx prefix.
  '''
  file = kwargs.pop('file', None)
  if file is None:
    file = sys.stderr
  elif file is not None:
    if isinstance(file, StringTypes):
      with open(file, "a") as fp:
        XP(msg, *args, file=fp)
      return
  file.write(prefix())
  file.write(': ')
  file.flush()
  return X(msg, *args, file=file)

def XX(prepfx, msg, *args, **kwargs):
  with PrePfx(prepfx):
    return XP(msg, *args, **kwargs)

def status(msg, *args, **kwargs):
  ''' Write a message to the terminal's status line.
      If there is no status line use the xterm title bar sequence :-(
  '''
  file = kwargs.pop('file', None)
  if file is None:
    file = sys.stderr
  try:
    has_ansi_status = file.has_ansi_status
  except AttributeError:
    try:
      import curses
    except ImportError:
      has_ansi_status = None
    else:
      curses.setupterm()
      has_status = curses.tigetflag('hs')
      if has_status == -1:
        warning('status: curses.tigetflag(hs): not a Boolean capability, presuming false')
        has_ansi_status = None
      elif has_status > 0:
        has_ansi_status = ( curses.tigetstr('to_status_line'),
                            curses.gtigetstr('from_status_line')
                          )
      else:
        warning('status: hs=%s, presuming false', has_status)
        has_ansi_status = None
    file.has_ansi_status = has_ansi_status
  if has_ansi_status:
    msg = has_ansi_status[0] + msg + has_ansi_status[1]
  else:
    msg = '\033]0;' + msg + '\007'
  file.write(msg)
  file.flush()

def nl(msg, *args, **kw):
  ''' Unconditionally write the message `msg` to `file` (default sys.stdout).
      If `args` is not empty, format `msg` using %-expansion with `args`.
  '''
  try:
    file = kw.pop('file')
  except KeyError:
    file = sys.stdout
  if kw:
    raise ValueError("unexpected keyword arguments: %r" % (kw,))
  msg = str(msg)
  if args:
    omsg = msg
    try:
      msg = msg % args
    except TypeError as e:
      nl("cannot expand msg: %s; msg=%r, args=%r", e, msg, args, file=sys.stderr)
      msg = "%s[%r]" % (msg, args)
  file.write(msg)
  file.write("\n")
  try:
    flush = file.flush
  except AttributeError:
    pass
  else:
    flush()

def add_log(filename, logger=None, mode='a', encoding=None, delay=False, format=None, no_prefix=False):
  ''' Add a FileHandler logging to the specified `filename`; return the chosen logger and the new handler.
      If `logger` is supplied and not None, add the FileHandler to that
      Logger, otherwise to the root Logger. If `logger` is a string, call
      logging.getLogger(logger) to obtain the logger.
      `mode`, `encoding` and `delay` are passed to the logging.FileHandler
      initialiser.
      `format` is used to override the handler's default format.
      `no_prefix`: do not put the Pfx context onto the front of the message.
  '''
  if logger is None:
    logger = logging.getLogger()
  elif type(logger) is str:
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

logTo = add_log

@contextmanager
def with_log(filename, **kw):
  logger, handler = add_log(filename, **kw)
  yield logger, handler
  logger.removeHandler(handler)

class NullHandler(logging.Handler):
  def emit(self, record):
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
      exception("EXCEPTION> "+line)
    sys.excepthook = curhook

class _PfxThreadState(threading.local):
  ''' _PfxThreadState is a thread local class to track Pfx stack state.
  '''

  def __init__(self):
    self.raise_needs_prefix = False
    self._ur_prefix = None
    self.stack = []
    self.trace = False

  @property
  def cur(self):
    ''' .cur is the current/topmost Pfx instance.
    '''
    global cmd
    stack = self.stack
    if not stack:
      # I'd do this in __init__ except that cs.logutils.cmd may get set too late
      stack.append(Pfx(cmd))
    return stack[-1]

  @property
  def prefix(self):
    ''' Return the prevailing message prefix.
    '''
    marks = []
    for P in reversed(list(self.stack)):
      marks.append(P.umark)
      if P.absolute:
        break
    if self._ur_prefix is not None:
      marks.append(self._ur_prefix)
    marks = reversed(marks)
    return unicode(': ').join(marks)

  def append(self, P):
    ''' Push a new Pfx instance onto the stack.
    '''
    self.stack.append(P)

  def pop(self):
    ''' Pop a Pfx instance from the stack.
    '''
    return self.stack.pop()

def OBSOLETE(func):
  ''' Decorator for obsolete functions.
      Use:
        @OBSOLETE
        def f(...):
  '''
  def wrapped(*args, **kwargs):
    import traceback
    frame = traceback.extract_stack(None, 2)[0]
    warning("OBSOLETE call to %s:%d %s(), called from %s:%d %s",
         func.__code__.co_filename, func.__code__.co_firstlineno,
         func.__name__, frame[0], frame[1], frame[2])
    return func(*args, **kwargs)
  return wrapped

def pfx_iter(tag, iter):
  ''' Wrapper for iterators to prefix exceptions with `tag`.
  '''
  with Pfx(tag):
    for i in iter:
      yield i

def pfx(func):
  ''' Decorator for functions that should run inside:
        with Pfx(func_name):
      Use:
        @pfx
        def f(...):
  '''
  def wrapped(*args, **kwargs):
    with Pfx(func.__name__):
      return func(*args, **kwargs)
  return wrapped

def pfxtag(tag, loggers=None):
  ''' Decorator for functions that should run inside:
        with Pfx(tag, loggers=loggers):
      Use:
        @pfxtag(tag)
        def f(...):
  '''
  def wrap(func):
    if tag is None:
      wraptag = func.__name__
    else:
      wraptag = tag
    def wrapped(*args, **kwargs):
      with Pfx(wraptag, loggers=loggers):
        return func(*args, **kwargs)
    return wrapped
  return wrap

class Pfx(object):
  ''' A context manager to maintain a per-thread stack of message prefices.
  '''

  # instantiate the thread-local state object
  _state = _PfxThreadState()

  def __init__(self, mark, *args, **kwargs):
    absolute = kwargs.pop('absolute', False)
    loggers = kwargs.pop('loggers', None)
    if kwargs:
      raise TypeError("unsupported keyword arguments: %r" % (kwargs,))

    self.mark = mark
    self.mark_args = args
    self.absolute = absolute
    self._umark = None
    self._loggers = None
    if loggers is not None:
      if not hasattr(loggers, '__getitem__'):
        loggers = (loggers, )
      self.logto(loggers)

  def __enter__(self):
    global loginfo
    _state = self._state
    _state.append(self)
    _state.raise_needs_prefix = True
    if _state.trace:
      info(self._state.prefix)

  def __exit__(self, exc_type, exc_value, traceback):
    global loginfo
    _state = self._state
    if exc_value is not None:
      if _state.raise_needs_prefix:
        # prevent outer Pfx wrappers from hacking stuff as well
        _state.raise_needs_prefix = False
        # now hack the exception attributes
        prefix = self._state.prefix
        def prefixify(text):
          if not isinstance(text, StringTypes):
            DP("%s: not a string (class %s), not prefixing: %r (sys.exc_info=%r)",
               prefix, text.__class__, text, sys.exc_info())
            return text
          return prefix + ': ' + ustr(text, errors='replace').replace('\n', '\n'+prefix)
        args = getattr(exc_value, 'args', None)
        if args is not None:
          if args:
            if isinstance(args, StringTypes):
              D("%s: expected args to be a tuple, got %r", prefix, args)
              args = prefixify(args)
            else:
              args = list(args)
              if len(exc_value.args) == 0:
                args = [ prefix ]
              else:
                args = [ prefixify(exc_value.args[0]) ] + list(exc_value.args[1:])
            exc_value.args = args
        elif hasattr(exc_value, 'message'):
          exc_value.message = prefixify(str(exc_value.message))
        elif hasattr(exc_value, 'reason'):
          if isinstance(exc_value.reason, StringTypes):
            exc_value.reason = prefixify(exc_value.reason)
          else:
            warning("Pfx.__exit__: exc_value.reason is not a string: %r", exc_value.reason)
        elif hasattr(exc_value, 'msg'):
          exc_value.msg = prefixify(str(exc_value.msg))
        else:
          # we can't modify this exception - at least report the current prefix state
          D("%s: Pfx.__exit__: exc_value = %s", prefix, O_str(exc_value))
          error(prefixify(str(exc_value)))
    _state.pop()
    if loginfo.upd_mode:
      info(self._state.prefix)
    return False

  @property
  def umark(self):
    ''' Return the unicode message mark for use with this Pfx.
        Used by Pfx._state.prefix to compute to full prefix.
    '''
    u = self._umark
    if u is None:
      mark = ustr(self.mark)
      if not isinstance(mark, unicode):
        if isinstance(mark, str):
          mark = unicode(mark, errors='replace')
        else:
          mark = unicode(mark)
      u = mark
      if self.mark_args:
        u = u % self.mark_args
      self._umark = u
    return u

  def logto(self, newLoggers):
    ''' Define the Loggers anew.
    '''
    self._loggers = newLoggers

  def partial(self, func, *a, **kw):
    ''' Return a function that will run the supplied function `func`
        within a surrounding Pfx context with the current mark string.
        This is intended for deferred call facilities like
        WorkerThreadPool, Later, and futures.
    '''
    pfx2 = Pfx(self.mark, absolute=True, loggers=self.loggers)
    def pfxfunc():
      with pfx2:
        return func(*a, **kw)
    return pfxfunc

  @property
  def loggers(self):
    ''' Return the loggers to use for this Pfx instance.
    '''
    _loggers = self._loggers
    if _loggers is None:
      for P in reversed(self._state.stack):
        if P._loggers is not None:
          _loggers = P._loggers
          break
      if _loggers is None:
        _loggers = (logging.getLogger(),)
    return _loggers

  enter = __enter__
  exit = __exit__

  # Logger methods
  def exception(self, msg, *args):
    for L in self.loggers:
      L.exception(msg, *args)
  def log(self, level, msg, *args, **kwargs):
    ## to debug format errors ## D("msg=%r, args=%r, kwargs=%r", msg, args, kwargs)
    for L in self.loggers:
      try:
        L.log(level, msg, *args, **kwargs)
      except Exception as e:
        XP("exception logging to %s msg=%r, args=%r, kwargs=%r: %s", L, msg, args, kwargs, e)
  def debug(self, msg, *args, **kwargs):
    self.log(logging.DEBUG, msg, *args, **kwargs)
  def info(self, msg, *args, **kwargs):
    self.log(logging.INFO, msg, *args, **kwargs)
  def warning(self, msg, *args, **kwargs):
    self.log(logging.WARNING, msg, *args, **kwargs)
  @OBSOLETE
  def warn(self, *args, **kwargs):
    self.warning(*args, **kwargs)
  def error(self, msg, *args, **kwargs):
    self.log(logging.ERROR, msg, *args, **kwargs)
  def critical(self, msg, *args, **kwargs):
    self.log(logging.CRITICAL, msg, *args, **kwargs)

def prefix():
  ''' Return the current Pfx prefix.
  '''
  return Pfx._state.prefix

@contextmanager
def PrePfx(pfx, *args):
  ''' Push a temporary value for Pfx._state._ur_prefix to enloundenify messages.
  '''
  if args:
    pfx = pfx % args
  state = Pfx._state
  old_ur_prefix = state._ur_prefix
  state._ur_prefix = pfx
  yield None
  state._ur_prefix = old_ur_prefix

class PfxCallInfo(Pfx):
  ''' Subclass of Pfx to insert current function an caller into messages.
  '''

  def __init__(self):
    import traceback
    grandcaller, caller, myframe = traceback.extract_stack(None, 3)
    Pfx.__init__(self,
                 "at %s:%d %s(), called from %s:%d %s()",
                 caller[0], caller[1], caller[2],
                 grandcaller[0], grandcaller[1], grandcaller[2])

# Logger public functions
def exception(msg, *args):
  Pfx._state.cur.exception(msg, *args)
def log(level, msg, *args, **kwargs):
  Pfx._state.cur.log(level, msg, *args, **kwargs)
def debug(msg, *args, **kwargs):
  log(logging.DEBUG, msg, *args, **kwargs)
def info(msg, *args, **kwargs):
  log(logging.INFO, msg, *args, **kwargs)
def warning(msg, *args, **kwargs):
  log(logging.WARNING, msg, *args, **kwargs)
@OBSOLETE
def warn(*args, **kwargs):
  warning(*args, **kwargs)
def error(msg, *args, **kwargs):
  log(logging.ERROR, msg, *args, **kwargs)
def critical(msg, *args, **kwargs):
  log(logging.CRITICAL, msg, *args, **kwargs)
def trace(msg, *args, **kwargs):
  log(trace_level, msg, *args, **kwargs)

class LogTime(object):
  ''' LogTime is a content manager that logs the elapsed time of the enclosed
      code. After the run, the field .elapsed contains the elapsed time in
      seconds.
  '''
  def __init__(self, tag, *args, **kwargs):
    threshold = kwargs.pop('threshold', 1.0)
    level = kwargs.pop('level', logging.INFO)
    warnThreshold = kwargs.pop('warnThreshold', None)
    warnLevel = kwargs.pop('warnLevel', logging.WARNING)
    self.tag = tag
    self.tag_args = args
    self.threshold = threshold
    self.level = level
    self.warnThreshold = warnThreshold
    self.warnLevel = warnLevel
  def __enter__(self):
    self.start = time.time()
    return self
  def __exit__(self, exc_type, exc_value, traceback):
    now = time.time()
    elapsed = now - self.start
    if self.threshold is not None and elapsed >= self.threshold:
      level = self.level
      if self.warnThreshold is not None and elapsed >= self.warnThreshold:
        level = self.warnLevel
      tag = self.tag
      if self.tag_args:
        tag = tag % self.tag_args
      log(level, "%s: ELAPSED %5.3fs" % (tag, elapsed))
    self.elapsed = elapsed
    return False

class UpdHandler(StreamHandler):
  ''' A StreamHandler subclass whose .emit method uses a cs.upd.Upd for transcription.
  '''

  def __init__(self, strm=None, nlLevel=None, ansi_mode=None):
    ''' Initialise the UpdHandler.
        `strm` is the output stream, default sys.stderr.
        `nlLevel` is the logging level at which conventional line-of-text
        output is written; log messages of a lower level go via the
        update-the-current-line method. Default is logging.WARNING.
        If `ansi_mode` is None, set if from strm.isatty().
        A true value causes the handler to colour certain logging levels
        using ANSI terminal sequences.
    '''
    if strm is None:
      strm = sys.stderr
    if nlLevel is None:
      nlLevel = logging.WARNING
    if ansi_mode is None:
      ansi_mode = strm.isatty()
    StreamHandler.__init__(self, strm)
    self.upd = Upd(strm)
    self.__nlLevel = nlLevel
    self.__ansi_mode = ansi_mode
    self.__lock = Lock()

  def emit(self, logrec):
    with self.__lock:
      if logrec.levelno >= self.__nlLevel:
        with self.upd._withoutContext():
          if self.__ansi_mode:
            if logrec.levelno >= logging.ERROR:
              logrec.msg = colourise(logrec.msg, 'red')
            elif logrec.levelno >= logging.WARN:
              logrec.msg = colourise(logrec.msg, 'yellow')
          self.upd.without(StreamHandler.emit, self, logrec)
      else:
        self.upd.out(logrec.getMessage())

  def flush(self):
    return self.upd.flush()

if __name__ == '__main__':
  setup_logging(sys.argv[0])

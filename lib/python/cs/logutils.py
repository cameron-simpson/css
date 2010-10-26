#!/usr/bin/python
#
# Convenience routines for logging.
#       - Cameron Simpson <cs@zip.com.au> 29aug2009
#

from __future__ import with_statement
import logging
import os
import sys
import time
from thread import allocate_lock
import threading
import traceback
import cs.misc

logging_level = logging.INFO

def setup_logging(cmd=None, format=None, level=None, upd_mode=None):
  ''' Arrange basic logging setup for conventional UNIX command
      line error messaging.
      Sets cs.misc.cmd to `cmd`.
      If `format` is None, set format to "cmd: levelname: message".
      If `level` is None, infer a level from the environment using
      infer_logging_level().
      If `upd_mode` is None, set it from sys.stderr.isatty().
      A true value causes the root logger to use cs.upd for logging.
      Returns the logging level.
  '''
  if cmd is None:
    import os.path
    cmd = os.path.basename(sys.argv[0])
  cs.misc.cmd = cmd
  if format is None:
    format = cmd.replace('%','%%')+': %(levelname)s: %(message)s'
  if level is None:
    level = infer_logging_level()
  if upd_mode is None:
    upd_mode = sys.stderr.isatty()
  if upd_mode:
    from cs.upd import UpdHandler
    rootLogger = logging.getLogger()
    rootLogger.setLevel(level)
    upd = UpdHandler(sys.stderr, logging.WARNING)
    upd.setFormatter(logging.Formatter(format))
    rootLogger.addHandler(upd)
  else:
    logging.basicConfig(level=level, format=format)
  logging_level = level
  return level

def infer_logging_level():
  ''' Infer a logging level from the environment.
      Default to logging.WARNING.
      If sys.stderr is a terminal, default to logging.INFO.
      If the environment variable DEBUG is set:
        "" or "0" => same as unset; leave default as above.
        numeric non-zero => logging.DEBUG
        "DEBUG" => logging.DEBUG
        "INFO"  => logging.INFO
        "WARNING" => logging.WARNING
        "ERROR" => logging.ERROR
      Return the inferred logging level.
  '''
  level = logging.WARNING
  if sys.stderr.isatty():
    level = logging.INFO
  env = os.environ.get('DEBUG', '')
  if env != '' and env != '0':
    level = logging.DEBUG
    env = env.upper()
    if env == 'DEBUG':
      level = logging.DEBUG
    elif env == 'INFO':
      level = logging.INFO
    elif env == 'WARN' or env == 'WARNING':
      level = logging.WARNING
    elif env == 'ERROR':
      level = logging.ERROR
  return level

def D(fmt, *args):
  ''' Unconditionally print formatted debug string straight to sys.stderr,
      bypassing the logging modules entirely.
      A quick'n'dirty debug tool.
  '''
  sys.stderr.write(fmt % args)
  sys.stderr.write("\n")

def logTo(filename, logger=None, mode='a', encoding=None, delay=False):
  ''' Log to the specified filename.
      If `logger` is supplied and not None, add the FileHandler to that
      Logger, otherwise to the root Logger. If `logger` is a string, call
      logging.getLogger(logger) to obtain the logger.
      `mode`, `encoding` and `delay` are passed to the logging.FileHandler
      initialiser.
      Returns the logger and handler.
  '''
  if logger is None:
    logger = logging.getLogger()
  elif type(logger) is str:
    logger = logging.getLogger(logger)
  handler = logging.FileHandler(filename, mode, encoding, delay)
  logger.addHandler(handler)
  return logger, handler

class NullHandler(logging.Handler):
  def emit(self, record):
    pass

__logExLock = allocate_lock()
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
    self.old = []

  @property
  def cur(self):
    ''' .cur is the current/topmost Pfx instance.
    '''
    if not self.old:
      self.push(Pfx(cs.misc.cmd))
    return self.old[-1]

  @property
  def prefix(self):
    ''' Return the prevailing message prefix.
    '''
    stack = list(self.old)
    stack.reverse()
    marks = []
    for P in stack:
      marks.append(P._mark)
      if P.absolute:
        break
    marks.reverse()
    return ': '.join(marks)

  def push(self, P):
    ''' Push a new Pfx instance onto the stack.
    '''
    self.old.append(P)

  def pop(self):
    ''' Pop a Pfx instance from the stack.
    '''
    return self.old.pop()

if sys.hexversion >= 0x02060000:
  myLoggerAdapter = logging.LoggerAdapter
else:
  class myLoggerAdapter(object):
    ''' A LoggerAdaptor implementation for pre-2.6 Pythons.
    '''
    def __init__(self, L, extra):
      self.__L = L
      self.__extra = extra
    # Logger methods
    def exception(self, msg, *args, **kwargs):
      msg, kwargs = self.process(msg, kwargs)
      self.__L.exception(msg, *args, **kwargs)
    def log(self, level, msg, *args, **kwargs):
      msg, kwargs = self.process(msg, kwargs)
      self.__L.log(level, msg, *args, **kwargs)
    def debug(self, msg, *args, **kwargs):
      self.log(logging.DEBUG, msg, *args, **kwargs)
    def info(self, msg, *args, **kwargs):
      self.log(logging.INFO, msg, *args, **kwargs)
    def warning(self, msg, *args, **kwargs):
      self.log(logging.WARNING, msg, *args, **kwargs)
    warn = warning
    def error(self, msg, *args, **kwargs):
      self.log(logging.ERROR, msg, *args, **kwargs)
    def critical(self, msg, *args, **kwargs):
      self.log(logging.CRITICAL, msg, *args, **kwargs)

class Pfx_LoggerAdapter(myLoggerAdapter):
  def process(self, msg, kwargs):
    prefix = _prefix.prefix
    if len(prefix) > 0:
      msg = prefix + ": " + msg
    return msg, kwargs

def pfx(func):
  ''' Decorator for functions that should run inside:
        with Pfx(func_name):
      Use:
        @pfx
        def f(...):
  '''
  def wrapped(*args, **kwargs):
    with Pfx(func.func_name):
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
    def wrapped(*args, **kwargs):
      with Pfx(tag, loggers=loggers):
        return func(*args, **kwargs)
    return wrapped
  return wrap

def OBSOLETE(func):
  ''' Decorator for obsolete functions.
      Use:
        @OBSOLETE
        def f(...):
  '''
  def wrapped(*args, **kwargs):
    import traceback
    frame = traceback.extract_stack(None, 2)[0]
    warn("OBSOLETE call to %s:%d %s(), called from %s:%d %s",
         func.func_code.co_filename, func.func_code.co_firstlineno,
         func.func_name, frame[0], frame[1], frame[2])
    return func(*args, **kwargs)
  return wrapped

class Pfx(object):
  ''' A context manager to maintain a per-thread stack of message prefices.
  '''
  def __init__(self, mark, absolute=False, loggers=None):
    self._mark = str(mark)
    self.absolute = absolute
    if loggers is not None:
      if not hasattr(loggers, '__getitem__'):
        loggers = (loggers, )
    self.logto(loggers)

  @property
  def mark(self):
    ''' Return the message prefix for use with this Pfx.
    '''
    if self.absolute:
      return self._mark
    global _prefix
    mark = _prefix.prefix
    if _prefix.cur is not self:
      mark = mark + ': ' + self._mark
    return mark

  def logto(self, newLoggers):
    ''' Define the Loggers anew.
    '''
    self._loggers = newLoggers
    self._loggerAdapters = None

  def func(self, func, *a, **kw):
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
    if self._loggerAdapters is None:
      _loggers = self._loggers
      if _loggers is None:
        # get the Logger list from an ancestor
        for P in _prefix.old:
          if P._loggers is not None:
            _loggers = P._loggers
            break
        if _loggers is None:
          _loggers = (logging.getLogger(),)
      self._loggerAdapters = list( Pfx_LoggerAdapter(L, {}) for L in _loggers )
    return self._loggerAdapters

  def __enter__(self):
    global _prefix
    _prefix.push(self)
    _prefix.raise_needs_prefix = True

  def __exit__(self, exc_type, exc_value, traceback):
    global _prefix
    if exc_value is not None:
      if exc_type is not SystemExit:
        if _prefix.raise_needs_prefix:
          prefix = self.mark
          ##sys.stderr.write("Pfx: [prefix=%s]\n" % (prefix,))
          if hasattr(exc_value, 'args'):
            ##sys.stderr.write("Pfx: [exc_value.args is = %s]\n" % (`exc_value.args`,))
            if len(exc_value.args) > 0:
              exc_value.args = [ prefix + ": " + str(exc_value.args[0]) ] \
                               + list(exc_value.args[1:])
              ##sys.stderr.write("Pfx: [exc_value.args now = %s]\n" % (`exc_value.args`,))
          else:
            # we can't modify this - at least report the current prefix state
            sys.stderr.write("%s: Pfx.__exit__: exc_value = %s\n" \
                             % (prefix, repr(exc_value),))
          # prevent outer Pfx wrappers from hacking stuff as well
          _prefix.raise_needs_prefix = False
    _prefix.pop()
    return False

  enter = __enter__
  exit = __exit__

  # Logger methods
  def exception(self, msg, *args):
    for L in self.loggers:
      L.exception(msg, *args)
  def log(self, level, msg, *args, **kwargs):
    for L in self.loggers:
      L.log(level, msg, *args, **kwargs)
  def debug(self, msg, *args, **kwargs):
    self.log(logging.DEBUG, msg, *args, **kwargs)
  def info(self, msg, *args, **kwargs):
    self.log(logging.INFO, msg, *args, **kwargs)
  def warning(self, msg, *args, **kwargs):
    self.log(logging.WARNING, msg, *args, **kwargs)
  warn = warning
  def error(self, msg, *args, **kwargs):
    self.log(logging.ERROR, msg, *args, **kwargs)
  def critical(self, msg, *args, **kwargs):
    self.log(logging.CRITICAL, msg, *args, **kwargs)

# instantiate the thread-local state object
_prefix = _PfxThreadState()

# Logger public functions
def exception(msg, *args):
  _prefix.cur.exception(msg, *args)
def log(level, msg, *args, **kwargs):
  _prefix.cur.log(level, msg, *args, **kwargs)
def debug(msg, *args, **kwargs):
  log(logging.DEBUG, msg, *args, **kwargs)
def info(msg, *args, **kwargs):
  log(logging.INFO, msg, *args, **kwargs)
def warning(msg, *args, **kwargs):
  log(logging.WARNING, msg, *args, **kwargs)
warn = warning
def error(msg, *args, **kwargs):
  log(logging.ERROR, msg, *args, **kwargs)
def critical(msg, *args, **kwargs):
  log(logging.CRITICAL, msg, *args, **kwargs)

def listargs(args, kwargs, tostr=None):
  ''' Take the list 'args' and dict 'kwargs' and return a list of
      strings representing them for printing.
  '''
  if tostr is None:
    tostr = str
  arglist = [ tostr(A) for A in args ]
  kw=kwargs.keys()
  if kw:
    kw.sort()
    for k in kw:
      arglist.append("%s=%s" % (k, tostr(kwargs[k])))
  return arglist

class LogTime(object):
  ''' LogTime is a content manager that logs the elapsed time of the enclosed
      code. After the run, the field .elapsed contains the elapsed time in
      seconds.
  '''
  def __init__(self, tag, level=None, threshold=None):
    if level is None:
      level = logging.INFO
    self.tag = tag
    self.level = level
    self.threshold = threshold
  def __enter__(self):
    self.start = time.time()
  def __exit__(self, exc_type, exc_value, traceback):
    now = time.time()
    elapsed = now - self.start
    if self.threshold is not None and elapsed >= self.threshold:
      log(self.level, "%s: ELAPSED %5.3fs" % (self.tag, elapsed))
    self.elapsed = elapsed
    return False

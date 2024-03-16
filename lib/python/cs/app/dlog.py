#!/usr/bin/env python3
#

''' Log a line in my daily log.

    This is an upgrade from my venerable shell script,
    whose logic was becoming unwieldy.

    I more or less live in the shell, so I log activity with brief
    1-line shell command commands eg:

        dlog HOME: set up the whatchamgig

    which I alias as `dl`, with additional aliases like `HOME` for `dl HOME:`
    and so forth; as short a throwaway line as I can get away with.
    In particular, I use this to make notes about work activity
    (none of the several time tracking tools I've tried work for me)
    and things like banking and purchases eg:

        dl MYBANK,VENDOR: xfer \\$99 to vendor for widget from bank acct rcpt 1234567

    I've got scripts to pull out the work ones for making invoices
    and an assortment of other scripts (eg my `alert` script) also log via `dlog`.

    The current incarnation logs to a flat text file (default `~/var/dlog-quick`)
    and to `SQLTags` SQLite database.
    It has a little daemon mode to reduce contention for the SQLite database too.
'''

from dataclasses import dataclass, field
from datetime import datetime
from getopt import getopt, GetoptError
import os
from os.path import expanduser
from queue import Queue
import re
from signal import SIGINT
from stat import S_ISFIFO
import sys
from threading import Thread
import time
from typing import Optional, Iterable, List, Union

from arrow import Arrow
from icontract import require
from typeguard import typechecked

from cs.buffer import CornuCopyBuffer
from cs.cmdutils import BaseCommand
from cs.context import stack_signals
from cs.dateutils import datetime2unixtime
from cs.deco import fmtdoc, promote
from cs.fstags import FSTags, uses_fstags
from cs.lex import skipwhite
from cs.logutils import warning
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.queues import get_batch
from cs.resources import RunState, uses_runstate
from cs.sqltags import SQLTags, DBURL_DEFAULT
from cs.tagset import Tag, TagSet
from cs.upd import print, builtin_print  # pylint: disable=redefined-builtin

__version__ = '20240316-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Topic :: System :: Logging",
        "Topic :: Utilities",
    ],
    'install_requires': [
        'arrow',
        'cs.buffer',
        'cs.cmdutils',
        'cs.context',
        'cs.dateutils',
        'cs.deco',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.progress',
        'cs.queues',
        'cs.resources',
        'cs.sqltags',
        'cs.tagset',
        'cs.upd',
        'icontract',
        'typeguard',
    ],
    'entry_points': {
        'console_scripts': {
            'dlog': 'cs.app.dlog:main',
        },
    },
}

def main(argv=None):
  ''' Run the `dlog` command line implementation.
  '''
  return DLogCommand(argv).run()

DEFAULT_DBPATH = DBURL_DEFAULT
DEFAULT_LOGPATH = '~/var/log/dlog-quick'
DEFAULT_PIPEPATH = '~/var/log/dlog.pipe'

CATS_RE = re.compile(r'([A-Z][A-Z0-9]*(,+[A-Z][A-Z0-9]*)*):\s*')

@dataclass
class DLog:
  ''' A log entry.
  '''
  headline: str
  categories: List[str] = field(default_factory=set)
  tags: TagSet = field(default_factory=TagSet)
  when: float = field(default_factory=time.time)

  def __str__(self):
    fields = [self.dt_s]
    if self.categories:
      fields.append(','.join(sorted(map(str.upper, self.categories))) + ':')
    for tag in self.tags:
      fields.append(f'+{tag}')
    fields.append('; '.join(self.headline.rstrip().split('\n')))
    return ' '.join(fields)

  @classmethod
  def from_str(
      cls,
      line,
      *,
      categories: Iterable[str] = (),
      multi_categories: bool = False,
  ):
    ''' Create a `DLog` instance from a log line.

        Parameters:
        * `line`: the log line from which to derive the `DLog` object
        * `categories`: optional iterable of category names, which will be lowercased
        * `multi_categories`: default `False`; if true the look for
          multiple leading *CAT*`,`...`:` preambles on the line to derive
          caetgory names instead of just one

        The expected format is:

            YYYY-MM-DD HH:MM:SS [cats,...:] [+tag[=value]]... log text

        Example:

            >>> DLog.from_str('2024-02-01 11:12:13 XX: +a +b=1 +c="zot"  +9 zoo=2') # doctest: +ELLIPSIS
            DLog(headline='+9 zoo=2', categories={'xx'}, tags=TagSet:{'a': None, 'b': 1, 'c': 'zot'}, when=...)

    '''
    m = re.match(r'(\d\d\d\d-\d\d-\d\d\s+\d\d:\d\d:\d\d)\s+', line)
    if m:
      offset = m.end()
      dt = pfx_call(datetime.fromisoformat, m.group(1))
      ar = Arrow.fromdatetime(dt, tzinfo='local')
      when = ar.float_timestamp
    else:
      when = time.time()
      offset = skipwhite(line)
    # categories
    cats = set(map(str.lower, categories))
    while m := CATS_RE.match(line, pos=offset):
      cats.update(map(str.lower, m.group(1).split(',')))
      offset = m.end()
      if not multi_categories:
        break
    # tags
    tags = TagSet()
    while (offset < len(line) - 1 and line.startswith('+', offset)
           and line[offset + 1].isalpha()):
      offset += 1
      tag, offset = Tag.from_str2(line, offset)
      tags.add(tag)
      offset = skipwhite(line, offset)
    return cls(
        headline=line[offset:],
        categories=cats,
        tags=tags,
        when=when,
    )

  @property
  def dt_s(self):
    ''' This log entry's local time as a string.
    '''
    return datetime.fromtimestamp(self.when).isoformat(
        sep=" ", timespec="seconds"
    )

  def quick(self, logf):
    ''' Write this log enty to the file `logf`.
        If `logf` is a string, treat it as a filename and open it for append.
    '''
    if isinstance(logf, str):
      with pfx_call(open, logf, 'a') as f:
        self.quick(f)
    else:
      builtin_print(self, file=logf, flush=True)

  @pfx_method
  @promote
  @require(
      lambda logpath, sqltags: logpath is not None or sqltags is not None,
      "one of logpath or sqltags must be supplied"
  )
  def log(
      self,
      logpath: Optional[str] = None,
      *,
      pipepath: Optional[str] = None,
      sqltags: Optional[SQLTags] = None,
  ):
    ''' Log to `pipepath`, falling back to `logpath` and/or `sqltags`.

        Parameters:
        * `pipepath`: optional filesystem path of a named pipe
           to which to write the log line
        * `logpath`: optional filesystem path of a regular file to
          which to append the log line
        * `sqltags`: optional `SQLTags` instance or filesystem path
          of an SQLite file to use with `SQLTags`; also log the `DLog`
          entry here

        One of `logpath` or `sqltags` must be provided.

        If `pipepath` exists and is logged to, `logpath` and `sqltags`
        are ignored - the daemon listening to the pipe will do the
        logging.

        If `pipepath` is not supplied or we fail to log to it, fall
        back to logging to `logpath` and/or `sqltags`.
    '''
    if pipepath:
      try:
        S = pfx_call(os.stat, pipepath)
      except FileNotFoundError:
        # no server pipe
        pass
      except Exception as e:  # pylint: disable=broad-exception-caught
        warning(
            "cannot stat pipepath:%r: %s, falling back to direct log",
            pipepath, e
        )
      else:
        if S_ISFIFO(S.st_mode):
          try:
            with pfx_call(open, pipepath, 'a') as pipef:
              builtin_print(self, file=pipef)
            return
          except Exception as e:  # pylint: disable=broad-exception-caught
            warning(
                "failed to log to pipeath:%r: %s, falling back to direct log",
                pipepath, e
            )
    if logpath is None and sqltags is None:
      raise ValueError(
          f'{self.__class__.__name__}.log: logpath and sqltags cannot both be None'
      )
    if logpath is not None:
      with pfx_call(open, logpath, 'a') as logf:
        builtin_print(self, file=logf)
    if sqltags is not None:
      sql_logtags = TagSet(self.tags)
      sql_logtags.categories = sorted(self.categories)
      sql_logtags.headline = self.headline
      with sqltags:
        sqltags.default_factory(None, unixtime=self.when, tags=sql_logtags)

  @staticmethod
  def _daemon_worker(pfd: int, q, *, runstate: RunState):
    bfr = CornuCopyBuffer.from_fd(pfd)
    lineno = 0
    while not runstate.cancelled:
      line = bfr.readline()
      lineno += 1
      if not line:
        # this does not actually happen because we have the pipe open read/write
        q.close()
        break
      line = line.decode('utf-8', errors='replace').rstrip()
      q.put((lineno, line))

  @classmethod
  @uses_runstate
  @promote
  @typechecked
  def daemon(
      cls,
      pipepath: str,
      logpath: Optional[str] = None,
      sqltags: Optional[SQLTags] = None,
      *,
      runstate: RunState,
  ):
    ''' Run a daemon reeading dlog lines from `pipepath`
        and logging them to `logpath` and/or `sqltags`.

        `pipepath` must not already exist, and will be removed at
        the end of this function. This is to avoid dlog clients
        trying to use an unattended pipe.
    '''
    pfx_call(os.mkfifo, pipepath)
    try:
      pfd = None
      try:
        pfd = pfx_call(os.open, pipepath, os.O_RDWR)
        q = Queue(1024)
        Thread(
            target=cls._daemon_worker,
            args=(pfd, q),
            kwargs=dict(runstate=runstate),  # pylint: disable=use-dict-literal
            daemon=True,
        ).start()
        while not runstate.cancelled:
          with stack_signals(SIGINT, lambda *_: sys.exit(1)):
            lines = get_batch(q)
          if not lines:
            break
          # open the db once around the whole batch
          with sqltags:
            for lineno, line in lines:
              with Pfx(lineno):
                try:
                  dl = DLog.from_str(line, multi_categories=True)
                except ValueError as e:
                  warning("bad log line: %s: %r", e, line)
                else:
                  dl.log(logpath=logpath, sqltags=sqltags)
      finally:
        if pfd is not None:
          os.close(pfd)
    finally:
      pfx_call(os.remove, pipepath)

class DLogCommand(BaseCommand):
  ''' The `dlog` command line implementation.
  '''

  @dataclass
  class Options(BaseCommand.Options):
    ''' Options for `DLogCommand`.
    '''
    categories: set = field(default_factory=set)
    dbpath: str = field(
        default_factory=lambda:
        (os.environ.get('DLOG_DBPATH') or expanduser(DEFAULT_DBPATH))
    )
    logpath: str = field(
        default_factory=lambda:
        (os.environ.get('DLOG_LOGPATH') or expanduser(DEFAULT_LOGPATH))
    )
    pipepath: str = field(
        default_factory=lambda:
        (os.environ.get('DLOG_PIPEPATH') or expanduser(DEFAULT_PIPEPATH))
    )
    tags: TagSet = field(default_factory=TagSet)
    when: float = field(default_factory=time.time)

  @staticmethod
  def cats_from_str(cats_s):
    ''' Return an iterable of lowercase category names from a comma
        or space separated string.
    '''
    return (category for category in cats_s.replace(',', ' ').lower().split())

  def cmd_daemon(self, argv):
    ''' Usage: {cmd} [pipepath]
          Listen on pipepath for new dlog messages.
          This serialises contention for the database.
    '''
    options = self.options
    dbpath = options.dbpath
    logpath = options.logpath
    pipepath = options.pipepath
    if argv:
      pipepath = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    DLog.daemon(pipepath, logpath=logpath, sqltags=dbpath)

  # pylint: disable=too-many-branches,too-many-locals
  @uses_fstags
  def cmd_log(self, argv, fstags: FSTags):
    ''' Usage: {cmd} [{{CATEGORIES:|tag=value}}...] headline
          Log headline to the dlog.
          Options:
          -c categories   Alternate categories specification.
          -d datetime     Timestamp for the log entry instead of "now".
    '''
    options = self.options
    badopts = False
    dt = None
    opts, argv = getopt(argv, 'c:d:')
    for opt, val in opts:
      with Pfx(opt if val is None else "%s %r" % (opt, val)):
        if opt == '-c':
          options.categories.update(self.cats_from_str(val))
        elif opt == '-d':
          try:
            dt = pfx_call(datetime.fromisoformat, val)
          except ValueError as e:
            # pylint: disable=raise-missing-from
            raise GetoptError("unparsed date: %s" % (e,))
          options.when = datetime2unixtime(dt)
        else:
          raise RuntimeError("unimplemented option")
    if dt is None:
      dt = datetime.fromtimestamp(options.when)
    if badopts:
      raise GetoptError("invalid preargv")
    if not argv:
      raise GetoptError("no headline")
    categories = options.categories
    pipepath = options.pipepath
    logpath = options.logpath
    dbpath = options.dbpath
    dl = DLog.from_str(
        f'{dt.isoformat(sep=" ",timespec="seconds")} {" ".join(argv)}',
        categories=categories,
    )
    if not dl.categories:
      # infer categories from the working directory
      auto_categories = self.cats_from_str(
          fstags['.'].all_tags.get('cs.dlog', '')
      )
      dl.categories.update(auto_categories)
    dl.log(logpath=logpath, pipepath=pipepath, sqltags=dbpath)

  def cmd_scan(self, argv):
    ''' Usage: {cmd} [{{-|filename}}]...
          Scan log files and report.
    '''
    if not argv:
      argv = ['-']
    runstate = self.options.runstate
    for arg in argv:
      runstate.raiseif()
      if arg == '-':
        for lineno, line in progressbar(enumerate(sys.stdin, 1)):
          runstate.raiseif()
          with Pfx("stdin:%d", lineno):
            try:
              dl = DLog.from_str(line, multi_categories=True)
            except ValueError as e:
              warning("%s", e)
            else:
              print(dl)
      else:
        with pfx_call(open, arg) as f:
          for lineno, line in progressbar(enumerate(f, 1)):
            runstate.raiseif()
            with Pfx("%s:%d", arg, lineno):
              try:
                dl = DLog.from_str(line, multi_categories=True)
              except ValueError as e:
                warning("%s", e)
              else:
                print(dl)

@pfx
@promote
@typechecked
@fmtdoc
def dlog(
    headline: str,
    *,
    logpath: Optional[str] = None,
    sqltags: Optional[SQLTags] = DEFAULT_DBPATH,
    tags=None,
    categories: Optional[Iterable] = None,
    when: Union[None, int, float, datetime] = None,
):
  ''' Log `headline` to the dlog.

      Parameters:
      * `headline`: the log line message
      * `logpath`: optional text log pathname,
        default `{DEFAULT_LOGPATH}` from DEFAULT_LOGPATH
      * `sqltags`: optional `SQLTags` instance,
        default uses `{DEFAULT_DBPATH}` from DEFAULT_DBPATH
      * `tags`: optional iterable of `Tag`s to associate with the log entry
      * `categories`: optional iterable of category strings
      * `when`: optional UNIX time or `datetime`, default now
  '''
  dl = DLog(
      headline=headline,
      categories=categories or [],
      tags=tags or TagSet(),
      when=when or time.time(),
  )
  if logpath is None:
    logpath = expanduser(DEFAULT_LOGPATH)
  return dl.log(logpath=logpath, sqltags=sqltags)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

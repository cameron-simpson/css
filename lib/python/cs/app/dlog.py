#!/usr/bin/env python3
#

''' Log a line in my daily log.

    This is an upgrade from my venerable shell script,
    whose logic was becoming unwieldy.
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from getopt import getopt, GetoptError
import os
from os.path import expanduser
import re
import sys
import time
from typing import Optional, Iterable, List, Union

from arrow import Arrow
from typeguard import typechecked

from cs.buffer import CornuCopyBuffer
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.dateutils import datetime2unixtime
from cs.deco import fmtdoc
from cs.fstags import FSTags
from cs.lex import get_dotted_identifier, skipwhite
from cs.logutils import debug, warning
from cs.pfx import Pfx, pfx, pfx_call
from cs.progress import progressbar
from cs.sqltags import SQLTags, DBURL_DEFAULT
from cs.tagset import Tag, TagSet
from cs.upd import print

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
    if self.tags:
      fields.append('+' + ','.join(map(str, self.tags)))
    fields.append('; '.join(self.headline.rstrip().split('\n')))
    return ' '.join(fields)

  @classmethod
  def from_str(cls, line, multi_categories: bool = False):
    ''' Create a `DLog` instance from a log line.

        The expected format is:

            YYYY-MM-DD HH:MM:SS [cats,...:] [+tag[=value]]... log text

        Example:

            >>> DLog.from_str('2024-02-01 11:12:13 XX: +a +b=1 +c="zot"  +9 zoo=2') # doctest: +ELLIPSIS
            DLog(headline='+9 zoo=2', categories={'xx'}, tags=TagSet:{'a': None, 'b': 1, 'c': 'zot'}, when=...)

    '''
    m = re.match(r'(\d\d\d\d-\d\d-\d\d\s+\d\d:\d\d:\d\d)\s+', line)
    if not m:
      raise ValueError('no leading YYYY-MM-DD HH:MM:SS')
    offset = m.end()
    dt = pfx_call(datetime.fromisoformat, m.group(1))
    ar = Arrow.fromdatetime(dt, tzinfo='local')
    when = ar.float_timestamp
    # categories
    cats = set()
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
      print(self, file=logf, flush=True)

class DLogCommand(BaseCommand):
  ''' The `dlog` command line implementation.
  '''

  @dataclass
  class Options(BaseCommand.Options):
    categories: set = field(default_factory=set)
    dbpath: str = field(default_factory=lambda: expanduser(DEFAULT_DBPATH))
    logpath: str = field(default_factory=lambda: expanduser(DEFAULT_LOGPATH))
    pipepath: str = field(
        default_factory=lambda:
        (os.environ.get('DLOG_PIPEPATH') or expanduser(DEFAULT_PIPEPATH))
    )
    tags: TagSet = field(default_factory=TagSet)
    when: float = field(default_factory=time.time)

  @contextmanager
  def run_context(self):
    ''' Prepare the logging `SQLTags` around each command invocation.
    '''
    with super().run_context():
      options = self.options
      dbpath = options.dbpath
      with FSTags() as fstags:
        with SQLTags(dbpath) as sqltags:
          with stackattrs(options, fstags=fstags, sqltags=sqltags,
                          verbose=True):
            yield

  @staticmethod
  def cats_from_str(s):
    ''' Return an iterable of lowercase category names from a comma
        or space separated string.
    '''
    return (category for category in s.replace(',', ' ').lower().split())

  # pylint: disable=too-many-branches,too-many-locals
  def cmd_log(self, argv):
    ''' Usage: {cmd} [{{CATEGORIES:|tag=value}}...] headline
          Log headline to the dlog.
          Options:
          -c categories   Alternate categories specification.
          -d datetime     Timestamp for the log entry instead of "now".
    '''
    options = self.options
    sqltags = options.sqltags
    badopts = False
    opts, argv = getopt(argv, 'c:d:')
    for opt, val in opts:
      with Pfx(opt if val is None else "%s %r" % (opt, val)):
        if opt == '-c':
          self.options.categories.update(self.cats_from_str(val))
        elif opt == '-d':
          try:
            dt = pfx_call(datetime.fromisoformat, val)
          except ValueError as e:
            # pylint: disable=raise-missing-from
            raise GetoptError("unparsed date: %s" % (e,))
          else:
            if dt.tzinfo is None:
              # create a nonnaive datetime in the local zone
              dt = dt.astimezone()
            self.options.when = datetime2unixtime(dt)
        else:
          raise RuntimeError("unimplemented option")
    # Gather leading CAT: and tag= arguments
    while argv:
      arg0 = argv[0]
      with Pfx(repr(arg0)):
        # CATS,...:
        m = self.CATS_RE.match(arg0)
        if m:
          argv.pop(0)
          options.categories.update(
              cat.lower() for cat in m.group(0)[:-1].split(',') if cat
          )
          continue
        # tag_name=...
        tag_name, offset = get_dotted_identifier(arg0)
        if tag_name and offset < len(arg0) and arg0[offset] == '=':
          argv.pop(0)
          try:
            tag = Tag.from_str(arg0)
          except ValueError as e:
            debug("invalid tag: %s", e)
            options.tags.add(Tag(tag_name, arg0[offset:]))
          else:
            options.tags.add(tag)
          continue
        break
    if badopts:
      raise GetoptError("invalid preargv")
    if not argv:
      raise GetoptError("no headline")
    if not options.categories:
      options.categories.update(
          self.cats_from_str(options.fstags['.'].all_tags.get('cs.dlog', ''))
      )
    headline = ' '.join(argv)
    dlog(
        headline,
        logpath=options.logpath,
        sqltags=sqltags,
        tags=options.tags,
        categories=options.categories,
        when=options.when
    )

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
@typechecked
@fmtdoc
def dlog(
    headline: str,
    *,
    logpath: Optional[str] = None,
    sqltags: Optional[SQLTags] = None,
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
  if sqltags is None:
    # pylint: disable=redefined-argument-from-local
    with SQLTags(expanduser(DEFAULT_DBPATH)) as sqltags:
      dlog(
          headline,
          logpath=logpath,
          sqltags=sqltags,
          tags=tags,
          categories=categories,
          when=when
      )
  if logpath is None:
    logpath = expanduser(DEFAULT_LOGPATH)
  logtags = TagSet()
  if tags:
    for tag in tags:
      logtags.add(tag)
  categories = sorted(
      () if categories is None else set(map(str.lower, categories))
  )
  if when is None:
    when = time.time()
  elif isinstance(when, (int, float)):
    pass
  elif isinstance(when, datetime):
    dt = when
    if dt.tzinfo is None:
      # create a nonnaive datetime in the local zone
      dt = dt.astimezone()
    when = datetime2unixtime(dt)
  else:
    raise TypeError("when=%s:%r: unhandled type" % (type(when).__name__, when))
  tt = time.localtime(when)
  print_args = [
      '{:4d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}'.format(
          tt.tm_year,
          tt.tm_mon,
          tt.tm_mday,
          tt.tm_hour,
          tt.tm_min,
          tt.tm_sec,
      )
  ]
  if categories:
    print_args.append(','.join(categories).upper() + ':')
  print_args.append(headline)
  if logtags:
    print_args.append('[' + ' '.join(map(str, logtags)) + ']')
  with pfx_call(open, logpath, 'a') as logf:
    print(*print_args, file=logf)
  # add the headline and categories to the tags
  logtags.add('headline', headline)
  if categories:
    logtags.add('categories', sorted(categories))
  sqltags.default_factory(None, unixtime=when, tags=logtags)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

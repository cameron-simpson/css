#!/usr/bin/env python3
#

''' Log a line in my daily log.

    This is an upgrade from my venerable shell script,
    whose logic was becoming unweildy.
'''

from contextlib import contextmanager
from datetime import datetime
from getopt import getopt, GetoptError
from os.path import expanduser
import re
import sys
import time
from typing import Optional, Iterable, Union

from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.dateutils import datetime2unixtime
from cs.deco import fmtdoc
from cs.fstags import FSTags
from cs.lex import get_dotted_identifier
from cs.logutils import debug
from cs.pfx import Pfx, pfx, pfx_call
from cs.sqltags import SQLTags
from cs.tagset import Tag, TagSet

def main(argv=None):
  ''' Run the `dlog` command line implementation.
  '''
  return DLogCommand(argv).run()

DEFAULT_DBPATH = '~/var/sqltags.sqlite'
DEFAULT_LOGPATH = '~/var/log/dlog-quick'

class DLogCommand(BaseCommand):
  ''' The `dlog` command line implementation.
  '''

  CATS_RE = re.compile('^[A-Z][A-Z0-9]*(,+[A-Z][A-Z0-9]*)*:$')

  def apply_defaults(self):
    ''' Set default options:
        * empty `.categories`, a `set`
        * empty `.tags`, a `TagSet`
        * `.when` = `time.time()`
    '''
    self.options.categories = set()
    self.options.dbpath = expanduser(DEFAULT_DBPATH)
    self.options.logpath = expanduser(DEFAULT_LOGPATH)
    self.options.tags = TagSet()
    self.options.when = time.time()

  @contextmanager
  def run_context(self):
    ''' Prepare the logging `SQLTags` around each command invocation.
    '''
    options = self.options
    dbpath = options.dbpath
    with FSTags() as fstags:
      with SQLTags(dbpath) as sqltags:
        with stackattrs(options, fstags=fstags, sqltags=sqltags, verbose=True):
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
          self.cats_from_str(options.fstags['.'].get('cs.dlog', ''))
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
  sqltags.default_factory(None, unixtime=when, tags=tags)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

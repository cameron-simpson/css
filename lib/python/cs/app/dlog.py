#!/usr/bin/env python3
#

''' Log a line in my daily log.

    This is an upgrade from my venerable shell script,
    whose logic was becoming unweildy.
'''
from cs.x import X

from contextlib import contextmanager
from datetime import datetime
from getopt import getopt, GetoptError
from os.path import expanduser
import re
import sys
import time

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.dateutils import datetime2unixtime
from cs.fstags import FSTags
from cs.lex import get_dotted_identifier
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call
from cs.sqltags import SQLTags
from cs.tagset import Tag, TagSet

def main(argv=None):
  ''' Run the `dlog` command line implementation.
  '''
  return DLogCommand(argv).run()

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
    self.options.dbpath = expanduser('~/var/sqltags.sqlite')
    self.options.logpath = expanduser('~/var/log/dlog-quick')
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
          options.categories.update(
              cat.lower() for cat in m.group(0)[:-1].split(',') if cat
          )
          argv.pop(0)
          continue
        # tag_name=...
        tag_name, offset = get_dotted_identifier(arg0)
        if tag_name and offset < len(arg0) and arg0[offset] == '=':
          argv.pop(0)
          try:
            tag = Tag.from_str(arg0)
          except ValueError as e:
            warning("invalid tag: %s", e)
            badopts = True
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
    with pfx_call(open, options.logpath, 'a') as logf:
      tt = time.localtime(options.when)
      print(
          '{:4d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} {}: {} {}'.format(
              tt.tm_year,
              tt.tm_mon,
              tt.tm_mday,
              tt.tm_hour,
              tt.tm_min,
              tt.tm_sec,
              ','.join(options.categories).upper(),
              headline,
              ' '.join(options.tags),
          ),
          file=logf
      )
    # add the headline and categories to the tags
    options.tags.add('headline', headline)
    if options.categories:
      options.tags.add('categories', list(options.categories))
    sqltags.default_factory(None, unixtime=options.when, tags=options.tags)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

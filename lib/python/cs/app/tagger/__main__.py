#!/usr/bin/env python3

from collections import defaultdict
from contextlib import contextmanager
from getopt import GetoptError, getopt
import os
from os.path import (
    exists as existspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
)
from pprint import pprint
import sys
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.fstags import FSTags
from cs.logutils import warning
from cs.pfx import Pfx, pfxprint
from . import Tagger
from .gui import TaggerGUI

def main(argv=None):
  ''' Command line for the tagger.
  '''
  return TaggerCommand(argv).run()

class TaggerCommand(BaseCommand):
  ''' Tagger command line implementation.
  '''

  SUBCOMMAND_ARGV_DEFAULT = 'gui'

  @contextmanager
  def run_context(self):
    ''' Set up around commands.
    '''
    options = self.options
    with FSTags() as fstags:
      tagger = Tagger(fstags=fstags)
      with stackattrs(options, tagger=tagger, fstags=fstags):
        yield

  @staticmethod
  def _autofile(path, *, tagger, no_link, do_remove):
    ''' Wrapper for `Tagger.file_by_tags` which reports actions.
    '''
    if not no_link and not existspath(path):
      warning("no such path, skipped")
      linked_to = []
    else:
      linked_to = tagger.file_by_tags(
          path, no_link=no_link, do_remove=do_remove
      )
      if linked_to:
        for linked in linked_to:
          pfxprint('=>', linked)
      else:
        ##pfxprint('not filed')
        pass
    return linked_to

  def cmd_autofile(self, argv):
    ''' Usage: {cmd} pathnames...
          Link pathnames to destinations based on their tags.
          -d    Treat directory pathnames like file - file the
                directory, not its contents.
                (TODO: we file by linking - this needs a rename.)
          -n    No link (default). Just print filing actions.
          -r    Recurse. Required to autofile a directory tree.
          -x    Remove the source file if linked successfully. Implies -y.
          -y    Link files to destinations.
    '''
    direct = False
    recurse = False
    no_link = True
    do_remove = False
    opts, argv = getopt(argv, 'dnrxy')
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-d':
          direct = True
        elif opt == 'n':
          no_link = True
          do_remove = False
        elif opt == '-r':
          recurse = True
        elif opt == '-x':
          no_link = False
          do_remove = True
        elif opt == '-y':
          no_link = False
        else:
          raise RuntimeError("unimplemented option")
    if not argv:
      raise GetoptError("missing pathnames")
    tagger = self.options.tagger
    fstags = tagger.fstags
    for path in argv:
      with Pfx(path):
        if direct or not isdirpath(path):
          self._autofile(
              path, tagger=tagger, no_link=no_link, do_remove=do_remove
          )
        elif not recurse:
          pfxprint("not autofiling directory, use -r for recursion")
        else:
          for subpath, dirnames, filenames in os.walk(path):
            with Pfx(subpath):
              # order the descent
              dirnames[:] = sorted(
                  dname for dname in dirnames
                  if dname and not dname.startswith('.')
              )
              tagged = fstags[subpath]
              if 'tagger.skip' in tagged:
                # prune this directory tree
                dirnames[:] = []
                continue
              for filename in sorted(filenames):
                with Pfx(filename):
                  filepath = joinpath(subpath, filename)
                  if not isfilepath(filepath):
                    pfxprint("not a regular file, skipping")
                    continue
                  self._autofile(
                      filepath,
                      tagger=tagger,
                      no_link=no_link,
                      do_remove=do_remove,
                  )

  def cmd_derive(self, argv):
    ''' Usage: {cmd} dirpaths...
          Derive an autofile mapping of tags to directory paths
          from the directory paths suppplied.
    '''
    if not argv:
      raise GetoptError("missing dirpaths")
    tagger = self.options.tagger
    mapping = defaultdict(list)
    tag_names = 'abn', 'invoice', 'vendor'
    for path in argv:
      print("scan", path)
      mapping = tagger.auto_file_map(path, tag_names, mapping)
      pprint(mapping)

  def cmd_gui(self, argv):
    ''' Usage: {cmd} pathnames...
          Run a GUI to tag pathnames.
    '''
    if not argv:
      raise GetoptError("missing pathnames")
    with TaggerGUI(self.options.tagger, argv) as gui:
      gui.run()

  def cmd_suggest(self, argv):
    ''' Usage: {cmd} pathnames...
          Suggest tags for each pathname.
    '''
    if not argv:
      raise GetoptError("missing pathnames")
    for path in argv:
      print(path)
      for tag_name, values in sorted(
          self.options.tagger.suggested_tags(path).items()):
        print(" ", tag_name, *sorted(values))

  def cmd_test(self, argv):
    ''' Usage: {cmd} path
          Run a test against path.
          Current we try out the suggestions.
    '''
    if not argv:
      raise GetoptError("missing path")
    path = argv.pop(0)
    if argv:
      raise GetopError("extra arguments: %r" % (argv,))
    tagger = self.options.tagger
    fstags = self.options.fstags
    tagged = fstags[path]
    changed = True
    while True:
      print(path, *tagged)
      if changed:
        suggestions = tagger.suggested_tags(path)
        for tag_name, values in sorted(suggestions.items()):
          print(" ", tag_name, values)
        for file_to in tagger.file_by_tags(path, no_link=True):
          print("=>", file_to)
        changed = False
      try:
        action = input("Action? ").strip()
      except EOFError:
        break
      if action:
        with Pfx(repr(action)):
          try:
            if action.startswith('-'):
              tag = Tag.from_str(action[1:].lstrip())
              tagged.discard(tag)
              changed = True
            elif action.startswith('+'):
              tag = Tag.from_str(action[1:].lstrip())
              tagged.add(tag)
              changed = True
            else:
              raise ValueError("unrecognised action")
          except ValueError as e:
            warning("action fails: %s", e)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

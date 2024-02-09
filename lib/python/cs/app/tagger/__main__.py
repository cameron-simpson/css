#!/usr/bin/env python3

''' cs.app.tagger main module.
'''

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from getopt import GetoptError, getopt
import json
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    isabs as isabspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
)
from pprint import pprint
import sys

from cs.cmdutils import BaseCommand, BaseCommandOptions
from cs.context import stackattrs
from cs.edit import edit_obj
from cs.fileutils import shortpath
from cs.fs import HasFSPath
from cs.fstags import FSTags, uses_fstags
from cs.gui_tk import BaseTkCommand
from cs.lex import r
from cs.logutils import warning
from cs.pfx import Pfx, pfxprint, pfx_method
from cs.queues import ListQueue
from cs.seq import unrepeated
from cs.tagset import Tag
from cs.upd import print  # pylint: disable=redefined-builtin

from . import Tagger

def main(argv=None):
  ''' Command line for the tagger.
  '''
  return TaggerCommand(argv).run()

class TaggerCommand(BaseCommand):
  ''' Tagger command line implementation.
  '''

  GETOPT_SPEC = 'd:nqv'

  @dataclass
  class Options(BaseCommandOptions, HasFSPath):
    fspath: str = '.'

  # pylint: disable=no-self-use
  @trace
  def apply_opt(self, opt, val):
    options = self.options
    if opt == '-d':
      options.fspath = val
    elif opt == '-n':
      options.dry_run = True
    elif opt == '-q':
      options.quiet = True
    elif opt == '-v':
      options.verbose = True
    else:
      raise RuntimeError(f'unhandled option: {opt!r}')

  @contextmanager
  @uses_fstags
  def run_context(self, *, fstags: FSTags):
    ''' Set up around commands.
    '''
    with super().run_context():
      options = self.options
      with fstags:
        tagger = Tagger(options.fspath)
        with tagger:
          with stackattrs(options, tagger=tagger):
            yield

  def tagger_for(self, fspath):
    ''' Return the `Tagger` for the filesystem path `fspath`.
    '''
    return self.options.tagger.tagger_for(fspath)

  # pylint: disable=too-many-branches,too-many-locals
  def cmd_autofile(self, argv):
    ''' Usage: {cmd} [-dnrx] paths...
          Link paths to destinations based on their tags.
          -d    Treat directory paths like files - file the
                directory, not its contents.
                (TODO: we file by linking - this needs a rename.)
          -n    No link (default). Just print filing actions.
          -r    Recurse. Required to autofile a directory tree.
          -x    Remove the source file if linked successfully. Implies -y.
          -y    Link files to destinations.
    '''
    options = self.options
    tagger = options.tagger
    direct = False
    recurse = False
    no_link = True
    do_remove = False
    opts, argv = getopt(argv, 'dnrxy')
    for opt, _ in opts:
      with Pfx(opt):
        if opt == '-d':
          direct = True
        elif opt == '-n':
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
      raise GetoptError("missing paths")
    q = ListQueue(argv, unique=True)
    for path in q:
      with Pfx(path):
        if not existspath(path):
          warning("no such path, skipping")
          continue
        if isdirpath(path) and not direct:
          if recurse:
            # queue the directory entries
            for entry in sorted(
                os.scandir(path),
                key=lambda entry: entry.name,
                reverse=True,
            ):
              if entry.name.startswith('.'):
                continue
              if (entry.is_dir(follow_symlinks=False)
                  or entry.is_file(follow_symlinks=False)):
                q.prepend((joinpath(path, entry.name),))
          else:
            warning("recursion disabled, skipping")
        else:
          linked_to = tagger.file_by_tags(
              path, no_link=no_link, do_remove=do_remove
          )
          for linkpath in linked_to:
            print(shortpath(path), '=>', shortpath(linkpath))
    return 0

  def cmd_autotag(self, argv):
    ''' Usage: {cmd} [-fn] paths...
          Apply the inference rules to each path.
          -f  Force. Overwrite existing tags.
          -n  No action. Recite inferred tags.
    '''
    infer_mode = 'infill'
    opts, argv = getopt(argv, 'fn')
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-f':
          infill_mode = 'overwrite'
        elif opt == '-n':
          infill_mode = 'infer'
        else:
          raise RuntimeError("unhandled option")
    if not argv:
      raise GetoptError("missing paths")
    for path in argv:
      with Pfx(path):
        print(path)
        tagger = self.tagger_for(dirname(path))
        print("  tagger =", tagger)
        print(" ", repr(tagger.conf))
        for tag in tagger.infer_tags(path, mode=infer_mode):
          print(" ", tag)
    return 0

  def cmd_conf(self, argv):
    ''' Usage: {cmd} [dirpath]
          Edit the tagger.file_by mapping for the default directory.
          dirpath    Edit the mapping for a different directory.
    '''
    options = self.options
    dirpath = options.fspath
    if argv:
      dirpath = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    if not isdirpath(dirpath):
      raise GetoptError("dirpath is not a directory: %r" % (dirpath,))
    tagger = self.tagger_for(dirpath)
    tagged = tagger.tagged
    conf = tagger.conf
    obj = conf.as_dict()
    edited = edit_obj(obj)
    for cf, value in edited.items():
      conf[cf] = value
    for cf in list(conf.keys()):
      if cf not in edited:
        del conf[cf]
    print(json.dumps(conf.as_dict(), sort_keys=True, indent=4))
    return 0

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
      mapping = tagger.per_tag_auto_file_map(path, tag_names)
      pprint(mapping)
    return 0

  def cmd_gui(self, argv):
    ''' Usage: {cmd} paths...
          Run a GUI to tag paths.
    '''
    if not argv:
      raise GetoptError("missing paths")
    from .gui_tk import main as gui_main  # pylint: disable=import-outside-toplevel
    return gui_main([self.cmd, *argv])

  def cmd_ont(self, argv):
    ''' Usage: {cmd} type_name
    '''
    tagger = self.options.tagger
    if not argv:
      raise GetoptError("missing type_name")
    type_name = argv.pop(0)
    with Pfx("type %r", type_name):
      if argv:
        raise GetoptError("extra arguments: %r" % (argv,))
    print(type_name)
    for type_value in tagger.ont_values(type_name):
      ontkey = f"{type_name}.{type_value}"
      with Pfx("ontkey = %r", ontkey):
        print(" ", r(type_value), tagger.ont[ontkey])

  def cmd_suggest(self, argv):
    ''' Usage: {cmd} paths...
          Suggest tags for each path.
    '''
    if not argv:
      raise GetoptError("missing paths")
    tagger = self.options.tagger
    for path in argv:
      print()
      print(path)
      for tag_name, values in sorted(tagger.suggested_tags(path).items()):
        print(" ", tag_name, repr(sorted(values)))

  def cmd_tagmap(self, argv):
    ''' Usage: {cmd} [dirpath]
          List the tag map for `dirpath`, default `'.'`.
    '''
    dirpath = '.'
    if argv:
      dirpath = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    if not isdirpath(dirpath):
      raise GetoptError("not a directory: %r" % (dirpath,))
    tagger = Tagger(dirpath)
    for tag_name, submap in sorted(tagger.subdir_tag_map().items()):
      for tag_value, paths in submap.items():
        print(
            Tag(tag_name, tag_value),
            repr([shortpath(path) for path in paths])
        )

  @uses_fstags
  def cmd_test(self, argv, *, fstags: FSTags):
    ''' Usage: {cmd} path
          Run a test against path.
          Current we try out the suggestions.
    '''
    tagger = self.options.tagger
    if not argv:
      raise GetoptError("missing path")
    path = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    tagged = fstags[path]
    changed = True
    while True:
      print(path, *tagged)
      if changed:
        changed = False
        suggestions = tagger.suggested_tags(path)
        for tag_name, values in sorted(suggestions.items()):
          print(" ", tag_name, repr(values))
        for file_to in tagger.file_by_tags(path, no_link=True):
          print("=>", shortpath(file_to))
        print("inferred:", repr(tagger.infer(path)))
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

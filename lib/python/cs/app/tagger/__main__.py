#!/usr/bin/env python3

''' cs.app.tagger main module.
'''

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from getopt import GetoptError
import json
import os
from os.path import (
    basename,
    dirname,
    isdir as isdirpath,
    join as joinpath,
    realpath,
)
from pprint import pprint
import sys

from cs.cmdutils import BaseCommand, popopts
from cs.context import contextif, stackattrs
from cs.edit import edit_obj
from cs.fileutils import shortpath
from cs.fs import HasFSPath
from cs.fstags import FSTags, uses_fstags
from cs.hashindex import HASHNAME_DEFAULT
from cs.lex import r
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call
from cs.queues import ListQueue
from cs.resources import RunState, uses_runstate
from cs.tagset import Tag
from cs.upd import print, run_task  # pylint: disable=redefined-builtin

from . import Tagger
from .rules import RULE_MODES

def main(argv=None):
  ''' Command line for the tagger.
  '''
  return TaggerCommand(argv).run()

class TaggerCommand(BaseCommand):
  ''' Tagger command line implementation.
  '''

  USAGE_KEYWORDS = {
      'HASHNAME_DEFAULT': HASHNAME_DEFAULT,
      'RULE_MODES': RULE_MODES,
  }

  @dataclass
  class Options(BaseCommand.Options, HasFSPath):
    fspath: str = '.'
    hashname: str = HASHNAME_DEFAULT

    # pylint: disable=use-dict-literal
    COMMON_OPT_SPECS = dict(
        **BaseCommand.Options.COMMON_OPT_SPECS,
        d_=('fspath', "The reference directory, default '.'."),
        h_=('hashname', 'The file content hash algorithm name.'),
    )

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
  @uses_runstate
  @popopts(
      _1='once',
      d=(
          'direct',
          'Treat directory paths like files - file the directory, not its contents.'
      ),
      f='force',
      M_=(
          'modes',
          ''' Only apply actions in modes, a comma separated list of modes
              from {RULE_MODES!r}.
          ''',
      ),
      r=('recurse', 'Recurse. Required to autofile a directory tree.'),
      y=('doit', 'Yes: link files to destinations.'),
  )
  def cmd_autofile(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-dnry] paths...
          Link paths to destinations based on their tags.
    '''
    if not argv:
      raise GetoptError("missing paths")
    options = self.options
    modes = options.modes
    if modes is None:
      modes = RULE_MODES
    else:
      modes = modes.split(',')
    if not all([mode in RULE_MODES for mode in modes]):
      raise GetoptError(f'invalid modes not in {RULE_MODES!r}: {modes!r}')
    direct = options.direct
    once = options.once
    recurse = options.recurse
    quiet = options.quiet
    taggers = set()
    ok = True
    xit = 0
    limit = 1 if once else None
    q = ListQueue(argv, unique=realpath)
    with contextif(not quiet, run_task, 'autofile') as proxy:
      for path in q:
        runstate.raiseif()
        with Pfx(path):
          if proxy: proxy.text = shortpath(path)
          if not direct and isdirpath(path):
            if recurse:
              # queue children
              q.extend(
                  [
                      joinpath(path, base)
                      for base in sorted(pfx_call(os.listdir, path))
                      if not base.startswith('.')
                  ]
              )
            # do not autofile directories
            continue
          tagger = Tagger(dirname(path))
          taggers.add(tagger)  # remember for reuse
          matches = tagger.process(basename(path))
          if matches:
            for match in matches:
              if match.filed_to:
                # process the filed paths ahead of the pending stuff
                # raise limit to process this file in the filed_to places
                q.prepend(match.filed_to)
                if limit is not None:
                  limit += len(match.filed_to)
            if limit is not None:
              # now drop the limit by 1
              limit -= 1
              if limit < 1:
                # we're done
                break
          continue
    return xit

  @popopts(f=('force', 'Force. Overwrite existing tags.'))
  def cmd_autotag(self, argv):
    ''' Usage: {cmd} paths...
          Apply the inference rules to each path.
    '''
    if not argv:
      raise GetoptError("missing paths")
    options = self.options
    infer_mode = 'overwrite' if options.force else 'infill'
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
          Derive an autofile mapping of tags to directory paths.
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
          Print ontology information about type_name.
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

  def cmd_show(self, argv):
    ''' Usage: {cmd} rules
          Show the filing rules.
    '''
    dirpath = '.'
    if not argv:
      argv = [
          'rules',
      ]
    tagger = Tagger(dirpath)
    rcfile = tagger.rcfile
    if rcfile is None:
      warning("no rcfile for %s", tagger)
    else:
      print(shortpath(rcfile))
      for n, rule in enumerate(tagger.rules, 1):
        print(n, rule)

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

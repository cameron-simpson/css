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
    realpath,
)
from pprint import pprint
from stat import S_ISDIR, S_ISREG
import sys

from cs.cmdutils import BaseCommand, BaseCommandOptions
from cs.context import stackattrs
from cs.edit import edit_obj
from cs.fileutils import shortpath
from cs.fs import HasFSPath
from cs.fstags import FSTags, uses_fstags
from cs.gui_tk import BaseTkCommand
from cs.hashindex import HASHNAME_DEFAULT
from cs.lex import r
from cs.logutils import warning
from cs.pfx import Pfx, pfxprint, pfx_call, pfx_method
from cs.queues import ListQueue
from cs.resources import RunState, uses_runstate
from cs.seq import unrepeated
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

  GETOPT_SPEC = 'd:nqv'

  USAGE_FORMAT = '''Usage: {cmd} [-d dirpath] [-nqv] subcommand [subargs...]
    -d dirpath  Specify the reference directory, default '.'.
    -h hashname Specify the content hash algorithm name, default: {HASHNAME_DEFAULT}.
    -n          No action, dry run.
    -q          Quiet.
    -v          Verbose.'''

  USAGE_KEYWORDS = {
      'HASHNAME_DEFAULT': HASHNAME_DEFAULT,
      'RULE_MODES': RULE_MODES,
  }

  @dataclass
  class Options(BaseCommand.Options, HasFSPath):
    fspath: str = '.'
    hashname: str = HASHNAME_DEFAULT

  # pylint: disable=no-self-use
  def apply_opt(self, opt, val):
    options = self.options
    if opt == '-d':
      options.fspath = val
    if opt == '-h':
      options.hashname = val
    elif opt == '-n':
      options.dry_run = True
    elif opt == '-q':
      options.quiet = True
    elif opt == '-v':
      options.verbose = True
    else:
      raise NotImplementedError(f'unsupported option: {opt!r}')

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
  def cmd_autofile(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-dnrx] paths...
          Link paths to destinations based on their tags.
          -d    Treat directory paths like files - file the
                directory, not its contents.
                (TODO: we file by linking - this needs a rename.)
          -h hashname
                Specify the content hash algorithm name, default: {HASHNAME_DEFAULT}.
          -M modes
                Only apply actions in modes, a comma separated list of modes
                from {RULE_MODES!r}.
          -n    No action (default). Just print filing actions.
          -r    Recurse. Required to autofile a directory tree.
          -y    Link files to destinations.
    '''
    options = self.options
    options.direct = False
    options.hashname = HASHNAME_DEFAULT
    options.modes = ",".join(RULE_MODES)
    options.once = False
    options.recurse = False
    options.popopts(
        argv,
        _1='once',
        d='direct',
        h='hashname',
        n='dry_run',  # no action
        M_=('modes', str),
        r='recurse',
        y='doit',  # inverse of -n
        v='verbose',
    )
    if not argv:
      raise GetoptError("missing paths")
    doit = options.doit
    direct = options.direct
    hashname = options.hashname
    modes = options.modes.split(',')
    if not all([mode in RULE_MODES for mode in modes]):
      raise GetoptError(f'invalid modes not in {RULE_MODES!r}: {modes!r}')
    once = options.once
    recurse = options.recurse
    verbose = options.verbose
    taggers = set()
    ok = True
    paths = []
    for path in argv:
      with Pfx(path):
        try:
          S = os.stat(path)
        except OSError as e:
          warning("cannot stat: %s", e)
          ok = False
          continue
        if S_ISREG(S.st_mode):
          paths.append(path)
        elif S_ISDIR(S.st_mode):
          if direct:
            paths.append(path)
          else:
            paths.extend(
                [
                    joinpath(path, base)
                    for base in sorted(pfx_call(os.listdir, path))
                    if not base.startswith('.')
                ]
            )
        else:
          warning("unhandled file type ignored")
    xit = 0
    limit = 1 if once else None
    q = ListQueue(paths, unique=realpath)
    with run_task('autofile') as proxy:
      for path in q:
        runstate.raiseif()
        with Pfx(path):
          proxy.text = shortpath(path)
          if not existspath(path):
            continue
          if isdirpath(path) and not direct:
            if recurse:
              # queue children
              q.extend(
                  [
                      joinpath(path, base)
                      for base in sorted(pfx_call(os.listdir, path))
                      if not base.startswith('.')
                  ]
              )
            continue
          if isfilepath(path) or (direct and isdirpath(path)):
            tagger = Tagger(dirname(path))
            taggers.add(tagger)  # remember for reuse
            matches = tagger.process(
                basename(path),
                hashname=hashname,
                modes=modes,
                doit=doit,
                verbose=verbose,
            )
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
          warning("not a regular file, skipping")
          xit = 1
          continue
    return xit

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
          raise NotImplementedError("unhandled option")
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
    print(shortpath(tagger.rcfile))
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

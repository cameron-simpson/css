#!/usr/bin/env python3

''' Simple filesystem based file tagging
    and the associated `fstags` command line script.

    Why `fstags`?
    By storing the tags in a separate file we:
    * can store tags without modifying a file
    * do no need to know the file's format,
      whether that supports metadata or not
    * can process tags on any kind of file
    * because tags are inherited from parent directories,
      tags can be automatically acquired merely by arranging your file tree

    Tags are stored in the file `.fstags` in each directory;
    there is a line for each entry in the directory with tags
    consisting of the directory entry name and the associated tags.

    Tags may be "bare", or have a value.
    If there is a value it is expressed with an equals (`'='`)
    followed by the JSON encoding of the value.

    The tags for a file are the union of its direct tags
    and all relevant ancestor tags,
    with priority given to tags closer to the file.

    For example, a media file for a television episode with the pathname
    `/path/to/series-name/season-02/episode-name--s02e03--something.mp4`
    might obtain the tags:

        series.title="Series Full Name"
        season=2
        sf
        episode=3
        episode.title="Full Episode Title"

    from the following `.fstags` entries:
    * tag file `/path/to/.fstags`:
      `series-name sf series.title="Series Full Name"`
    * tag file `/path/to/series-name/.fstags`:
      `season-02 season=2`
    * tag file `/path/to/series-name/season-02/.fstags`:
      `episode-name--s02e03--something.mp4 episode=3 episode.title="Full Episode Title"`
'''

from collections import defaultdict, namedtuple
from configparser import ConfigParser
import errno
from getopt import getopt, GetoptError
import json
import os
from os.path import (
    abspath, basename, dirname, exists as existspath, expanduser, isdir as
    isdirpath, join as joinpath, realpath, relpath, samefile
)
from pathlib import PurePath
import re
import shutil
import sys
import threading
from threading import Lock, RLock
from icontract import require
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.edit import edit_strings
from cs.lex import get_nonwhite
from cs.logutils import setup_logging, error, warning, info, trace
from cs.pfx import Pfx, pfx_method
from cs.resources import MultiOpenMixin
from cs.tagset import TagSet, Tag, TagChoice
from cs.threads import locked, locked_property
from cs.upd import Upd

__version__ = '20200210'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts': ['fstags = cs.fstags:main'],
    },
    'install_requires': [
        'cs.cmdutils', 'cs.context', 'cs.deco', 'cs.edit', 'cs.lex',
        'cs.logutils', 'cs.pfx', 'cs.resources', 'cs.tagset', 'cs.threads',
        'cs.upd', 'icontract'
    ],
}

TAGSFILE = '.fstags'
RCFILE = '~/.fstagsrc'

XATTR_B = (
    b'user.cs.fstags'
    if hasattr(os, 'getxattr') and hasattr(os, 'setxattr') else None
)

FIND_OUTPUT_FORMAT_DEFAULT = '{filepath}'
LS_OUTPUT_FORMAT_DEFAULT = '{filepath_encoded} {tags}'

def main(argv=None):
  ''' Command line mode.
  '''
  if argv is None:
    argv = sys.argv
  return FSTagsCommand().run(argv)

class _State(threading.local):
  ''' Per-thread default context stack.
  '''

  def __init__(self, **kw):
    threading.local.__init__(self)
    for k, v in kw.items():
      setattr(self, k, v)

state = _State(verbose=False)

def verbose(msg, *a):
  ''' Emit message if in verbose mode.
  '''
  if state.verbose:
    trace(msg, *a)

class FSTagsCommand(BaseCommand):
  ''' `fstags` main command line class.
  '''

  GETOPT_SPEC = ''
  USAGE_FORMAT = '''Usage:
    {cmd} autotag paths...
        Tag paths based on rules from the rc file.
    {cmd} cp [-fnv] srcpath dstpath
    {cmd} cp [-fnv] srcpaths... dstdirpath
        Copy files and their tags into targetdir.
        -f  Force: remove destination if it exists.
        -n  No remove: fail if the destination exists.
        -v  Verbose: show copied files.
    {cmd} scrub paths...
        Remove all tags for missing paths.
        If a path is a directory, scrub the immediate paths in the directory.
    {cmd} find [--for-rsync] path {{tag[=value]|-tag}}...
        List files from path matching all the constraints.
        -d          treat directories like files (do no recurse).
        --direct    Use direct tags instead of all tags.
        --for-rsync Instead of listing matching paths, emit a
                    sequence of rsync(1) patterns suitable for use with
                    --include-from in order to do a selective rsync of the
                    matched paths.
        -o output_format
                    Use output_format as a Python format string to lay out
                    the listing.
                    Default: ''' + FIND_OUTPUT_FORMAT_DEFAULT.replace(
      '{', '{{'
  ).replace('}', '}}') + '''
    {cmd} json_import --prefix=tag_prefix {{-|path}} {{-|tags.json}}
        Apply JSON data to path.
        A path named "-" indicates that paths should be read from
        the standard input.
        The JSON tag data come from the file "tags.json"; the name
        "-" indicates that the JSON data should be read from the
        standard input.
    {cmd} ln [-fnv] srcpath dstpath
    {cmd} ln [-fnv] srcpaths... dstdirpath
        Link files and their tags into targetdir.
        -f  Force: remove destination if it exists.
        -n  No remove: fail if the destination exists.
        -v  Verbose: show linked files.
    {cmd} ls [--direct] [-o output_format] [paths...]
        List files from paths and their tags.
        --direct    List direct tags instead of all tags.
        -o output_format
                    Use output_format as a Python format string to lay out
                    the listing.
                    Default: ''' + LS_OUTPUT_FORMAT_DEFAULT.replace(
      '{', '{{'
  ).replace(
      '}', '}}'
  ) + '''
    {cmd} mv [-fnv] srcpath dstpath
    {cmd} mv [-fnv] srcpaths... dstdirpath
        Move files and their tags into targetdir.
        -f  Force: remove destination if it exists.
        -n  No remove: fail if the destination exists.
        -v  Verbose: show moved files.
    {cmd} tag {{-|path}} {{tag[=value]|-tag}}...
        Associate tags with a path.
        With the form "-tag", remove the tag from the immediate tags.
        A path named "-" indicates that paths should be read from the
        standard input.
    {cmd} tagpaths {{tag[=value]|-tag}} {{-|paths...}}
        Associate a tag with multiple paths.
        With the form "-tag", remove the tag from the immediate tags.
        A single path named "-" indicates that paths should be read
        from the standard input.
    {cmd} test [--direct] path {{tag[=value]|-tag}}...
        Test whether the path matches all the constraints.
        --direct    Use direct tags instead of all tags.
    {cmd} xattr_import {{-|paths...}}
        Import tag information from extended attributes.
    {cmd} xattr_export {{-|paths...}}
        Update extended attributes from tags.
  '''

  def apply_defaults(self, options):
    ''' Set up the default values in `options`.
    '''
    setup_logging(options.cmd)
    options.fstags = FSTags()

  @staticmethod
  def parse_tag_choices(argv):
    ''' Parse a list of tag specifications of the form:
        * `-`*tag_name*: a negative requirement for *tag_name*
        * *tag_name*[`=`*value*]: a positive requirement for a *tag_name*
          with optional *value*.
        Return a list of `(arg,choice,Tag)` for each `arg` in `argv`.
    '''
    choices = []
    for arg in argv:
      with Pfx(arg):
        choice, offset = TagChoice.parse(arg)
        if offset < len(arg):
          raise ValueError("unparsed: %r" % (arg[offset:],))
        choices.append(choice)
    return choices

  @staticmethod
  def cmd_autotag(argv, options, *, cmd):
    ''' Tag paths based on rules from the rc file.
    '''
    fstags = options.fstags
    if not argv:
      argv = ['.']
    filename_rules = fstags.config.filename_rules
    with stackattrs(state, verbose=True):
      with fstags:
        with Upd(sys.stderr) as U:
          for top_path in argv:
            for isdir, path in rpaths(top_path, yield_dirs=True):
              U.out(path)
              with Pfx(path):
                tagged_path = fstags[path]
                direct_tags = tagged_path.direct_tags
                all_tags = tagged_path.merged_tags()
                for autotag in tagged_path.infer_from_basename(filename_rules):
                  U.out(path + ' ' + str(autotag))
                  if autotag not in all_tags:
                    direct_tags.add(autotag, verbose=state.verbose)
                if not isdir:
                  try:
                    S = os.stat(path)
                  except OSError:
                    pass
                  else:
                    direct_tags.add('filesize', S.st_size)
                # update the
                all_tags = tagged_path.merged_tags()
                cascaded = set()
                for cascade_rule in fstags.config.cascade_rules:
                  if cascade_rule.target in direct_tags:
                    continue
                  if cascade_rule.target in cascaded:
                    continue
                  tag = cascade_rule.infer_tag(all_tags)
                  if tag is None:
                    continue
                  if tag not in all_tags:
                    direct_tags.add(tag)

  @staticmethod
  def cmd_edit(argv, options, *, cmd):
    ''' Edit filenames and tags in a directory.
    '''
    fstags = options.fstags
    xit = 0
    if not argv:
      path = '.'
    else:
      path = argv.pop(0)
      if argv:
        raise GetoptError("extra arguments after path: %r" % (argv,))
    with Pfx(path):
      if not isdirpath(path):
        error("not a directory")
        return 1
    with stackattrs(state, verbose=True):
      with fstags:
        if not fstags.edit_dirpath(path):
          xit = 1
    return xit

  @classmethod
  def cmd_find(cls, argv, options, *, cmd):
    ''' Find paths matching tag criteria.
    '''
    fstags = options.fstags
    badopts = False
    use_direct_tags = False
    as_rsync_includes = False
    output_format = FIND_OUTPUT_FORMAT_DEFAULT
    options, argv = getopt(argv, 'o:', longopts=['direct', 'for-rsync'])
    for option, value in options:
      with Pfx(option):
        if option == '--direct':
          use_direct_tags = True
        elif option == '--for-rsync':
          as_rsync_includes = True
        elif option == '-o':
          output_format = value
        else:
          raise RuntimeError("unsupported option")
    if not argv:
      warning("missing path")
      badopts = True
    else:
      path = argv.pop(0)
      if not argv:
        warning("missing tag criteria")
        badopts = True
      else:
        try:
          tag_choices = cls.parse_tag_choices(argv)
        except ValueError as e:
          warning("bad tag specifications: %s", e)
          badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    U = Upd(sys.stderr) if sys.stderr.isatty() else None
    filepaths = fstags.find(
        realpath(path), tag_choices, use_direct_tags=use_direct_tags, U=U
    )
    if as_rsync_includes:
      for include in rsync_patterns(filepaths, path):
        print(include)
    else:
      for filepath in filepaths:
        if U:
          oldU = U.out('')
        print(
            output_format.format(
                **fstags[filepath].format_kwargs(direct=use_direct_tags)
            )
        )
        if U:
          U.out(oldU)

  @classmethod
  def cmd_json_import(cls, argv, options, *, cmd):
    ''' Import tags for `path` from `tags.json`.
    '''
    fstags = options.fstags
    tag_prefix = None
    badopts = False
    options, argv = getopt(argv, '', longopts=['prefix='])
    for option, value in options:
      with Pfx(option):
        if option == '--prefix':
          tag_prefix = value
        else:
          raise RuntimeError("unsupported option")
    if tag_prefix is None:
      warning("missing require --prefix")
      badopts = True
    if not argv:
      warning("missing path")
      badopts = True
    else:
      path = argv.pop(0)
    if not argv:
      warning("missing tags.json")
      badopts = True
    else:
      json_path = argv.pop(0)
    if path == '-' and json_path == '-':
      warning('path and tags.pjson may not both be "-"')
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    if path == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = [path]
    if json_path == '-':
      with Pfx("json.load(sys.stdin)"):
        data = json.load(sys.stdin)
    else:
      with Pfx("open(%r)", json_path):
        with open(json_path) as f:
          with Pfx("json.load"):
            data = json.load(f)
    if not isinstance(data, dict):
      error("JSON data do not specify a dict: %s", type(dict))
      return 1
    with stackattrs(state, verbose=True):
      with fstags:
        for path in paths:
          with Pfx(path):
            tagged_path = fstags[path]
            for key, value in data.items():
              tag_name = '.'.join((tag_prefix, key)) if tag_prefix else key
              tagged_path.direct_tags.add(Tag(tag_name, value))
    return 0

  @staticmethod
  def cmd_ls(argv, options, *, cmd):
    ''' List paths and their tags.
    '''
    fstags = options.fstags
    directories_like_files = False
    use_direct_tags = False
    output_format = LS_OUTPUT_FORMAT_DEFAULT
    options, argv = getopt(argv, 'do:', longopts=['direct'])
    for option, value in options:
      with Pfx(option):
        if option == '-d':
          directories_like_files = True
        elif option == '--direct':
          use_direct_tags = True
        elif option == '-o':
          output_format = value
        else:
          raise RuntimeError("unsupported option")
    paths = argv or ['.']
    for path in paths:
      with Pfx(path):
        fullpath = realpath(path)
        for filepath in ((fullpath,)
                         if directories_like_files else rfilepaths(fullpath)):
          print(
              output_format.format(
                  **fstags[filepath].format_kwargs(direct=use_direct_tags)
              )
          )

  def cmd_cp(self, argv, options, *, cmd):
    ''' POSIX cp equivalent, but copying the tags.
    '''
    return self._cmd_mvcpln(options.fstags.copy, argv, options)

  def cmd_ln(self, argv, options, *, cmd):
    ''' POSIX ln equivalent, but copying the tags.
    '''
    return self._cmd_mvcpln(options.fstags.link, argv, options)

  def cmd_mv(self, argv, options, *, cmd):
    ''' POSIX mv equivalent, but copying the tags.
    '''
    return self._cmd_mvcpln(options.fstags.move, argv, options)

  @staticmethod
  def _cmd_mvcpln(attach, argv, options):
    ''' Move/copy/link paths and their tags into a destination.
    '''
    xit = 0
    fstags = options.fstags
    cmd_force = False
    cmd_verbose = False
    subopts, argv = getopt(argv, 'fnv')
    for subopt, value in subopts:
      if subopt == '-f':
        cmd_force = True
      elif subopt == '-n':
        cmd_force = False
      elif subopt == '-v':
        cmd_verbose = True
      else:
        raise RuntimeError("unhandled subopt: %r" % (subopt,))
    if len(argv) < 2:
      raise GetoptError("missing paths or targetdir")
    endpath = argv[-1]
    if isdirpath(endpath):
      with stackattrs(state, verbose=True):
        with fstags:
          dirpath = argv.pop()
          for srcpath in argv:
            dstpath = joinpath(dirpath, basename(srcpath))
            try:
              attach(srcpath, dstpath, force=cmd_force)
            except (ValueError, OSError) as e:
              print(e, file=sys.stderr)
              xit = 1
            else:
              if cmd_verbose:
                print(srcpath, '->', dstpath)
    else:
      if len(argv) != 2:
        raise GetoptError(
            "expected exactly 2 arguments if the last is not a directory, got: %r"
            % (argv,)
        )
      with stackattrs(state, verbose=True):
        with fstags:
          srcpath, dstpath = argv
          try:
            attach(srcpath, dstpath, force=cmd_force)
          except (ValueError, OSError) as e:
            print(e, file=sys.stderr)
            xit = 1
          else:
            if cmd_verbose:
              print(srcpath, '->', dstpath)
    return xit

  @classmethod
  def cmd_scrub(cls, argv, options, *, cmd):
    ''' Scrub paths.
    '''
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing paths")
    with stackattrs(state, verbose=True):
      with fstags:
        for path in argv:
          fstags.scrub(path)

  @classmethod
  def cmd_tag(cls, argv, options, *, cmd):
    ''' Tag a path with multiple tags.
    '''
    badopts = False
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing path")
    path = argv.pop(0)
    if not argv:
      raise GetoptError("missing tags")
    try:
      tag_choices = cls.parse_tag_choices(argv)
    except ValueError as e:
      warning("bad tag specifications: %s", e)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    if path == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = [path]
    with stackattrs(state, verbose=True):
      fstags.apply_tag_choices(tag_choices, paths)

  @classmethod
  def cmd_tagpaths(cls, argv, options, *, cmd):
    ''' Tag multiple paths with a single tag.
    '''
    badopts = False
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing tag choice")
    tag_choice = argv.pop(0)
    try:
      tag_choices = cls.parse_tag_choices([tag_choice])
    except ValueError as e:
      warning("bad tag specifications: %s", e)
      badopts = True
    if not argv:
      warning("missing paths")
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    if len(argv) == 1 and argv[0] == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = argv
    with stackattrs(state, verbose=True):
      fstags.apply_tag_choices(tag_choices, paths)

  @classmethod
  def cmd_test(cls, argv, options, *, cmd):
    ''' Find paths matching tag criteria.
    '''
    fstags = options.fstags
    badopts = False
    use_direct_tags = False
    options, argv = getopt(argv, '', longopts=['direct'])
    for option, _ in options:
      with Pfx(option):
        if option == '--direct':
          use_direct_tags = True
        else:
          raise RuntimeError("unsupported option")
    if not argv:
      warning("missing path")
      badopts = True
    else:
      path = argv.pop(0)
      if not argv:
        warning("missing tag criteria")
        badopts = True
      else:
        try:
          tag_choices = cls.parse_tag_choices(argv)
        except ValueError as e:
          warning("bad tag specifications: %s", e)
          badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    return (
        0 if fstags.test(path, tag_choices, use_direct_tags=use_direct_tags)
        else 1
    )

  @classmethod
  def cmd_xattr_export(cls, argv, options, *, cmd):
    ''' Update extended attributes from fstags.
    '''
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing paths")
    if len(argv) == 1 and argv[0] == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = argv
    fstags.export_xattrs(paths)

  @classmethod
  def cmd_xattr_import(cls, argv, options, *, cmd):
    ''' Update fstags from extended attributes.
    '''
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing paths")
    if len(argv) == 1 and argv[0] == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = argv
    with stackattrs(state, verbose=True):
      fstags.import_xattrs(paths)

class FSTags(MultiOpenMixin):
  ''' A class to examine filesystem tags.
  '''

  def __init__(self, tagsfile=None, use_xattrs=None):
    MultiOpenMixin.__init__(self)
    if tagsfile is None:
      tagsfile = TAGSFILE
    if use_xattrs is None:
      use_xattrs = XATTR_B is not None
    self.use_xattrs = use_xattrs
    self.config = FSTagsConfig()
    self.config.tagsfile = tagsfile
    self._tagfiles = {}  # cache of per directory `TagFile`s
    self._tagged_paths = {}  # cache of per abspath `TaggedPath`
    self._lock = RLock()

  def startup(self):
    ''' Stub for startup.
    '''

  def shutdown(self):
    ''' Save any modified tag files on shutdown.
    '''
    for tagfile in self._tagfiles.values():
      try:
        tagfile.save()
      except FileNotFoundError as e:
        error("%s.save: %s", tagfile, e)

  @property
  def tagsfile(self):
    ''' The tag file basename.
    '''
    return self.config.tagsfile

  def __str__(self):
    return "%s(tagsfile=%r)" % (type(self).__name__, self.tagsfile)

  @locked
  def __getitem__(self, path):
    ''' Return the `TaggedPath` for `path`.
    '''
    path = abspath(path)
    tagged_path = self._tagged_paths.get(path)
    if tagged_path is None:
      tagged_path = self._tagged_paths[path] = TaggedPath(path, self)
    return tagged_path

  @locked
  def dir_tagfile(self, dirpath):
    ''' Return the `TagFile` associated with `dirpath`.
    '''
    tagfilepath = joinpath(dirpath, self.tagsfile)
    tagfile = self._tagfiles.get(tagfilepath)
    if tagfile is None:
      tagfile = self._tagfiles[tagfilepath] = TagFile(
          str(tagfilepath), fstags=self
      )
    return tagfile

  def path_tagfiles(self, filepath):
    ''' Return a list of `TagFileEntry`s
        for the `TagFile`s affecting `filepath`
        in order from the root to `dirname(filepath)`
        where `name` is the key within `TagFile`.
    '''
    with Pfx("path_tagfiles(%r)", filepath):
      absfilepath = PurePath(abspath(filepath))
      root, *subparts = absfilepath.parts
      if not subparts:
        raise ValueError("root=%r and no subparts" % (root,))
      tagfiles = []
      current = root
      while subparts:
        next_part = subparts.pop(0)
        tagfiles.append(TagFileEntry(self.dir_tagfile(current), next_part))
        current = joinpath(current, next_part)
      return tagfiles

  def apply_tag_choices(self, tag_choices, paths):
    ''' Apply the `tag_choices` to `paths`.

        Parameters:
        * `tag_choices`:
          an iterable of `Tag` or `(spec,choice,Tag)`;
          the former is equivalent to `(None,True,Tag)`.
          Each item applies or removes a `Tag`
          from each path's direct tags.
        * `paths`:
          an iterable of filesystem paths.
    '''
    tag_choices = [
        TagChoice(str(tag_choice), True, tag_choice)
        if isinstance(tag_choice, Tag) else tag_choice
        for tag_choice in tag_choices
    ]
    with self:
      for path in paths:
        with Pfx(path):
          tagged_path = self[path]
          for spec, choice, tag in tag_choices:
            with Pfx(spec):
              if choice:
                # add tag
                tagged_path.add(tag)
              else:
                # delete tag
                tagged_path.discard(tag)

  def export_xattrs(self, paths):
    ''' Import the extended attributes of `paths`
        and use them to update the fstags.
    '''
    with self:
      for path in paths:
        with Pfx(path):
          self[path].export_xattrs()

  def import_xattrs(self, paths):
    ''' Update the extended attributes of `paths`
        from their fstags.
    '''
    with self:
      for path in paths:
        with Pfx(path):
          self[path].import_xattrs()

  def find(self, path, tag_choices, use_direct_tags=False, U=None):
    ''' Walk the file tree from `path`
        searching for files matching the supplied `tag_choices`.
        Yield the matching file paths.

        Parameters:
        * `path`: the top of the file tree to walk
        * `tag_choices`: an iterable of `TagChoice`s
        * `use_direct_tags`: test the direct_tags if true,
          otherwise the all_tags.
          Default: `False`
    '''
    for _, filepath in rpaths(path, yield_dirs=use_direct_tags, U=U):
      if self.test(filepath, tag_choices, use_direct_tags=use_direct_tags):
        yield filepath

  def test(self, path, tag_choices, use_direct_tags=False):
    ''' Test a path against `tag_choices`.

        Parameters:
        * `path`: path to test
        * `tag_choices`: an iterable of `TagChoice`s
        * `use_direct_tags`: test the direct_tags if true,
          otherwise the all_tags.
          Default: `False`
    '''
    tagged_path = self[path]
    tags = (
        tagged_path.direct_tags if use_direct_tags else tagged_path.all_tags
    )
    ok = True
    for _, choice, tag in tag_choices:
      if choice:
        # tag_choice not present or not with same nonNone value
        if tag not in tags:
          ok = False
          break
      else:
        if tag in tags:
          ok = False
          break
    return ok

  def edit_dirpath(self, dirpath):
    ''' Edit the filenames and tags in a directory.
    '''
    ok = True
    tagfile = self.dir_tagfile(dirpath)
    tagsets = tagfile.tagsets
    names = set(
        name for name in os.listdir(dirpath)
        if (name and name not in ('.', '..') and not name.startswith('.'))
    )
    lines = [tagfile.tags_line(name, tagfile[name]) for name in sorted(names)]
    changed = edit_strings(lines)
    for old_line, new_line in changed:
      old_name, _ = tagfile.parse_tags_line(old_line)
      new_name, new_tags = tagfile.parse_tags_line(new_line)
      with Pfx(old_name):
        if old_name != new_name:
          if new_name in tagsets:
            warning("new name %r already exists", new_name)
            ok = False
            continue
          del tagsets[old_name]
          old_path = joinpath(dirpath, old_name)
          if existspath(old_path):
            new_path = joinpath(dirpath, new_name)
            if not existspath(new_path):
              with Pfx("rename => %r", new_path):
                try:
                  os.rename(old_path, new_path)
                except OSError as e:
                  warning("%s", e)
                  ok = False
                  continue
                info("renamed")
        tagsets[new_name] = new_tags
    tagfile.save()
    return ok

  def scrub(self, path):
    ''' Scrub tags for names which do not exist in the filesystem.
    '''
    with Pfx("scrub %r", path):
      if isdirpath(path):
        tagfile = self.dir_tagfile(path)
        tagsets = tagfile.tagsets
        modified = False
        for name in sorted(tagsets.keys()):
          with Pfx(name):
            subpath = joinpath(path, name)
            if not existspath(subpath):
              verbose("does not exist, delete")
              del tagsets[name]
              modified = True
        if modified:
          tagfile.save()
      elif not existspath(path):
        dirpath = dirname(path)
        base = basename(path)
        tagfile = self.dir_tagfile(dirpath)
        tagsets = tagfile.tagsets
        if base in tagsets:
          verbose("%r: does not exist, deleted", base)
          del tagsets[base]
          tagfile.save()

  @pfx_method
  def copy(self, srcpath, dstpath, force=False):
    ''' Copy `srcpath` to `dstpath`.
    '''
    return self.attach_path(shutil.copy2, srcpath, dstpath, force=force)

  @pfx_method
  def link(self, srcpath, dstpath, force=False):
    ''' Link `srcpath` to `dstpath`.
    '''
    return self.attach_path(os.link, srcpath, dstpath, force=force)

  @pfx_method
  def move(self, srcpath, dstpath, force=False):
    ''' Move `srcpath` to `dstpath`.
    '''
    return self.attach_path(shutil.move, srcpath, dstpath, force=force)

  def attach_path(self, attach, srcpath, dstpath, *, force=False):
    ''' Attach `srcpath` to `dstpath` using the `attach` callable.

        Parameters:
        * `attach`: callable accepting `attach(srcpath,dstpath)`
          to do the desired attachment,
          such as a copy, link or move
        * `srcpath`: the source filesystem object
        * `dstpath`: the destination filesystem object
        * `force`: default `False`.
          If true and the destination exists
          try to remove it before calling `attach`.
          Otherwise if the destination exists
          raise a `ValueError`.
    '''
    with Pfx("%r => %r", srcpath, dstpath):
      if not existspath(srcpath):
        raise ValueError("source does not exist")
      src_taggedpath = self[srcpath]
      dst_taggedpath = self[dstpath]
      if existspath(dstpath):
        if samefile(srcpath, dstpath):
          raise ValueError("these are the same file")
        if force:
          warning("removing existing dstpath: %r", dstpath)
          with Pfx("os.remove(%r)", dstpath):
            os.remove(dstpath)
        else:
          raise ValueError("destination already exists")
      result = attach(srcpath, dstpath)
      old_modified = dst_taggedpath.modified
      for tag in src_taggedpath.direct_tags:
        dst_taggedpath.direct_tags.add(tag)
      try:
        dst_taggedpath.save()
      except OSError as e:
        if e.errno == errno.EACCES:
          warning("save tags: %s", e)
          dst_taggedpath.modified = old_modified
        else:
          raise
      return result

class HasFSTagsMixin:
  ''' Mixin providing a `.fstags` property.
  '''

  _default_fstags = None

  @property
  def fstags(self):
    ''' Return the `.fstags` property,
        default a shared default `FSTags` instance.
    '''
    _fstags = getattr(self, '_fstags')
    if _fstags is None:
      _fstags = self._default_fstags
      if _fstags is None:
        _fstags = self._default_fstags = FSTags()
    return _fstags

  @fstags.setter
  def fstags(self, new_fstags):
    ''' Set the `.fstags` property.
    '''
    self._fstags = new_fstags

class TagFile(HasFSTagsMixin):
  ''' A reference to a specific file containing tags.

      This manages a mapping of `name` => `TagSet`,
      itself a mapping of tag name => tag value.
  '''

  @require(lambda filepath: isinstance(filepath, str))
  def __init__(self, filepath, *, fstags=None):
    if fstags is not None:
      self.fstags = fstags
    self.filepath = filepath
    self.dirpath = dirname(filepath)
    self._lock = Lock()

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.filepath)

  def __getitem__(self, name):
    ''' Return the `TagSet` associated with `name`.
    '''
    return self.tagsets[name]

  @staticmethod
  def encode_name(name):
    ''' Encode `name`.

        If the `name` is not empty and does not start with a double quote
        and contains no whitespace,
        return it as-is
        otherwise JSON encode the name.
    '''
    if name and not name.startswith('"'):
      _, offset = get_nonwhite(name)
      if offset == len(name):
        return name
    return json.dumps(name)

  @staticmethod
  def decode_name(s, offset=0):
    ''' Decode the *name* from the string `s` at `offset` (default `0`).
    '''
    if s.startswith('"'):
      name, suboffset = Tag.JSON_DECODER.raw_decode(s[offset:])
      offset += suboffset
    else:
      name, offset = get_nonwhite(s, offset)
    return name, offset

  def parse_tags_line(self, line):
    ''' Parse a "name tags..." line as from a `.fstags` file,
        return `(name,TagSet)`.
    '''
    name, offset = self.decode_name(line)
    if offset < len(line) and not line[offset].isspace():
      _, offset2 = get_nonwhite(line, offset)
      name = line[:offset2]
      warning(
          "offset %d: expected whitespace, adjusted name to %r", offset, name
      )
      offset = offset2
    if offset < len(line) and not line[offset].isspace():
      warning("offset %d: expected whitespace", offset)
    tags = TagSet.from_line(line, offset)
    return name, tags

  def load_tagsets(self, filepath):
    ''' Load `filepath` and return
        a mapping of `name`=>`tag_name`=>`value`.
    '''
    with Pfx("loadtags(%r)", filepath):
      tagsets = defaultdict(TagSet)
      try:
        with open(filepath) as f:
          with stackattrs(state, verbose=False):
            for lineno, line in enumerate(f, 1):
              with Pfx(lineno):
                line = line.strip()
                if not line or line.startswith('#'):
                  continue
                name, tags = self.parse_tags_line(line)
                tagsets[name] = tags
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise
      return tagsets

  @classmethod
  def tags_line(cls, name, tagmap):
    ''' Transcribe a `name` and its `tagmap` for use as a `.fstags` file line.
    '''
    fields = [cls.encode_name(name)]
    for tag in tagmap:
      fields.append(str(tag))
    return ' '.join(fields)

  @classmethod
  def save_tagsets(cls, filepath, tagsets):
    ''' Save a tagmap to `filepath`.
    '''
    with Pfx("savetags(%r)", filepath):
      trace("SAVE %r", filepath)
      name_tags = sorted(tagsets.items())
      with open(filepath, 'w') as f:
        for name, tags in name_tags:
          if not tags:
            continue
          f.write(cls.tags_line(name, tags))
          f.write('\n')
      for _, tags in name_tags:
        tags.modified = False

  @locked
  def save(self):
    ''' Save the tag map to the tag file.
    '''
    if getattr(self, '_tagsets', None) is None:
      # TagSets never loaded
      return
    if not any(map(lambda tagset: tagset.modified, self._tagsets.values())):
      # no modified TagSets
      return
    self.save_tagsets(self.filepath, self.tagsets)

  @locked_property
  def tagsets(self):
    ''' The tag map from the tag file,
        a mapping of name=>`TagSet`.
    '''
    return self.load_tagsets(self.filepath)

  @property
  def names(self):
    ''' The names from this `TagFile`.
    '''
    return list(self.tagsets.keys())

  @require(lambda name: isinstance(name, str))
  def add(self, name, tag, value=None):
    ''' Add a tag to the tags for `name`.
    '''
    return self[name].add(tag, value, verbose=state.verbose)

  def discard(self, name, tag_name, value=None):
    ''' Discard the tag matching `(tag_name,value)`.
        Return a `Tag` with the old value,
        or `None` if there was no matching tag.
    '''
    return self[name].discard(tag_name, value, verbose=state.verbose)

TagFileEntry = namedtuple('TagFileEntry', 'tagfile name')

class TaggedPath(HasFSTagsMixin):
  ''' Class to manipulate the tags for a specific path.
  '''

  def __init__(self, filepath, fstags=None):
    if fstags is None:
      fstags = self.fstags
    else:
      self.fstags = fstags
    self.filepath = PurePath(filepath)
    self._tagfile_entries = fstags.path_tagfiles(filepath)
    self._lock = Lock()

  def __repr__(self):
    return "%s(%s)" % (type(self).__name__, self.filepath)

  def __str__(self):
    return TagFile.encode_name(str(self.filepath)) + ' ' + str(self.all_tags)

  def __contains__(self, tag):
    ''' Test for the presence of `tag` in the `all_tags`.
    '''
    return tag in self.all_tags

  def format_kwargs(self, *, direct=False):
    ''' Format arguments suitable for `str.format`.
    '''
    filepath = str(self.filepath)
    format_tags = TagSet(defaults=defaultdict(str))
    format_tags.update(self.direct_tags if direct else self.all_tags)
    kwargs = dict(
        basename=basename(filepath),
        filepath=filepath,
        filepath_encoded=TagFile.encode_name(filepath),
        tags=format_tags,
    )
    return kwargs

  @property
  def basename(self):
    ''' The name of the final path component.
    '''
    return self._tagfile_entries[-1].name

  @property
  def direct_tagfile(self):
    ''' The `TagFile` for the final path component.
    '''
    return self._tagfile_entries[-1].tagfile

  @property
  def direct_tags(self):
    ''' The direct `TagSet` for the file.
    '''
    return self.direct_tagfile.tagsets[self.basename]

  @property
  def modified(self):
    ''' The modification state of the `TagSet`.
    '''
    return self.direct_tags.modified

  @modified.setter
  def modified(self, new_modified):
    ''' The modification state of the `TagSet`.
    '''
    self.direct_tags.modified = new_modified

  def save(self):
    ''' Update the associated `TagFile`.
    '''
    self.direct_tagfile.save()

  def merged_tags(self):
    ''' Return the cumulative tags for this path as a `TagSet`
        by merging the tags from the root to the path.
    '''
    tags = TagSet()
    with stackattrs(state, verbose=False):
      for tagfile, name in self._tagfile_entries:
        for tag in tagfile[name]:
          tags.add(tag)
    return tags

  @locked_property
  def all_tags(self):
    ''' Cached cumulative tags for this path as a `TagSet`
        by merging the tags from the root to the path.

        Note that subsequent changes to some path component's `direct_tags`
        will not affect this `TagSet`.
    '''
    return self.merged_tags()

  def add(self, tag, value=None):
    ''' Add the `tag`=`value` to the direct tags.

        If `tag` is not a `str` and `value` is omitted or `None`
        then `tag` should be an object with `.name` and `.value` attributes,
        such as a `Tag`.
    '''
    self.direct_tagfile.add(self.basename, tag, value)

  def discard(self, tag, value=None):
    ''' Discard the `tag`=`value` from the direct tags.

        If `tag` is not a `str` and `value` is omitted or `None`
        then `tag` should be an object with `.name` and `.value` attributes,
        such as a `Tag`.
    '''
    self.direct_tagfile.discard(self.basename, tag, value)

  def pop(self, tag_name):
    ''' Remove the tag named `tag_name` from the direct tags.
        Raises `KeyError` if `tag_name` is not present in the direct tags.
    '''
    self.direct_tags.pop(tag_name)

  def infer_from_basename(self, rules=None):
    ''' Apply `rules` to the basename of this `TaggedPath`,
        return a `TagSet` of inferred `Tag`s.

        Tag values from earlier rules override values from later rules.
    '''
    if rules is None:
      rules = self.fstags.config.filename_rules
    name = self.basename
    tagset = TagSet()
    with stackattrs(state, verbose=False):
      for rule in rules:
        for tag in rule.infer_tags(name):
          if tag.name not in tagset:
            tagset.add(tag)
    return tagset

  @fmtdoc
  def get_xattr_tagset(self, xattr_name=None):
    ''' Return a new `TagSet`
        from the extended attribute `xattr_name` of `self.filepath`.
        The default `xattr_name` is `XATTR_B` (`{XATTR_B!r}`).
    '''
    if xattr_name is None:
      xattr_name = XATTR_B
    xattr_s = get_xattr_value(self.filepath, xattr_name)
    if xattr_s is None:
      return TagSet()
    return TagSet.from_line(xattr_s)

  def import_xattrs(self):
    ''' Update the direct tags from the file's extended attributes.
    '''
    filepath = self.filepath
    xa_tags = self.get_xattr_tagset()
    # import tags from other xattrs if not present
    for xattr_name, tag_name in self.fstags.config['xattr'].items():
      if tag_name not in xa_tags:
        tag_value = get_xattr_value(filepath, xattr_name)
        if tag_value is not None:
          xa_tags.add(tag_name, tag_value)
    # merge with the direct tags
    # if missing from the all_tags
    # TODO: common merge_tags method
    all_tags = self.all_tags
    direct_tags = self.direct_tags
    for tag in xa_tags:
      if tag not in all_tags:
        direct_tags.add(tag)

  def export_xattrs(self):
    ''' Update the extended attributes of the file.
    '''
    filepath = self.filepath
    all_tags = self.all_tags
    direct_tags = self.direct_tags
    update_xattr_value(filepath, XATTR_B, str(direct_tags))
    # export tags to other xattrs
    for xattr_name, tag_name in self.fstags.config['xattr'].items():
      tag_value = all_tags.get(tag_name)
      update_xattr_value(
          filepath, xattr_name, None if tag_value is None else str(tag_value)
      )

class CascadeRule:
  ''' A cascade rule of possible source tag names to provide a target tag.
  '''

  def __init__(self, target, cascade):
    self.target = target
    self.cascade = cascade

  def __str__(self):
    return "%s(%s<=%r)" % (type(self).__name__, self.target, self.cascade)

  def infer_tag(self, tagset):
    ''' Apply the rule to the `TagSet` `tagset`.
        Return a new `Tag(self.target,value)`
        for the first cascade `value` found in `tagset`,
        or `None` if there is no match.
    '''
    for tag_name in self.cascade:
      if tag_name in tagset:
        return Tag(self.target, tagset[tag_name])
    return None

class RegexpTagRule:
  ''' A regular expression based `Tag` rule.
  '''

  def __init__(self, regexp):
    self.regexp_src = regexp
    self.regexp = re.compile(regexp)

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.regexp_src)

  def infer_tags(self, s):
    ''' Apply the rule to the string `s`, return a list of `Tag`s.
    '''
    tags = []
    m = self.regexp.search(s)
    if m:
      for tag_name, value in m.groupdict().items():
        if value is not None:
          # TODO: honour the JSON decode strings
          try:
            value = int(value)
          except ValueError:
            pass
          tags.append(Tag(tag_name, value))
    return tags

def rpaths(path, *, yield_dirs=False, name_selector=None, U=None):
  ''' Recurse over `path`, yielding `(is_dir,subpath)`
      for all selected subpaths.
  '''
  if name_selector is None:
    name_selector = lambda name: name and not name.startswith('.')
  pending = [path]
  while pending:
    dirpath = pending.pop(0)
    U and U.out(dirpath)
    with Pfx(dirpath):
      with Pfx("scandir"):
        try:
          dirents = sorted(os.scandir(dirpath), key=lambda entry: entry.name)
        except NotADirectoryError:
          yield False, dirpath
          continue
        except (FileNotFoundError, PermissionError) as e:
          warning("%s", e)
          continue
      for entry in dirents:
        name = entry.name
        with Pfx(name):
          if not name_selector(name):
            continue
          entrypath = entry.path
          if entry.is_dir(follow_symlinks=False):
            if yield_dirs:
              yield True, entrypath
            pending.append(entrypath)
          else:
            yield False, entrypath

def rfilepaths(path, name_selector=None, U=None):
  ''' Generator yielding pathnames of files found under `path`.
  '''
  return (
      subpath for is_dir, subpath in
      rpaths(path, yield_dirs=False, name_selector=name_selector, U=U)
      if not is_dir
  )

def rsync_patterns(paths, top_path):
  ''' Return a list of rsync include lines
      suitable for use with the `--include-from` option.
  '''
  patterns = []
  include_dirs = set()
  for path in paths:
    path = relpath(path, top_path)
    ancestors = []
    dirpath = dirname(path)
    while dirpath:
      if dirpath in include_dirs:
        break
      ancestors.append(dirpath)
      dirpath = dirname(dirpath)
    for dirpath in reversed(ancestors):
      patterns.append('+ /' + dirpath)
      include_dirs.add(dirpath)
    patterns.append('+ /' + path)
  patterns.append('- *')
  return patterns

class FSTagsConfig:
  ''' A configuration for fstags.
  '''

  @fmtdoc
  def __init__(self, rcfilepath=None):
    ''' Initialise the config.

        Parameters:
        * `rcfilepath`: the path to the confguration file
          If `None`, default to `'{RCFILE}'` (from `RCFILE`).
    '''
    if rcfilepath is None:
      rcfilepath = expanduser(RCFILE)
    self.filepath = rcfilepath

  @pfx_method
  def __getattr__(self, attr):
    if attr == 'config':
      self.config = self.load_config(self.filepath)
      return self.config
    if attr == 'filename_rules':
      self.filename_rules = self.filename_rules_from_config(self.config)
      return self.filename_rules
    if attr == 'cascade_rules':
      self.cascade_rules = self.cascade_rules_from_config(self.config)
      return self.cascade_rules
    raise AttributeError(attr)

  def __getitem__(self, section):
    return self.config[section]

  @staticmethod
  def load_config(rcfilepath):
    ''' Read an rc file, return a ConfigParser instance.
    '''
    with Pfx(rcfilepath):
      config = ConfigParser()
      config.add_section('filename_autotag')
      config.add_section('cascade')
      config.add_section('general')
      config.add_section('xattr')
      config['general']['tagsfile'] = TAGSFILE
      try:
        config.read(rcfilepath)
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise
      return config

  @staticmethod
  def filename_rules_from_config(config):
    ''' Return a list of the `[filename_autotag]` tag rules from the config.
    '''
    rules = []
    for rule_name, pattern in config['filename_autotag'].items():
      with Pfx("%s = %s", rule_name, pattern):
        if pattern.startswith('/') and pattern.endswith('/'):
          rules.append(RegexpTagRule(pattern[1:-1]))
        else:
          warning("invalid autotag rule")
    return rules

  @staticmethod
  def cascade_rules_from_config(config):
    ''' Return a list of the `[cascade]` tag rules from the config.
    '''
    rules = []
    for target, cascade in config['cascade'].items():
      with Pfx("%s = %s", target, cascade):
        rules.append(CascadeRule(target, cascade.split()))
    return rules

  @property
  @fmtdoc
  def tagsfile(self):
    ''' The tags filename, default `{TAGSFILE!r}`.
    '''
    return self.config.get('general', 'tagsfile') or TAGSFILE

  @tagsfile.setter
  def tagsfile(self, tagsfile):
    ''' Set the tags filename.
    '''
    self.config['general']['tagsfile'] = tagsfile

FSTagsCommand.add_usage_to_docstring()

def get_xattr_value(filepath, xattr_name):
  ''' Read the extended attribute `xattr_name` of `filepath`.
      Return the extended attribute value as a string,
      or `None` if the attribute does not exist.

      Parameters:
      * `filepath`: the filesystem path to update
      * `xattr_name`: the extended attribute to obtain
        if this is a `str`, the attribute is the UTF-8 encoding of that name.
  '''
  if isinstance(xattr_name, str):
    xattr_name_b = xattr_name.encode()
  else:
    xattr_name_b = xattr_name
  with Pfx("get_xattr_value(%r,%r)", filepath, xattr_name_b):
    try:
      old_xattr_value_b = os.getxattr(filepath, xattr_name_b)
    except OSError as e:
      if e.errno not in (errno.ENOTSUP, errno.ENOENT, errno.ENODATA):
        raise
      old_xattr_value_b = None
  if old_xattr_value_b is None:
    old_xattr_value = None
  else:
    old_xattr_value = old_xattr_value_b.decode(errors='replace')
  return old_xattr_value

def update_xattr_value(filepath, xattr_name, new_xattr_value):
  ''' Update the extended attributes of `filepath`
      with `new_xattr_value` for `xattr_name`.
      Return the previous value, or `None` if the attribute was missing.

      We avoid calling `os.setxattr` if the value will not change.

      Parameters:
      * `filepath`: the filesystem path to update
      * `xattr_name`: the extended attribute to update;
        if this is a `str`, the attribute is the UTF-8 encoding of that name.
      * `new_xattr_value`: the new extended attribute value, a `str`
        which should be the transcription of `TagSet`
        i.e. `str(tagset)`
  '''
  if isinstance(xattr_name, str):
    xattr_name_b = xattr_name.encode()
  else:
    xattr_name_b = xattr_name
  with Pfx("update_xattr_value(%r, %s) <= %s", filepath, xattr_name,
           new_xattr_value):
    old_xattr_value = get_xattr_value(filepath, xattr_name)
    if new_xattr_value is None:
      # remove old xattr if present
      if old_xattr_value is not None:
        try:
          os.removexattr(filepath, xattr_name_b)
        except OSError as e:
          if e.errno not in (errno.ENOTSUP, errno.ENOENT):
            raise
    elif old_xattr_value is None or old_xattr_value != new_xattr_value:
      # set new value
      new_xattr_value_b = new_xattr_value.encode(errors='xmlcharrefreplace')
      with Pfx("setxattr(%r,%r,%r)", filepath, xattr_name_b,
               new_xattr_value_b):
        try:
          os.setxattr(
              filepath, xattr_name_b, new_xattr_value_b, (
                  os.XATTR_CREATE
                  if old_xattr_value is None else os.XATTR_REPLACE
              )
          )
        except OSError as e:
          if e.errno not in (errno.ENOTSUP, errno.ENOENT):
            raise
    return old_xattr_value

if __name__ == '__main__':
  sys.argv[0] = basename(sys.argv[0])
  sys.exit(main(sys.argv))

#!/usr/bin/env python3

''' Simple filesystem based file tagging
    and the associated `fstags` command line script.

    Many basic tasks can be performed with the `fstags` command line utility,
    documented under the `FSTagsCommand` class below.

    Why `fstags`?
    By storing the tags in a separate file we:
    * can store tags without modifying a file
    * do not need to know the file's format,
      or even whether that format supports metadata
    * can process tags on any kind of file
    * because tags are inherited from parent directories,
      tags can be automatically acquired merely by arranging your file tree

    Tags are stored in the file `.fstags` in each directory;
    there is a line for each entry in the directory
    consisting of the directory entry name and the associated tags.

    Tags may be "bare", or have a value.
    If there is a value it is expressed with an equals (`'='`)
    followed by the JSON encoding of the value.

    The tags for a file are the union of its direct tags
    and all relevant ancestor tags,
    with priority given to tags closer to the file.

    For example, a media file for a television episode with the pathname
    `/path/to/series-name/season-02/episode-name--s02e03--something.mp4`
    might have the tags:

        series_title="Series Full Name"
        season=2
        sf
        episode=3
        episode_title="Full Episode Title"

    obtained from the following `.fstags` entries:
    * tag file `/path/to/.fstags`:

        series-name sf series_title="Series Full Name"

    * tag file `/path/to/series-name/.fstags`:

      season-02 season=2

    * tag file `/path/to/series-name/season-02/.fstags`:

      episode-name--s02e03--something.mp4 episode=3 episode_title="Full Episode Title"

    ## `fstags` Examples ##

    ### Backing up a media tree too big for the removable drives ###

    Walk the media tree for files tagged for backup to `archive2`:

        fstags find /path/to/media backup=archive2

    Walk the media tree for files not assigned to a backup archive:

        fstags find /path/to/media -backup

    Backup the `archive2` files using `rsync`:

        fstags find --for-rsync /path/to/media backup=archive2 \\
        | rsync -ia --include-from=- /path/to/media /path/to/backup_archive2

'''

from collections import defaultdict, namedtuple
from configparser import ConfigParser
from contextlib import contextmanager
import csv
import errno
from getopt import getopt, GetoptError
import json
import os
from os.path import (
    abspath, basename, dirname, exists as existspath, expanduser, isdir as
    isdirpath, isfile as isfilepath, join as joinpath, realpath, relpath,
    samefile, splitext
)
from pathlib import PurePath
import shutil
import sys
import threading
from threading import Lock, RLock
from icontract import require
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.fileutils import crop_name, findup, shortpath
from cs.lex import (
    get_nonwhite, get_ini_clause_entryname, FormatableMixin, FormatAsError
)
from cs.logutils import error, warning, ifverbose
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx, pfx_method, XP
from cs.resources import MultiOpenMixin
from cs.tagset import (
    TagSet, Tag, TagChoice, TagsOntology, TaggedEntity, TagsCommandMixin,
    RegexpTagRule
)
from cs.threads import locked, locked_property
from cs.upd import Upd, print  # pylint: disable=redefined-builtin

__version__ = '20200717.1-post'

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
        'cs.cmdutils', 'cs.context', 'cs.deco', 'cs.fileutils', 'cs.lex',
        'cs.logutils', 'cs.obj>=20200716', 'cs.pfx', 'cs.resources',
        'cs.tagset', 'cs.threads', 'cs.upd', 'icontract'
    ],
}

TAGSFILE = '.fstags'
RCFILE = '~/.fstagsrc'

XATTR_B = (
    b'user.cs.fstags'
    if hasattr(os, 'getxattr') and hasattr(os, 'setxattr') else None
)

FIND_OUTPUT_FORMAT_DEFAULT = '{filepath.pathname}'
LS_OUTPUT_FORMAT_DEFAULT = '{filepath.encoded} {tags}'

def main(argv=None):
  ''' Command line mode.
  '''
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
  ifverbose(state.verbose, msg, *a)

class FSTagsCommand(BaseCommand, TagsCommandMixin):
  ''' `fstags` main command line utility.
  '''

  def apply_defaults(self, options):
    ''' Set up the default values in `options`.
    '''
    options.fstags = FSTags()

  @staticmethod
  @contextmanager
  def run_context(argv, options):
    ''' Push the `FSTags`.
    '''
    with options.fstags:
      yield

  @staticmethod
  def cmd_autotag(argv, options):
    ''' Usage: {cmd} paths...
          Tag paths based on rules from the rc file.
    '''
    fstags = options.fstags
    U = options.upd
    if not argv:
      argv = ['.']
    filename_rules = fstags.config.filename_rules
    with stackattrs(state, verbose=True):
      for top_path in argv:
        for isdir, path in rpaths(top_path, yield_dirs=True):
          spath = shortpath(path)
          U.out(spath)
          with Pfx(spath):
            ont = fstags.ontology(path)
            tagged_path = fstags[path]
            direct_tags = tagged_path.direct_tags
            all_tags = tagged_path.merged_tags()
            for autotag in tagged_path.infer_from_basename(filename_rules):
              U.out(spath + ' ' + str(autotag))
              if ont:
                autotag = ont.convert_tag(autotag)
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
            for tag in fstags.cascade_tags(all_tags):
              if tag.name not in direct_tags:
                direct_tags.add(tag)

  @staticmethod
  def cmd_edit(argv, options):
    ''' Usage: {cmd} [-d] [path]
          Edit the direct tagsets of path, default: '.'
          If path is a directory, provide the tags of its entries.
          Otherwise edit just the tags for path.
          -d          Treat directories like files: edit just its tags.
    '''
    fstags = options.fstags
    directories_like_files = False
    xit = 0
    options, argv = getopt(argv, 'd')
    for option, _ in options:
      with Pfx(option):
        if option == '-d':
          directories_like_files = True
    if not argv:
      path = '.'
    else:
      path = argv.pop(0)
      if argv:
        raise GetoptError("extra arguments after path: %r" % (argv,))
    with stackattrs(state, verbose=True):
      with Pfx(path):
        if directories_like_files or not isdirpath(path):
          tags = fstags[path].direct_tags
          tags.edit(verbose=state.verbose)
        elif not fstags.edit_dirpath(path):
          xit = 1
    return xit

  @classmethod
  def cmd_export(cls, argv, options):
    ''' Usage: {cmd} [-a] [--direct] path {{tag[=value]|-tag}}...
          Export tags for files from path matching all the constraints.
          -a        Export all paths, not just those with tags.
          --direct  Export the direct tags instead of the computed tags.
          The output is in the same CSV format as that from "sqltags export",
          with the following columns:
          * unixtime: the file's st_mtime from os.stat.
          * id: empty
          * name: the file path
          * tags: the file's direct or indirect tags
    '''
    fstags = options.fstags
    badopts = False
    all_paths = False
    use_direct_tags = False
    options, argv = getopt(argv, 'a', longopts=['direct'])
    for option, value in options:
      with Pfx(option):
        if option == '-a':
          all_paths = True
        elif option == '--direct':
          use_direct_tags = True
        else:
          raise RuntimeError("unimplemented option")
    if not argv:
      warning("missing path")
      badopts = True
    else:
      path = argv.pop(0)
      try:
        tag_choices = cls.parse_tagset_criteria(argv)
      except ValueError as e:
        warning("bad tag specifications: %s", e)
        badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    xit = 0
    csvw = csv.writer(sys.stdout)
    for filepath in fstags.find(realpath(path), tag_choices,
                                use_direct_tags=use_direct_tags):
      te = fstags[filepath].as_TaggedEntity(indirect=not use_direct_tags)
      if all_paths or te.tags:
        csvw.writerow(te.csvrow)
    return xit

  @classmethod
  def cmd_find(cls, argv, options):
    ''' Usage: {cmd} [--direct] [--for-rsync] [-o output_format] path {{tag[=value]|-tag}}...
          List files from path matching all the constraints.
          --direct    Use direct tags instead of all tags.
          --for-rsync Instead of listing matching paths, emit a
                      sequence of rsync(1) patterns suitable for use with
                      --include-from in order to do a selective rsync of the
                      matched paths.
          -o output_format
                      Use output_format as a Python format string to lay out
                      the listing.
                      Default: {FIND_OUTPUT_FORMAT_DEFAULT}
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
          output_format = fstags.resolve_format_string(value)
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
          tag_choices = cls.parse_tagset_criteria(argv)
        except ValueError as e:
          warning("bad tag specifications: %s", e)
          badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    xit = 0
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
        try:
          output = fstags[filepath].format_as(
              output_format, error_sep='\n  ', direct=use_direct_tags
          )
        except FormatAsError as e:
          error(str(e))
          xit = 1
          continue
        print(output)
        if U:
          U.out(oldU)
    return xit

  @staticmethod
  def cmd_import(argv, options):
    ''' Usage: {cmd} {{-|srcpath}}...
          Import CSV data in the format emitted by "export".
          Each argument is a file path or "-", indicating standard input.
    '''
    fstags = options.fstags
    badopts = False
    if not argv:
      warning("missing srcpaths")
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    for srcpath in argv:
      if srcpath == '-':
        with Pfx("stdin"):
          fstags.import_csv_file(sys.stdin)
      else:
        with Pfx(srcpath):
          with open(srcpath) as f:
            fstags.import_csv_file(f)

  def import_csv_file(self, f, *, convert_name=None):
    ''' Import CSV data from the file `f`.

        Parameters:
        * `f`: the source CSV file
        * `convert_name`: a callable to convert each input name
          into a file path; the default is to use the input name directly
    '''
    csvr = csv.reader(f)
    for csvrow in csvr:
      with Pfx(csvr.line_num):
        te = TaggedEntity.from_csvrow(csvrow)
        if convert_name:
          with Pfx("convert_name(%r)", te.name):
            path = convert_name(te.name)
        else:
          path = te.name
        self.add_tagged_entity(te, path=path)

  def add_tagged_entity(self, te, path=None):
    ''' Import a `TaggedEntity` as `path` (default `te.name`).
    '''
    TaggedPath.from_TaggedEntity(te, fstags=self, path=path)

  @classmethod
  def cmd_json_import(cls, argv, options):
    ''' Usage: json_import --prefix=tag_prefix {{-|path}} {{-|tags.json}}
          Apply JSON data to path.
          A path named "-" indicates that paths should be read from
          the standard input.
          The JSON tag data come from the file "tags.json"; the name
          "-" indicates that the JSON data should be read from the
          standard input.
    '''
    fstags = options.fstags
    tag_prefix = None
    path = None
    json_path = None
    badopts = False
    options, argv = getopt(argv, '', longopts=['prefix='])
    for option, value in options:
      with Pfx(option):
        if option == '--prefix':
          tag_prefix = value
        else:
          raise RuntimeError("unimplemented option")
    if tag_prefix is None:
      warning("missing required --prefix option")
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
      for path in paths:
        with Pfx(path):
          ont = fstags.ontology(path)
          tagged_path = fstags[path]
          for key, value in data.items():
            tag_name = '.'.join((tag_prefix, key)) if tag_prefix else key
            tagged_path.direct_tags.add(
                Tag(tag_name, value, ontology=ont), verbose=verbose
            )
    return 0

  @staticmethod
  def cmd_ls(argv, options):
    ''' Usage: {cmd} [-d] [--direct] [-o output_format] [paths...]
        List files from paths and their tags.
        -d          Treat directories like files, do not recurse.
        --direct    List direct tags instead of all tags.
        -o output_format
                    Use output_format as a Python format string to lay out
                    the listing.
                    Default: {LS_OUTPUT_FORMAT_DEFAULT}
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
          output_format = fstags.resolve_format_string(value)
        else:
          raise RuntimeError("unsupported option")
    xit = 0
    paths = argv or ['.']
    for path in paths:
      fullpath = realpath(path)
      for filepath in ((fullpath,)
                       if directories_like_files else rfilepaths(fullpath)):
        with Pfx(filepath):
          try:
            listing = fstags[filepath].format_as(
                output_format, error_sep='\n  ', direct=use_direct_tags
            )
          except FormatAsError as e:
            error(str(e))
            xit = 1
            continue
          print(listing)
    return xit

  def cmd_cp(self, argv, options):
    ''' Usage: {cmd} [-finv] srcpath dstpath, {cmd} [-finv] srcpaths... dstdirpath
          POSIX cp(1) equivalent, but also copying tags:
          copy files and their tags into targetdir.
          -f  Force: remove destination if it exists.
          -i  Interactive: fail if the destination exists.
          -n  No remove: fail if the destination exists.
          -v  Verbose: show copied files.
    '''
    return self._cmd_mvcpln(options.fstags.copy, argv, options)

  def cmd_ln(self, argv, options):
    ''' Usage: {cmd} [-finv] srcpath dstpath, {cmd} [-finv] srcpaths... dstdirpath
          POSIX ln(1) equivalent, but also copying the tags:
          link files and their tags into targetdir.
          -f  Force: remove destination if it exists.
          -i  Interactive: fail if the destination exists.
          -n  No remove: fail if the destination exists.
          -v  Verbose: show linked files.
    '''
    return self._cmd_mvcpln(options.fstags.link, argv, options)

  def cmd_mv(self, argv, options):
    ''' Usage: {cmd} [-finv] srcpath dstpath, {cmd} [-finv] srcpaths... dstdirpath
          POSIX mv(1) equivalent, but also copying the tags:
          move files and their tags into targetdir.
          -f  Force: remove destination if it exists.
          -i  Interactive: fail if the destination exists.
          -n  No remove: fail if the destination exists.
          -v  Verbose: show moved files.
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
    subopts, argv = getopt(argv, 'finv')
    for subopt, _ in subopts:
      if subopt == '-f':
        cmd_force = True
      elif subopt == '-i':
        cmd_force = False
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
        dirpath = argv.pop()
        for srcpath in argv:
          dstpath = joinpath(dirpath, basename(srcpath))
          try:
            attach(srcpath, dstpath, force=cmd_force, crop_ok=True)
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
        srcpath, dstpath = argv
        try:
          attach(srcpath, dstpath, force=cmd_force, crop_ok=True)
        except (ValueError, OSError) as e:
          print(e, file=sys.stderr)
          xit = 1
        else:
          if cmd_verbose:
            print(srcpath, '->', dstpath)
    return xit

  @staticmethod
  def cmd_ns(argv, options):
    ''' Usage: {cmd} [-d] [--direct] [paths...]
          Report on the available primary namespace fields for formatting.
          Note that because the namespace used for formatting has
          inferred field names there are also unshown secondary field
          names available in format strings.
          -d          Treat directories like files, do not recurse.
          --direct    List direct tags instead of all tags.
    '''
    fstags = options.fstags
    directories_like_files = False
    use_direct_tags = False
    options, argv = getopt(argv, 'd', longopts=['direct'])
    for option, _ in options:
      with Pfx(option):
        if option == '-d':
          directories_like_files = True
        elif option == '--direct':
          use_direct_tags = True
        else:
          raise RuntimeError("unsupported option")
    xit = 0
    paths = argv or ['.']
    for path in paths:
      fullpath = realpath(path)
      for filepath in ((fullpath,)
                       if directories_like_files else rfilepaths(fullpath)):
        with Pfx(filepath):
          tags = fstags[filepath].format_tagset(direct=use_direct_tags)
          print(filepath)
          for tag in sorted(tags.as_tags()):
            print(" ", tag)
    return xit

  @staticmethod
  def cmd_ont(argv, options):
    ''' Ontology operations.

        Usage: {cmd} [subcommand [args...]]
          With no arguments, locate the ontology.
          Subcommands:
            tags tag[=value]...
              Query ontology information for the specified tags.
    '''
    ont = options.fstags.ontology('.')
    if not argv:
      print(ont)
      return 0
    subcmd = argv.pop(0)
    with Pfx(subcmd):
      if subcmd == 'tags':
        if not argv:
          raise GetoptError("missing tags")
        for tag_arg in argv:
          with Pfx(tag_arg):
            tag = Tag.from_string(tag_arg, ontology=ont)
            typedata = tag.typedata
            print(" ", typedata)
            print(" ", repr(tag.value))
            print(" ", repr(tag.metadata))
      else:
        raise GetoptError("unrecognised subcommand")
    return 0

  @staticmethod
  def cmd_rename(argv, options):
    ''' Usage: {cmd} -n newbasename_format paths...
          Rename paths according to a format string.
          -n newbasename_format
              Use newbasename_format as a Python format string to
              compute the new basename for each path.
    '''
    xit = 0
    fstags = options.fstags
    name_format = None
    subopts, argv = getopt(argv, 'n:')
    for subopt, value in subopts:
      if subopt == '-n':
        name_format = fstags.resolve_format_string(value)
      else:
        raise RuntimeError("unhandled subopt: %r" % (subopt,))
    if name_format is None:
      raise GetoptError("missing -n option")
    if not argv:
      raise GetoptError("missing paths")
    if len(argv) == 1 and argv[0] == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = argv
    xit = 0
    U = Upd(sys.stderr) if sys.stderr.isatty() else None
    with stackattrs(state, verbose=True):
      for filepath in paths:
        if U:
          oldU = U.out('')
        with Pfx(filepath):
          if filepath == '-':
            warning(
                "ignoring name %r: standard input is only supported alone",
                filepath
            )
            xit = 1
            continue
          dirpath = dirname(filepath)
          base = basename(filepath)
          try:
            newbase = fstags[filepath].format_as(
                name_format, error_sep='\n  ', direct=False
            )
          except FormatAsError as e:
            error(str(e))
            xit = 1
            continue
          newbase = newbase.replace(os.sep, ':')
          if base == newbase:
            continue
          dstpath = joinpath(dirpath, newbase)
          verbose("-> %s", dstpath)
          try:
            options.fstags.move(filepath, dstpath, crop_ok=True)
          except OSError as e:
            error("-> %s: %s", dstpath, e)
            xit = 1
    if U:
      U.out(oldU)
    return xit

  @classmethod
  def cmd_scrub(cls, argv, options):
    ''' Usage: {cmd} paths...
          Remove all tags for missing paths.
          If a path is a directory, scrub the immediate paths in the directory.
    '''
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing paths")
    with stackattrs(state, verbose=True):
      for path in argv:
        fstags.scrub(path)

  @classmethod
  def cmd_tag(cls, argv, options):
    ''' Usage: {cmd} {{-|path}} {{tag[=value]|-tag}}...
          Tag a path with multiple tags.
          With the form "-tag", remove that tag from the direct tags.
          A path named "-" indicates that paths should be read from the
          standard input.
    '''
    badopts = False
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing path")
    path = argv.pop(0)
    if not argv:
      raise GetoptError("missing tags")
    tag_choices = []
    for arg in argv:
      with Pfx(arg):
        try:
          tag_choice = TagChoice.from_str(arg)
        except ValueError as e:
          warning("bad tag specifications: %s", e)
          badopts = True
        else:
          tag_choices.append(tag_choice)
    if badopts:
      raise GetoptError("bad arguments")
    if path == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = [path]
    with stackattrs(state, verbose=True):
      fstags.apply_tag_choices(tag_choices, paths)

  @classmethod
  def cmd_tagfile(cls, argv, options):
    ''' Usage: {cmd} tagfile_path [subcommand ...]
          Subcommands:
            tag tagset_name {{tag[=value]|-tag}}...
              Directly modify tag_name within the tag file tagfile_path.
    '''
    try:
      tagfilepath = argv.pop(0)
    except IndexError:
      raise GetoptError("missing tagfile_path")
    with Pfx(tagfilepath):
      try:
        subcmd = argv.pop(0)
      except IndexError:
        raise GetoptError("missing subcommand")
      with Pfx(subcmd):
        if subcmd == 'tag':
          try:
            tagset_name = argv.pop(0)
          except IndexError:
            raise GetoptError("missing tagset_name")
          with Pfx(tagset_name):
            if not argv:
              raise GetoptError("missing tags")
            badopts = False
            try:
              tag_choices = cls.parse_tagset_criteria(argv)
            except ValueError as e:
              warning("bad tag specifications: %s", e)
              badopts = True
            if badopts:
              raise GetoptError("bad arguments")
            with stackattrs(state, verbose=True):
              with TagFile(tagfilepath) as tagfile:
                tags = tagfile[tagset_name]
                for choice in tag_choices:
                  with Pfx(choice.spec):
                    if choice.choice:
                      # add tag
                      tags.add(choice.tag, verbose=state.verbose)
                    else:
                      # delete tag
                      tags.discard(choice.tag, verbose=state.verbose)
        else:
          raise GetoptError("unrecognised subcommand")

  @classmethod
  def cmd_tagpaths(cls, argv, options):
    ''' Usage: {cmd} {{tag[=value]|-tag}} {{-|paths...}}
        Tag multiple paths.
        With the form "-tag", remove the tag from the immediate tags.
        A single path named "-" indicates that paths should be read
        from the standard input.
    '''
    badopts = False
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing tag choice")
    tag_choice = argv.pop(0)
    try:
      tag_choices = cls.parse_tagset_criteria([tag_choice])
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
  def cmd_test(cls, argv, options):
    ''' Usage: {cmd} [--direct] path {{tag[=value]|-tag}}...
          Test whether the path matches all the constraints.
          --direct    Use direct tags instead of all tags.
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
          tag_choices = cls.parse_tagset_criteria(argv)
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
  def cmd_xattr_export(cls, argv, options):
    ''' Usage: {cmd} {{-|paths...}}
          Import tag information from extended attributes.
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
  def cmd_xattr_import(cls, argv, options):
    ''' Usage: {cmd} {{-|paths...}}
          Update extended attributes from tags.
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

FSTagsCommand.add_usage_to_docstring()

class FSTags(MultiOpenMixin):
  ''' A class to examine filesystem tags.
  '''

  def __init__(self, tagsfile=None, ontologyfile=None):
    MultiOpenMixin.__init__(self)
    if tagsfile is None:
      tagsfile = TAGSFILE
    if ontologyfile is None:
      ontologyfile = tagsfile + '-ontology'
    self.config = FSTagsConfig()
    self.config.tagsfile = tagsfile
    self.config.ontologyfile = ontologyfile
    self._tagfiles = {}  # cache of `TagFile`s from their actual paths
    self._tagged_paths = {}  # cache of per abspath `TaggedPath`
    self._dirpath_ontologies = {}  # cache of per dirpath(path) `TagsOntology`
    self._lock = RLock()

  def startup(self):
    ''' Stub for startup.
    '''

  def shutdown(self):
    ''' Save any modified tag files on shutdown.
    '''
    self.sync()

  @locked
  def sync(self):
    ''' Flush modified tag files.
    '''
    for tagfile in self._tagfiles.values():
      try:
        tagfile.save()
      except FileNotFoundError as e:
        error("%s.save: %s", tagfile, e)

  def _tagfile(self, path, *, find_parent=False, no_ontology=False):
    ''' Obtain and cache the `TagFile` at `path`.
    '''
    ontology = None if no_ontology else self.ontology(path)
    tagfile = self._tagfiles[path] = TagFile(
        path, find_parent=find_parent, ontology=ontology
    )
    return tagfile

  @property
  def tagsfile(self):
    ''' The tag file basename.
    '''
    return self.config.tagsfile

  @property
  def ontologyfile(self):
    ''' The ontology file basename.
    '''
    return self.config.ontologyfile

  def __str__(self):
    return "%s(tagsfile=%r)" % (type(self).__name__, self.tagsfile)

  @locked
  def __getitem__(self, path):
    ''' Return the `TaggedPath` for `abspath(path)`.
    '''
    path = abspath(path)
    tagged_path = self._tagged_paths.get(path)
    if tagged_path is None:
      tagged_path = self._tagged_paths[path] = TaggedPath(path, self)
    return tagged_path

  @pfx_method
  def resolve_format_string(self, format_string):
    ''' See if `format_string` looks like `[`*clausename*`]`*entryname*.
        if so, return the corresponding config entry string,
        otherwise return `format_string` unchanged.
    '''
    try:
      clausename, entryname, offset = get_ini_clause_entryname(format_string)
    except ValueError:
      pass
    else:
      if offset == len(format_string):
        try:
          format_string = self.config[clausename][entryname]
        except KeyError as e:
          warning("config clause entry %r not found: %s", format_string, e)
    return format_string

  @locked
  def ontology(self, path):
    ''' Return the `TagsOntology` associated with `path`.
        Returns `None` if an ontology cannot be found.
    '''
    cache = self._dirpath_ontologies
    path = abspath(path)
    dirpath = path if isdirpath(path) else dirname(path)
    ont = cache.get(dirpath)
    if ont is None:
      # locate the ancestor directory containing the first ontology file
      ontbase = self.ontologyfile
      ontdirpath = next(
          findup(
              realpath(dirpath),
              lambda p: isfilepath(joinpath(p, ontbase)),
              first=True
          )
      )
      if ontdirpath is not None:
        ontpath = joinpath(ontdirpath, ontbase)
        ont_tagfile = self._tagfile(
            ontpath, find_parent=True, no_ontology=True
        )
        ont = TagsOntology(ont_tagfile)
        ont_tagfile.ontology = ont
        cache[dirpath] = ont
    return ont

  def path_tagfiles(self, filepath):
    ''' Return a list of `TagFileEntry`s
        for the `TagFile`s affecting `filepath`
        in order from the root to `dirname(filepath)`
        where `name` is the key within `TagFile`.
    '''
    with Pfx("path_tagfiles(%r)", filepath):
      absfilepath = abspath(filepath)
      root, *subparts = PurePath(absfilepath).parts
      if not subparts:
        raise ValueError("root=%r and no subparts" % (root,))
      tagfiles = []
      current = root
      while subparts:
        next_part = subparts.pop(0)
        tagfiles.append(TagFileEntry(self.dir_tagfile(current), next_part))
        current = joinpath(current, next_part)
      return tagfiles

  @locked
  def dir_tagfile(self, dirpath):
    ''' Return the `TagFile` associated with `dirpath`.
    '''
    return self._tagfile(joinpath(abspath(dirpath), self.tagsfile))

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
          for choice in tag_choices:
            with Pfx(choice.spec):
              if choice.choice:
                # add tag
                tagged_path.add(choice.tag)
              else:
                # delete tag
                tagged_path.discard(choice.tag)

  def cascade_tags(self, tags, cascade_rules=None):
    ''' Yield `Tag`s
        which cascade from the `TagSet` `tags`
        via `cascade_rules` (an iterable of `CascadeRules`).
    '''
    if cascade_rules is None:
      cascade_rules = self.config.cascade_rules
    cascaded = set()
    for cascade_rule in cascade_rules:
      if cascade_rule.target in tags:
        continue
      if cascade_rule.target in cascaded:
        continue
      tag = cascade_rule.infer_tag(tags)
      if tag is None:
        continue
      if tag.name not in tags:
        yield tag
        cascaded.add(tag.name)

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
    for tag_choice in tag_choices:
      if not tag_choice.match(tags):
        return False
    return True

  @pfx_method
  def edit_dirpath(self, dirpath):
    ''' Edit the filenames and tags in a directory.
    '''
    ok = True
    tagfile = self.dir_tagfile(dirpath)
    tagsets = tagfile.tagsets
    names = sorted(
        set(
            name for name in os.listdir(dirpath)
            if (name and name not in ('.', '..') and not name.startswith('.'))
        )
    )
    tes = []
    te_id_map = {}
    for name in names:
      if not name or os.sep in name:
        warning("skip bogus name %r", name)
        continue
      path = joinpath(dirpath, name)
      tagged_path = self[path]
      te = tagged_path.as_TaggedEntity(name=name)
      tes.append(te)
      te_id_map[id(te)] = name, tagged_path, te
    # now apply any file renames
    changed_tes = TaggedEntity.edit_entities(tes)  # verbose-state.verbose
    for te in changed_tes:
      old_name, tagged_path, old_te = te_id_map[id(te)]
      assert te is old_te
      new_name = te.name
      if old_name == new_name:
        continue
      with Pfx("%r => %r", old_name, new_name):
        if not new_name:
          warning("name removed? ignoring")
          continue
        old_path = joinpath(dirpath, old_name)
        if not existspath(old_path):
          warning("old path does not exist: %r", old_path)
          ok = False
          continue
        new_path = joinpath(dirpath, new_name)
        if existspath(new_path):
          warning("new path exists, not renaming to %r", new_path)
          ok = False
          continue
        with Pfx("os.rename(%r, %r)", old_path, new_path):
          try:
            os.rename(old_path, new_path)
          except OSError as e:
            warning("%s", e)
            ok = False
            continue
          else:
            ifverbose(True, "renamed")
        # update tags of new path
        self.dir_tagfile(dirname(new_path)).tagsets[new_name].set_from(te.tags)
        del tagsets[old_name]
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
  def copy(self, srcpath, dstpath, **kw):
    ''' Copy `srcpath` to `dstpath`.
    '''
    return self.attach_path(shutil.copy2, srcpath, dstpath, **kw)

  @pfx_method
  def link(self, srcpath, dstpath, **kw):
    ''' Link `srcpath` to `dstpath`.
    '''
    return self.attach_path(os.link, srcpath, dstpath, **kw)

  @pfx_method
  def move(self, srcpath, dstpath, **kw):
    ''' Move `srcpath` to `dstpath`.
    '''
    return self.attach_path(shutil.move, srcpath, dstpath, **kw)

  def attach_path(
      self, attach, srcpath, dstpath, *, force=False, crop_ok=False
  ):
    ''' Attach `srcpath` to `dstpath` using the `attach` callable.

        Parameters:
        * `attach`: callable accepting `attach(srcpath,dstpath)`
          to do the desired attachment,
          such as a copy, link or move
        * `srcpath`: the source filesystem object
        * `dstpath`: the destination filesystem object
        * `crop_ok`: if true and the OS raises `OSError(ENAMETOOLONG)`
          attempt to crop the name before the file extension and retry
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
      try:
        result = attach(srcpath, dstpath)
      except OSError as e:
        if e.errno == errno.ENAMETOOLONG and crop_ok:
          dstdirpath = dirname(dstpath)
          dstbasename = basename(dstpath)
          newbasename = crop_name(dstbasename)
          if newbasename != dstbasename:
            return self.attach_path(
                attach,
                srcpath,
                joinpath(dstdirpath, newbasename),
                force=force,
                crop_ok=False
            )
        else:
          raise
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

class TagFile(SingletonMixin):
  ''' A reference to a specific file containing tags.

      This manages a mapping of `name` => `TagSet`,
      itself a mapping of tag name => tag value.
  '''

  @classmethod
  def _singleton_key(cls, filepath, *, ontology=None, find_parent=False):
    return filepath, ontology, find_parent

  @require(lambda filepath: isinstance(filepath, str))
  def __init__(self, filepath, *, ontology=None, find_parent=False):
    if hasattr(self, 'filepath'):
      return
    self.filepath = filepath
    self.ontology = ontology
    self.find_parent = find_parent
    self._lock = Lock()

  def __str__(self):
    return "%s(%r,%s)" % (
        type(self).__name__, shortpath(self.filepath), self.find_parent
    )

  def __repr__(self):
    return "%s(%r,find_parent=%r)" % (
        type(self).__name__, self.filepath, self.find_parent
    )

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, exc_traceback):
    ''' Save the tagsets if modified.
        Do not save if there's an exception pending.
    '''
    if exc_type is None:
      self.save()

  # Mapping mathods, proxying through to .tagsets.
  def keys(self):
    ''' `tagsets.keys`
    '''
    ks = self.tagsets.keys()
    return ks

  def values(self):
    ''' `tagsets.values`
    '''
    return self.tagsets.values()

  def items(self):
    ''' `tagsets.items`
    '''
    return self.tagsets.items()

  def __getitem__(self, name):
    ''' Return the `TagSet` associated with `name`.
    '''
    with Pfx("%s.__getitem__[%r]", self, name):
      tagfile = self
      while tagfile is not None:
        if name in tagfile.tagsets:
          break
        tagfile = tagfile.parent if tagfile.find_parent else None
      if tagfile is None:
        # not available in parents, use self
        # this will autocreate an empty TagSet in self
        tagfile = self
      return tagfile.tagsets[name]

  def __delitem__(self, name):
    del self.tagsets[name]

  def __getattr__(self, attr):
    if attr == 'parent':
      # locate parent TagFile
      dirpath = dirname(self.filepath)
      updirpath = dirname(dirpath)
      if updirpath == dirpath:
        parent = None
      else:
        filebase = basename(self.filepath)
        parent_dirpath = next(
            findup(
                updirpath,
                lambda dirpath: isfilepath(joinpath(dirpath, filebase)),
                first=True
            )
        )
        if parent_dirpath:
          parent_filepath = joinpath(parent_dirpath, filebase)
          parent = type(self)(parent_filepath, find_parent=True)
        else:
          parent = None
      self.parent = parent
      return parent
    raise AttributeError(attr)

  @locked_property
  @pfx_method
  def tagsets(self):
    ''' The tag map from the tag file,
        a mapping of name=>`TagSet`.

        This is loaded on demand.
    '''
    return self.load_tagsets(self.filepath, self.ontology)

  @property
  def names(self):
    ''' The names from this `TagFile` as a list.
    '''
    return list(self.tagsets.keys())

  @classmethod
  def parse_tags_line(cls, line, ontology=None):
    ''' Parse a "name tags..." line as from a `.fstags` file,
        return `(name,TagSet)`.
    '''
    name, offset = Tag.parse_value(line)
    if offset < len(line) and not line[offset].isspace():
      _, offset2 = get_nonwhite(line, offset)
      name = line[:offset2]
      # This is normal.
      ##warning(
      ##    "offset %d: expected whitespace, adjusted name to %r", offset, name
      ##)
      offset = offset2
    if offset < len(line) and not line[offset].isspace():
      warning("offset %d: expected whitespace", offset)
    tags = TagSet.from_line(
        line, offset, ontology=ontology, verbose=state.verbose
    )
    return name, tags

  @classmethod
  def load_tagsets(cls, filepath, ontology):
    ''' Load `filepath` and return
        a mapping of `name`=>`tag_name`=>`value`.
    '''
    with Pfx("%r", filepath):
      tagsets = defaultdict(lambda: TagSet(ontology=ontology))
      try:
        with open(filepath) as f:
          with stackattrs(state, verbose=False):
            for lineno, line in enumerate(f, 1):
              with Pfx(lineno):
                line = line.strip()
                if not line or line.startswith('#'):
                  continue
                name, tags = cls.parse_tags_line(line, ontology=ontology)
                tagsets[name] = tags
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise
      return tagsets

  @classmethod
  def tags_line(cls, name, tags):
    ''' Transcribe a `name` and its `tags` for use as a `.fstags` file line.
    '''
    fields = [Tag.transcribe_value(name)]
    for tag in tags:
      fields.append(str(tag))
    return ' '.join(fields)

  @classmethod
  def save_tagsets(cls, filepath, tagsets):
    ''' Save `tagsets` to `filepath`.

        This method will create the required intermediate directories
        if missing.
    '''
    with Pfx("savetags(%r)", filepath):
      dirpath = dirname(filepath)
      if not isdirpath(dirpath):
        verbose("makedirs(%r)", dirpath)
        with Pfx("makedirs(%r)", dirpath):
          os.makedirs(dirpath)
      name_tags = sorted(tagsets.items())
      try:
        with open(filepath, 'w') as f:
          for name, tags in name_tags:
            if not tags:
              continue
            f.write(cls.tags_line(name, tags))
            f.write('\n')
      except OSError as e:
        error("save fails: %s", e)
      else:
        for _, tags in name_tags:
          tags.modified = False

  def save(self):
    ''' Save the tag map to the tag file.
    '''
    tagsets = getattr(self, '_tagsets', None)
    if tagsets is None:
      # TagSets never loaded
      return
    with self._lock:
      if any(map(lambda tagset: tagset.modified, tagsets.values())):
        # modified TagSets
        self.save_tagsets(self.filepath, self.tagsets)
        for tagset in tagsets.values():
          tagset.modified = False
    if self.find_parent:
      parent = self.parent
      if parent:
        self.parent.save()

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

  def update(self, name, tags, *, prefix=None):
    ''' Update the tags for `name` from the supplied `tags`
        as for `Tagset.update`.
    '''
    if prefix:
      tags = [
          Tag.with_prefix(tag.name, tag.value, prefix=prefix) for tag in tags
      ]
    return self[name].update(tags, prefix=prefix, verbose=state.verbose)

class TagFileEntry(namedtuple('TagFileEntry', 'tagfile name')):
  ''' An entry withing a `TagFile`.

      Attributes:
      * `name`: the name of the `TagSet` entry within `tagfile`
      * `tagfile`: the `TagFile` containing `name`
  '''

  @property
  def tagset(self):
    ''' The `TagSet` from `tagfile`.
    '''
    return self.tagfile[self.name]

class TaggedPath(HasFSTagsMixin, FormatableMixin):
  ''' Class to manipulate the tags for a specific path.
  '''

  def __init__(self, filepath, fstags=None):
    if fstags is None:
      fstags = self.fstags
    else:
      self.fstags = fstags
    self.filepath = filepath
    self._tagfile_stack = fstags.path_tagfiles(filepath)
    self._lock = Lock()

  def __repr__(self):
    return "%s(%s)" % (type(self).__name__, self.filepath)

  def __str__(self):
    return Tag.transcribe_value(str(self.filepath)) + ' ' + str(self.all_tags)

  def __contains__(self, tag):
    ''' Test for the presence of `tag` in the `all_tags`.
    '''
    return tag in self.all_tags

  def as_TaggedEntity(self, te_id=None, name=None, indirect=False):
    ''' Return a `TaggedEntity` for this `TaggedPath`,
        useful for export.

        Parameters:
        * `te_id`: a value for the `TaggedEntity.id` attribute, default `None`
        * `name`: a value for the `TaggedEntity.name` attribute,
          default `self.filepath`
        * `indirect`: if true, use a copy of `self.all_tags`
          for `TaggedEntity.tags`, otherwise a copy of `self.direct_tags`.
          The default is `False`.
    '''
    if name is None:
      name = self.filepath
    try:
      S = os.stat(self.filepath)
    except OSError:
      unixtime = None
    else:
      unixtime = S.st_mtime
    tags = TagSet()
    tags.update(self.all_tags if indirect else self.direct_tags)
    return TaggedEntity(id=te_id, name=name, unixtime=unixtime, tags=tags)

  @classmethod
  def from_TaggedEntity(cls, te, *, fstags, path=None):
    ''' Factory to create a `TaggedPath` from a `TaggedEntity`.

        Parameters:
        * `te`: the source `TaggedEntity`
        * `fstags`: the associated `FSTags` instance
        * `path`: the path for the new instance,
          default from `te.name`

        Note that the `te.tags` are merged into the existing `TagSet`
        for the `path`.
    '''
    if path is None:
      path = te.name
    tagged_path = fstags[path]
    if te.tags:
      tagged_path.direct_tags.update(te.tags)
    return tagged_path

  @property
  def ontology(self):
    ''' The ontology for use with this file, or `None`.
    '''
    try:
      return self.fstags.ontology(self.filepath)
    except ValueError:
      return None

  def format_tagset(self, *, direct=False):
    ''' Compute a `TagSet` from this file's tags
        with additional derived tags.

        This can be converted into an `ExtendedNamespace`
        suitable for use with `str.format_map`
        via the `TagSet`'s `.format_kwargs()` method.

        In addition to the normal `TagSet.ns()` names
        the following additional names are available:
        * `filepath.basename`: basename of the `TaggedPath.filepath`
        * `filepath.ext`: the file extension of the basename
          of the `TaggedPath.filepath`
        * `filepath.pathname`: the `TaggedPath.filepath`
        * `filepath.encoded`: the JSON encoded filepath
    '''
    ont = self.ontology
    kwtags = TagSet(ontology=ont)
    kwtags.update(self.direct_tags if direct else self.all_tags)
    # add in cascaded values
    for tag in list(self.fstags.cascade_tags(kwtags)):
      if tag.name not in kwtags:
        kwtags.add(tag)
    # tags based on the filepath
    filepath = self.filepath
    for pathtag in (
        Tag('filepath.basename', basename(filepath), ontology=ont),
        Tag('filepath.ext', splitext(basename(filepath))[1], ontology=ont),
        Tag('filepath.pathname', filepath, ontology=ont),
        Tag('filepath.encoded', Tag.transcribe_value(filepath), ontology=ont),
    ):
      if pathtag.name not in kwtags:
        kwtags.add(pathtag)
    return kwtags

  def format_kwargs(self, *, direct=False):
    ''' Format arguments suitable for `str.format_map`.

        This returns an `ExtendedNamespace` from `TagSet.ns()`
        for a computed `TagSet`.

        In addition to the normal `TagSet.ns()` names
        the following additional names are available:
        * `filepath.basename`: basename of the `TaggedPath.filepath`
        * `filepath.pathname`: the `TaggedPath.filepath`
        * `filepath.encoded`: the JSON encoded filepath
        * `tags`: the `TagSet` as a string
    '''
    kwtags = self.format_tagset(direct=direct)
    kwtags['tags'] = str(kwtags)
    # convert the TagSet to an ExtendedNamespace
    kwargs = kwtags.format_kwargs()
    ##XP("format_kwargs=%s", kwargs)
    return kwargs

  @property
  def basename(self):
    ''' The name of the final path component.
    '''
    return self._tagfile_stack[-1].name

  @property
  def direct_tagfile(self):
    ''' The `TagFile` for the final path component.
    '''
    return self._tagfile_stack[-1].tagfile

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
    tags = TagSet(ontology=self.ontology)
    with stackattrs(state, verbose=False):
      for tagfile, name in self._tagfile_stack:
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

  def update(self, tags, *, prefix=None):
    ''' Update the direct tags from `tags`
        as for `TagSet.update`.
    '''
    self.direct_tagfile.update(self.basename, tags, prefix=prefix)

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
    tagset = TagSet(ontology=self.ontology)
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
      return TagSet(ontology=self.ontology)
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

@pfx
def rpaths(path, *, yield_dirs=False, name_selector=None, U=None):
  ''' Recurse over `path`, yielding `(is_dir,subpath)`
      for all selected subpaths.
  '''
  if name_selector is None:
    name_selector = lambda name: name and not name.startswith('.')
  pending = [path]
  while pending:
    dirpath = pending.pop(0)
    U.out(dirpath)
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

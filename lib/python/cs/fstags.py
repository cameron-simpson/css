#!/usr/bin/env python3
#
# pylint: disable=too-many-lines

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
from threading import Lock, RLock
from icontract import require
from typeguard import typechecked
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.fileutils import crop_name, findup, shortpath
from cs.lex import (get_ini_clause_entryname, FormatAsError)
from cs.logutils import error, warning, ifverbose
from cs.pfx import Pfx, pfx, pfx_method
from cs.resources import MultiOpenMixin
from cs.tagset import (
    Tag,
    TagSet,
    TagBasedTest,
    TagsOntology,
    TagFile,
    TagsOntologyCommand,
    TagsCommandMixin,
    RegexpTagRule,
    tag_or_tag_value,
)
from cs.threads import locked, locked_property, State
from cs.upd import print  # pylint: disable=redefined-builtin

__version__ = '20210306'

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
        'cs.logutils', 'cs.pfx', 'cs.resources', 'cs.tagset', 'cs.threads',
        'cs.upd', 'icontract', 'typeguard'
    ],
}

TAGSFILE_BASENAME = '.fstags'
RCFILE = '~/.fstagsrc'

XATTR_B = (
    b'user.cs.fstags'
    if hasattr(os, 'getxattr') and hasattr(os, 'setxattr') else None
)

FIND_OUTPUT_FORMAT_DEFAULT = '{filepath.pathname}'
LS_OUTPUT_FORMAT_DEFAULT = '{filepath.encoded} {tags}'

# pylint: disable=too-many-locals

def main(argv=None):
  ''' Command line mode.
  '''
  return FSTagsCommand(argv).run()

def is_valid_basename(name: str):
  ''' Test whether `name` is a valid basefile for something in a directory.
  '''
  return name and name not in ('.', '..') and os.sep not in name

state = State(verbose=False)

def verbose(msg, *a):
  ''' Emit message if in verbose mode.
  '''
  ifverbose(state.verbose, msg, *a)

# pylint: disable=too-many-public-methods
class FSTagsCommand(BaseCommand, TagsCommandMixin):
  ''' `fstags` main command line utility.
  '''

  GETOPT_SPEC = 'o:'

  USAGE_FORMAT = '''Usage: {cmd} [-o ontology] subcommand [...]
  -o ontology   Specify the path to an ontology file.'''

  def apply_defaults(self):
    ''' Set up the default values in `options`.
    '''
    self.options.ontology_path = None

  def apply_opt(self, opt, val):
    ''' Apply command line option.
    '''
    options = self.options
    if opt == '-o':
      options.ontology_path = val
    else:
      raise RuntimeError("unhandled option")

  @contextmanager
  def run_context(self):
    ''' Push the `FSTags`.
    '''
    options = self.options
    fstags = FSTags(ontologyfile=options.ontology_path)
    with fstags:
      with stackattrs(options, fstags=fstags):
        yield

  def cmd_autotag(self, argv):
    ''' Usage: {cmd} paths...
          Tag paths based on rules from the rc file.
    '''
    options = self.option
    fstags = options.fstags
    U = options.upd
    if not argv:
      argv = ['.']
    filename_rules = fstags.config.filename_rules
    with state(verbose=True):
      for top_path in argv:
        for isdir, path in rpaths(top_path, yield_dirs=True):
          spath = shortpath(path)
          U.out(spath)
          with Pfx(spath):
            ont = fstags.ontology_for(path)
            tagged_path = fstags[path]
            all_tags = tagged_path.merged_tags()
            for autotag in tagged_path.infer_from_basename(filename_rules):
              U.out(spath + ' ' + str(autotag))
              if ont:
                autotag = ont.convert_tag(autotag)
              if autotag not in all_tags:
                tagged_path.add(autotag, verbose=state.verbose)
            if not isdir:
              try:
                S = os.stat(path)
              except OSError:
                pass
              else:
                tagged_path.add('filesize', S.st_size)
            # update the merged tags
            all_tags = tagged_path.merged_tags()
            for tag in fstags.cascade_tags(all_tags):
              if tag.name not in tagged_path:
                tagged_path.add(tag)

  def cmd_edit(self, argv):
    ''' Usage: {cmd} [-d] [path]
          Edit the direct tagsets of path, default: '.'
          If path is a directory, provide the tags of its entries.
          Otherwise edit just the tags for path.
          -d          Treat directories like files: edit just its tags.
    '''
    options = self.options
    fstags = options.fstags
    directories_like_files = False
    xit = 0
    opts, argv = getopt(argv, 'd')
    for opt, _ in opts:
      with Pfx(opt):
        if opt == '-d':
          directories_like_files = True
    if not argv:
      path = '.'
    else:
      path = argv.pop(0)
      if argv:
        raise GetoptError("extra arguments after path: %r" % (argv,))
    with state(verbose=True):
      with Pfx(path):
        if directories_like_files or not isdirpath(path):
          fstags[path].edit(verbose=state.verbose)
        elif not fstags.edit_dirpath(path):
          xit = 1
    return xit

  def cmd_export(self, argv):
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
    options = self.options
    fstags = options.fstags
    badopts = False
    all_paths = False
    use_direct_tags = False
    opts, argv = getopt(argv, 'a', longopts=['direct'])
    for opt, _ in opts:
      with Pfx(opt):
        if opt == '-a':
          all_paths = True
        elif opt == '--direct':
          use_direct_tags = True
        else:
          raise RuntimeError("unimplemented option")
    if not argv:
      warning("missing path")
      badopts = True
    else:
      path = argv.pop(0)
    tag_choices, argv = self.parse_tagset_criteria(argv)
    if argv:
      warning("extra arguments (invalid tag choices?): %r", argv)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    xit = 0
    csvw = csv.writer(sys.stdout)
    for filepath in fstags.find(realpath(path), tag_choices,
                                use_direct_tags=use_direct_tags):
      tagged_path = fstags[filepath]
      if (not all_paths
          and not (tagged_path if use_direct_tags else tagged_path.all_tags)):
        continue
      # TODO: this always writes the direct tags only
      csvw.writerow(tagged_path.csvrow)
    return xit

  # pylint: disable=too-many-branches
  def cmd_find(self, argv):
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
    options = self.options
    fstags = options.fstags
    badopts = False
    use_direct_tags = False
    as_rsync_includes = False
    output_format = FIND_OUTPUT_FORMAT_DEFAULT
    opts, argv = getopt(argv, 'o:', longopts=['direct', 'for-rsync'])
    for opt, value in opts:
      with Pfx(opt):
        if opt == '--direct':
          use_direct_tags = True
        elif opt == '--for-rsync':
          as_rsync_includes = True
        elif opt == '-o':
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
        tag_choices, argv = self.parse_tagset_criteria(argv)
      if argv:
        warning("extra arguments (invalid tag choices?): %r", argv)
        badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    xit = 0
    U = options.upd
    filepaths = fstags.find(
        realpath(path), tag_choices, use_direct_tags=use_direct_tags, U=U
    )
    if as_rsync_includes:
      for include in rsync_patterns(filepaths, path):
        print(include)
    else:
      for filepath in filepaths:
        with Pfx(filepath):
          try:
            output = fstags[filepath].format_as(
                output_format, error_sep='\n  ', direct=use_direct_tags
            )
          except FormatAsError as e:
            error(str(e))
            xit = 1
            continue
          print(output)
    return xit

  def cmd_import(self, argv):
    ''' Usage: {cmd} {{-|srcpath}}...
          Import CSV data in the format emitted by "export".
          Each argument is a file path or "-", indicating standard input.
    '''
    options = self.options
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
        te = TagSet.from_csvrow(csvrow)
        if convert_name:
          with Pfx("convert_name(%r)", te.name):
            path = convert_name(te.name)
        else:
          path = te.name
        self[path].update(te)

  # pylint: disable=too-many-branches
  def cmd_json_import(self, argv):
    ''' Usage: json_import --prefix=tag_prefix {{-|path}} {{-|tags.json}}
          Apply JSON data to path.
          A path named "-" indicates that paths should be read from
          the standard input.
          The JSON tag data come from the file "tags.json"; the name
          "-" indicates that the JSON data should be read from the
          standard input.
    '''
    options = self.options
    fstags = options.fstags
    tag_prefix = None
    path = None
    json_path = None
    badopts = False
    opts, argv = getopt(argv, '', longopts=['prefix='])
    for opt, value in opts:
      with Pfx(opt):
        if opt == '--prefix':
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
    with state(verbose=True):
      for path in paths:
        with Pfx(path):
          ont = fstags.ontology_for(path)
          tagged_path = fstags[path]
          for key, value in data.items():
            tag_name = '.'.join((tag_prefix, key)) if tag_prefix else key
            tagged_path.add(
                Tag(tag_name, value, ontology=ont), verbose=verbose
            )
    return 0

  def cmd_ls(self, argv):
    ''' Usage: {cmd} [-d] [--direct] [-o output_format] [paths...]
        List files from paths and their tags.
        -d          Treat directories like files, do not recurse.
        --direct    List direct tags instead of all tags.
        -o output_format
                    Use output_format as a Python format string to lay out
                    the listing.
                    Default: {LS_OUTPUT_FORMAT_DEFAULT}
    '''
    options = self.options
    fstags = options.fstags
    directories_like_files = False
    use_direct_tags = False
    output_format = LS_OUTPUT_FORMAT_DEFAULT
    opts, argv = getopt(argv, 'do:', longopts=['direct'])
    for opt, value in opts:
      with Pfx(opt):
        if opt == '-d':
          directories_like_files = True
        elif opt == '--direct':
          use_direct_tags = True
        elif opt == '-o':
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

  def cmd_cp(self, argv):
    ''' Usage: {cmd} [-finv] srcpath dstpath, {cmd} [-finv] srcpaths... dstdirpath
          POSIX cp(1) equivalent, but also copying tags:
          copy files and their tags into targetdir.
          -f  Force: remove destination if it exists.
          -i  Interactive: fail if the destination exists.
          -n  No remove: fail if the destination exists.
          -v  Verbose: show copied files.
    '''
    return self._cmd_mvcpln(argv, self.options.fstags.copy)

  def cmd_ln(self, argv):
    ''' Usage: {cmd} [-finv] srcpath dstpath, {cmd} [-finv] srcpaths... dstdirpath
          POSIX ln(1) equivalent, but also copying the tags:
          link files and their tags into targetdir.
          -f  Force: remove destination if it exists.
          -i  Interactive: fail if the destination exists.
          -n  No remove: fail if the destination exists.
          -v  Verbose: show linked files.
    '''
    return self._cmd_mvcpln(argv, self.options.fstags.link)

  def cmd_mv(self, argv):
    ''' Usage: {cmd} [-finv] srcpath dstpath, {cmd} [-finv] srcpaths... dstdirpath
          POSIX mv(1) equivalent, but also copying the tags:
          move files and their tags into targetdir.
          -f  Force: remove destination if it exists.
          -i  Interactive: fail if the destination exists.
          -n  No remove: fail if the destination exists.
          -v  Verbose: show moved files.
    '''
    return self._cmd_mvcpln(argv, self.options.fstags.move)

  # pylint: disable=too-many-branches
  def _cmd_mvcpln(self, argv, attach):
    ''' Move/copy/link paths and their tags into a destination.
    '''
    xit = 0
    cmd_force = False
    cmd_verbose = False
    opts, argv = getopt(argv, 'finv')
    for subopt, _ in opts:
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
      with state(verbose=True):
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
      with state(verbose=True):
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

  def cmd_ns(self, argv):
    ''' Usage: {cmd} [-d] [--direct] [paths...]
          Report on the available primary namespace fields for formatting.
          Note that because the namespace used for formatting has
          inferred field names there are also unshown secondary field
          names available in format strings.
          -d          Treat directories like files, do not recurse.
          --direct    List direct tags instead of all tags.
    '''
    options = self.options
    fstags = options.fstags
    directories_like_files = False
    use_direct_tags = False
    opts, argv = getopt(argv, 'd', longopts=['direct'])
    for opt, _ in opts:
      with Pfx(opt):
        if opt == '-d':
          directories_like_files = True
        elif opt == '--direct':
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

  def cmd_ont(self, argv):
    ''' Ontology operations.

        Usage: {cmd} [subcommand [args...]]
          With no arguments, print the ontology.
    '''
    options = self.options
    ont_path = options.ontology_path
    if ont_path is None or isdirpath(ont_path):
      ont = options.fstags.ontology_for(ont_path or '.')
    else:
      raise GetoptError(
          "unhandled ontology path, expected directory: %r" % (ont_path,)
      )
    if not argv:
      print(ont)
      return
    with stackattrs(options, ontology=ont):
      TagsOntologyCommand().run([options.cmd] + argv, options=options)
    return

  def cmd_rename(self, argv):
    ''' Usage: {cmd} -n newbasename_format paths...
          Rename paths according to a format string.
          -n newbasename_format
              Use newbasename_format as a Python format string to
              compute the new basename for each path.
    '''
    xit = 0
    options = self.options
    fstags = options.fstags
    name_format = None
    opts, argv = getopt(argv, 'n:')
    for subopt, value in opts:
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
    U = options.upd
    with state(verbose=True):
      for filepath in paths:
        U.out(filepath)
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
    return xit

  def cmd_scrub(self, argv):
    ''' Usage: {cmd} paths...
          Remove all tags for missing paths.
          If a path is a directory, scrub the immediate paths in the directory.
    '''
    if not argv:
      raise GetoptError("missing paths")
    fstags = self.options.fstags
    with state(verbose=True):
      for path in argv:
        fstags.scrub(path)

  def cmd_tag(self, argv):
    ''' Usage: {cmd} {{-|path}} {{tag[=value]|-tag}}...
          Tag a path with multiple tags.
          With the form "-tag", remove that tag from the direct tags.
          A path named "-" indicates that paths should be read from the
          standard input.
    '''
    badopts = False
    fstags = self.options.fstags
    if not argv:
      raise GetoptError("missing path")
    path = argv.pop(0)
    if not argv:
      raise GetoptError("missing tags")
    try:
      tag_choices = self.parse_tag_choices(argv)
    except ValueError as e:
      raise GetoptError(str(e))  # pylint: disable=raise-missing-from
    if badopts:
      raise GetoptError("bad arguments")
    if path == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = [path]
    with state(verbose=True):
      fstags.apply_tag_choices(tag_choices, paths)

  def cmd_tagfile(self, argv):
    ''' Usage: {cmd} tagfile_path [subcommand ...]
          Subcommands:
            tag tagset_name {{tag[=value]|-tag}}...
              Directly modify tag_name within the tag file tagfile_path.
    '''
    fstags = self.options.fstags
    try:
      tagfilepath = argv.pop(0)
    except IndexError:
      raise GetoptError("missing tagfile_path")  # pylint: disable=raise-missing-from
    with Pfx(tagfilepath):
      try:
        subcmd = argv.pop(0)
      except IndexError:
        raise GetoptError("missing subcommand")  # pylint: disable=raise-missing-from
      with Pfx(subcmd):
        if subcmd == 'tag':
          try:
            tagset_name = argv.pop(0)
          except IndexError:
            raise GetoptError("missing tagset_name")  # pylint: disable=raise-missing-from
          with Pfx(tagset_name):
            if not argv:
              raise GetoptError("missing tags")
            badopts = False
            tag_choices, argv = self.parse_tagset_criteria(argv)
            if argv:
              warning("extra arguments (invalid tag choices?): %r", argv)
              badopts = True
            if badopts:
              raise GetoptError("bad arguments")
            with state(verbose=True):
              with FSTagsTagFile(tagfilepath, fstags=fstags) as tagfile:
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

  def cmd_tagpaths(self, argv, options):
    ''' Usage: {cmd} {{tag[=value]|-tag}} {{-|paths...}}
        Tag multiple paths.
        With the form "-tag", remove the tag from the immediate tags.
        A single path named "-" indicates that paths should be read
        from the standard input.
    '''
    badopts = False
    if not argv:
      warning("missing tag choice")
      badopts = True
    else:
      tag_choice_s = argv.pop(0)
      with Pfx(repr(tag_choice_s)):
        try:
          tag_choice = self.parse_tagset_criterion(tag_choice_s)
        except ValueError as e:
          warning(e)
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
    with state(verbose=True):
      self.options.fstags.apply_tag_choices([tag_choice], paths)

  def cmd_test(self, argv):
    ''' Usage: {cmd} [--direct] path {{tag[=value]|-tag}}...
          Test whether the path matches all the constraints.
          --direct    Use direct tags instead of all tags.
    '''
    badopts = False
    use_direct_tags = False
    opts, argv = getopt(argv, '', longopts=['direct'])
    for opt, _ in opts:
      with Pfx(opt):
        if opt == '--direct':
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
        tag_choices, argv = self.parse_tagset_criteria(argv)
        if argv:
          warning("extra arguments (invalid tag choices?): %r", argv)
          badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    return (
        0 if self.options.fstags
        .test(path, tag_choices, use_direct_tags=use_direct_tags) else 1
    )

  def cmd_xattr_export(self, argv):
    ''' Usage: {cmd} {{-|paths...}}
          Import tag information from extended attributes.
    '''
    if not argv:
      raise GetoptError("missing paths")
    if len(argv) == 1 and argv[0] == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = argv
    self.options.fstags.export_xattrs(paths)

  def cmd_xattr_import(self, argv, options):
    ''' Usage: {cmd} {{-|paths...}}
          Update extended attributes from tags.
    '''
    if not argv:
      raise GetoptError("missing paths")
    if len(argv) == 1 and argv[0] == '-':
      paths = [line.rstrip('\n') for line in sys.stdin]
    else:
      paths = argv
    with state(verbose=True):
      self.options.fstags.import_xattrs(paths)

FSTagsCommand.add_usage_to_docstring()

# pylint: disable=too-many-public-methods
class FSTags(MultiOpenMixin):
  ''' A class to examine filesystem tags.
  '''

  def __init__(self, tagsfile_basename=None, ontologyfile=None):
    MultiOpenMixin.__init__(self)
    if tagsfile_basename is None:
      tagsfile_basename = TAGSFILE_BASENAME
    if ontologyfile is None:
      ontologyfile = tagsfile_basename + '-ontology'
    self.config = FSTagsConfig()
    self.config.tagsfile_basename = tagsfile_basename
    self.config.ontologyfile = ontologyfile
    self._tagfiles = {}  # cache of `FSTagsTagFile`s from their actual paths
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
  @pfx_method
  def sync(self):
    ''' Flush modified tag files.
    '''
    for tagfile in self._tagfiles.values():
      try:
        tagfile.save()
      except FileNotFoundError as e:
        error("%s.save: %s", tagfile, e)

  @typechecked
  def _tagfile(
      self, path: str, *, no_ontology: bool = False
  ) -> "FSTagsTagFile":
    ''' Obtain and cache the `FSTagsTagFile` at `path`.
    '''
    ontology = None if no_ontology else self.ontology_for(path)
    tagfile = self._tagfiles[path] = FSTagsTagFile(
        path, ontology=ontology, fstags=self
    )
    return tagfile

  @property
  def tagsfile_basename(self):
    ''' The tag file basename.
    '''
    return self.config.tagsfile_basename

  @property
  def ontologyfile(self):
    ''' The ontology file basename.
    '''
    return self.config.ontologyfile

  def __str__(self):
    return "%s(tagsfile_basename=%r)" % (
        type(self).__name__, self.tagsfile_basename
    )

  @locked
  def __getitem__(self, path):
    ''' Return the `TaggedPath` for `abspath(path)`.
    '''
    path = abspath(path)
    tagged_path = self._tagged_paths.get(path)
    if tagged_path is None:
      tagfile = self.tagfile_for(path)
      tagged_path = self._tagged_paths[path] = tagfile[basename(path)]
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

  @property
  def ontology(self):
    ''' The primary `TagsOntology`, or `None` if `self.ontologyfile` was `None`.
    '''
    if not self.ontologyfile:
      return None
    ont_tagfile = self._tagfile(self.ontologyfile, no_ontology=True)
    ont = TagsOntology(ont_tagfile)
    ont_tagfile.ontology = ont
    return ont

  @locked
  def ontology_for(self, path):
    ''' Return the `TagsOntology` associated with `path`.
        Returns `None` if an ontology cannot be found.
    '''
    ont = self.ontology
    if ont:
      return ont
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
        ont_tagfile = self._tagfile(ontpath, no_ontology=True)
        ont = TagsOntology(ont_tagfile)
        ont_tagfile.ontology = ont
        cache[dirpath] = ont
    return ont

  def path_tagfiles(self, filepath):
    ''' Generator yielding a sequence of `(FSTagsTagFile,name)` pairs
        where `name` is the key within the `FSTagsTagFile`
        for the `FSTagsTagFile`s affecting `filepath`
        in order from the root to `dirname(filepath)`.
    '''
    absfilepath = abspath(filepath)
    root, *subparts = PurePath(absfilepath).parts
    if not subparts:
      raise ValueError("root=%r and no subparts" % (root,))
    current = root
    while subparts:
      next_part = subparts.pop(0)
      yield self.dir_tagfile(current), next_part
      current = joinpath(current, next_part)

  @locked
  @typechecked
  def dir_tagfile(self, dirpath: str) -> "FSTagsTagFile":
    ''' Return the `FSTagsTagFile` associated with `dirpath`.
    '''
    return self._tagfile(joinpath(abspath(dirpath), self.tagsfile_basename))

  def tagfile_for(self, filepath):
    ''' Return the `FSTagsTagFile` storing the `Tag`s for `filepath`.
    '''
    return self.dir_tagfile(dirname(abspath(filepath)))

  def apply_tag_choices(self, tag_choices, paths):
    ''' Apply the `tag_choices` to `paths`.

        Parameters:
        * `tag_choices`:
          an iterable of `Tag` or an equality `TagBasedTest`.
          Each item applies or removes a `Tag`
          from each path's direct tags.
        * `paths`:
          an iterable of filesystem paths.
    '''
    tag_choices = [
        TagBasedTest(str(tag_choice), True, tag_choice, '=')
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

  def find(self, path, tag_tests, use_direct_tags=False, U=None):
    ''' Walk the file tree from `path`
        searching for files matching the supplied `tag_tests`.
        Yield the matching file paths.

        Parameters:
        * `path`: the top of the file tree to walk
        * `tag_tests`: an iterable of `TagBasedTest`s
        * `use_direct_tags`: test the direct_tags if true,
          otherwise the all_tags.
          Default: `False`
    '''
    for _, filepath in rpaths(path, yield_dirs=use_direct_tags, U=U):
      if self.test(filepath, tag_tests, use_direct_tags=use_direct_tags):
        yield filepath

  def test(self, path, tag_tests, use_direct_tags=False):
    ''' Test a path against `tag_tests`.

        Parameters:
        * `path`: path to test
        * `tag_tests`: an iterable of `TagBasedTest`s
        * `use_direct_tags`: test the `direct_tags` if true,
          otherwise the `all_tags`.
          Default: `False`
    '''
    tagged_path = self[path]
    te = tagged_path.as_tags(all_tags=not use_direct_tags)
    return all(criterion.match_tagged_entity(te) for criterion in tag_tests)

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
    for name in names:
      if not name or os.sep in name:
        warning("skip bogus name %r", name)
        continue
      path = joinpath(dirpath, name)
      tagged_path = self[path]
      tes.append(tagged_path)
    # edit entities, return modified entities
    changed_tes = TagSet.edit_many(tes)  # verbose-state.verbose
    # now apply any file renames
    for old_name, new_name, te in changed_tes:
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
            new_tagged_path = self[new_path]
            new_tagged_path.set_from(te)
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

  # pylint: disable=too-many-branches
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
      for tag in src_taggedpath:
        dst_taggedpath.add(tag)
      try:
        dst_taggedpath.save()
      except OSError as e:
        if e.errno == errno.EACCES:
          warning("save tags: %s", e)
          dst_taggedpath.modified = old_modified
        else:
          raise
      return result

# pylint: disable=too-few-public-methods
class HasFSTagsMixin:
  ''' Mixin providing an automatic `.fstags` property.
  '''

  @property
  def fstags(self):
    ''' Return the `.fstags` property,
        default a shared default `FSTags` instance.
    '''
    _fstags = self.__dict__.get('_fstags')
    if _fstags is None:
      _fstags = self.__dict__['_fstags'] = FSTags()
    return _fstags

  @fstags.setter
  def fstags(self, new_fstags):
    ''' Set the `.fstags` property.
    '''
    self._fstags = new_fstags

class TaggedPath(TagSet, HasFSTagsMixin):
  ''' Class to manipulate the tags for a specific path.
  '''

  def __init__(self, filepath, fstags=None, _id=None, _ontology=None):
    if _ontology is None:
      _ontology = fstags.ontology_for(filepath)
    self.__dict__.update(
        _fstags=fstags,
        filepath=filepath,
        _lock=Lock(),
        _all_tags=None,
        tagfile=None
    )
    super().__init__(_id=_id, _ontology=_ontology)

  def __repr__(self):
    return "%s(%s):%r" % (type(self).__name__, self.filepath, self.as_dict())

  def __str__(self):
    return Tag.transcribe_value(str(self.filepath)) + ' ' + str(self.all_tags)

  @property
  def name(self):
    ''' The `.name` is `basename(self.filepath)`.
    '''
    return basename(self.filepath)

  # pylint: disable=redefined-outer-name
  @tag_or_tag_value
  def discard(self, tag_name, value, *, verbose=None):
    assert tag_name != 'name'
    super().discard(tag_name, value, verbose=verbose)

  def set(self, tag_name, value, **kw):
    assert tag_name != 'name'
    super().set(tag_name, value, **kw)

  # pylint: disable=arguments-differ
  def as_tags(self, prefix=None, all_tags=False):
    ''' Yield the tag data as `Tag`s.

        This overrides `TagSet.as_tags`,
        honouring an `optional `all_tags` parameter.
    '''
    if not all_tags:
      return super().as_tags(prefix=prefix)
    tags = self.all_tags
    if not prefix:
      return tags
    return tags.as_tags(prefix=prefix)

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
    kwtags = TagSet(_ontology=ont)
    kwtags.update(self if direct else self.all_tags)
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
    return basename(self.filepath)

  @property
  def tagfile(self):
    ''' Return the `FSTagsTagFile` storing the state for this `TaggedPath`.
    '''
    return self.fstags.tagfile_for(self.filepath)

  def save(self):
    ''' Update the associated `FSTagsTagFile`.
    '''
    self.tagfile.save()

  def merged_tags(self):
    ''' Compute the cumulative tags for this path as a `TagSet`
        by merging the tags from the root to the path.
    '''
    tags = TagSet(_ontology=self.ontology)
    with state(verbose=False):
      for tagfile, name in self.fstags.path_tagfiles(self.filepath):
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

  def infer_from_basename(self, rules=None):
    ''' Apply `rules` to the basename of this `TaggedPath`,
        return a `TagSet` of inferred `Tag`s.

        Tag values from earlier rules override values from later rules.
    '''
    if rules is None:
      rules = self.fstags.config.filename_rules
    name = self.basename
    tagset = TagSet(_ontology=self.ontology)
    with state(verbose=False):
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
      return TagSet(_ontology=self.ontology)
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
    for tag in xa_tags:
      if tag not in all_tags:
        self.add(tag)

  def export_xattrs(self):
    ''' Update the extended attributes of the file.
    '''
    filepath = self.filepath
    all_tags = self.all_tags
    update_xattr_value(filepath, XATTR_B, str(self))
    # export tags to other xattrs
    for xattr_name, tag_name in self.fstags.config['xattr'].items():
      tag_value = all_tags.get(tag_name)
      update_xattr_value(
          filepath, xattr_name, None if tag_value is None else str(tag_value)
      )

class FSTagsTagFile(TagFile, HasFSTagsMixin):
  ''' A `FSTagsTagFile` indexing `TagSet`s for file paths
      which lives in the file path's directory.
  '''

  @typechecked
  def __init__(self, filepath: str, *, ontology=Ellipsis, fstags=None):
    if ontology is Ellipsis:
      ontology = fstags.ontology
    self.__dict__.update(_fstags=fstags)
    super().__init__(filepath, ontology=ontology)

  @typechecked
  @require(
      lambda name: is_valid_basename(name),  # pylint: disable=unnecessary-lambda
      "name should be a clean file basename"
  )
  def TagSetClass(self, name: str) -> TaggedPath:
    ''' factory to create a `TaggedPath` from a `name`.
    '''
    filepath = joinpath(dirname(self.filepath), name)
    return TaggedPath(filepath, fstags=self.fstags)

  @property
  def dirpath(self):
    ''' Return the path of the directory associated with this `FSTagsTagFile`.
    '''
    return dirname(self.filepath)

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
    if U:
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
    ''' Read an rc file, return a `ConfigParser` instance.
    '''
    with Pfx(rcfilepath):
      config = ConfigParser()
      config.add_section('filename_autotag')
      config.add_section('cascade')
      config.add_section('general')
      config.add_section('xattr')
      config['general']['tagsfile'] = TAGSFILE_BASENAME
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
  def tagsfile_basename(self):
    ''' The tags filename, default `{TAGSFILE_BASENAME!r}`.
    '''
    return self.config.get('general', 'tagsfile') or TAGSFILE_BASENAME

  @tagsfile_basename.setter
  def tagsfile_basename(self, tagsfile_basename):
    ''' Set the tags filename.
    '''
    self.config['general']['tagsfile'] = tagsfile_basename

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

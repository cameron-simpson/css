#!/usr/bin/env python3

''' Simple filesystem based file tagging
    and the associated `fstags` command line script.

    Tags are stored in the file `.fstags` in each directory;
    there is a line for each entry in the directory with tags
    consisting of the directory entry name and the associated tags.

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

    Tags may be "bare", or have a value.
    If there is a value it is expressed with an equals (`'='`)
    followed by the JSON encoding of the value.
'''

from collections import defaultdict, namedtuple
from datetime import date, datetime
import errno
from getopt import getopt, GetoptError
import json
from json import JSONEncoder, JSONDecoder
import os
from os.path import (
    basename, dirname, exists as existspath, expanduser, isdir as isdirpath,
    isfile as isfilepath, join as joinpath, realpath, relpath
)
from pathlib import Path
import re
import shutil
import sys
from threading import Lock
from cs.cmdutils import BaseCommand
from cs.deco import fmtdoc
from cs.edit import edit_strings
from cs.lex import (
    get_dotted_identifier, get_nonwhite, is_dotted_identifier, skipwhite
)
from cs.logutils import setup_logging, error, warning, info
from cs.pfx import Pfx, pfx_method
from cs.threads import locked, locked_property
from icontract import require

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
        'cs.cmdutils', 'cs.deco', 'cs.edit', 'cs.lex', 'cs.logutils', 'cs.pfx',
        'cs.threads', 'icontract'
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

class FSTagsCommand(BaseCommand):
  ''' `fstags` main command line class.
  '''

  GETOPT_SPEC = ''
  USAGE_FORMAT = '''Usage:
    {cmd} autotag paths...
        Tag paths based on rules from the rc file.
    {cmd} find [--for-rsync] path {{tag[=value]|-tag}}...
        List files from path matching all the constraints.
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
    {cmd} mv paths... targetdir
        Move files and their tags into targetdir.
    {cmd} tag path {{tag[=value]|-tag}}...
        Associate tags with a path.
        With the form "-tag", remove the tag from the immediate tags.
    {cmd} tagpaths {{tag[=value]|-tag}} paths...
        Associate a tag with multiple paths.
        With the form "-tag", remove the tag from the immediate tags.
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
    rules = loadrc()
    for top_path in argv:
      for path in rpaths(top_path):
        with Pfx(path):
          tagged_path = TaggedPath(path, fstags)
          for autotag in tagged_path.autotag(rules):
            print("autotag %r + %s" % (tagged_path.basename, autotag))

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
    filepaths = fstags.find(path, tag_choices, use_direct_tags=use_direct_tags)
    if as_rsync_includes:
      for include in rsync_patterns(filepaths, path):
        print(include)
    else:
      for filepath in filepaths:
        print(
            output_format.format(
                **TaggedPath(filepath, fstags=fstags
                             ).format_kwargs(direct=use_direct_tags)
            )
        )

  @staticmethod
  def cmd_ls(argv, options, *, cmd):
    ''' List paths and their tags.
    '''
    fstags = options.fstags
    use_direct_tags = False
    output_format = LS_OUTPUT_FORMAT_DEFAULT
    options, argv = getopt(argv, 'o:', longopts=['direct'])
    for option, value in options:
      with Pfx(option):
        if option == '--direct':
          use_direct_tags = True
        elif option == '-o':
          output_format = value
        else:
          raise RuntimeError("unsupported option")
    paths = argv or ['.']
    for path in paths:
      with Pfx(path):
        for filepath in rfilepaths(path):
          print(
              output_format.format(
                  **TaggedPath(filepath, fstags=fstags
                               ).format_kwargs(direct=use_direct_tags)
              )
          )

  @staticmethod
  def cmd_mv(argv, options, *, cmd):
    ''' Move paths and their tags into a destination.
    '''
    if len(argv) < 2:
      raise GetoptError("missing paths or targetdir")
    target_dirpath = argv.pop()
    if not isdirpath(target_dirpath):
      raise GetoptError("targetdir %r: not a directory" % (target_dirpath,))
    xit = 0
    for path in argv:
      with Pfx(path):
        if not existspath(path):
          error("path does not exist")
          xit = 1
          continue
        src_tags = TaggedPath(path).direct_tags
        src_basename = basename(path)
        target_path = joinpath(target_dirpath, src_basename)
        with Pfx("=>%r", target_path):
          if existspath(target_path):
            error("target already exists")
            xit = 1
            continue
          print(path, '=>', target_path)
          dst_taggedpath = TaggedPath(target_path)
          with Pfx("shutil.move"):
            shutil.move(path, target_path)
          for tag in src_tags:
            dst_taggedpath.add(tag)
          dst_taggedpath.save()
    return xit

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
    fstags.apply_tag_choices(tag_choices, [path])

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
    fstags.apply_tag_choices(tag_choices, argv)

class Tag:
  ''' A Tag has a `.name` (`str`) and a `.value`.

      The `name` must be a dotted identifier.

      A "bare" `Tag` has a `value` of `None`.
  '''

  __slots__ = ('name', 'value')

  @require(lambda name: isinstance(name, str))
  def __init__(self, name, value):
    self.name = name
    self.value = value

  def __eq__(self, other):
    return self.name == other.name and self.value == other.value

  def __lt__(self, other):
    if self.name < other.name:
      return True
    if self.name > other.name:
      return False
    return self.value < other.value

  def __repr__(self):
    return "%s(name=%r,value=%r)" % (
        type(self).__name__, self.name, self.value
    )

  def __str__(self):
    ''' Encode `tag_name` and `value`.
    '''
    name = self.name
    value = self.value
    if value is None:
      return name
    if isinstance(value, str):
      # bare dotted identifiers
      if is_dotted_identifier(value):
        return name + '=' + value
    for type_, parse, transcribe in self.EXTRA_TYPES:
      if isinstance(value, type_):
        return name + '=' + transcribe(value)
    value = self.for_json(value)
    encoder = self.json_encoder()
    return name + '=' + encoder.encode(value)

  @classmethod
  def from_name_value(cls, name, value):
    ''' Support method for functions accepting either a tag or a name and value.

        If `name` is a str make a new Tag from `name` and `value`.
        Otherwise check that `value is `None`
        and that `name` has a `.name` and `.value`
        and return it as a tag ducktype.

        This supports functions of the form:

            def f(x, y, tag_name, value=None):
              tag = Tag.from_name_value(tag_name, value)

        so that that may accept a `Tag` or a tag name or a tag name and value.

        Exanples:

            >>> Tag.from_name_value('a', 3)
            Tag(name='a',value=3)
            >>> T = Tag('b', None)
            >>> Tag.from_name_value(T, None)
            Tag(name='b',value=None)
    '''
    with Pfx("%s.from_name_value(name=%r,value=%r)", cls.__name__, name,
             value):
      if isinstance(name, str):
        # (name,value) => Tag
        return cls(name, value)
      if value is not None:
        raise ValueError("name is not a str, value must be None")
      tag = name
      if not hasattr(tag, 'name'):
        raise ValueError("tag has no .name attribute")
      if not hasattr(tag, 'value'):
        raise ValueError("tag has no .value attribute")
      # Tag ducktype
      return tag

  @staticmethod
  def is_valid_name(name):
    ''' Test whether a tag name is valid: a dotted identifier including dash.
    '''
    return is_dotted_identifier(name, extras='_-')

  @staticmethod
  def parse_name(s, offset=0):
    ''' Parse a tag name from `s` at `offset`: a dotted identifier including dash.
    '''
    return get_dotted_identifier(s, offset=offset, extras='_-')

  EXTRA_TYPES = [
      (date, date.fromisoformat, date.isoformat),
      (datetime, datetime.fromisoformat, datetime.isoformat),
  ]

  @classmethod
  def from_json(cls, value):
    ''' Convert various formats to richer types.
    '''
    for type_, parse, transcribe in cls.EXTRA_TYPES:
      try:
        return parse(value)
      except (ValueError, TypeError):
        pass
    return value

  @classmethod
  def for_json(cls, obj):
    ''' Convert from types to strings for JSON.
    '''
    for type_, parse, transcribe in cls.EXTRA_TYPES:
      if isinstance(obj, type_):
        return transcribe(obj)
    return obj

  @staticmethod
  def json_encoder():
    ''' Prepare a JSON encoder for tag values.
    '''
    return JSONEncoder(separators=(',', ':'))

  def matches(self, tag_name, value=None):
    ''' Test whether this `Tag` matches `(tag_name,value)`.
    '''
    other_tag = self.from_name_value(tag_name, value)
    if self.name != other_tag.name:
      return False
    return other_tag.value is None or self.value == other_tag.value

  @classmethod
  def parse(cls, s, offset=0):
    ''' Parse tag_name[=value], return `(tag,offset)`.
    '''
    decoder = JSONDecoder()
    with Pfx("%s.parse(%r)", cls.__name__, s[offset:]):
      name, offset = cls.parse_name(s, offset)
      with Pfx(name):
        if offset < len(s):
          sep = s[offset]
          if sep.isspace():
            value = None
          elif sep == '=':
            offset += 1
            if offset >= len(s) or s[offset].isspace():
              warning("offset %d: missing value part", offset)
              value = None
            elif s[offset].isalpha():
              value, offset = cls.parse_name(s, offset)
            else:
              nonwhite, nw_offset = get_nonwhite(s, offset)
              nw_value = None
              for type_, parse, transcribe in cls.EXTRA_TYPES:
                try:
                  nw_value = parse(nonwhite)
                except ValueError:
                  pass
              if nw_value is not None:
                value = nw_value
                offset = nw_offset
              else:
                value_part = s[offset:]
                value, suboffset = decoder.raw_decode(value_part)
                offset += suboffset
                value = cls.from_json(value)
          else:
            name_end, offset = get_nonwhite(s, offset)
            name += name_end
            value = None
            ##warning("bad separator %r, adjusting tag to %r" % (sep, name))
        else:
          value = None
      return cls(name, value), offset

class TagChoice(namedtuple('TagChoice', 'spec choice tag')):
  ''' A "tag choice", an apply/reject flag and a `Tag`,
      used to apply changes to a `TagSet`
      or as a criterion for a tag search.

      Attributes:
      * `spec`: the source text from which this choice was parsed,
        possibly `None`
      * `choice`: the apply/reject flag
      * `tag`: the `Tag` representing the criterion
  '''

  @classmethod
  def parse(cls, s, offset=0):
    ''' Parse a tag choice from `s` at `offset` (default `0`).
        Return the `TagChoice` and new offset.
    '''
    offset0 = offset
    if s.startswith('-', offset):
      choice = False
      offset += 1
    else:
      choice = True
    tag, offset = Tag.parse(s, offset=offset)
    return cls(s[offset0:offset], choice, tag), offset

class TagSet:
  ''' A setlike class associating a set of tag names with values.
      A `TagFile` maintains one of these for each name.
  '''

  def __init__(self, *, defaults=None):
    ''' Initialise the `TagSet`.

        Parameters:
        * `defaults`: a mapping of name->TagSet to provide default values.
    '''
    if defaults is None:
      defaults = {}
    self.tagmap = {}
    self.defaults = defaults

  def __str__(self):
    ''' The `TagSet` suitable for writing to a tag file.
    '''
    return ' '.join(sorted(str(T) for T in self.as_tags()))

  def __repr__(self):
    return "%s[%s]" % (
        type(self).__name__, ','.join(str(T) for T in self.as_tags())
    )

  @classmethod
  def from_line(cls, line, offset=0):
    ''' Create a new `TagSet` from a line of text.
    '''
    tags = cls()
    offset = skipwhite(line, offset)
    while offset < len(line):
      tag, offset = Tag.parse(line, offset)
      tags.add(tag)
      offset = skipwhite(line, offset)
    return tags

  @classmethod
  def from_bytes(cls, bs):
    ''' Create a new `TagSet` from the bytes `bs`,
        a UTF-8 encoding of a `TagSet` line.
    '''
    line = bs.decode(errors='replace')
    return cls.from_line(line)

  def __contains__(self, tag):
    tagmap = self.tagmap
    if isinstance(tag, str):
      return tag in tagmap
    for mytag in self.as_tags():
      if mytag.matches(tag):
        return True
    return False

  def __getitem__(self, tag_name):
    ''' Fetch tag value by `tag_name`.
        Raises `KeyError` for missing `tag_name`.
    '''
    try:
      return self.tagmap[tag_name]
    except KeyError:
      return self.defaults[tag_name]

  def get(self, tag_name, default=None):
    ''' Fetch tag value by `tag_name`, or `default`.
    '''
    try:
      value = self[tag_name]
    except KeyError:
      value = default
    return value

  def as_tags(self):
    ''' Yield the tag data as `Tag`s.
    '''
    for tag_name, value in self.tagmap.items():
      yield Tag(tag_name, value)

  __iter__ = as_tags

  def add(self, tag_name, value=None):
    ''' Add a tag to these tags.
    '''
    tag = Tag.from_name_value(tag_name, value)
    self.tagmap[tag.name] = tag.value

  def discard(self, tag_name, value=None):
    ''' Discard the tag matching `(tag_name,value)`.
        Return a `Tag` with the old value,
        or `None` if there was no matching tag.

        Note that if the tag value is `None`
        then the tag is unconditionally discarded.
        Otherwise the tag is only discarded
        if its value matches.
    '''
    tag = Tag.from_name_value(tag_name, value)
    tag_name = tag.name
    if tag_name in self:
      tagmap = self.tagmap
      value = tag.value
      if value is None or tagmap[tag_name] == value:
        old_value = tagmap.pop(tag_name)
      return Tag(tag_name, old_value)
    return None

  def update(self, other):
    ''' Update this `TagSet` from `other`,
        a dict or an iterable of taggy things.
    '''
    if isinstance(other, dict):
      self.update(Tag.from_name_value(k, v) for k, v in other.items())
    else:
      for tag in other:
        self.add(tag)

  # Assorted computed properties.

  def titleify(self, tag_name):
    ''' Return the tag value for `tag_name`.
        If this is empty or missing,
        look at `tag_name+'_lc'`;
        if not empty
        replace the dashes with spaces and titlecase it.
    '''
    value = self.get(tag_name)
    if value:
      return value
    value_lc = self.get(tag_name + '_lc')
    if value_lc:
      return value_lc.replace('-', ' ').title()
    return None

  @property
  def episode_title(self):
    ''' File title.
    '''
    return self.titleify('episode_title')

  @property
  def title(self):
    ''' File title.
    '''
    return self.titleify('title')

  @staticmethod
  @fmtdoc
  def xattr_value(filepath, xattr_name=None):
    ''' Read the extended attribute `xattr_name` of `filepath`.

        Return the extended attribute value as a string,
        or `None` if the attribute does not exist.

        Parameters:
        * `filepath`: the filesystem path to update
        * `xattr_name`: the extended attribute to update;
          if this is a `str`, the attribute is the UTF-8 encoding of that name.
          The default is `XATTR_B` ({XATTR_B!r}).
    '''
    if xattr_name is None:
      xattr_name_b = XATTR_B
    elif isinstance(xattr_name, str):
      xattr_name_b = xattr_name.encode()
    else:
      xattr_name_b = xattr_name
    with Pfx("getxattr(%r,%r)", filepath, xattr_name_b):
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

  @classmethod
  @fmtdoc
  def set_xattr_value(cls, filepath, new_xattr_value, xattr_name=None):
    ''' Update the extended attributes of `filepath`
        with `new_xattr_value`,
        the transcription of a `TagSet` as a string.

        Return the extended attribute value as a string,
        or `None` if the attribute does not exist.

        Parameters:
        * `filepath`: the filesystem path to update
        * `new_xattr_value`: the new extended attribute value, a `str`
          which should be the transcription of `TagSet`
          i.e. `str(tagset)`
        * `xattr_name`: the extended attribute to update;
          if this is a `str`, the attribute is the UTF-8 encoding of that name.
          The default is `XATTR_B` ({XATTR_B!r}).
    '''
    if xattr_name is None:
      xattr_name_b = XATTR_B
    elif isinstance(xattr_name, str):
      xattr_name_b = xattr_name.encode()
    else:
      xattr_name_b = xattr_name
    old_xattr_value = cls.xattr_value(filepath, xattr_name=xattr_name)
    if old_xattr_value is None or old_xattr_value != new_xattr_value:
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

  @fmtdoc
  def update_xattr(self, filepath, xattr_name=None):
    ''' Update the extended attributes of `filepath`
        with the tags from this `TagSet`.

        Parameters:
        * `filepath`: the filesystem path to update
        * `xattr_name`: the extended attribute to update;
          if this is a `str`, the attribute is the UTF-8 encoding of that name.
          The default is `XATTR_B` ({XATTR_B!r}).
    '''
    old_xattr_value = self.xattr_value(filepath, xattr_name=xattr_name)
    new_xattr_value = str(self)
    if old_xattr_value is None or old_xattr_value != new_xattr_value:
      self.set_xattr_value(filepath, new_xattr_value, xattr_name=xattr_name)

  @classmethod
  def from_xattr(cls, filepath, xattr_name=None):
    ''' Create a new `TagSet`
        from the extended attribute `xattr_name` of `filepath`.
    '''
    xattr_s = cls.xattr_value(filepath, xattr_name)
    if xattr_s is None:
      return cls()
    return cls.from_line(xattr_s)

  @fmtdoc
  def update_from_xattr(self, filepath, xattr_name=None):
    ''' Update the `TagSet` from the extended attributes of `filepath`.

        Parameters:
        * `filepath`: the filesystem path to update
        * `xattr_name`: the extended attribute to update;
          if this is a `str`, the attribute is the UTF-8 encoding of that name.
          The default is `XATTR_B` ({XATTR_B!r}).
    '''
    xattr_tags = self.from_xattr(filepath, xattr_name=xattr_name)
    self.update(xattr_tags)

class TagFile:
  ''' A reference to a specific file containing tags.

      This manages a mapping of `name` => `TagSet`,
      itself a mapping of tag name => tag value.
  '''

  @require(lambda filepath: isinstance(filepath, str))
  def __init__(self, filepath):
    self.filepath = filepath
    self.dirpath = dirname(realpath(filepath))
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
      decoder = JSONDecoder()
      name, suboffset = decoder.raw_decode(s[offset:])
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
          for lineno, line in enumerate(f, 1):
            with Pfx(lineno):
              line = line.strip()
              if not line or line.startswith('#'):
                continue
              name, tags = self.parse_tags_line(line)
              if XATTR_B is not None:
                name_path = joinpath(self.dirpath, name)
                tags.update_from_xattr(name_path, xattr_name=XATTR_B)
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
      with open(filepath, 'w') as f:
        for name, tags in sorted(tagsets.items()):
          if not tags:
            continue
          f.write(cls.tags_line(name, tags))
          f.write('\n')

  @locked
  def save(self):
    ''' Save the tag map to the tag file.
    '''
    self.save_tagsets(self.filepath, self.tagsets)
    if XATTR_B is not None:
      for name, tagset in self.tagsets.items():
        name_path = joinpath(self.dirpath, name)
        tagset.update_xattr(name_path, xattr_name=XATTR_B)

  @locked_property
  def tagsets(self):
    ''' The tag map from the tag file,
        a mapping of name=>TagSet.
    '''
    return self.load_tagsets(self.filepath)

  @property
  def names(self):
    ''' The names from this `TagFile`.
    '''
    return list(self.tagsets.keys())

  def direct_tags_of(self, name):
    ''' Return the direct tags of `name`.
    '''
    return self.tagsets[name]

  @require(lambda name: isinstance(name, str))
  def add(self, name, tag, value=None):
    ''' Add a tag to the tags for `name`.
    '''
    return self.direct_tags_of(name).add(tag, value)

  def discard(self, name, tag_name, value=None):
    ''' Discard the tag matching `(tag_name,value)`.
        Return a `Tag` with the old value,
        or `None` if there was no matching tag.
    '''
    return self.tagsets[name].discard(tag_name, value)

TagFileEntry = namedtuple('TagFileEntry', 'tagfile name')

class FSTags:
  ''' A class to examine filesystem tags.
  '''

  def __init__(self, tagsfile=None):
    if tagsfile is None:
      tagsfile = TAGSFILE
    self.tagsfile = tagsfile
    self._tagmaps = {}  # cache of per directory `TagFile`s
    self._lock = Lock()

  def __str__(self):
    return "%s(tagsfile=%r)" % (type(self).__name__, self.tagsfile)

  @locked
  def dir_tagfile(self, dirpath):
    ''' Return the `TagFile` associated with `dirpath`.
    '''
    dirpath = Path(dirpath)
    tagfilepath = dirpath / self.tagsfile
    tagfile = self._tagmaps.get(tagfilepath)
    if tagfile is None:
      tagfile = self._tagmaps[tagfilepath] = TagFile(str(tagfilepath))
    return tagfile

  def path_tagfiles(self, filepath):
    ''' Return a list of `TagFileEntry`s
        for the `TagFile`s affecting `filepath`
        in order from the root to `dirname(filepath)`
        where `name` is the key within `TagFile`.

        Note that this is computed from `realpath(filepath)`.

        TODO: optional `as_is` parameter to skip the realpath call?
    '''
    with Pfx("path_tagfiles", filepath):
      real_filepath = Path(realpath(filepath))
      root, *subparts = real_filepath.parts
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
    for path in paths:
      with Pfx(path):
        tagged_path = TaggedPath(path, fstags=self)
        for spec, choice, tag in tag_choices:
          with Pfx(spec):
            if choice:
              # add tag
              tagged_path.add(tag)
            else:
              # delete tag
              tagged_path.discard(tag)
        tagged_path.save()

  def find(self, path, tag_choices, use_direct_tags=False):
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
    for filepath in rpaths(path, yield_dirs=use_direct_tags):
      tagged_path = TaggedPath(filepath, fstags=self)
      tags = (
          tagged_path.direct_tags if use_direct_tags else tagged_path.all_tags
      )
      choose = True
      for _, choice, tag in tag_choices:
        if choice:
          # tag_choice not present or not with same nonNone value
          if tag not in tags:
            choose = False
            break
        else:
          if tag in tags:
            choose = False
            break
      if choose:
        yield filepath

  @pfx_method(use_str=True)
  def edit_dirpath(self, dirpath):
    ''' Edit the filenames and tags in a directory.
    '''
    ok = True
    with Pfx("dirpath=%r", dirpath):
      dirpath = realpath(dirpath)
      tagfile = self.dir_tagfile(dirpath)
      tagsets = tagfile.tagsets
      names = set(
          name for name in os.listdir(dirpath)
          if (name and name not in ('.', '..') and not name.startswith('.'))
      )
      lines = [
          tagfile.tags_line(name, tagfile.direct_tags_of(name))
          for name in sorted(names)
      ]
      changed = edit_strings(lines)
      for old_line, new_line in changed:
        old_name, old_tags = tagfile.parse_tags_line(old_line)
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
                with Pfx("rename %r => %r", old_path, new_path):
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

class TaggedPath:
  ''' Class to manipulate the tags for a specific path.
  '''

  def __init__(self, filepath, fstags=None):
    if fstags is None:
      fstags = FSTags()
    self.filepath = Path(filepath)
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
    try:
      S = os.stat(filepath)
    except OSError:
      pass
    else:
      kwargs.update(filesize=S.st_size)
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

  def save(self):
    ''' Update the associated `TagFile`.
    '''
    self.direct_tagfile.save()

  def merged_tags(self):
    ''' Return the cumulative tags for this path as a `TagSet`
        by merging the tags from the root to the path.
    '''
    tags = TagSet()
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

  def autotag(self, rules=None, *, no_save=False):
    ''' Apply `rules`to this `TaggedPath`,
        update the `direct_tags` with new tags.

        Parameters:
        * `rules`: an iterable of objects with a `.infer_tags(name)` method
          which returns an iterable of `Tag`s.
          Each rule will be passed the `TaggedPath.basename` as the `name`.
        * `no_save`: if true (default `False`)
          suppress the save of updated tags back to the filesystem.
    '''
    name = self.basename
    tags = self.direct_tags
    all_tags = self.all_tags
    new_tags = TagSet()
    updated = False
    for autotag in infer_tags(name, rules=rules):
      if autotag not in all_tags:
        new_tags.add(autotag)
        updated = True
    if updated:
      tags.update(new_tags)
      if not no_save:
        self.save()
    return new_tags

def infer_tags(name, rules=None):
  ''' Infer `Tag`s from `name` via `rules`. Return a `TagSet`.

      `rules` is an iterable of objects with a `.infer_tags(name)` method
      which returns an iterable of `Tag`s.
  '''
  with Pfx("infer_tags(%r,..)", name):
    if rules is None:
      rules = loadrc()
    tags = TagSet()
    for rule in rules:
      with Pfx(rule):
        for autotag in rule.infer_tags(name):
          with Pfx(autotag):
            if autotag.name not in tags:
              tags.add(autotag)
    return tags

class RegexpTagRule:
  ''' A regular expression based `Tag` rule.
  '''

  def __init__(self, regexp):
    self.regexp_src = regexp
    self.regexp = re.compile(regexp)

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.regexp_src)

  def infer_tags(self, s):
    ''' Apply the rule to the string `s`, return `Tag`s.
    '''
    tags = []
    m = self.regexp.search(s)
    if m:
      for tag_name, value in m.groupdict().items():
        if value is not None:
          try:
            value = int(value)
          except ValueError:
            pass
          tags.append(Tag(tag_name, value))
    return tags

def rpaths(path, yield_dirs=False, name_selector=None):
  ''' Generator yielding pathnames found under `path`.
  '''
  if name_selector is None:
    name_selector = lambda name: name and not name.startswith('.')
  if isfilepath(path):
    yield path
  else:
    for dirpath, dirnames, filenames in os.walk(path):
      if yield_dirs:
        yield dirpath
      dirnames[:] = sorted(filter(name_selector, dirnames))
      for filename in sorted(filter(name_selector, filenames)):
        yield joinpath(dirpath, filename)

def rfilepaths(path, name_selector=None):
  ''' Generator yielding pathnames of files found under `path`.
  '''
  return rpaths(path, yield_dirs=False, name_selector=name_selector)

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

@fmtdoc
def loadrc(rcfilepath=None):
  ''' Read rc file, return rules.

      If `rcfilepath` is `None` default to `'{RCFILE}'` (from `RCFILE`).
  '''
  if rcfilepath is None:
    rcfilepath = expanduser(RCFILE)
  with Pfx("loadrc(%r)", rcfilepath):
    rules = []
    try:
      with open(rcfilepath) as f:
        for lineno, line in enumerate(f, 1):
          with Pfx(lineno):
            line = line.strip()
            if not line or line.startswith('#'):
              continue
            if line.startswith('/') and line.endswith('/'):
              rules.append(RegexpTagRule(line[1:-1]))
    except OSError as e:
      if e.errno != errno.ENOENT:
        raise
    return rules

FSTagsCommand.__doc__ += '\n\n    ' + FSTagsCommand.USAGE_FORMAT.format(
    cmd='fstags'
).replace('\n', '\n    ')

if __name__ == '__main__':
  sys.argv[0] = basename(sys.argv[0])
  sys.exit(main(sys.argv))

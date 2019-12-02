#!/usr/bin/env python3
#

''' Simple filesystem based file tagging
    and the associated `fstags` command line script.

    Tags are stored in a file `.fstags` in directories
    with a line for each entry in the directory
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

    from the following `.fstags` files and entries:
    * `/path/to/.fstags`:
      `series-name sf series.title="Series Full Name"`
    * `/path/to/series-name/.fstags`:
      `season-02 season=2`
    * `/path/to/series-name/season-02.fstags`:
      `episode-name--s02e03--something.mp4 episode=3 episode.title="Full Episode Title"`

    Tags may be "bare", or have a value.
    If there is a value it is expressed with an equals (`'='`)
    followed by the JSON encoding of the value.
'''

from collections import defaultdict
from datetime import date, datetime
import errno
from getopt import GetoptError
import json
from json import JSONEncoder, JSONDecoder
import os
from os.path import (
    basename, exists as existspath, expanduser, isdir as isdirpath, isfile as
    isfilepath, join as joinpath, realpath
)
from pathlib import Path
import re
import sys
from threading import Lock
from cs.cmdutils import BaseCommand
from cs.edit import edit_strings
from cs.lex import (
    get_dotted_identifier, get_nonwhite, is_dotted_identifier, skipwhite
)
from cs.logutils import setup_logging, error, warning, info
from cs.pfx import Pfx
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
        'cs.cmdutils', 'cs.edit', 'cs.lex', 'cs.logutils', 'cs.pfx',
        'cs.threads', 'icontract'
    ],
}

TAGSFILE = '.fstags'

RCFILE = '~/.fstagsrc'

def main(argv=None):
  ''' Command line mode.
  '''
  if argv is None:
    argv = sys.argv
  return FSTagCommand().run(argv)

class FSTagCommand(BaseCommand):
  ''' fstag main command line class.
  '''

  GETOPT_SPEC = ''
  USAGE_FORMAT = '''Usage:
    {cmd} autotag paths...
        Tag paths based on rules from the rc file.
    {cmd} find path {{tag[=value]|-tag}}...
        List files from path matching all the constraints.
    {cmd} ls [paths...]
        List files from paths and their tags.
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
        offset = 0
        if arg.startswith('-'):
          choice = False
          offset += 1
        else:
          choice = True
        tag, offset = Tag.parse(arg, offset=offset)
        if offset < len(arg):
          raise ValueError("unparsed: %r", arg[offset:])
        choices.append((arg, choice, tag))
    return choices

  @staticmethod
  def cmd_autotag(argv, options, *, cmd):
    ''' Tag paths based on rules from the rc file.
    '''
    fstags = options.fstags
    if not argv:
      argv = ['.']
    rules = loadrc(expanduser(RCFILE))
    for path in argv:
      for filepath in rfilepaths(path):
        with Pfx(filepath):
          for tagfile, name in fstags.path_tagfiles(filepath):
            tags = tagfile.tags_of(name)
            updated = False
            for rule in rules:
              for autotag in rule.apply(name):
                if autotag.name not in tags:
                  print("autotag %r + %s" % (name, autotag))
                  tags[autotag.name] = autotag.value
                  updated = True
            if updated:
              tagfile.save()

  @staticmethod
  def cmd_edit(argv, options, *, cmd):
    ''' Edit filenames and tags in a directory.
    '''
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
      path = realpath(path)
      fstags = options.fstags
      tagfile = fstags.dir_tagfile(path)
      tagmap = tagfile.direct_tagmap
      names = set(tagmap.keys())
      # infill with untagged names
      for name in os.listdir(path):
        if (name and name not in ('.', '..') and not name.startswith('.')):
          names.add(name)
      lines = [
          tagfile.tags_line(name, tagfile.tags_of(name))
          for name in sorted(names)
      ]
      changed = edit_strings(lines)
      for old_line, new_line in changed:
        old_name, old_tags = tagfile.parse_tags_line(old_line)
        new_name, new_tags = tagfile.parse_tags_line(new_line)
        with Pfx(old_name):
          if old_name != new_name:
            if new_name in tagmap:
              warning("new name %r already exists", new_name)
              xit = 1
              continue
            del tagmap[old_name]
            old_path = joinpath(path, old_name)
            if existspath(old_path):
              new_path = joinpath(path, new_name)
              if not existspath(new_path):
                with Pfx("rename %r => %r", old_path, new_path):
                  try:
                    os.rename(old_path, new_path)
                  except OSError as e:
                    warning("%s", e)
                    xit = 1
                  else:
                    info("renamed")
          # rewrite the tags under the new_name
          # (possibly the same as old_name)
          tagmap[new_name] = {tag.name: tag.value for tag in new_tags}
      tagfile.save()
    return xit

  @classmethod
  def cmd_find(cls, argv, options, *, cmd):
    ''' Find paths matching tag criteria.
    '''
    fstags = options.fstags
    badopts = False
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
    for filepath in fstags.find(path, tag_choices):
      print(filepath)

  @staticmethod
  def cmd_ls(argv, options, *, cmd):
    ''' List paths and their tags.
    '''
    fstags = options.fstags
    paths = argv or ['.']
    for path in paths:
      with Pfx(path):
        for filepath in rfilepaths(path):
          print(PathTags(filepath, fstags=fstags))

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
    try:
      tag_choices = cls.parse_tag_choices([arg])
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

  @staticmethod
  def is_valid_name(name):
    ''' Test whether a tag name is valid: a dotted identifier.
    '''
    return is_dotted_identifier(name, extras='_-')

  def parse_name(s, offset=0):
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

class TagFile:
  ''' A reference to a specific file containing tags.
  '''

  @require(lambda filepath: isinstance(filepath, str))
  def __init__(self, filepath, fstags):
    self.filepath = filepath
    self.fstags = fstags
    self._lock = Lock()

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.filepath)

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
        return `(name,tags)`.
    '''
    name, offset = self.decode_name(line)
    if offset < len(line) and not line[offset].isspace():
      _, offset2 = get_nonwhite(line, offset)
      name = line[:offset2]
      offset = offset2
      warning(
          "offset %d: expected whitespace, adjusted name to %r", offset, name
      )
    with Pfx(name):
      assert isinstance(name, str)
      tags = []
      while offset < len(line):
        if not line[offset].isspace():
          warning("line offset %d: expected whitespace", offset)
        else:
          offset = skipwhite(line, offset)
        tag, offset = Tag.parse(line, offset)
        tags.append(tag)
    return name, tags

  def load_tagmap(self, filepath):
    ''' Load `filepath` and return
        a mapping of `name`=>`tag_name`=>`value`.
    '''
    with Pfx("loadtags(%r)", filepath):
      tagmap = defaultdict(dict)
      try:
        with open(filepath) as f:
          for lineno, line in enumerate(f, 1):
            with Pfx(lineno):
              line = line.strip()
              if not line or line.startswith('#'):
                continue
              name, tags = self.parse_tags_line(line)
              for tag in tags:
                tag_name, value = tag.name, tag.value
                assert isinstance(tag_name, str)
                tagmap[name][tag_name] = value
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise
      return tagmap

  @classmethod
  def tags_line(cls, name, tagmap):
    ''' Transcribe a `name` and its `tagmap` for use as a `.fstags` file line.
    '''
    fields = [cls.encode_name(name)]
    for tag_name, value in sorted(tagmap.items()):
      fields.append(str(Tag(tag_name, value)))
    return ' '.join(fields)

  @classmethod
  def save_tagmap(cls, filepath, tagmap):
    ''' Save a tagmap to `filepath`.
    '''
    with Pfx("savetags(%r)", filepath):
      with open(filepath, 'w') as f:
        for name, tags in sorted(tagmap.items()):
          if not tags:
            continue
          f.write(cls.tags_line(name, tagmap[name]))
          f.write('\n')

  @locked
  def save(self):
    ''' Save the tag map to the tag file.
    '''
    self.save_tagmap(self.filepath, self.direct_tagmap)

  @locked_property
  def direct_tagmap(self):
    ''' The tag map from the tag file.
    '''
    return self.load_tagmap(self.filepath)

  @property
  def names(self):
    ''' The names from this `TagFile`.
    '''
    return list(self.direct_tagmap.keys())

  def tags_of(self, name):
    ''' Return the direct tags of `name`.
    '''
    return self.direct_tagmap[name]

  @require(lambda name: isinstance(name, str))
  def add(self, name, tag, value=None):
    ''' Add the `tag`=`value` to the tags for `name`.

        If `tag` is not a `str` and `value` is omitted or `None`
        then `tag` should be an object with `.name` and `.value` attributes,
        such as a `Tag`.
    '''
    if not isinstance(tag, str) and value is None:
      tag, value = tag.name, tag.value
    assert isinstance(tag, str), "name=%r,tag=%r,value=%r" % (name, tag, value)
    self.direct_tagmap[name][tag] = value

  def discard(self, name, tag_name):
    ''' Discard the tag named `tag_name` from the tags for `name`.
        Return a `Tag` with the old value, or `None`.
    '''
    name_tags = self.direct_tagmap[name]
    if tag_name in name_tags:
      old_value = name_tags.pop(tag_name)
      return Tag(tag_name, old_value)
    return None

class FSTags:
  ''' A class to examine filesystem tags.
  '''

  def __init__(self, tagsfile=None):
    if tagsfile is None:
      tagsfile = TAGSFILE
    self.tagsfile = tagsfile
    self._tagmaps = {}  # cache of per directory `TagFile`s
    self._lock = Lock()

  @locked
  def dir_tagfile(self, dirpath):
    ''' Return the `TagFile` associated with `dirpath`.
    '''
    dirpath = Path(dirpath)
    tagfilepath = dirpath / self.tagsfile
    tagfile = self._tagmaps.get(tagfilepath)
    if tagfile is None:
      tagfile = self._tagmaps[tagfilepath] = TagFile(str(tagfilepath), self)
    return tagfile

  def path_tagfiles(self, filepath):
    ''' Return a list of `(TagFile,name)`
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
        tagfiles.append((self.dir_tagfile(current), next_part))
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
        (None, True, tag) if isinstance(tag, Tag) else tag
        for tag in tag_choices
    ]
    for path in paths:
      with Pfx(path):
        pathtags = PathTags(path, fstags=self)
        for spec, choice, tag in tag_choices:
          with Pfx(tag):
            if choice:
              # add tag
              pathtags.add(tag)
            else:
              # delete tag
              direct_tags = pathtags.direct_tags
              if tag.name in direct_tags:
                if tag.value is None or direct_tags[tag.name] == tag.value:
                  direct_tags.pop(tag.name)
        pathtags.save()

  def find(self, path, tag_choices):
    ''' Walk the file tree from `path`
        searching for files matching the supplied `tag_choices`.
        Yield the matching file paths.
    '''
    for filepath in rfilepaths(path):
      tags = PathTags(filepath, fstags=self).tags
      tagmap = {tag.name: tag for tag in tags}
      choose = True
      for arg, choice, tag in tag_choices:
        if choice:
          # tag_choice not present or not with same nonNone value
          if (tag.name not in tagmap or
              (tag.value is not None and tagmap[tag.name].value != tag.value)):
            choose = False
            break
        else:
          if (tag.name in tagmap
              and (tag.value is None or tagmap[tag.name].value == tag.value)):
            choose = False
            break
      if choose:
        yield filepath

class PathTags:
  ''' Class to manipulate the tags for a specific path.
  '''

  def __init__(self, filepath, fstags=None):
    if fstags is None:
      fstags = FSTags()
    self.filepath = Path(filepath)
    self._tagfiles = fstags.path_tagfiles(filepath)
    self.direct_tagfile = self._tagfiles[-1][0]
    self.direct_tagfile_name = self._tagfiles[-1][1]
    self.direct_tags = self.direct_tagfile.direct_tagmap[
        self.direct_tagfile_name]

  def __repr__(self):
    return "%s(%s)" % (type(self).__name__, self.filepath)

  def __str__(self):
    parts = [TagFile.encode_name(str(self.filepath))]
    for tag in sorted(self.tags):
      parts.append(str(tag))
    return ' '.join(parts)

  def save(self):
    ''' Update the associated `TagFile`.
    '''
    self.direct_tagfile.save()

  @property
  def tags(self):
    ''' Return the cumulative tags for this path
        by merging the tags from the root to the path.
    '''
    tags = {}
    for tagfile, name in self._tagfiles:
      for tag_name, value in tagfile.direct_tagmap[name].items():
        tags[tag_name] = value

    return [Tag(tag_name, value) for tag_name, value in tags.items()]

  def add(self, tag, value=None):
    ''' Add the `tag`=`value` to the direct tags.

        If `tag` is not a `str` and `value` is omitted or `None`
        then `tag` should be an object with `.name` and `.value` attributes,
        such as a `Tag`.
    '''
    self.direct_tagfile.add(self.direct_tagfile_name, tag, value)

  def pop(self, tag_name):
    ''' Remove the tag named `tag_name` from the direct tags.
        Raises `KeyError` if `tag_name` is not present in the direct tags.
    '''
    self.direct_tags.pop(tag_name)

class RegexpTagRule:
  ''' A regular expression based `Tag` rule.
  '''

  def __init__(self, regexp):
    if isinstance(regexp, str):
      regexp = re.compile(regexp)
    self.regexp = regexp

  def apply(self, s):
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

def rfilepaths(path):
  ''' Generator yielding pathnames of files found under `path`.
  '''
  if isfilepath(path):
    yield path
  else:
    for dirpath, dirnames, filenames in os.walk(path):
      dirnames[:] = sorted(dirnames)
      for filename in sorted(filename for filename in filenames
                             if filename and not filename.startswith('.')):
        yield joinpath(dirpath, filename)

def loadrc(rcfilepath):
  ''' Read rc file, return rules.
  '''
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
              regexp = re.compile(line[1:-1])
              rules.append(RegexpTagRule(regexp))
    except OSError as e:
      if e.errno != errno.ENOENT:
        raise
    return rules

if __name__ == '__main__':
  sys.argv[0] = basename(sys.argv[0])
  sys.exit(main(sys.argv))

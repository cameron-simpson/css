#!/usr/bin/env python3
#

''' Simple filesystem based file tagging.

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
        episode=3
        episode.title="Full Episode Title"

    from the following `.fstags` files and entries:
    * `/path/to/.fstags`:
      `series-name series.title="Series Full Name"`
    * `/path/to/series-name/.fstags`:
      `season-02 season=2`
    * `/path/to/series-name/season-02.fstags`:
      `episode-name--s02e03--something.mp4 episode=3 episode.title="Full Episode Title"`
'''

from collections import defaultdict
import errno
from getopt import GetoptError
import json
from json import JSONDecoder
import os
from os.path import (
    basename, dirname, exists as existspath, isdir as isdirpath, isfile as
    isfilepath, join as joinpath, normpath, realpath
)
from pathlib import Path
from pprint import pprint, pformat
import sys
from threading import Lock
from cs.cmdutils import BaseCommand
from cs.lex import (
    get_dotted_identifier, get_nonwhite, is_dotted_identifier, skipwhite
)
from cs.logutils import setup_logging, warning
from cs.pfx import Pfx
from cs.threads import locked, locked_property
from icontract import require

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.cmdutils','cs.lex','cs.pfx','cs.threads','icontract'],
}

TAGSFILE = '.fstags'

class FSTagCommand(BaseCommand):
  ''' fstag main command line class.
  '''

  GETOPT_SPEC = ''
  USAGE_FORMAT = '''Usage:
    {cmd} ls [paths...]
        List files from paths and their tags.
    {cmd} tag path {{tag[=value]|-tag}}...
        Associate tags with a path.
        With the form "-tag", remove the tag from the immediate tags.
    {cmd} tagpaths {{tag[=value]|-tag}} paths...
        Associate a tag with multiple paths.
        With the form "-tag", remove the tag from the immediate tags.
  '''

  # {cmd} untag tag paths...

  def apply_defaults(self, options):
    setup_logging(options.cmd)
    options.fstags = FSTags()

  @staticmethod
  def cmd_ls(argv, options, *, cmd):
    fstags = options.fstags
    paths = argv or ['.']
    for path in paths:
      with Pfx(path):
        if isfilepath(path):
          print(PathTags(path, fstags=fstags))
        else:
          for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = sorted(dirnames)
            for filename in sorted(filename for filename in filenames
                                   if filename and not filename.startswith('.')
                                   ):
              print(PathTags(joinpath(dirpath, filename), fstags=fstags))

  @staticmethod
  def cmd_tag(argv, options, *, cmd):
    badopts = False
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing path")
    path = argv.pop(0)
    if not argv:
      raise GetoptError("missing tags")
    tags = []
    for arg in argv:
      with Pfx(arg):
        remove_tag=False
        offset=0
        if arg.startswith('-'):
          remove_tag=True
          offset+=1
        tag, offset = Tag.parse(arg,offset=offset)
        if offset < len(arg):
          warning("unparsed: %r", arg[offset:])
          badopts = True
        elif remove_tag and value is not None:
          warning("a value may not be supplied in the \"-tag\" form")
          badopts=True
        else:
          tags.append((remove_tag,tag))
    if badopts:
      raise GetoptError("bad arguments")
    fstags.apply_tags(tags,[path])

  @staticmethod
  def cmd_tagpaths(argv, options, *, cmd):
    badopts = False
    fstags = options.fstags
    if not argv:
      raise GetoptError("missing tag")
    arg=argv.pop(0)
    remove_tag=False
    offset=0
    if arg.startswith('-'):
      remove_tag=True
      offset+=1
    tag, offset=Tag.parse(arg,offset)
    if offset<len(tag):
      warning("unparsed: %r", arg[offset:])
      badopts = True
    if not argv:
      warning("missing paths")
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    fstags.apply_tags([(remove_tag,tag)],argv)

class Tag:
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

  def __str__(self):
    ''' Encode `tag_name` and `value`.
    '''
    name = self.name
    value = self.value
    if value is None:
      return name
    if isinstance(value, str):
      if is_dotted_identifier(value):
        return name + '=' + value
    return name + '=' + json.dumps(value, separators=(',', ':'))

  @staticmethod
  def is_valid_name(name):
    ''' Test whether a tag name is valid: a dotted identifier.
    '''
    return is_dotted_identifier(name)

  @classmethod
  def parse(cls, s, offset=0):
    ''' Parse tag_name[=value], return `(tag,offset)`.
    '''
    json_decoder = JSONDecoder()
    with Pfx("%s.parse(%r)", cls.__name__, s[offset:]):
      offset0 = offset
      name, offset = get_dotted_identifier(s, offset)
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
              value, offset = get_dotted_identifier(s, offset)
            else:
              value_part = s[offset:]
              value, suboffset = json_decoder.raw_decode(value_part)
              offset += suboffset
          else:
            name_end, offset = get_nonwhite(s, offset)
            name += name_end
            value = None
            warning("bad separator %r, adjusting tag to %r" % (sep, name))
        else:
          value = None
      return cls(name, value), offset

class TagFile:
  ''' A reference to a specific file containing tags.
  '''

  @require(lambda filepath: isinstance(filepath, str))
  def __init__(self, filepath):
    self.filepath = filepath
    self._lock = Lock()

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.filepath)

  @staticmethod
  def encode_name(name):
    if is_dotted_identifier(name,extras='_-:'):
      return name
    return json.dumps(name)

  @staticmethod
  def decode_name(s, offset=0):
    json_decoder = JSONDecoder()
    if s[offset].isalpha():
      name, offset = get_dotted_identifier(s, offset,extras='_-:')
    else:
      name, suboffset = json_decoder.raw_decode(s[offset:])
      offset += suboffset
    return name, offset

  @classmethod
  def load_tagmap(cls, filepath):
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
              name, offset = cls.decode_name(line)
              if offset < len(line) and not line[offset].isspace():
                _, offset2 = get_nonwhite(line, offset)
                name = line[:offset2]
                offset = offset2
                warning(
                    "offset %d: expected whitespace, adjusted name to %r",
                    offset, name
                )
              with Pfx(name):
                assert isinstance(name, str)
                while offset < len(line):
                  if not line[offset].isspace():
                    warning("line offset %d: expected whitespace", offset)
                  else:
                    offset = skipwhite(line, offset)
                  tag, offset = Tag.parse(line, offset)
                  tag_name, value = tag.name, tag.value
                  assert isinstance(tag_name, str)
                  tagmap[name][tag_name] = value
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise
      return tagmap

  @classmethod
  def save_tagmap(cls, filepath, tagmap):
    ''' Save a tagmap to `filepath`.
    '''
    with Pfx("savetags(%r)", filepath):
      with open(filepath, 'w') as f:
        for name, tags in sorted(tagmap.items()):
          if not tags:
            continue
          f.write(cls.encode_name(name))
          tag_items = list(tagmap[name].items())
          for tag_name, value in sorted(tagmap[name].items()):
            f.write(' ')
            f.write(str(Tag(tag_name, value)))
          f.write('\n')

  @locked
  def save(self):
    self.save_tagmap(self.filepath, self.direct_tags)

  @locked_property
  def direct_tags(self):
    return self.load_tagmap(self.filepath)

  @property
  def names(self):
    ''' The names from this `TagFile`.
    '''
    return list(self.direct_tags.keys())

  @property
  def tags_of(self, name):
    return self.direct_tags[name]

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
    self.direct_tags[name][tag] = value

  def discard(self, name, tag_name):
    ''' Discard the tag named `tag_name` from the tags for `name`.
        Return a `Tag` with the old value, or `None`.
    '''
    name_tags = self.direct_tags[name]
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
      tagfile = self._tagmaps[tagfilepath] = TagFile(str(tagfilepath))
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

  def apply_tags(self, tags, paths):
    ''' Apply the `tags` to `paths`.

        Parameters:
        * `tags`:
          an iterable of `Tag` or `(remove_tag,Tag)`;
          the former is equivalent to `(False,Tag)`.
          Each item applies or removes a `Tag`
          from each path's direct tags.
        * `paths`:
          an iterable of filesystem paths.
    '''
    tags=[(False,tag) if isinstance(tag,Tag) else tag
        for tag in tags]
    for path in paths:
      with Pfx(path):
        pathtags = PathTags(path, fstags=self)
        for remove_tag,tag in tags:
          with Pfx(tag):
            if remove_tag:
              try:
                pathtags.pop(tag.name)
              except KeyError:
                warning("not a direct tag, not removed")
            else:
              pathtags.add(tag)
        pathtags.save()

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
    self.direct_tags = self.direct_tagfile.direct_tags[self.direct_tagfile_name
                                                       ]

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
      for tag_name, value in tagfile.direct_tags[name].items():
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

if __name__ == '__main__':
  sys.argv[0] = basename(sys.argv[0])
  sys.exit(FSTagCommand().run(sys.argv))

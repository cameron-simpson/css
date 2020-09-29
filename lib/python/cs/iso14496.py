#!/usr/bin/python
#
# ISO14496 parser. - Cameron Simpson <cs@cskk.id.au> 26mar2016
#

'''
Facilities for ISO14496 files - the ISO Base Media File Format,
the basis for several things including MP4 and MOV.

ISO make the standard available here:
* [link](http://standards.iso.org/ittf/PubliclyAvailableStandards/index.html)
* [link](http://standards.iso.org/ittf/PubliclyAvailableStandards/c068960_ISO_IEC_14496-12_2015.zip)
'''

from __future__ import print_function
from base64 import b64encode, b64decode
from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from getopt import getopt, GetoptError
import os
import stat
import sys
from cs.binary import (
    Packet,
    PacketField,
    BytesesField,
    ListField,
    UInt8,
    Int16BE,
    UTF16NULField,
    Int32BE,
    UInt16BE,
    UInt32BE,
    UInt64BE,
    UTF8NULField,
    BytesField,
    BytesRunField,
    EmptyField,
    EmptyPacketField,
    BinaryMultiStruct,
    deferred_field,
)
from cs.buffer import CornuCopyBuffer
from cs.cmdutils import BaseCommand
from cs.context import StackableState
from cs.fstags import FSTags, TaggedPath, rpaths
from cs.lex import get_identifier, get_decimal_value
from cs.logutils import debug, warning, error
from cs.pfx import Pfx
from cs.py.func import prop
from cs.tagset import TagSet, Tag
from cs.units import transcribe_bytes_geek as geek, transcribe_time
from cs.upd import print, out

__version__ = '20200229'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Topic :: Multimedia :: Video",
    ],
    'install_requires': [
        'cs.binary',
        'cs.buffer',
        'cs.cmdutils',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
        'cs.tagset',
        'cs.units',
        'cs.upd',
    ],
}

PARSE_MODE = StackableState(copy_boxes=False, discard_data=False)

def main(argv=None):
  ''' Command line mode.
  '''
  if argv is None:
    argv = sys.argv
  return MP4Command().run(argv)

class MP4Command(BaseCommand):

  GETOPT_SPEC = ''

  TAG_PREFIX = 'mp4'

  def cmd_autotag(self, argv, options):
    ''' Usage: {cmd} autotag [-n] [-p prefix] [--prefix=prefix] paths...
          Tag paths based on embedded MP4 metadata.
          -n  No action.
          -p prefix, --prefix=prefix
              Set the prefix of added tags, default: 'mp4'
    '''
    xit = 0
    fstags = FSTags()
    no_action = False
    tag_prefix = self.TAG_PREFIX
    opts, argv = getopt(argv, 'np:', longopts=['prefix'])
    for option, value in opts:
      with Pfx(option):
        if option == '-n':
          no_action = True
        elif option in ('-p', '--prefix'):
          tag_prefix = value
        else:
          raise RuntimeError("unsupported option")
    if not argv:
      argv = [os.getcwd()]
    with fstags:
      for top_path in argv:
        for _, path in rpaths(top_path):
          out(path)
          with Pfx(path):
            tagged_path = fstags[path]
            with PARSE_MODE(discard_data=True):
              try:
                for box, tags in parse_tags(path, tag_prefix=tag_prefix):
                  for tag in tags:
                    if no_action:
                      tag_s = str(tag)
                      if len(tag_s) > 32:
                        tag_s = tag_s[:29] + '...'
                      print(path, '+', tag_s)
                    else:
                      tagged_path.add(tag)
              except (TypeError, NameError, AttributeError, AssertionError):
                raise
              except Exception as e:
                warning("%s: %s", type(e).__name__, e)
                xit = 1
    return xit

  @staticmethod
  def cmd_deref(argv, options):
    ''' Dereference a Box specification against ISO14496 files.
    '''
    spec = argv.pop(0)
    with Pfx(spec):
      if spec == '-':
        parsee = sys.stdin.fileno()
      else:
        parsee = spec
      over_box = parse(parsee)
      over_box.dump()
      for path in argv:
        with Pfx(path):
          B = deref_box(over_box, path)
          print(path, "offset=%d" % B.offset, B)

  @staticmethod
  def cmd_extract(argv, options):
    ''' Usage: {cmd} extract [-H] filename boxref output
          Extract the referenced Box from the specified filename into output.
          -H  Skip the Box header.
    '''
    skip_header = False
    if argv and argv[0] == '-H':
      argv.pop(0)
      skip_header = True
    if not argv:
      warning("missing filename")
      badopts = True
    else:
      filename = argv.pop(0)
    if not argv:
      warning("missing boxref")
      badopts = True
    else:
      boxref = argv.pop(0)
    if not argv:
      warning("missing output")
      badopts = True
    else:
      output = argv.pop(0)
    if argv:
      warning("extra argments after boxref: %s", ' '.join(argv))
      badopts = True
    if badopts:
      raise GetoptError("invalid arguments")
    over_box = parse(filename)
    over_box.dump()
    B = over_box
    for box_type_s in boxref.split('.'):
      B = getattr(B, box_type_s.upper())
    with Pfx(filename):
      fd = os.open(filename, os.O_RDONLY)
      bfr = CornuCopyBuffer.from_fd(fd)
      offset = B.offset
      need = B.length
      if skip_header:
        offset += B.header_length
        if need is not None:
          need -= B.header_length
      bfr.seek(offset)
      with Pfx(output):
        with open(output, 'wb') as ofp:
          for chunk in bfr:
            if need is not None and need < len(chunk):
              chunk = chunk[need]
            ofp.write(chunk)
            need -= len(chunk)
      os.close(fd)

  @staticmethod
  def cmd_info(argv, options):
    ''' Usage: {cmd} info [{{-|filename}}]...]
          Print informative report about each source.
    '''
    if not argv:
      argv = ['-']
    for spec in argv:
      with Pfx(spec):
        if spec == '-':
          parsee = sys.stdin.fileno()
        else:
          parsee = spec
        with PARSE_MODE(discard_data=True):
          over_box = parse(parsee)
        print(spec + ":")
        for top_box in over_box:
          for box, tags in top_box.gather_metadata():
            if tags:
              print(box.box_type_path, "%d:" % (len(tags),), tags)

  @staticmethod
  def cmd_parse(argv, options):
    ''' Usage: {cmd} [parse [{{-|filename}}]...]
          Parse the named files (or stdin for "-").
    '''
    if not argv:
      argv = ['-']
    for spec in argv:
      with Pfx(spec):
        if spec == '-':
          parsee = sys.stdin.fileno()
        else:
          parsee = spec
        over_box = parse(parsee)
        over_box.dump(crop_length=None)

  def cmd_tags(self, argv, options):
    ''' Usage: {cmd} path
          Report the tags of `path` based on embedded MP4 metadata.
    '''
    xit = 0
    fstags = FSTags()
    tag_prefix = self.TAG_PREFIX
    opts, argv = getopt(argv, 'p:', longopts=['prefix'])
    for option, value in opts:
      with Pfx(option):
        if option in ('-p', '--prefix'):
          tag_prefix = value
        else:
          raise RuntimeError("unsupported option")
    if not argv:
      raise GetoptError("missing path")
    path = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments after path: %r" % (argv,))
    with fstags:
      out(path)
      with Pfx(path):
        with PARSE_MODE(discard_data=True):
          for box, tags in parse_tags(path, tag_prefix=tag_prefix):
            for tag in tags:
              print(tag)
    return xit

  @staticmethod
  def cmd_test(argv, options):
    ''' Usage: {cmd} [testnames...]
          Run self tests.
    '''
    import cs.iso14496_tests
    cs.iso14496_tests.selftest(
        ["%s: %s" % (options.cmd, options.subcmd)] + argv
    )

# a convenience chunk of 256 zero bytes, mostly for use by 'free' blocks
B0_256 = bytes(256)

# an arbitrary maximum read size for fetching the data section
SIZE_16MB = 1024 * 1024 * 16

def parse_deref_path(path, offset=0):
  ''' Parse a `path` string from `offset`.
      Return the path components and the offset where the parse stopped.

      Path components:
      * _identifier_: an identifier represents a Box field or if such a
        field is not present, a the first subbox of this type
      * `[`_index_`]`: the subbox with index _index_

      Examples:

          >>> parse_deref_path('.abcd[5]')
          ['abcd', 5]
  '''
  parts = []
  while offset < len(path):
    try:
      # .type
      if path.startswith('.', offset):
        name, offset2 = get_identifier(path, offset + 1)
        if name:
          parts.append(name)
          offset = offset2
          continue
    except ValueError as e:
      pass
    try:
      # [index]
      if path.startswith('[', offset):
        n, offset2 = get_decimal_value(path, offset + 1)
        if path.startswith(']', offset2):
          parts.append(n)
          offset = offset2 + 1
          continue
    except ValueError as e:
      pass
    break
  return parts, offset

def deref_box(B, path):
  ''' Dereference a path with respect to this Box.
  '''
  with Pfx("deref_path(%r)", path):
    if isinstance(path, str):
      parts, offset = parse_deref_path(path)
      if offset < len(path):
        raise ValueError(
            "parse_path(%r): stopped early at %d:%r" %
            (path, offset, path[offset:])
        )
      return deref_box(B, parts)
    for i, part in enumerate(path):
      with Pfx("%d:%r", i, part):
        nextB = None
        if isinstance(part, str):
          # .field_name[n] or .field_name or .box_type
          try:
            nextB = getattr(B, part)
          except AttributeError:
            if len(part) == 4:
              try:
                nextB = getattr(B, part.upper())
              except AttributeError:
                pass
        elif isinstance(part, int):
          # [index]
          nextB = B[part]
        else:
          raise ValueError("unhandled path component")
        if nextB is None:
          raise IndexError("no match")
        B = nextB
    return B

Matrix9Long = BinaryMultiStruct(
    'Matrix9Long', '>lllllllll', 'v0 v1 v2 v3 v4 v5 v6 v7 v8'
)

class UTF8or16Field(BaseBinaryMultiValue):
  ''' An ISO14496 UTF8 or UTF16 encoded string.
  '''

  FIELD_TYPES = {
      'bom': bytes,
      'text': str,
  }

  TEST_CASES = (
      b'\0',
      b'abc\0',
      b'\xfe\xffa\x00b\x00c\x00\x00\x00',
      b'\xff\xfe\x00a\x00b\x00c\x00\x00',
  )

  BOM_ENCODING = {
      b'\xfe\xff': 'utf_16_le',
      b'\xff\xfe': 'utf_16_be',
  }

  @classmethod
  def parse(cls, bfr):
    ''' Gather optional BOM and then UTF8 or UTF16 string.
    '''
    self = cls()
    bfr.extend(1)
    if bfr[0] == 0:
      # empty sting, no BOM
      bom = b''
      text = BinaryUTF8NUL.parse_value(bfr)
    else:
      bom = bfr.take(2)
      encoding = cls.BOM_ENCODING.get(bom)
      if encoding is None:
        # not a BOM, presume UTF8
        bfr.push(bom)
        bom = b''
        text = BinaryUTF8NUL.parse_value(bfr)
      else:
        # UTF16
        text = BinaryUTF16NUL.parse_value(bfr, encoding=encoding)
    self.bom = bom
    self.text = text
    return self

  def transcribe(self):
    ''' Transcribe the field suitably encoded.
    '''
    if self.bom:
      yield self.bom
      yield UTF16NULField.transcribe_value(
          self.text, encoding=self.BOM_ENCODING[self.bom]
      )
    else:
      yield BinaryUTF8NUL.transcribe_value(self.text)

class TimeStampMixin:
  ''' Methods to assist with ISO14496 timestamps.
  '''

  @property
  def datetime(self):
    ''' This timestamp as an UTC datetime.
    '''
    if self.value in (0x7fffffffffffffff, 0x8000000000000000,
                      0xfffffffffffffffe, 0xffffffffffffffff):
      return None
    try:
      dt = datetime.utcfromtimestamp(self.value)
    except (OverflowError, OSError) as e:
      warning(
          "%s.datetime: datetime.utcfromtimestamp(%s): %s, returning None",
          type(self).__name__, self.value, e
      )
      return None
    return dt.replace(year=dt.year - 66)

  @property
  def unixtime(self):
    ''' This timestamp as a UNIX time (seconds since 1 January 1970).
    '''
    dt = self.datetime
    if dt is None:
      return None
    return dt.timestamp()

class TimeStamp32(UInt32BE, TimeStampMixin):
  ''' The 32 bit form of an ISO14496 timestamp.
  '''

  def __str__(self):
    return str(self.datetime) or str(self.value)

class TimeStamp64(UInt64BE, TimeStampMixin):
  ''' The 64 bit form of an ISO14496 timestamp.
  '''

  def __str__(self):
    return str(self.datetime) or str(self.value)

class BoxHeader(BinaryMultiValue('BoxHeader', {
    'box_size': UInt32BE,
})):
  ''' An ISO14496 Box header packet.
  '''

  # speculative max size that will fit in the UInt32BE box_size
  # with room for bigger sizes in the optional UInt64BE length field
  MAX_BOX_SIZE_32 = 2**32 - 8

  @classmethod
  def parse(cls, bfr):
    ''' Decode a box header from `bfr`.
    '''
    self = cls()
    # note start of header
    self.offset = bfr.offset
    box_size = UInt32BE.parse_value(bfr)
    box_type = self.box_type = bfr.take(4)
    if box_size == 0:
      # box extends to end of data/file
      self.box_size = Ellipsis
    elif box_size == 1:
      # 64 bit length
      self.box_size = UInt64BE.parse_value(bfr)
    else:
      # other box_size values are the length
      self.box_size = box_size
    if box_type == b'uuid':
      # user supplied 16 byte type
      self.user_type = bfr.take(16)
    else:
      self.user_type = None
    # note end of self
    self.end_offset = bfr.offset
    self.type = box_type
    return self

  def transcribe(self):
    box_size = self.box_size
    if box_size is Ellipsis:
      box_size = 0
    elif box_size > self.MAX_BOX_SIZE_32:
      box_size = 1
    yield UInt32BE.transcribe_value(box_size)
    yield self.box_type
    if self.box_size is not Ellipsis and self.box_size > self.MAX_BOX_SIZE_32:
      yield UInt64BE.transcribe_value(self.box_size)
    if self.box_type == b'uuid':
      yield self.user_type

class BoxBody(BaseBinaryMultiValue, ABC):
  ''' Abstract basis for all `Box` bodies.
  '''

  FIELD_TYPES = dict(offset=int, post_offset=int)

  @staticmethod
  def S_CHOOSE_NAME(name):
    ''' Tweak the str/repr output a bit.
    '''
    return not name.startswith('_') and name != 'parent'

  def __getattr__(self, attr):
    ''' The following virtual attributes are defined:
        * *TYPE*`s`:
          "boxes of type *TYPE*",
          an uppercased box type name with a training `s`;
          a list of all elements whose `.box_type`
          equals *TYPE*`.lower().encode('ascii')`.
          The elements are obtained by iterating over `self`
          which normally means iterating over the `.boxes` attribute.
        * *TYPE*:
          "the box of type *TYPE*",
          an uppercased box type name;
          the sole element whose box type matches the type,
          obtained from `.`*TYPE*`s[0]`
          with a requirement that there is exactly one match.
        * *TYPE*`0`:
          "the optional box of type *TYPE*",
          an uppercased box type name with a trailing `0`;
          the sole element whose box type matches the type,
          obtained from `.`*TYPE*`s[0]`
          with a requirement that there is exactly zero or one match.
          If there are zero matches, return `None`.
          Otherwise return the matching box.
    '''
    # .TYPE - the sole item in self.boxes matching b'type'
    if len(attr) == 4 and attr.isupper():
      box, = getattr(self, attr + 's')
      return box
    # .TYPEs - all items of self.boxes matching b'type'
    if len(attr) == 5 and (attr.endswith('s') or attr.endswith('0')):
      attr4 = attr[:4]
      if attr4.isupper():
        box_type = attr4.lower().encode('ascii')
        boxes = [box for box in self if box.box_type == box_type]
        if attr.endswith('s'):
          return boxes
        if attr.endswith('0'):
          if len(boxes) == 0:
            return None
          box, = boxes
          return box
    raise AttributeError("%s.%s" % (type(self).__name__, attr))

  def __iter__(self):
    yield from ()

  @classmethod
  def parse(cls, bfr, *, box):
    ''' Create an emtpy `BoxBody` and fill it in via its `parse_fields`
        method.

        Note that this function is expected to be called from
        `Box.parse` and therefore that `bfr` is expected to
        be a bounded `CornuCopyBuffer` if the `Box` length is specified.
        Various `BoxBody` subclasses gather some data "until the end of the
        `Box`", and we rely on this bound rather than keeping a close eye on
        some unsupplied "end offset" value.
    '''
    self = cls()
    self.parent = box
    self.offset = bfr.offset
    self.parse_fields(bfr)
    self.post_offset = bfr.offset
    self.self_check()
    return self

  def parse_fields(self, bfr):
    ''' Gather the Box body fields from `bfr`.

        A generic BoxBody has no additional fields. Subclasses call
        their superclass' `parse_buffer` and then gather their
        specific fields.
    '''

  @abstractmethod
  def transcribe(self):
    raise NotImplementedError(
        "class %s requires a .transcribe method" % (type(self),)
    )

  @classmethod
  def boxbody_type_from_klass(cls):
    ''' Compute the Box's 4 byte type field from the class name.
    '''
    class_name = cls.__name__
    if len(class_name) == 11 and class_name.endswith('BoxBody'):
      class_prefix = class_name[:4]
      if class_prefix.rstrip('_').isupper():
        return class_prefix.replace('_', ' ').lower().encode('ascii')
    raise AttributeError("no automatic box type for %s" % (cls,))

class Box(BaseBinaryMultiValue):
  ''' Base class for all boxes - ISO14496 section 4.2.

      This has the following fields:
      * `header`: a `BoxHeader`
      * `body`: a `BoxBody` instance, usually a specific subclass
      * `unparsed`: any unconsumed bytes from the `Box` are stored as here
  '''

  FIELD_TYPES = {
      'header': BoxHeader,
      'body': (True, (BoxBody, BinaryByteses)),
      'unparsed': BinaryByteses,
      'offset': int,
      'unparsed_offset': int,
      'end_offset': int,
  }

  def __init__(self, parent=None):
    super().__init__()
    self.parent = parent

  def __str__(self):
    type_name = self.box_type_path
    try:
      body = self.body
    except AttributeError:
      s = "%s[%d]:NO_BODY" % (type_name, len(self))
    else:
      length = len(self)
      s = "%s[%d]:%s" % (type_name, len(self), body)
    unparsed_bs = self.unparsed_bs
    if unparsed_bs and unparsed_bs != b'\0':
      s += ":unparsed=%r" % (unparsed_bs[:16],)
    return s

  __repr__ = __str__

  def __getattr__(self, attr):
    ''' If there is no direct attribute from `BaseBinaryMultiValue.__getattr__`,
        have a look in the `.header` and `.body`.
    '''
    if attr in ('header', 'body'):
      raise AttributeError("%s.%s: not present" % (type(self).__name__, attr))
    try:
      value = getattr(self.header, attr)
    except AttributeError:
      try:
        value = getattr(self.body, attr)
      except AttributeError:
        raise AttributeError(
            "%s.%s: not present via %r or the .header or .body fields" % (
                type(self).__name__, attr,
                ",".join(map(lambda cls: cls.__name__,
                             type(self).__mro__))
            )
        )
    return value

  def __iter__(self):
    ''' Iterating over a `Box` iterates over its body.
        Typically that would be the `.body.boxes`
        but might be the samples if the body is a sample box,
        etc.
    '''
    if self.body is None:
      ##warning("iter(%s): body is None", self)
      return
    yield from iter(self.body)

  @classmethod
  def parse(cls, bfr):
    ''' Decode a Box from `bfr` and return it.
    '''
    self = cls()
    self.offset = bfr.offset
    header = self.header = BoxHeader.parse(bfr)
    length = header.box_size
    if length is Ellipsis:
      end_offset = Ellipsis
      bfr_tail = bfr
      warning("Box.parse_buffer: Box %s has no length", header)
    else:
      end_offset = self.offset + length
      bfr_tail = bfr.bounded(end_offset)
    body_class = pick_boxbody_class(header.type)
    self.body = body_class.parse(bfr_tail, box=self)
    self.unparsed_offset = bfr_tail.offset
    self.unparsed = BinaryByteses.parse(bfr_tail)
    if bfr_tail is not bfr:
      assert not bfr_tail.bufs, "bfr_tail.bufs=%r" % (bfr_tail.bufs,)
    self.end_offset = bfr.offset
    self.self_check()
    bfr.report_offset(self.offset)
    copy_boxes = PARSE_MODE.copy_boxes
    if copy_boxes:
      copy_boxes(self)
    return self

  @property
  def parse_length(self):
    ''' The length of the box as consumed from the buffer,
        computed as `self.end_offset-self.offset`.
    '''
    return self.end_offset - self.offset

  @property
  def unparsed_bs(self):
    ''' The unparsed data as a single `bytes` instance.
    '''
    return b''.join(self.unparsed_bss)

  @property
  def unparsed_bss(self):
    ''' The `bytes` instances comprising `.unparsed`.
    '''
    return self.unparsed.values

  def transcribe(self):
    ''' Transcribe the `Box`.

        Before transcribing the data, we compute the total box_size
        from the lengths of the current header, body and unparsed
        components, then set the header length if that has changed.
        Since setting the header length can change its representation
        we compute the length again and abort if it isn't stable.
        Otherwise we proceeed with a regular transcription.
    '''
    header = self.header
    body = self.body
    unparsed = self.unparsed
    new_length = len(header) + len(body) + len(unparsed)
    box_size = header.box_size
    if box_size is Ellipsis or box_size != new_length:
      # change the box_size
      header.box_size = new_length
      # Recompute the length from the header, body and unparsed
      # components, then set it on the header to get the prepare
      # transcription.
      new_length2 = len(header) + len(body) + len(unparsed)
      if new_length2 != header.box_size:
        # the header has changed size because we changed the length, try again
        header.box_size = new_length2
        new_length3 = len(header) + len(body) + len(unparsed)
        if new_length3 != header.box_size:
          # the header has changed size again, unstable, need better algorithm
          raise RuntimeError(
              "header size unstable, maybe we need a header mode to force the representation"
          )
    yield self.header.transcribe()
    yield self.body.transcribe()
    yield self.unparsed.transcribe()

  def self_check(self):
    ''' Sanity check this Box.
    '''
    super().self_check()
    # sanity check the supplied box_type
    # against the box types this class supports
    with Pfx("%s", self):
      box_type = self.header.type
      try:
        BOX_TYPE = self.BOX_TYPE
      except AttributeError:
        try:
          BOX_TYPES = self.BOX_TYPES
        except AttributeError:
          if not isinstance(self, Box):
            raise RuntimeError(
                "no BOX_TYPE or BOX_TYPES to check in class %r" %
                (type(self),)
            )
        else:
          if box_type not in BOX_TYPES:
            warning(
                "box_type should be in %r but got %r", BOX_TYPES,
                bytes(box_type)
            )
      else:
        if box_type != BOX_TYPE:
          warning("box_type should be %r but got %r", BOX_TYPE, box_type)
      parent = self.parent
      if parent is not None and not isinstance(parent, Box):
        warning("parent should be a Box, but is %r", type(self))

  @contextmanager
  def reparse_buffer(self):
    ''' Context manager for continuing a parse from the `unparsed` field.

        Pops the final `unparsed` field from the `Box`,
        yields a `CornuCopyBuffer` make from it,
        then pushes the `unparsed` field again
        with the remaining contents of the buffer.
    '''
    unparsed = self.unparsed
    self.unparsed = BinaryByteses()
    bfr = CornuCopyBuffer(unparsed.values)
    yield bfr
    self.unparsed = BinaryByteses.parse(bfr)

  @property
  def box_type(self):
    ''' The Box header type.
    '''
    return self.header.type

  @property
  def box_type_s(self):
    ''' The Box header type as a string.

        If the header type bytes decode as ASCII, return that,
        otherwise the header bytes' repr().
    '''
    box_type_b = bytes(self.box_type)
    try:
      box_type_name = box_type_b.decode('ascii')
    except UnicodeDecodeError:
      box_type_name = repr(box_type_b)
    return box_type_name

  @property
  def box_type_path(self):
    ''' The type path to this Box.
    '''
    types = [self.box_type_s]
    box = self.parent
    while box is not None:
      try:
        path_elem = box.box_type_s
      except AttributeError as e:
        raise RuntimeError(
            "%s.box_type_path: no .box_type_s on %r: %s" %
            (type(self).__name__, box, e)
        )
      types.append(path_elem)
      box = box.parent
    return '.'.join(reversed(types))

  @property
  def user_type(self):
    ''' The header user_type.
    '''
    return self.header.user_type

  # NB: a @property instead of @prop to preserve AttributeError
  @property
  def BOX_TYPE(self):
    ''' The default .BOX_TYPE is inferred from the class name.
    '''
    return type(self).boxbody_type_from_klass()

  def ancestor(self, box_type):
    ''' Return the closest ancestor box of type `box_type`.
    '''
    if isinstance(box_type, str):
      box_type = box_type.encode('ascii')
    parent = self.parent
    while parent:
      if parent.box_type == box_type:
        return parent
      parent = parent.parent
    return parent

  def dump(self, **kw):
    ''' Dump this Box.
    '''
    return dump_box(self, **kw)

  def walk(self):
    ''' Walk this `Box` hierarchy.

        Yields the starting box and its children as `(self,subboxes)`
        and then yields `(subbox,subsubboxes)` for each child in turn.

        As with `os.walk`, the returned `subboxes` list
        may be modified to prune the subsequent walk.
    '''
    subboxes = list(self)
    yield self, subboxes
    for subbox in subboxes:
      if isinstance(subbox, Box):
        yield from subbox.walk()

  def metatags(self):
    ''' Return a `TagSet` containing metadata for this box.
    '''
    with Pfx("metatags(%r)", self.box_type):
      box_prefix = self.box_type_s
      tags = TagSet()
      meta_box = self.META0
      if meta_box:
        tags.update(meta_box.tagset(), prefix=box_prefix + '.meta')
      else:
        pass  # X("NO .META0")
      udta_box = self.UDTA0
      if udta_box:
        pass  # X("UDTA?")
        udta_meta_box = udta_box.META0
        if udta_meta_box:
          ilst_box = udta_meta_box.ILST0
          if ilst_box:
            tags.update(ilst_box.tags, prefix=box_prefix + '.udta.meta.ilst')
      else:
        pass  # X("NO UDTA")
      ##dump_box(self, crop_length=None)
      return tags

  def gather_metadata(self):
    ''' Walk the `Box` hierarchy looking for metadata.
        Yield `(Box,TagSet)` for each `b'moov'` or `b'trak'` `Box`.
    '''
    for box, subboxes in self.walk():
      if box.box_type in (b'moov', b'trak'):
        yield box, box.metatags()

# patch us in
Box.FIELD_TYPES['parent'] = (False, (type(None), Box))
BoxBody.FIELD_TYPES['parent'] = Box

# mapping of known box subclasses for use by factories
KNOWN_BOXBODY_CLASSES = {}

class FallbackBoxBody(BoxBody):

  def transcribe(self):
    pass

def pick_boxbody_class(box_type: bytes):
  ''' Infer a `BoxBody` subclass from the bytes `box_type`.

      * `box_type`: the 4 byte box type
  '''
  return KNOWN_BOXBODY_CLASSES.get(box_type, FallbackBoxBody)

def add_body_class(klass):
  ''' Register a box body class in KNOWN_BOXBODY_CLASSES.
  '''
  global KNOWN_BOXBODY_CLASSES
  with Pfx("add_body_class(%s)", klass):
    try:
      box_types = klass.BOX_TYPES
    except AttributeError:
      box_type = klass.boxbody_type_from_klass()
      box_types = (box_type,)
    for box_type in box_types:
      if box_type in KNOWN_BOXBODY_CLASSES:
        raise TypeError(
            "box_type %r already in KNOWN_BOXBODY_CLASSES as %s" %
            (box_type, KNOWN_BOXBODY_CLASSES[box_type])
        )
      KNOWN_BOXBODY_CLASSES[box_type] = klass

def add_body_subclass(superclass, box_type, section, desc):
  ''' Create and register a new BoxBody class that is simply a subclass of
      another.  Returns the new class.
  '''
  if isinstance(box_type, bytes):
    box_type = box_type.decode('ascii')
  classname = box_type.upper() + 'BoxBody'
  box_type = box_type.encode('ascii')
  K = type(classname, (superclass,), {})
  K.__doc__ = (
      "Box type %r %s box - ISO14496 section %s." % (box_type, desc, section)
  )
  add_body_class(K)
  return K

class HasBoxesMixin:

  def __iter__(self):
    return iter(self.boxes)

  def __getattr__(self, attr):
    # .TYPE - the sole item in self.boxes matching b'type'
    if len(attr) == 4 and attr.isupper():
      box, = getattr(self, attr + 's')
      return box
    # .TYPEs - all items of self.boxes matching b'type'
    if len(attr) == 5 and attr.endswith('s'):
      attr4 = attr[:4]
      if attr4.isupper():
        box_type = attr4.lower().encode('ascii')
        boxes = [box for box in self.boxes if box.box_type == box_type]
        return boxes
    raise AttributeError(type(self).__name__ + '.' + attr)

class OverBox(BinaryListValues, HasBoxesMixin):
  ''' A fictitious `Box` encompassing all the Boxes in an input buffer.
  '''

  @pfx_method
  def __len__(self):
    return sum(map(len, self.values))

  @property
  def boxes(self):
    return self.values

  @classmethod
  def parse(cls, bfr):
    offset = bfr.offset
    self = super().parse(bfr, pt=Box)
    self.offset = offset
    self.end_offset = bfr.offset
    return self

  @property
  def length(self):
    ''' The `OverBox` is as long as the subsidary Boxes.
    '''
    return sum(map(len, self.boxes))

  def dump(self, **kw):
    ''' Dump this OverBox.
    '''
    return dump_box(self, **kw)

  def walk(self):
    ''' Walk the `Box`es in the `OverBox`.

        This does not yield the `OverBox` itself, it isn't really a `Box`.
    '''
    for box in self:
      yield from box.walk()

class FullBoxBody(BoxBody):
  ''' A common extension of a basic BoxBody, with a version and flags field.
      ISO14496 section 4.2.
  '''

  FIELD_TYPES = dict(
      BoxBody.FIELD_TYPES,
      version=int,
      flags0=int,
      flags1=int,
      flags2=int,
  )

  def parse_fields(self, bfr):
    super().parse_fields(bfr)
    self.version = UInt8.parse_value(bfr)
    self.flags0 = UInt8.parse_value(bfr)
    self.flags1 = UInt8.parse_value(bfr)
    self.flags2 = UInt8.parse_value(bfr)

  def transcribe(self):
    yield UInt8.transcribe_value(self.version)
    yield UInt8.transcribe_value(self.flags0)
    yield UInt8.transcribe_value(self.flags0)
    yield UInt8.transcribe_value(self.flags0)

  @property
  def flags(self):
    ''' The flags value, computed from the 3 flag bytes.
    '''
    return (self.flags0 << 16) | (self.flags1 << 8) | self.flags2

class MDATBoxBody(BoxBody):
  ''' A Media Data Box - ISO14496 section 8.1.1.
  '''

  FIELD_TYPES = dict(BoxBody.FIELD_TYPES, data=BinaryByteses)

  def parse_fields(self, bfr):
    ''' Gather all data to the end of the field.
    '''
    super().parse_fields(bfr)
    if PARSE_MODE.discard_data:
      self.data = None
      bfr.skipto(bfr.end_offset)
    else:
      self.data = BinaryByteses.parse(bfr)

  def transcribe(self):
    return self.data.transcribe()

add_body_class(MDATBoxBody)

class FREEBoxBody(BoxBody):
  ''' A 'free' or 'skip' box - ISO14496 section 8.1.2.
      Note the length and discard the data portion.
  '''

  FIELD_TYPES = dict(
      BoxBody.FIELD_TYPES,
      free_size=int,
  )

  BOX_TYPES = (b'free', b'skip')

  def parse_fields(self, bfr, end_offset=Ellipsis, **kw):
    ''' Gather the `padding` field.
    '''
    super().parse_fields(bfr, **kw)
    offset0 = bfr.offset
    self.free_size = bfr.end_offset - bfr.offset
    bfr.skipto(bfr.end_offset)

  def transcribe(self):
    free_size = self.free_size
    n256 = len(B0_256)
    while free_size >= n256:
      yield B0_256
      free_size -= n256
    if free_size > 0:
      yield bytes(free_size)

add_body_class(FREEBoxBody)

class FTYPBoxBody(BoxBody):
  ''' An 'ftyp' File Type box - ISO14496 section 4.3.
      Decode the major_brand, minor_version and compatible_brands.
  '''

  FIELD_TYPES = dict(
      BoxBody.FIELD_TYPES,
      major_brand=bytes,
      minor_version=int,
      brands_bs=bytes,
  )

  def parse_fields(self, bfr, **kw):
    ''' Gather the `major_brand`, `minor_version` and `brand_bs` fields.
    '''
    super().parse_fields(bfr, **kw)
    self.major_brand = bfr.take(4)
    self.minor_version = UInt32BE.parse_value(bfr)
    self.brands_bs = b''.join(bfr)

  @pfx_method
  def transcribe(self):
    yield self.major_brand
    yield UInt32BE.transcribe_value(self.minor_version)
    yield self.brands_bs

  @property
  def compatible_brands(self):
    ''' The compatible brands as a list of 4 bytes bytes instances.
    '''
    return [
        self.brands_bs[offset:offset + 4]
        for offset in range(0, len(self.brands_bs), 4)
    ]

add_body_class(FTYPBoxBody)

class PDINBoxBody(FullBoxBody):
  ''' A 'pdin' Progressive Download Information box - ISO14496 section 8.1.3.
  '''

  FIELD_TYPES = dict(
      FullBoxBody.FIELD_TYPES,
      pdinfo=ListField,
  )

  # field names for the tuples in a PDINBoxBody
  PDInfo = BinaryMultiStruct('PDInfo', '>LL', 'rate initial_delay')

  def parse_fields(self, bfr, **kw):
    ''' Gather the (rate, initial_delay) pairs of the data section as the `pdinfo` field.
    '''
    super().parse_fields(bfr, **kw)
    self.pdinfo = BinaryListValues.parse(bfr, pt=PDINBoxBody.PDInfo)

add_body_class(PDINBoxBody)

class ContainerBoxBody(BoxBody):

  FIELD_TYPES = dict(BoxBody.FIELD_TYPES, boxes=BinaryListValues)

  @pfx_method
  def parse_fields(self, bfr):
    super().parse_fields(bfr)
    self.boxes = BinaryListValues.parse(bfr, pt=Box)

  def transcribe(self):
    return self.boxes.transcribe()

  def __iter__(self):
    return iter(self.boxes)

class MOOVBoxBody(ContainerBoxBody):
  ''' An 'moov' Movie box - ISO14496 section 8.2.1.
      Decode the contained boxes.
  '''
  pass

add_body_class(MOOVBoxBody)

class MVHDBoxBody(FullBoxBody):
  ''' An 'mvhd' Movie Header box - ISO14496 section 8.2.2.
  '''

  FIELD_TYPES = dict(
      FullBoxBody.FIELD_TYPES,
      creation_time=(True, (TimeStamp32, TimeStamp64)),
      modification_time=(True, (TimeStamp32, TimeStamp64)),
      timescale=UInt32BE,
      duration=(True, (UInt32BE, UInt64BE)),
      rate_long=Int32BE,
      volume_short=Int16BE,
      reserved1=bytes,
      matrix=Matrix9Long,
      predefined1=bytes,
      next_track_id=UInt32BE,
  )

  def parse_fields(self, bfr):
    super().parse_fields(bfr)
    # obtain box data after version and flags decode
    if self.version == 0:
      self.parse_field('creation_time', bfr, TimeStamp32)
      self.parse_field('modification_time', bfr, TimeStamp32)
      self.parse_field('timescale', bfr, UInt32BE)
      self.parse_field('duration', bfr, UInt32BE)
    elif self.version == 1:
      self.parse_field('creation_time', bfr, TimeStamp64)
      self.parse_field('modification_time', bfr, TimeStamp64)
      self.parse_field('timescale', bfr, UInt32BE)
      self.parse_field('duration', bfr, UInt64BE)
    else:
      raise ValueError("MVHD: unsupported version %d" % (self.version,))
    self.parse_field('rate_long', bfr, Int32BE)
    self.parse_field('volume_short', bfr, Int16BE)
    self.parse_field('reserved1', bfr, 10)  # 2-reserved, 2x4 reserved
    self.parse_field('matrix', bfr, Matrix9Long),
    self.parse_field('predefined1', bfr, 24)  # 6x4 predefined
    self.parse_field('next_track_id', bfr, UInt32BE)
    return self

  def transcribe(self):
    yield super().transcribe()
    yield self.creation_time.transcribe()
    yield self.modification_time.transcribe()
    yield self.timescale.transcribe()
    yield self.duration.transcribe()
    yield self.rate_long.transcribe()
    yield self.volume_short.transcribe()
    yield self.reserved1
    yield self.matrix.transcribe()
    yield self.predefined1
    yield self.next_track_id.transcribe()

  @prop
  def rate(self):
    ''' Rate field converted to float: 1.0 represents normal rate.
    '''
    rate_long = self.rate_long
    return (rate_long >> 16) + (rate_long & 0xffff) / 65536.0

  @prop
  def volume(self):
    ''' Volume field converted to float: 1.0 represents full volume.
    '''
    volume_short = self.volume_short
    return (volume_short >> 8) + (volume_short & 0xff) / 256.0

add_body_class(MVHDBoxBody)

add_body_subclass(ContainerBoxBody, 'trak', '8.3.1', 'Track')

class TKHDBoxBody(FullBoxBody):
  ''' A 'tkhd' Track Header box - ISO14496 section 8.2.2.
  '''

  TKHDMatrix = BinaryMultiStruct(
      'TKHDMatrix', '>lllllllll', 'v0 v1 v2 v3 v4 v5 v6 v7 v8'
  )

  FIELD_TYPES = dict(
      FullBoxBody.FIELD_TYPES,
      creation_time=(True, (TimeStamp32, TimeStamp64)),
      modification_time=(True, (TimeStamp32, TimeStamp64)),
      track_id=UInt32BE,
      reserved1=UInt32BE,
      duration=(True, (UInt32BE, UInt64BE)),
      reserved2=UInt32BE,
      reserved3=UInt32BE,
      layer=Int16BE,
      alternate_group=Int16BE,
      volume=Int16BE,
      reserved4=UInt16BE,
      matrix=TKHDMatrix,
      width=UInt32BE,
      height=UInt32BE,
  )

  def parse_fields(self, bfr, **kw):
    super().parse_fields(bfr, **kw)
    # obtain box data after version and flags decode
    if self.version == 0:
      self.parse_field('creation_time', bfr, TimeStamp32)
      self.parse_field('modification_time', bfr, TimeStamp32)
      self.parse_field('track_id', bfr, UInt32BE)
      self.parse_field('reserved1', bfr, UInt32BE)
      self.parse_field('duration', bfr, UInt32BE)
    elif self.version == 1:
      self.parse_field('creation_time', bfr, TimeStamp64)
      self.parse_field('modification_time', bfr, TimeStamp64)
      self.parse_field('track_id', bfr, UInt32BE)
      self.parse_field('reserved1', bfr, UInt32BE)
      self.parse_field('duration', bfr, UInt64BE)
    else:
      raise ValueError("TRHD: unsupported version %d" % (self.version,))
    self.parse_field('reserved2', bfr, UInt32BE)
    self.parse_field('reserved3', bfr, UInt32BE)
    self.parse_field('layer', bfr, Int16BE)
    self.parse_field('alternate_group', bfr, Int16BE)
    self.parse_field('volume', bfr, Int16BE)
    self.parse_field('reserved4', bfr, UInt16BE)
    self.parse_field('matrix', bfr, TKHDBoxBody.TKHDMatrix)
    self.parse_field('width', bfr, UInt32BE)
    self.parse_field('height', bfr, UInt32BE)

  def transcribe(self):
    yield super().transcribe()
    yield self.creation_time.transcribe()
    yield self.modification_time.transcribe()
    yield self.track_id.transcribe()
    yield self.reserved1.transcribe()
    yield self.duration.transcribe()
    yield self.reserved2.transcribe()
    yield self.reserved3.transcribe()
    yield self.layer.transcribe()
    yield self.alternate_group.transcribe()
    yield self.volume.transcribe()
    yield self.reserved4.transcribe()
    yield self.matrix.transcribe()
    yield self.width.transcribe()
    yield self.height.transcribe()

  @prop
  def track_enabled(self):
    ''' Test flags bit 0, 0x1, track_enabled.
    '''
    return (self.flags & 0x1) != 0

  @prop
  def track_in_movie(self):
    ''' Test flags bit 1, 0x2, track_in_movie.
    '''
    return (self.flags & 0x2) != 0

  @prop
  def track_in_preview(self):
    ''' Test flags bit 2, 0x4, track_in_preview.
    '''
    return (self.flags & 0x4) != 0

  @prop
  def track_size_is_aspect_ratio(self):
    ''' Test flags bit 3, 0x8, track_size_is_aspect_ratio.
    '''
    return (self.flags & 0x8) != 0

  @prop
  def timescale(self):
    ''' The `timescale` comes from the movie header box (8.3.2.3).
    '''
    return self.ancestor('mvhd').timescale

add_body_class(TKHDBoxBody)

##add_body_subclass(ContainerBoxBody, 'tref', '8.3.3', 'track Reference')

class TREFBoxBody(ContainerBoxBody):
  ''' Track Reference BoxBody, container for trackReferenceTypeBoxes - ISO14496 section 8.3.3.
  '''

add_body_class(TREFBoxBody)

class TrackReferenceTypeBoxBody(BoxBody):
  ''' A TrackReferenceTypeBoxBody contains references to other tracks - ISO14496 section 8.3.3.2.
  '''
  FIELD_TYPES = dict(BoxBody.FIELD_TYPES, track_ids=ListField)

  BOX_TYPES = (
      b'hint',
      b'cdsc',
      b'chap',
      b'font',
      b'hind',
      b'vdep',
      b'vplx',
      b'subt',
      b'forc',
  )

  def parse_fields(self, bfr):
    ''' Gather the `track_ids` field.
    '''
    super().parse_fields(bfr)
    self.track_ids = BinaryListValues.parse(bfr, pt=UInt32BE)

add_body_class(TrackReferenceTypeBoxBody)
add_body_subclass(ContainerBoxBody, 'trgr', '8.3.4', 'Track Group')

class TrackGroupTypeBoxBody(FullBoxBody):
  ''' A TrackGroupTypeBoxBody contains a track group id - ISO14496 section 8.3.3.2.
  '''

  def __init__(self, box_type, box_data):
    FullBoxBody.__init__(self, box_type, box_data)

  def parse_fields(self, bfr):
    ''' Gather the `track_group_id` field.
    '''
    super().parse_fields(bfr)
    self.parse_field('track_group_id', bfr, UInt32BE)

add_body_subclass(
    TrackGroupTypeBoxBody, 'msrc', '8.3.4.3',
    'Multi-source presentation Track Group'
)
add_body_subclass(ContainerBoxBody, 'mdia', '8.4.1', 'Media')

class MDHDBoxBody(FullBoxBody):
  ''' A MDHDBoxBody is a Media Header box - ISO14496 section 8.4.2.
  '''

  FIELD_TYPES = dict(
      FullBoxBody.FIELD_TYPES,
      creation_time=(True, (TimeStamp32, TimeStamp64)),
      modification_time=(True, (TimeStamp32, TimeStamp64)),
      timescale=UInt32BE,
      duration=(True, (UInt32BE, UInt64BE)),
      language_short=UInt16BE,
      pre_defined=UInt16BE,
  )

  def parse_fields(self, bfr):
    ''' Gather the `creation_time`, `modification_time`, `timescale`,
        `duration` and `language_short` fields.
    '''
    super().parse_fields(bfr)
    # obtain box data after version and flags decode
    if self.version == 0:
      self.parse_field('creation_time', bfr, TimeStamp32)
      self.parse_field('modification_time', bfr, TimeStamp32)
      self.parse_field('timescale', bfr, UInt32BE)
      self.parse_field('duration', bfr, UInt32BE)
    elif self.version == 1:
      self.parse_field('creation_time', bfr, TimeStamp64)
      self.parse_field('modification_time', bfr, TimeStamp64)
      self.parse_field('timescale', bfr, UInt32BE)
      self.parse_field('duration', bfr, UInt64BE)
    else:
      raise RuntimeError("unsupported version %d" % (self.version,))
    self.parse_field('language_short', bfr, UInt16BE)
    self.parse_field('pre_defined', bfr, UInt16BE)

  @prop
  def language(self):
    ''' The ISO 639‐2/T language code as decoded from the packed form.
    '''
    language_short = self.language_short
    return bytes(
        [
            x + 0x60 for x in (
                (language_short >> 10) & 0x1f, (language_short >> 5) & 0x1f,
                language_short & 0x1f
            )
        ]
    ).decode('ascii')

add_body_class(MDHDBoxBody)

class HDLRBoxBody(FullBoxBody):
  ''' A HDLRBoxBody is a Handler Reference box - ISO14496 section 8.4.3.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      pre_defined=UInt32BE,
      handler_type_long=UInt32BE,
      reserved1=UInt32BE,
      reserved2=UInt32BE,
      reserved3=UInt32BE,
      name=UTF8NULField,
  )

  def parse_fields(self, bfr):
    ''' Gather the `handler_type_long` and `name` fields.
    '''
    super().parse_fields(bfr)
    # NB: handler_type is supposed to be an unsigned long, but in
    # practice seems to be 4 ASCII bytes, so we load it as a string
    # for readability
    self.add_from_buffer('pre_defined', bfr, UInt32BE)
    self.add_from_buffer('handler_type_long', bfr, UInt32BE)
    self.add_from_buffer('reserved1', bfr, UInt32BE)
    self.add_from_buffer('reserved2', bfr, UInt32BE)
    self.add_from_buffer('reserved3', bfr, UInt32BE)
    self.add_from_buffer('name', bfr, UTF8NULField)

  @property
  def handler_type(self):
    ''' The handler_type as an ASCII string, its usual form.
    '''
    return bytes(self.handler_type_long).decode('ascii')

add_body_class(HDLRBoxBody)
add_body_subclass(ContainerBoxBody, b'minf', '8.4.4', 'Media Information')
add_body_subclass(FullBoxBody, 'nmhd', '8.4.5.2', 'Null Media Header')

class ELNGBoxBody(FullBoxBody):
  ''' A ELNGBoxBody is a Extended Language Tag box - ISO14496 section 8.4.6.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      extended_language=UTF8NULField,
  )

  def parse_fields(self, bfr):
    ''' Gather the `extended_language` field.
    '''
    super().parse_fields(bfr)
    # extended language based on RFC4646
    self.add_from_buffer('extended_language', bfr, UTF8NULField)

add_body_class(ELNGBoxBody)
add_body_subclass(ContainerBoxBody, b'stbl', '8.5.1', 'Sample Table')

class _SampleTableContainerBoxBody(FullBoxBody):
  ''' An intermediate FullBoxBody subclass which contains more boxes.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      entry_count=UInt32BE,
      boxes=SubBoxesField,
  )

  def __iter__(self):
    return iter(self.boxes)

  def parse_fields(self, bfr):
    ''' Gather the `entry_count` and `boxes`.
    '''
    super().parse_fields(bfr)
    # obtain box data after version and flags decode
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    boxes = self.add_from_buffer(
        'boxes',
        bfr,
        SubBoxesField,
        end_offset=Ellipsis,
        max_boxes=entry_count,
        parent=self.box,
        copy_boxes=copy_boxes
    )
    if len(boxes) != entry_count:
      raise ValueError(
          "expected %d contained Boxes but parsed %d" %
          (entry_count, len(boxes))
      )

add_body_subclass(
    _SampleTableContainerBoxBody, b'stsd', '8.5.2', 'Sample Description'
)

class _SampleEntry(BoxBody):
  ''' Superclass of Sample Entry boxes.
  '''

  def parse_fields(self, bfr):
    ''' Gather the `data_reference_inde` field.
    '''
    self.add_from_buffer('reserved', bfr, BytesField, length=6)
    self.add_from_buffer('data_reference_index', bfr, UInt16BE)
    super().parse_fields(bfr)

class BTRTBoxBody(BoxBody):
  ''' BitRateBoxBody - section 8.5.2.2.
  '''

  def parse_fields(self, bfr):
    ''' Gather the `bufferSizeDB`, `maxBitrate` and `avgBitrate` fields.
    '''
    self.add_from_buffer('bufferSizeDB', bfr, UInt32BE)
    self.add_from_buffer('maxBitrate', bfr, UInt32BE)
    self.add_from_buffer('avgBitRate', bfr, UInt32BE)
    super().parse_fields(bfr)

add_body_class(BTRTBoxBody)
add_body_subclass(
    _SampleTableContainerBoxBody, b'stdp', '8.5.3', 'Degradation Priority'
)

TTSB_Sample = namedtuple('TTSB_Sample', 'count delta')

def add_generic_sample_boxbody(
    box_type,
    section,
    desc,
    struct_format_v0,
    sample_fields,
    struct_format_v1=None,
    has_inferred_entry_count=False,
):
  ''' Create and add a specific Time to Sample box - section 8.6.1.
  '''
  if struct_format_v1 is None:
    struct_format_v1 = struct_format_v0
  class_name = box_type.decode('ascii').upper() + 'BoxBody'
  sample_class_name = class_name + 'Sample'
  sample_type_v0 = BinaryMultiStruct(
      sample_class_name + 'V0', struct_format_v0, sample_fields
  )
  sample_type_v1 = BinaryMultiStruct(
      sample_class_name + 'V1', struct_format_v1, sample_fields
  )

  class SpecificSampleBoxBody(FullBoxBody):
    ''' Time to Sample box - section 8.6.1.
    '''
    PACKET_FIELDS = dict(
        FullBoxBody.PACKET_FIELDS,
        entry_count=(False, UInt32BE),
        ##samples=ListField,
    )

    def parse_fields(self, bfr):
      super().parse_fields(bfr)
      if self.version == 0:
        sample_type = self.sample_type = sample_type_v0
      elif self.version == 1:
        sample_type = self.sample_type = sample_type_v1
      else:
        warning(
            "unsupported version %d, treating like version 1", self.version
        )
        sample_type = self.sample_type = sample_type_v1
      sample_size = sample_type.struct.size
      self.has_inferred_entry_count = has_inferred_entry_count
      if has_inferred_entry_count:
        entry_count = Ellipsis
      else:
        entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
      # gather the sample data but do not bother to parse it yet
      # because that can be very expensive
      if entry_count is Ellipsis:
        end_offset = bfr.end_offset
        entry_count = (end_offset - bfr.offset) // sample_size
      self.samples_count = entry_count
      self.add_deferred_field('samples', bfr, entry_count * sample_size)

    def transcribe(self):
      ''' Transcribe the regular fields
          then transcribe the source data of the samples.
      '''
      yield super().transcribe()
      yield self._samples__raw_data

    @deferred_field
    def samples(self, bfr):
      ''' The `sample_data` decoded.
      '''
      sample_type = self.sample_type
      decoded = []
      for _ in range(self.samples_count):
        decoded.append(sample_type.from_buffer(bfr))
      return decoded

  SpecificSampleBoxBody.__name__ = class_name
  SpecificSampleBoxBody.__doc__ = (
      "Box type %r %s box - ISO14496 section %s." % (box_type, desc, section)
  )
  # we define these here because the names collide with the closure
  SpecificSampleBoxBody.struct_format_v0 = struct_format_v0
  SpecificSampleBoxBody.sample_type_v0 = sample_type_v0
  SpecificSampleBoxBody.struct_format_v1 = struct_format_v1
  SpecificSampleBoxBody.sample_type_v1 = sample_type_v1
  add_body_class(SpecificSampleBoxBody)
  return SpecificSampleBoxBody

def add_time_to_sample_boxbody(box_type, section, desc):
  ''' Add a Time to Sample box - section 8.6.1.
  '''
  return add_generic_sample_boxbody(
      box_type,
      section,
      desc,
      '>LL',
      'count delta',
      has_inferred_entry_count=False,
  )

add_time_to_sample_boxbody(b'stts', '8.6.1.2.1', 'Time to Sample')

add_generic_sample_boxbody(
    b'ctts', '8.6.1.3', 'Composition Time to Sample', '>LL', 'count offset',
    '>Ll'
)

class CSLGBoxBody(FullBoxBody):
  ''' A 'cslg' Composition to Decode box - section 8.6.1.4.
  '''

  CSLG_PARAM_NAMES = (
      'compositionToDTSShift', 'leastDecodeToDisplayDelta',
      'greatestDecodeToDisplayDelta', 'compositionStartTime',
      'compositionEndTime'
  )
  CSLGParamsLong = BinaryMultiStruct(
      'CSLGParamsLong', '>lllll', CSLG_PARAM_NAMES
  )
  CSLGParamsQuad = BinaryMultiStruct(
      'CSLGParamsLong', '>qqqqq', CSLG_PARAM_NAMES
  )

  def parse_fields(self, bfr):
    ''' Gather the compositionToDTSShift`, `leastDecodeToDisplayDelta`,
        `greatestDecodeToDisplayDelta`, `compositionStartTime` and
        `compositionEndTime` fields.
    '''
    super().parse_fields(bfr)
    if self.version == 0:
      param_type = self.CSLGParamsLong
    elif self.version == 1:
      param_type = self.CSLGParamsQuad
    else:
      warning("unsupported version %d, treating like version 1")
      param_type = self.CSLGParamsQuad
    self.add_field('params', param_type)

  def __getattr__(self, attr):
    ''' Present the `params` attributes at the top level.
    '''
    try:
      return getattr(self.params, attr)
    except AttributeError:
      return super().__getattr__(attr)

add_body_class(CSLGBoxBody)

add_generic_sample_boxbody(b'stss', '8.6.2', 'Sync Sample', '>L', 'number')

add_generic_sample_boxbody(
    b'stsh', '8.6.3', 'Shadow Sync Table', '>LL',
    'shadowed_sample_number sync_sample_number'
)

add_generic_sample_boxbody(
    b'sdtp',
    '8.6.4',
    'Independent and Disposable Samples',
    '>HHHH',
    'is_leading sample_depends_on sample_is_depended_on sample_has_redundancy',
    has_inferred_entry_count=True
)

add_body_subclass(BoxBody, b'edts', '8.6.5.1', 'Edit')

add_generic_sample_boxbody(
    b'elst', '8.6.6', 'Edit List', '>Ll', 'segment_duration media_time', '>Qq'
)

add_body_subclass(ContainerBoxBody, b'dinf', '8.7.1', 'Data Information')

class URL_BoxBody(FullBoxBody):
  ''' An 'url ' Data Entry URL BoxBody - section 8.7.2.1.
  '''

  def parse_fields(self, bfr):
    ''' Gather the `location` field.
    '''
    self.add_from_buffer('location', bfr, UTF8NULField)
    super().parse_fields(bfr)

add_body_class(URL_BoxBody)

class URN_BoxBody(FullBoxBody):
  ''' An 'urn ' Data Entry URL BoxBody - section 8.7.2.1.
  '''

  def parse_fields(self, bfr):
    ''' Gather the `name` and `location` fields.
    '''
    self.add_from_buffer('name', bfr, UTF8NULField)
    self.add_from_buffer('location', bfr, UTF8NULField)
    super().parse_fields(bfr)

add_body_class(URN_BoxBody)

class STSZBoxBody(FullBoxBody):
  ''' A 'stsz' Sample Size box - section 8.7.3.2.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      sample_size=UInt32BE,
      sample_count=UInt32BE,
      ##entry_sizes=(False, ListField),
  )

  def parse_fields(self, bfr):
    ''' Gather the `sample_size`, `sample_count`, and `entry_sizes` fields.
    '''
    sample_size = self.add_from_buffer('sample_size', bfr, UInt32BE)
    sample_count = self.add_from_buffer('sample_count', bfr, UInt32BE)
    super().parse_fields(bfr)
    if sample_size == 0:
      # a zero sample size means that each sample's individual size
      # is specified in `entry_sizes`
      self.add_deferred_field(
          'entry_sizes', bfr, sample_count * UInt32BE.length
      )

  @deferred_field
  def entry_sizes(self, bfr):
    ''' Parse the `UInt32BE` entry sizes from stashed buffer.
      '''
    entry_sizes = []
    for _ in range(self.sample_count):
      entry_sizes.append(UInt32BE.from_buffer(bfr))
    return entry_sizes

add_body_class(STSZBoxBody)

class STZ2BoxBody(FullBoxBody):
  ''' A 'stz2' Compact Sample Size box - section 8.7.3.3.
  '''

  def parse_fields(self, bfr):
    ''' Gather the `field_size`, `sample_count` and `entry_sizes` fields.
    '''
    self.add_from_buffer('reserved', bfr, BytesField, length=3)
    field_size = self.add_from_buffer('field_size', bfr, UInt8)
    sample_count = self.add_from_buffer('sample_count', bfr, UInt32BE)
    entry_sizes = []
    if field_size == 4:
    super().parse_fields(bfr)
      # nybbles packed into bytes
      for i in range(sample_count):
        if i % 2 == 0:
          bs = bfr.take(1)
          entry_sizes.append(bs[0] >> 4)
        else:
          entry_sizes.append(bs[0] & 0x0f)
      self.add_field('entry_sizes', ListField(entry_sizes))
    elif field_size == 8:
      for _ in range(sample_count):
        entry_sizes.append(UInt8.from_buffer(bfr))
      self.add_field('entry_sizes', ListField(entry_sizes))
    elif field_size == 16:
      for _ in range(sample_count):
        entry_sizes.append(UInt16BE.from_buffer(bfr))
      self.add_field('entry_sizes', ListField(entry_sizes))
    else:
      warning("unhandled field_size=%s, not parsing entry_sizes", field_size)

class STSCBoxBody(FullBoxBody):
  ''' 'stsc' (Sample Table box - section 8.7.4.1.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      entry_count=UInt32BE,
      ##entries=ListField,
  )

  STSCEntry = BinaryMultiStruct(
      'STSCEntry', '>LLL',
      'first_chunk samples_per_chunk sample_description_index'
  )

  def parse_fields(self, bfr):
    ''' Gather the `entry_count` and `entries` fields.
    '''
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    self.add_deferred_field(
        'entries', bfr, entry_count * STSCBoxBody.STSCEntry.length
    )
    super().parse_fields(bfr)

  @deferred_field
  def entries(self, bfr):
    ''' Parse the `STSCEntry` list from stashed buffer.
    '''
    entries = []
    for _ in range(self.entry_count):
      entries.append(STSCBoxBody.STSCEntry.from_buffer(bfr))
    return entries

add_body_class(STSCBoxBody)

class STCOBoxBody(FullBoxBody):
  ''' A 'stco' Chunk Offset box - section 8.7.5.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      entry_count=UInt32BE,
      ##chunk_offsets=ListField,
  )

  def parse_fields(self, bfr):
    ''' Gather the `entry_count` and `chunk_offsets` fields.
    '''
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    self.add_deferred_field(
        'chunk_offsets', bfr, entry_count * UInt32BE.length
    )
    super().parse_fields(bfr)

  @deferred_field
  def chunk_offsets(self, bfr):
    ''' Parse the `UInt32BE` chunk offsets from stashed buffer.
    '''
    chunk_offsets = []
    for _ in range(self.entry_count):
      chunk_offsets.append(UInt32BE.from_buffer(bfr))
    return chunk_offsets

add_body_class(STCOBoxBody)

class CO64BoxBody(FullBoxBody):
  ''' A 'c064' Chunk Offset box - section 8.7.5.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      entry_count=UInt32BE,
      ##chunk_offsets=ListField,
  )

  def parse_fields(self, bfr):
    ''' Gather the `entry_count` and `chunk_offsets` fields.
    '''
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    self.add_deferred_field(
        'chunk_offsets', bfr, entry_count * UInt64BE.length
    )
    super().parse_fields(bfr)

  @deferred_field
  def chunk_offsets(self, bfr):
    offsets = []
    for _ in range(self.entry_count):
      offsets.append(UInt64BE.from_buffer(bfr))
    return offsets

add_body_class(CO64BoxBody)

class DREFBoxBody(FullBoxBody):
  ''' A 'dref' Data Reference box containing Data Entry boxes - section 8.7.2.1.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      entry_count=UInt32BE,
      boxes=SubBoxesField,
  )

  def parse_fields(self, bfr):
    ''' Gather the `entry_count` and `boxes` fields.
    '''
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    self.add_from_buffer(
        'boxes',
        bfr,
        SubBoxesField,
        end_offset=Ellipsis,
        max_boxes=entry_count,
        parent=self.box,
        copy_boxes=copy_boxes
    super().parse_fields(bfr)
    )

add_body_class(DREFBoxBody)

add_body_subclass(ContainerBoxBody, b'udta', '8.10.1', 'User Data')

class CPRTBoxBody(FullBoxBody):
  ''' A 'cprt' Copyright box - section 8.10.2.
  '''

  def parse_fields(self, bfr):
    ''' Gather the `language` and `notice` fields.
    '''
    self.add_from_buffer('language_packed', UInt16BE)
    self.add_from_buffer('notice', UTF8or16Field)
    super().parse_fields(bfr)

  @property
  def language(self):
    ''' The `language_field` as the 3 character ISO 639-2/T language code.
    '''
    packed = self.language_packed.value
    return bytes(
        (packed & 0x1f) + 0x60, ((packed >> 5) & 0x1f) + 0x60,
        ((packed >> 10) & 0x1f) + 0x60
    ).decode('ascii')

  @language.setter
  def language(self, language_code):
    ''' Pack a 3 character ISO 639-2/T language code.
    '''
    ch1, ch2, ch3 = language_code
    packed = bytes(
        (ord(ch1) - 0x60) & 0x1f, ((ord(ch1) - 0x60) & 0x1f) << 5,
        ((ord(ch1) - 0x60) & 0x1f) << 10
    )
    self.language_packed.value = packed

class METABoxBody(FullBoxBody):
  ''' A 'meta' Meta BoxBody - section 8.11.1.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      theHandler=Box,
      boxes=SubBoxesField,
  )

  def __iter__(self):
    return iter(self.boxes)

  def parse_fields(self, bfr):
    ''' Gather the `theHandler` Box and gather the following Boxes as `boxes`.
    '''
    theHandler = self.add_field('theHandler', Box.from_buffer(bfr))
    theHandler.parent = self.box
    self.add_from_buffer(
        'boxes',
        bfr,
        SubBoxesField,
        end_offset=Ellipsis,
        parent=self.box,
        copy_boxes=copy_boxes
    )
    super().parse_fields(bfr)

  def __getattr__(self, attr):
    ''' Present the ilst attributes if present.
    '''
    try:
      return super().__getattr__(attr)
    except AttributeError:
      ilst = super().__getattr__('ISLT0')
      if ilst is not None:
        value = getattr(ilst, attr, None)
        if value is not None:
          return value
      raise

add_body_class(METABoxBody)

def ILSTRawSchema(attribute_name):
  ''' Namedtuple type for ILST raw schema.
  '''
  return namedtuple('ILSTRawSchema', 'attribute_name parse transcribe')(
      attribute_name, lambda bfr: bfr.take(...), lambda bs: bs
  )

def ILSTTextSchema(attribute_name):
  ''' Namedtuple type for ILST text schema.
  '''
  return namedtuple('ILSTTextSchema', 'attribute_name parse transcribe')(
      attribute_name, lambda bfr: bfr.take(...).decode(),
      lambda text: text.encode()
  )

def ILSTUInt32BESchema(attribute_name):
  ''' Namedtuple type for ILST UInt32BE schema.
  '''
  return namedtuple('ILSTUInt8Schema', 'attribute_name parse transcribe')(
      attribute_name, UInt32BE.parse_value, UInt32BE.transcribe_value
  )

def ILSTUInt8Schema(attribute_name):
  ''' Namedtuple type for ILST UInt8BE schema.
  '''
  return namedtuple('ILSTUInt8Schema', 'attribute_name parse transcribe')(
      attribute_name, UInt8.parse_value, UInt8.transcribe_value
  )

def ILSTAofBSchema(attribute_name):
  ''' Namedtuple type for ILST "A of B" schema.
  '''
  return namedtuple('ILSTUInt8Schema', 'attribute_name parse transcribe')(
      attribute_name, lambda bfr: namedtuple('member_n_of', 'n total')
      (UInt32BE.parse_value(bfr), UInt32BE.parse_value(bfr)),
      lambda member_n_of: UInt32BE.transcribe_value(member_n_of.n) + UInt32BE.
      transcribe_value(member_n_of.total)
  )

def ILSTISOFormatSchema(attribute_name):
  ''' Namedtuple type for ILST ISO format schema.
  '''
  return namedtuple('ILSTISOFormatSchema', 'attribute_name parse transcribe')(
      attribute_name,
      lambda bfr: datetime.fromisoformat(bfr.take(...).decode()),
      lambda dt: dt.isoformat(sep=' ', timespec='seconds').encode()
  )

itunes_media_type = namedtuple('itunes_media_type', 'type stik')

def decode_itunes_date_field(data):
  ''' The iTunes 'Date' meta field: a year or an ISO timestamp.
  '''
  try:
    value = datetime.fromisoformat(data)
  except ValueError:
    value = datetime(int(data), 1, 1)
  return value

itunes_store_country_code = namedtuple(
    'itunes_store_country_code',
    'country_name iso_3166_1_code itunes_store_code'
)

class ILSTBoxBody(ContainerBoxBody):
  ''' iTunes Information List, container for iTunes metadata fields.

      The basis of the format knowledge here comes from AtomicParsley's
      documentation here:

          http://atomicparsley.sourceforge.net/mpeg-4files.html

      and additional information from:

          https://github.com/sergiomb2/libmp4v2/wiki/iTunesMetadata
  '''

  FIELD_TYPES = dict(
      ContainerBoxBody.FIELD_TYPES,
      tags=TagSet,
  )

  SUBBOX_SCHEMA = {
      b'\xa9alb': ILSTTextSchema('album_title'),
      b'\xa9art': ILSTTextSchema('artist'),
      b'\xa9ART': ILSTTextSchema('album_artist'),
      b'\xa9cmt': ILSTTextSchema('comment'),
      b'\xa9cpy': ILSTTextSchema('copyright'),
      b'\xa9day': ILSTTextSchema('year'),
      b'\xa9gen': ILSTTextSchema('custom_genre'),
      b'\xa9grp': ILSTTextSchema('grouping'),
      b'\xa9lyr': ILSTTextSchema('lyrics'),
      b'\xa9nam': ILSTTextSchema('episode_title'),
      b'\xa9too': ILSTTextSchema('encoder'),
      b'\xa9wrt': ILSTTextSchema('composer'),
      b'aART': ILSTTextSchema('album_artist'),
      b'catg': ILSTTextSchema('category'),
      b'cnID': ILSTUInt32BESchema('itunes_catalogue_id'),
      b'covr': ILSTRawSchema('cover'),
      b'cpil': ILSTUInt8Schema('compilation'),
      b'cprt': ILSTTextSchema('copyright'),
      b'desc': ILSTTextSchema('description'),
      b'disk': ILSTUInt8Schema('disk_number'),
      b'egid': ILSTRawSchema('episode_guid'),
      b'genr': ILSTTextSchema('genre'),
      b'hdvd': ILSTUInt8Schema('is_high_definition'),
      b'keyw': ILSTTextSchema('keyword'),
      b'ldes': ILSTTextSchema('long_description'),
      b'pcst': ILSTUInt8Schema('podcast'),
      b'pgap': ILSTUInt8Schema('gapless_playback'),
      b'purd': ILSTISOFormatSchema('purchase_date'),
      b'purl': ILSTUInt8Schema('podcast_url'),
      b'rtng': ILSTUInt8Schema('rating'),
      b'sfID': ILSTUInt32BESchema('itunes_store_country_code'),
      b'soal': ILSTTextSchema('song_album_title'),
      b'soar': ILSTTextSchema('song_artist'),
      b'sonm': ILSTTextSchema('song_name'),
      b'stik': ILSTUInt8Schema('itunes_media_type'),
      b'tmpo': ILSTUInt8Schema('bpm'),
      b'trkn': ILSTAofBSchema('track_number'),
      b'tven': ILSTTextSchema('tv_episode_number'),
      b'tves': ILSTUInt32BESchema('tv_episode'),
      b'tvnn': ILSTTextSchema('tv_network_name'),
      b'tvsh': ILSTTextSchema('tv_show_name'),
      b'tvsn': ILSTUInt32BESchema('tv_season'),
  }

  SFID_ISO_3166_1_ALPHA_3_CODE = {
      iscc.itunes_store_code: iscc
      for iscc in (
          itunes_store_country_code('Australia', 'AUS', 143460),
          itunes_store_country_code('Austria', 'AUT', 143445),
          itunes_store_country_code('Belgium', 'BEL', 143446),
          itunes_store_country_code('Canada', 'CAN', 143455),
          itunes_store_country_code('Denmark', 'DNK', 143458),
          itunes_store_country_code('Finland', 'FIN', 143447),
          itunes_store_country_code('France', 'FRA', 143442),
          itunes_store_country_code('Germany', 'DEU', 143443),
          itunes_store_country_code('Greece', 'GRC', 143448),
          itunes_store_country_code('Ireland', 'IRL', 143449),
          itunes_store_country_code('Italy', 'ITA', 143450),
          itunes_store_country_code('Japan', 'JPN', 143462),
          itunes_store_country_code('Luxembourg', 'LUX', 143451),
          itunes_store_country_code('Netherlands', 'NLD', 143452),
          itunes_store_country_code('New Zealand', 'NZL', 143461),
          itunes_store_country_code('Norway', 'NOR', 143457),
          itunes_store_country_code('Portugal', 'PRT', 143453),
          itunes_store_country_code('Spain', 'ESP', 143454),
          itunes_store_country_code('Sweden', 'SWE', 143456),
          itunes_store_country_code('Switzerland', 'CHE', 143459),
          itunes_store_country_code('United Kingdom', 'GBR', 143444),
          itunes_store_country_code('United States', 'USA', 143441),
      )
  }

  STIK_MEDIA_TYPES = {
      imt.stik: imt
      for imt in (
          itunes_media_type('Movie', 0),
          itunes_media_type('Music', 1),
          itunes_media_type('Audiobook', 2),
          itunes_media_type('Music Video', 6),
          itunes_media_type('Movie', 9),
          itunes_media_type('TV Show', 10),
          itunes_media_type('Booklet', 11),
          itunes_media_type('Ringtone', 14),
      )
  }

  SUBSUBBOX_SCHEMA = {
      'com.apple.iTunes': {
          'Browsepath': None,
          'Date': decode_itunes_date_field,
          'HasChapters': int,
          'MaxAudioJump': float,
          'MaxSourceFps': float,
          'MaxVideoJump': float,
          'MinSourceFps': float,
          'PLVF': int,
          'Path': None,
          # this is actually XML
          'Properties': lambda s: b64decode(s).decode(),
          'ProviderName': None,
          'RecordingTimestamp': datetime.fromisoformat,
          'SourceFps': float,
          'Sourceid': None,
          'Status': int,
          'Thumbnailurl': None,
          'TotalAudioJumps': int,
          'TotalVideoJumps': int,
      }
  }

  def parse_fields(self, bfr):
    super().parse_fields(bfr)
    self.tags = TagSet()
    for subbox in self.boxes:
      subbox_type = bytes(subbox.box_type)
      with Pfx("subbox %r", subbox_type):
        with subbox.reparse_buffer() as subbfr:
          subbox.add_from_buffer(
              'boxes', subbfr, SubBoxesField, end_offset=...
          )
          inner_boxes = subbox.boxes
          if subbox_type == b'----':
            # 3 boxes: mean, name, value
            mean_box, name_box, data_box = inner_boxes
            assert mean_box.box_type == b'mean'
            assert name_box.box_type == b'name'
            with mean_box.reparse_buffer() as meanbfr:
              mean_box.add_from_buffer('n1', meanbfr, UInt32BE)
              mean_box.add_from_value(
                  'text',
                  meanbfr.take(...).decode(), str.encode
              )
            with Pfx("mean %r", mean_box.text):
              with name_box.reparse_buffer() as namebfr:
                name_box.add_from_buffer('n1', namebfr, UInt32BE)
                name_box.add_from_value(
                    'text',
                    namebfr.take(...).decode(), str.encode
                )
              with Pfx("name %r", name_box.text):
                with data_box.reparse_buffer() as databfr:
                  data_box.add_from_buffer('n1', databfr, UInt32BE)
                  data_box.add_from_buffer('n2', databfr, UInt32BE)
                  data_box.add_from_value(
                      'text',
                      databfr.take(...).decode(), str.encode
                  )
                value = data_box.text
                decoder = self.SUBSUBBOX_SCHEMA.get(mean_box.text,
                                                    {}).get(name_box.text)
                if decoder is not None:
                  value = decoder(value)
                # annotate the subbox and the ilst
                attribute_name = '.'.join((mean_box.text, name_box.text))
                setattr(subbox, attribute_name, value)
                self.tags.add(attribute_name, value)
          else:
            # single data box
            data_box, = inner_boxes
            with data_box.reparse_buffer() as databfr:
              subbox_schema = self.SUBBOX_SCHEMA.get(subbox_type)
              if subbox_schema is None:
                debug("%r: no schema", subbox_type)
              else:
                data_box.add_from_buffer('n1', databfr, UInt32BE)
                data_box.add_from_buffer('n2', databfr, UInt32BE)
                with Pfx("%s", subbox_schema.from_buffer):
                  try:
                    value = subbox_schema.from_buffer(databfr)
                  except (ValueError, TypeError) as e:
                    warning("decode fails: %s", e)
                  else:
                    data_box.add_from_value(
                        subbox_schema.attribute_name, value,
                        subbox_schema.transcribe_value
                    )
                    # annotate the subbox and the ilst
                    setattr(subbox, subbox_schema.attribute_name, value)
                    if isinstance(value, bytes):
                      self.tags.add(
                          subbox_schema.attribute_name,
                          b64encode(value).decode('ascii')
                      )
                    else:
                      self.tags.add(subbox_schema.attribute_name, value)

  def __getattr__(self, attr):
    for schema_code, schema in self.SUBBOX_SCHEMA.items():
      if schema.attribute_name == attr:
        subbox_attr = schema_code.decode('iso8859-1').upper()
        subbox = getattr(self, subbox_attr)
        return None
    return super().__getattr__(attr)

add_body_class(ILSTBoxBody)

class VMHDBoxBody(FullBoxBody):
  ''' A 'vmhd' Video Media Headerbox - section 12.1.2.
  '''

  OpColor = BinaryMultiStruct('OpColor', '>HHH', 'red green blue')

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      graphicsmode=UInt16BE,
      opcolor=OpColor,
  )

  def parse_fields(self, bfr):
    ''' Gather the `graphicsmode` and `opcolor` fields.
    '''
    self.add_from_buffer('graphicsmode', bfr, UInt16BE)
    self.add_from_buffer('opcolor', bfr, VMHDBoxBody.OpColor)
    super().parse_fields(bfr)

add_body_class(VMHDBoxBody)

class SMHDBoxBody(FullBoxBody):
  ''' A 'smhd' Sound Media Headerbox - section 12.2.2.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      balance=Int16BE,
      reserved=UInt16BE,
  )

  def parse_fields(self, bfr):
    ''' Gather the `balance` field.
    '''
    self.add_from_buffer('balance', bfr, Int16BE)
    self.add_from_buffer('reserved', bfr, UInt16BE)
    super().parse_fields(bfr)

add_body_class(SMHDBoxBody)

def parse_tags(path, tag_prefix=None, **kw):
  ''' Parse the tags from `path`.
      Yield `(box,tags)` for each subbox with tags.

      The optional `tag_prefix` parameter
      may be specified to prefix each tag name with a prefix.
      Other keyword arguments are passed to `parse()`
      (typical example: `discard_data=True`).
  '''
  over_box, = parse(path, discard_data=True)
  for top_box in over_box:
    for box, tags in top_box.gather_metadata():
      if tags:
        if tag_prefix:
          new_tags = TagSet()
          new_tags.update(
              Tag.with_prefix(tag.name, tag.value, prefix=tag_prefix)
              for tag in tags
          )
          tags = new_tags
        yield box, tags

def parse(o, **kw):
  ''' Return the OverBoxes from a source (str, int, file).

      The leading `o` parameter may be one of:
      * `str`: a filesystem file pathname
      * `int`: a OS file descriptor
      * `file`: if not `int` or `str` the presumption
        is that this is a file-like object

      Keyword arguments are as for `OverBox.from_buffer`.
  '''
  close = None
  if isinstance(o, str):
    fd = os.open(o, os.O_RDONLY)
    over_boxes = parse_fd(fd, **kw)
    close = partial(os.close, fd)
  elif isinstance(o, int):
    over_boxes = parse_fd(o, **kw)
  else:
    over_boxes = parse_file(o, **kw)
  if close:
    close()
  return over_boxes

def parse_fd(fd, discard_data=False, **kw):
  ''' Parse an ISO14496 stream from the file descriptor `fd`, yield top level Boxes.

      Parameters:
      * `fd`: a file descriptor open for read
      * `discard_data`: whether to discard unparsed data, default `False`
      * `copy_offsets`: callable to receive `BoxBody` offsets
  '''
  if not discard_data and stat.S_ISREG(os.fstat(fd).st_mode):
    return parse_buffer(
        CornuCopyBuffer.from_mmap(fd), discard_data=False, **kw
    )
  return parse_buffer(
      CornuCopyBuffer.from_fd(fd), discard_data=discard_data, **kw
  )

def parse_file(fp, **kw):
  ''' Parse an ISO14496 stream from the file `fp`, yield top level Boxes.

      Parameters:
      * `fp`: a file open for read
      * `discard_data`: whether to discard unparsed data, default `False`
      * `copy_offsets`: callable to receive `BoxBody` offsets
  '''
  return parse_buffer(CornuCopyBuffer.from_file(fp), **kw)

def parse_chunks(chunks, **kw):
  ''' Parse an ISO14496 stream from the iterator of data `chunks`,
      yield top level Boxes.

      Parameters:
      * `chunks`: an iterator yielding bytes objects
      * `discard_data`: whether to discard unparsed data, default `False`
      * `copy_offsets`: callable to receive BoxBody offsets
  '''
  return parse_buffer(CornuCopyBuffer(chunks), **kw)

def parse_buffer(bfr, copy_offsets=None, **kw):
  ''' Parse an ISO14496 stream from the CornuCopyBuffer `bfr`,
      yield top level OverBoxes.

      Parameters:
      * `bfr`: a `CornuCopyBuffer` provided the stream data,
        preferably seekable
      * `discard_data`: whether to discard unparsed data, default `False`
      * `copy_offsets`: callable to receive Box offsets
  '''
  if copy_offsets is not None:
    bfr.copy_offsets = copy_offsets
  while not bfr.at_eof():
    yield OverBox.from_buffer(bfr, **kw)

def dump_box(B, indent='', fp=None, crop_length=170, indent_incr=None):
  ''' Recursively dump a Box.
  '''
  if fp is None:
    fp = sys.stdout
  if indent_incr is None:
    indent_incr = '  '
  fp.write(indent)
  summary = str(B)
  if crop_length is not None:
    if len(summary) > crop_length - len(indent):
      summary = summary[:crop_length - len(indent) - 4] + '...)'
  fp.write(summary)
  fp.write('\n')
  boxes = getattr(B, 'boxes', None)
  body = getattr(B, 'body', None)
  if body:
    for field_name in body.field_names:
      if field_name == 'boxes':
        boxes = None
      field = body[field_name]
      if isinstance(field, SubBoxesField):
        if field_name != 'boxes':
          fp.write(indent + indent_incr)
          fp.write(field_name)
          if field.value:
            fp.write(':\n')
          else:
            fp.write(': []\n')
        for subbox in field.value:
          subbox.dump(
              indent=indent + indent_incr, fp=fp, crop_length=crop_length
          )
  if boxes:
    for subbox in boxes:
      subbox.dump(indent=indent + indent_incr, fp=fp, crop_length=crop_length)

def report(box, indent='', fp=None, indent_incr=None):
  ''' Report some human friendly information about a box.
  '''
  if fp is None:
    fp = sys.stdout
  if indent_incr is None:
    indent_incr = '  '

  def p(*a):
    a0 = a[0]
    return print(indent + a0, *a[1:], file=fp)

  def p1(*a):
    a0 = a[0]
    return print(indent + indent_incr + a0, *a[1:], file=fp)

  def subreport(box):
    return report(
        box, indent=indent + indent_incr, indent_incr=indent_incr, fp=fp
    )

  if isinstance(box, OverBox):
    ftyp = box.FTYP
    p("File type: %r, brands=%r" % (ftyp.major_brand, ftyp.brands_bs))
    for subbox in box:
      btype = subbox.box_type_s
      if btype in ('ftyp',):
        continue
      subreport(subbox)
  else:
    # normal Boxes
    btype = box.box_type_s
    if btype == 'free':
      p(geek(len(box)), "of free space")
    elif btype == 'mdat':
      p(geek(len(box.body)), "of media data")
    elif btype == 'moov':
      mvhd = box.MVHD
      p(
          "Movie:", f"timescale={mvhd.timescale}", f"duration={mvhd.duration}",
          f"next_track_id={mvhd.next_track_id}"
      )
      for moov_box in box:
        btype = moov_box.box_type_s
        if btype == 'mvhd':
          continue
        subreport(moov_box)
    elif btype == 'trak':
      trak = box
      edts = trak.EDTS0
      mdia = trak.MDIA
      mdhd = mdia.MDHD
      tkhd = trak.TKHD
      p(f"Track #{tkhd.track_id}:", f"duration={tkhd.duration}")
      if edts is None:
        p1("No EDTS.")
      else:
        p1("EDTS:", edts)
      duration_s = transcribe_time(mdhd.duration / mdhd.timescale)
      p1(f"MDIA: duration={duration_s} language={mdhd.language}")
      for tbox in trak:
        btype = tbox.box_type_s
        if btype in ('edts', 'mdia', 'tkhd'):
          continue
        subreport(tbox)
    else:
      box_s = str(box)
      if len(box_s) > 58:
        box_s = box_s[:55] + '...'
      p(box_s)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
  ##from cProfile import run
  ##run('main(sys.argv)', sort='ncalls')

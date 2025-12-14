#!/usr/bin/env python3
#
# ISO14496 parser. - Cameron Simpson <cs@cskk.id.au> 26mar2016
#

'''
Facilities for ISO14496 files - the ISO Base Media File Format,
the basis for several things including MP4 and MOV.

ISO make the standard available here:
* [available standards main page](http://standards.iso.org/ittf/PubliclyAvailableStandards/index.html)
* [zip file download](http://standards.iso.org/ittf/PubliclyAvailableStandards/c068960_ISO_IEC_14496-12_2015.zip)
'''

from base64 import b64decode
from collections import namedtuple
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from functools import cached_property
from getopt import GetoptError
import os
import sys
from typing import Iterable, List, Mapping, Tuple, Union
from uuid import UUID

from icontract import require
from typeguard import typechecked

from cs.binary import (
    AbstractBinary,
    UInt8,
    Int16BE,
    Int32BE,
    UInt16BE,
    UInt32BE,
    UInt64BE,
    BinaryBytes,
    BinaryUTF8NUL,
    BinaryUTF16NUL,
    SimpleBinary,
    BinaryStruct,
    BinaryMultiValue,
    BinarySingleValue,
    ListOfBinary,
    binclass,
    parse_offsets,
    pt_spec,
)
from cs.buffer import CornuCopyBuffer
from cs.cmdutils import BaseCommand, popopts
from cs.deco import decorator
from cs.fs import scandirpaths
from cs.fstags import FSTags, uses_fstags
from cs.lex import (
    cropped_repr,
    cutsuffix,
    get_identifier,
    get_decimal_value,
    printt,
)
from cs.logutils import warning, debug
from cs.pfx import Pfx, pfx_call, pfx_method, XP
from cs.tagset import TagSet, Tag
from cs.threads import locked_property, ThreadState
from cs.units import geek_bytes, human_time
from cs.upd import print, out  # pylint: disable=redefined-builtin

__version__ = '20241122-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Topic :: Multimedia :: Video",
    ],
    'install_requires': [
        'cs.binary',
        'cs.buffer',
        'cs.cmdutils',
        'cs.fs',
        'cs.fstags',
        'cs.imageutils',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
        'cs.tagset',
        'cs.threads',
        'cs.units',
        'cs.upd',
        'icontract',
        'typeguard',
    ],
}

PARSE_MODE = ThreadState(copy_boxes=False, discard_data=False)

def main(argv=None):
  ''' Command line mode.
  '''
  return MP4Command(argv).run()

class MP4Command(BaseCommand):

  GETOPT_SPEC = ''

  TAG_PREFIX = 'mp4'

  @uses_fstags
  def cmd_autotag(self, argv, fstags: FSTags):
    ''' Usage: {cmd} [-n] [-p prefix] [--prefix=prefix] paths...
          Tag paths based on embedded MP4 metadata.
          -n  No action.
          -p prefix, --prefix=prefix
              Set the prefix of added tags, default: 'mp4'
    '''
    options = self.options
    options.tag_prefix = self.TAG_PREFIX
    options.popopts(
        argv,
        p_=(
            'tag_prefix',
            f'prefix to the applied tags, default "{self.TAG_PREFIX}."'
        ),
        prefix_=(
            'tag_prefix',
            f'prefix to the applied tags, default "{self.TAG_PREFIX}."'
        ),
    )
    doit = options.doit
    tag_prefix = options.tag_prefix
    verbose = options.verbose or not options.doit
    if not argv:
      argv = [os.getcwd()]
    xit = 0
    with fstags:
      for top_path in argv:
        for path in scandirpaths(top_path):
          out(path)
          with Pfx(path):
            tagged_path = fstags[path]
            with PARSE_MODE(discard_data=True):
              for _, tags in parse_tags(path, tag_prefix=tag_prefix):
                for tag in tags:
                  if verbose:
                    tag_s = str(tag)
                    if len(tag_s) > 64:
                      tag_s = tag_s[:61] + '...'
                    print(path, '+', tag_s)
                  if doit:
                    tagged_path.add(tag)
    return xit

  def cmd_deref(self, argv):
    ''' Usage: {cmd} boxspec paths...
          Dereference a Box specification against ISO14496 files.
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

  @popopts(H=('skip_header', 'Skip the Box header.'))
  def cmd_extract(self, argv):
    ''' Usage: {cmd} [-H] filename boxref output
          Extract the referenced Box from the specified filename into output.
    '''
    options = self.options
    skip_header = options.skip_header
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
      warning("extra argments after boxref: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError("invalid arguments")
    top_box_type, *sub_box_types = boxref.split('.')
    B = over_box
    for box_type_s in boxref.split('.'):
      B = getattr(B, box_type_s.upper())
    bfr = CornuCopyBuffer.from_filename(filename)
    with closing(bfr):
      for topbox in Box.scan(bfr):
        if topbox.box_type_s == top_box_type:
          break
      else:
        warning("no top box of type %r found", top_box_type)
        return 1
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

  def cmd_probe(self, argv):
    ''' Usage: {cmd} [{{-|filename}}]...]
          Print informative report about each source.
    '''
    if not argv:
      argv = ['-']
    xit = 0
    first = True
    for filespec, bfr in self.pop_buffers(argv):
      if bfr is None:
        continue
      with Pfx(filespec):
        if first:
          first = False
        else:
          print()
        print(filespec)
        table = []
        with PARSE_MODE(discard_data=True):
          for box in Box.scan(bfr):
            box.report_table(table)
            for subbox, tags in box.gather_metadata():
              if not tags:
                table.append((f'  {box.box_type_s}', 'No metadata.'))
              else:
                table.append((f'  {box.box_type_s}',))
                for tag_name, tag_value in tags.items():
                  table.append((f'    {tag_name}', tag_value))
        printt(*table)
    return xit

  @popopts(
      with_data='Include the data components of boxes.',
      with_fields='Include a line for each box field.',
      with_offsets='Include Box and Box body offsets in the dump.',
  )
  def cmd_scan(self, argv):
    ''' Usage: {cmd} [--with-data] [--with-fields] [{{-|filename}} [type_paths...]]
          Parse the named files (or stdin for "-").
    '''
    options = self.options
    if not argv:
      argv = ['-']
    xit = 0
    filespec, bfr = self.pop_buffer(argv)
    if bfr is None:
      return 1
    type_paths = list(argv)
    with Pfx("%r", filespec):
      print(filespec)
      with PARSE_MODE(discard_data=not options.with_data):
        rows = []
        seen_paths = dict.fromkeys(type_paths, False)
        scan_table = []
        for topbox in Box.scan(bfr):
          if not type_paths:
            scan_table.extend(
                topbox.dump_table(
                    recurse=True,
                    dump_fields=options.with_fields,
                    dump_offsets=options.with_offsets,
                )
            )
          else:
            for type_path in type_paths:
              first_match = True
              toptype, *tail_types = type_path.split('.')
              if topbox.box_type_s == toptype:
                if not tail_types:
                  if first_match:
                    print(type_path)
                    first_match = False
                    seen_paths[type_path] = True
                  scan_table.extend(
                      topbox.dump_table(
                          recurse=True,
                          dump_fields=options.with_fields,
                          dump_offsets=options.with_offsets,
                          indent='  '
                      )
                  )
                else:
                  for subbox in topbox.descendants(tail_types):
                    if first_match:
                      print(type_path)
                      first_match = False
                      seen_paths[type_path] = True
                    scan_table.extend(
                        subbox.dump_table(
                            recurse=True,
                            dump_fields=options.with_fields,
                            dump_offsets=options.with_offsets,
                            indent='  '
                        )
                    )
    printt(*scan_table)
    if type_paths:
      for type_path in type_paths:
        if not seen_paths[type_path]:
          warning("no match for %r", type_path)
          xit = 1
    return xit

  @popopts(tag_prefix_='Specify the tag prefix, default {TAG_PREFIX!r}.')
  def cmd_tags(self, argv):
    ''' Usage: {cmd} [--tag-prefix prefix] path
          Report the tags of path based on embedded MP4 metadata.
    '''
    options = self.options
    tag_prefix = options.tag_prefix
    if tag_prefix is None:
      tag_prefix = self.TAG_PREFIX
    xit = 0
    fstags = FSTags()
    if not argv:
      raise GetoptError("missing path")
    path = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments after path: {argv!r}')
    with fstags:
      out(path)
      with Pfx(path):
        with PARSE_MODE(discard_data=True):
          for _, tags in parse_tags(path, tag_prefix=tag_prefix):
            for tag in tags:
              print(tag)
    return xit

  def cmd_test(self, argv):
    ''' Usage: {cmd} [testnames...]
          Run self tests.
    '''
    import cs.iso14496_tests
    cs.iso14496_tests.selftest([self.options.cmd] + argv)

# a convenience chunk of 256 zero bytes, mostly for use by 'free' blocks
B0_256 = bytes(256)

# an arbitrary maximum read size for fetching the data section
SIZE_16MB = 1024 * 1024 * 16

def get_deref_path(path, offset=0):
  ''' Parse a `path` string from `offset`.
      Return the path components and the offset where the parse stopped.

      Path components:
      * _identifier_: an identifier represents a `Box` field or if such a
        field is not present, a the first subbox of this type
      * `[`_index_`]`: the subbox with index _index_

      Examples:

          >>> get_deref_path('.abcd[5]')
          (['abcd', 5], 8)
  '''
  parts = []
  while offset < len(path):
    # .type
    if path.startswith('.', offset):
      name, offset2 = get_identifier(path, offset + 1)
      if name:
        parts.append(name)
        offset = offset2
        continue
    # [index]
    if path.startswith('[', offset):
      n, offset2 = get_decimal_value(path, offset + 1)
      if path.startswith(']', offset2):
        parts.append(n)
        offset = offset2 + 1
        continue
    break
  return parts, offset

def deref_box(B, path):
  ''' Dereference a path with respect to this Box.
  '''
  with Pfx("deref_path(%r)", path):
    if isinstance(path, str):
      parts, offset = get_deref_path(path)
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

Matrix9Long = BinaryStruct(
    'Matrix9Long', '>lllllllll', 'v0 v1 v2 v3 v4 v5 v6 v7 v8'
)

class UTF8or16Field(SimpleBinary):
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
  def parse(cls, bfr: CornuCopyBuffer):
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
      yield BinaryUTF16NUL.transcribe_value(
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
      dt = pfx_call(datetime.fromtimestamp, self.value, timezone.utc)
    except (OverflowError, OSError) as e:
      warning("%s.datetime: returning None", type(self).__name__, e)
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
  ''' An ISO14496 `Box` header packet.
  '''

  # speculative max size that will fit in the UInt32BE box_size
  # with room for bigger sizes in the optional UInt64BE length field
  MAX_BOX_SIZE_32 = 2**32 - 8

  @classmethod
  @parse_offsets
  def parse(cls, bfr: CornuCopyBuffer):
    ''' Decode a box header from `bfr`.
    '''
    self = cls()
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

  @cached_property
  def type_uuid(self) -> UUID:
    ''' The `UUID` for the box header type, if `self.type` is `b'uuid'`,
        made from `self.user_type`.
    '''
    if self.type != b'uuid':
      raise AttributeError(
          f'{self.__class__.__name__}.box_type_uuid: header type is not b"uuid"'
      )
    return UUID(bytes=self.user_type)

class BoxBody(SimpleBinary):
  ''' The basis for all `Box` bodies.
      This base class does not parse any of the body content.
  '''

  FIELD_TYPES = dict(offset=int, end_offset=int)
  SUBCLASSES_BY_BOXTYPE = {}

  @classmethod
  def __init_subclass__(cls, bodyclass_name=None, doc=None):
    super().__init_subclass__()
    if bodyclass_name is not None:
      cls.__name__ = bodyclass_name
    if doc is not None:
      cls.__doc__ = doc
    # apply some default docstrings to known methods
    for method_name, method_doc_str in (
        ('parse_fields', 'Gather the fields of `{cls.__name__}`.'),
        ('transcribe', 'Transcribe a `{cls.__name__}`.'),
    ):
      method = getattr(cls, method_name)
      if not (getattr(method, '__doc__', None) or '').strip():
        try:
          method.__doc__ = method_doc_str.format(cls=cls)
        except AttributeError as e:
          debug(
              "%s: cannot set %s.__doc__: %s", cls.__name__, method.__name__, e
          )
    if cls.__name__ == 'BinClass':
      # This came from the BinClass inside the @binclass decorator.
      # Because this subclasses BoxBody (because it subclasses cls, a BoxBody)
      # we get it when made, before it gets its __name__.
      # Skip the registration here.
      pass
    else:
      BoxBody._register_subclass_boxtypes(cls)

  def __init__(self, *ns_a, **ns_kw):
    super().__init__(*ns_a, _parsed_field_names=[], **ns_kw)

  @staticmethod
  def _register_subclass_boxtypes(cls, prior_cls=None):
    # update the mapping of box_type to BoxBody subclass
    try:
      # explicit list of box_type byte strings
      box_types = cls.BOX_TYPES
    except AttributeError:
      # infer the box_type from the class name's leading 4 characters
      try:
        box_type = cls.boxbody_type_from_class()
      except ValueError as e:
        debug("cannot infer box type from cls %s %r: %s", cls, cls.__name__, e)
        box_types = ()
      else:
        box_types = (box_type,)
    SUBCLASSES_BY_BOXTYPE = BoxBody.SUBCLASSES_BY_BOXTYPE
    for box_type in box_types:
      try:
        existing_box_class = SUBCLASSES_BY_BOXTYPE[box_type]
      except KeyError:
        # new box_type as expected
        SUBCLASSES_BY_BOXTYPE[box_type] = cls
      else:
        if prior_cls is not None and existing_box_class is prior_cls:
          # replace prior_cls with cls
          SUBCLASSES_BY_BOXTYPE[box_type] = cls
        else:
          raise TypeError(
              f'box_type {box_type!r} already in BoxBody.SUBCLASSES_BY_BOXTYPE as {existing_box_class.__name__}'
          )

  @staticmethod
  @require(lambda box_type: len(box_type) == 4)
  @typechecked
  def for_box_type(box_type: bytes):
    ''' Return the `BoxBody` subclass suitable for the `box_type`.
    '''
    return BoxBody.SUBCLASSES_BY_BOXTYPE.get(box_type, BoxBody)

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
    # .boxes - pretend we have an empty .boxes if missing
    if attr == 'boxes':
      return ()
    # .TYPE - the sole item in self.boxes matching b'type'
    if len(attr) == 4 and attr.isupper():
      box, = getattr(self, f'{attr}s')
      return box
    # .TYPEs - all items of self.boxes matching b'type'
    # .TYPE0 - the sole box in self.boxes or None if empty
    if len(attr) == 5 and attr.endswith(('s', '0')):
      attr4 = attr[:4]
      if attr4.isupper():
        box_type = attr4.lower().encode('ascii')
        boxes = [box for box in self.boxes if box.box_type == box_type]
        if attr.endswith('s'):
          return boxes
        if attr.endswith('0'):
          if len(boxes) == 0:
            return None
          box, = boxes
          return box
    gsa = super().__getattr__
    try:
      return gsa(attr)
    except AttributeError as e:
      raise AttributeError(f'{self.__class__.__name__}.{attr}') from e

  def __str__(self, attr_names=None):
    if attr_names is None:
      attr_names = sorted(
          attr_name for attr_name in getattr(self, '_parsed_field_names', ())
          if not attr_name.startswith('_') and attr_name != 'boxes'
      )
    return super().__str__(attr_names)

  def __len__(self):
    return self.end_offset - self.offset

  def __iter__(self):
    yield from self.boxes

  @classmethod
  @parse_offsets
  def parse(cls, bfr: CornuCopyBuffer):
    ''' Create a new instance and gather the `Box` body fields from `bfr`.

        Subclasses implement a `parse_fields` method to parse additional fields.
    '''
    self = cls()
    self.parse_fields(bfr)
    return self

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Parse additional fields.
        This base class implementation consumes nothing.
    '''

  def parse_field_value(self, field_name, bfr: CornuCopyBuffer, binary_cls):
    ''' Parse a single value binary, store the value as `field_name`,
        store the instance as the field `field_name+'__Binary'`
        for transcription.

        Note that this disassociates the plain value attribute
        from what gets transcribed.
    '''
    instance = binary_cls.parse(bfr)
    self.add_field(f'_{field_name}__Binary', instance)
    setattr(self, field_name, instance.value)

  def parse_field(self, field_name, bfr: CornuCopyBuffer, binary_cls):
    ''' Parse an instance of `binary_cls` from `bfr`
        and store it as the attribute named `field_name`.

        `binary_cls` may also be an `int`, in which case that many
        bytes are read from `bfr`.
    '''
    if binary_cls is ... or isinstance(binary_cls, int):
      # collect raw data
      value = bfr.take(binary_cls)
    else:
      value = pt_spec(binary_cls).parse(bfr)
    self.add_field(field_name, value)

  def add_field(self, field_name, value):
    ''' Add a field named `field_name` with the specified `value`
        to the box fields.
    '''
    assert field_name not in self._parsed_field_names
    setattr(self, field_name, value)
    self._parsed_field_names.append(field_name)

  def transcribe(self):
    ''' Transcribe the binary structure.

        This default implementation transcribes the fields parsed with the
        `parse_field` method in the order parsed.
    '''
    return self.transcribe_fields()

  def transcribe_fields(self):
    ''' Transcribe the fields parsed with the `parse_field` method in the
        order parsed.
    '''
    return map(
        lambda field_name: getattr(self, field_name),
        self._parsed_field_names,
    )

  def parse_boxes(
      self, bfr: CornuCopyBuffer, field_name='boxes', **box_scan_kw
  ):
    ''' Utility method to parse the remainder of the buffer as a
        sequence of `Box`es and to save them as the attribute named by `field_name`<
        default `".boxes"`.
    '''
    setattr(self, field_name, list(Box.scan(bfr, **box_scan_kw)))
    self._parsed_field_names.append(field_name)

  @classmethod
  def boxbody_type_from_class(cls):
    ''' Compute the Box's 4 byte type field from the class name.
    '''
    class_name = cls.__name__
    if ((class_prefix := cutsuffix(class_name,
                                   ('BoxBody', 'BoxBody2'))) is not class_name
        and len(class_prefix) == 4):
      if class_prefix.rstrip('_').isupper():
        return class_prefix.replace('_', ' ').lower().encode('ascii')
    raise ValueError(f'no automatic box type for class named {class_name!r}')

@decorator
def boxbodyclass(cls):
  ''' A decorator for `@binclass` style `BoxBody` subclasses
      which reregisters the new binclass in the
      `BoxBody.SUBCLASSES_BY_BOXTYPE` mapping.
  '''
  if not issubclass(cls, BoxBody):
    raise TypeError(f'@boxbodyclass: {cls=} is not a subclass of BoxBody')
  cls0 = cls
  cls = binclass(cls0)
  BoxBody._register_subclass_boxtypes(cls, cls0)
  return cls

class Box(SimpleBinary):
  ''' Base class for all boxes - ISO14496 section 4.2.

      This has the following fields:
      * `header`: a `BoxHeader`
      * `body`: a `BoxBody` instance, usually a specific subclass
      * `unparsed`: any unconsumed bytes from the `Box` are stored as here
  '''

  FIELD_TYPES = {
      'header': BoxHeader,
      'body': BoxBody,
      'unparsed': list,
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
      s = f'{type_name}:NO_BODY'
    else:
      s = f'{type_name}[{self.parse_length}]{body}'
    unparsed_bs = getattr(self, 'unparsed_bs', None)
    if unparsed_bs and unparsed_bs != b'\0':
      s += f':{unparsed_bs[:16]=}'
    return s

  __repr__ = __str__

  def __bool__(self):
    ''' A `Box` is always true, prevents implied call of `len()` for truthiness.
    '''
    return True

  def __getattr__(self, attr):
    ''' If there is no direct attribute from `SimpleBinary.__getattr__`,
        have a look in the `.header` and `.body`.
    '''
    with Pfx("%s.%s", self.__class__.__name__, attr):
      if attr not in ('header', 'body'):
        try:
          value = getattr(self.header, attr)
        except AttributeError:
          try:
            value = getattr(self.body, attr)
          except AttributeError as e:
            raise AttributeError(
                f'not present via {self.__class__.__mro__} or the .header or .body fields'
            ) from e
        return value
      raise AttributeError("not present")

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
  @parse_offsets(report=True)
  def parse(cls, bfr: CornuCopyBuffer, body_type_for=None):
    ''' Decode a `Box` from `bfr` and return it.
    '''
    if body_type_for is None:
      body_type_for = BoxBody.for_box_type
    self = cls()
    header = self.header = BoxHeader.parse(bfr)
    with Pfx("%s[%s].parse", cls.__name__, header.box_type):
      length = header.box_size
      if length is Ellipsis:
        end_offset = Ellipsis
        bfr_tail = bfr
        warning("Box.parse: Box %s has no length", header)
      else:
        end_offset = self.offset + length
        bfr_tail = bfr.bounded(end_offset)
      body_class = body_type_for(header.type)
      body_offset = bfr_tail.offset
      self.body = body_class.parse(bfr_tail)
      # attach subBoxen to self
      for subbox in self.body.boxes:
        subbox.parent = self
      self.body.parent = self
      self.body.offset = body_offset
      self.body.self_check()
      self.unparsed_offset = bfr_tail.offset
      self.unparsed = list(bfr_tail)
      if bfr_tail is not bfr:
        assert not bfr_tail.bufs, "bfr_tail.bufs=%r" % (bfr_tail.bufs,)
        bfr_tail.flush()
      self.self_check()
      copy_boxes = PARSE_MODE.copy_boxes
      if copy_boxes:
        copy_boxes(self)
      return self

  def parse_field(self, field_name, bfr: CornuCopyBuffer, binary_cls):
    ''' `parse_field` delegates to the `Box` body `parse_field`.
    '''
    return self.body.parse_field(field_name, bfr, binary_cls)

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
    return b''.join(self.unparsed)

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
    new_length = (
        header.transcribed_length() + body.transcribed_length() +
        sum(map(len, unparsed))
    )
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
    yield self.header
    yield self.body
    yield self.unparsed

  def self_check(self):
    ''' Sanity check this Box.
    '''
    super().self_check()
    # sanity check the supplied box_type
    # against the box types this class supports
    with Pfx(self):
      box_type = self.header.type
      try:
        BOX_TYPE = self.BOX_TYPE
      except AttributeError:
        try:
          BOX_TYPES = self.BOX_TYPES
        except AttributeError as e:
          if not isinstance(self, Box):
            raise RuntimeError(
                f'no BOX_TYPE or BOX_TYPES to check in class {type(self)!r}'
            ) from e
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
    ''' A context manager for continuing a `Box` parse from the `unparsed` field.

        Pops the final `unparsed` field from the `Box`,
        yields a `CornuCopyBuffer` made from it,
        then pushes the `unparsed` field again
        with the remaining contents of the buffer
        after the reparse is done.
    '''
    unparsed = self.unparsed
    self.unparsed = []
    bfr = CornuCopyBuffer(unparsed)
    try:
      yield bfr
    finally:
      self.unparsed = list(bfr)

  @property
  def box_type(self):
    ''' The `Box` header type.
    '''
    return self.header.type

  @property
  def box_type_s(self) -> str:
    ''' The `Box` header type as a string.

        If the header type is a UUID, return its `str` form.
        Otherwise, if the header type bytes decode as ASCII, return that.
        Otherwise the header bytes' repr().
    '''
    box_type_b = bytes(self.box_type)
    if box_type_b == b'uuid':
      return str(self.box_type_uuid)
    try:
      box_type_name = box_type_b.decode('ascii')
    except UnicodeDecodeError:
      box_type_name = repr(box_type_b)
    else:
      if not all(c.isprintable() for c in box_type_name):
        box_type_name = repr(box_type_b)
    return box_type_name

  @property
  def box_type_uuid(self) -> UUID:
    ''' The `Box` header type `UUID` for boxes whose `box_type` is `b'uuid'`.
    '''
    return self.header.type_uuid

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
        warning(
            "%s.box_type_path: no .box_type_s on %r: %s" %
            (type(self).__name__, box, e)
        )
        path_elem = type(box).__name__
      types.append(path_elem)
      box = box.parent
    return '.'.join(reversed(types))

  @property
  def user_type(self):
    ''' The header user_type.
    '''
    return self.header.user_type

  @property
  def BOX_TYPE(self):
    ''' The default `.BOX_TYPE` is inferred from the class name.
    '''
    try:
      return self.boxbody_type_from_class()
    except ValueError as e:
      raise AttributeError(
          f'no {self.__class__.__name__}.BOX_TYPE: {e}'
      ) from e

  def ancestor(self, box_type):
    ''' Return the closest ancestor box of type `box_type`.
        Raise `ValueError` if there is no such ancestor.
    '''
    if isinstance(box_type, str):
      box_type = box_type.encode('ascii')
    parent = self.parent
    while parent:
      if parent.box_type == box_type:
        return parent
      parent = parent.parent
    raise ValueError(f'no ancestor with {box_type=}')

  def descendants(self, sub_box_types: str | List):
    ''' A generator to scan descendants of this box for boxes
        matching `sub_box_types`.

        The `sub_box_types` may be a dot separated string or a list.
    '''
    if isinstance(sub_box_types, str):
      sub_box_types = sub_box_types.split('.')
    box_type_s, *tail_box_types = sub_box_types
    for subbox in self.boxes:
      if subbox.box_type_s == box_type_s:
        if tail_box_types:
          yield from subbox.descendants(tail_box_types)
        else:
          yield subbox

  def dump_table(
      self,
      table=None,
      indent='',
      subindent='  ',
      dump_fields=False,
      dump_offsets=False,
      recurse=False,
  ) -> List[Tuple[str, str]]:
    ''' Dump this `Box` as a table of descriptions.
        Return a list of `(title,description)` 2-tuples
        suitable for use with `cs.lex.printt()`.
    '''
    if table is None:
      table = []
    for level, box, subboxes in self.walk(limit=(None if recurse else 0)):
      row_indent = indent + subindent * level
      body = box.body
      if body.__class__ is BoxBody:
        box_desc = ''
      else:
        box_desc = body.__class__.__doc__.strip().split("\n")[0]
      if dump_fields:
        table.append(
            (
                f'{row_indent}{box.box_type_s}:{body.__class__.__name__}',
                box_desc,
            )
        )
      else:
        box_content = str(body)
        box_content__ = cutsuffix(box_content, '()')
        if box_content__ is not box_content:
          # there are fields in the brackets
          if box_desc:
            box_content = f'{box_content__}: {box_desc}'
          else:
            box_content = box_content__
        table.append((
            f'{row_indent}{box.box_type_s}',
            box_content,
        ))
      if dump_fields:
        # indent the subrows
        field_indent = row_indent + subindent
        for field_name in sorted(filter(
            lambda name: all((
                not name.startswith('_'),
                (dump_offsets or name not in ('offset', 'end_offset')),
                (not recurse or name not in ('boxes',)),
            )),
            body.__dict__.keys(),
        )):
          field = getattr(body, field_name)
          table.append((f'{field_indent}.{field_name}', cropped_repr(field)))
    return table

  def dump(self, file=None, **dump_table_kw):
    ''' Dump this `Box` to `file` (default `sys.stdout` per `cs.lex.printt`.
        Other keyword paramaters are passed to `Box.dump_table`.
    '''
    printt(*self.dump_table(**dump_table_kw), file=file)

  # pylint: disable=too-many-locals,too-many-branches
  def report_table(
      self,
      table=None,
      indent='',
      subindent='  ',
  ):
    ''' Report some human friendly information as a table.
        Return a list of `(title,description)` 2-tuples
        suitable for use with `cs.lex.printt()`.
    '''
    if table is None:
      table = []
    indent2 = indent + subindent
    box_type = self.box_type_s
    if box_type == 'ftyp':
      table.append(
          (
              f'{indent}File type',
              f'File type: {self.major_brand}, brands={self.brands_bs}'
          )
      )
    elif box_type == 'free':
      table.append((f'{indent}Free space', str(geek_bytes(len(self))[-2:])))
    elif box_type == 'mdat':
      table.append(
          (f'{indent}Media data', str(geek_bytes(len(self.body))[-2:]))
      )
    elif box_type == 'moov':
      mvhd = self.MVHD
      table.append(
          (
              f'{indent}Movie',
              ", ".join(
                  (
                      f'timescale={mvhd.timescale}',
                      f'duration={mvhd.duration}',
                      f'next_track_id={mvhd.next_track_id}',
                  )
              ),
          )
      )
      for moov_box in self:
        box_type = moov_box.box_type_s
        if box_type == 'mvhd':
          continue
        moov_box.report_table(table, indent=indent2, subindent=subindent)
    elif box_type == 'trak':
      trak = self
      edts = trak.EDTS0
      mdia = trak.MDIA
      mdhd = mdia.MDHD
      tkhd = trak.TKHD
      table.append((f'{indent}Track', f'duration={tkhd.duration}'))
      table.append((f'{indent2}EDTS', ('No EDTS' if edts is None else edts)))
      duration_s = human_time(mdhd.duration.value / mdhd.timescale.value)
      table.append(
          (
              f'{indent2}Media',
              f'duration={duration_s} language={mdhd.language}'
          )
      )
      for tbox in trak:
        tbox_type = tbox.box_type_s
        if tbox_type in ('edts', 'mdia', 'tkhd'):
          continue
        tbox.report_table(table, indent=indent2, subindent=subindent)
    else:
      box_s = str(self)
      if len(box_s) > 58:
        box_s = box_s[:55] + '...'
      table.append((f'{indent}{box_type}', box_s))
    return table

  def report(self, file=None, **report_table_kw):
    ''' Report on this `Box` to `file` (default `sys.stdout` per `cs.lex.printt`.
        Other keyword paramaters are passed to `Box.report_table`.
    '''
    printt(*self.report_table(**report_table_kw), file=file)

  def walk(self,
           *,
           level=0,
           limit=None) -> Iterable[Tuple[int, "Box", List["Box"]]]:
    ''' Walk this `Box` hierarchy.

        Yield `(level,self,subboxes)` 3-tuples starting with the top box (`self`)
        and recursing into its subboxes.

        As with `os.walk`, the returned `subboxes` list
        may be modified in place to prune or reorder the subsequent walk.
    '''
    # We don't go list(self) or [].extend(self) because both of those fire
    # the transcription of the box because of list's preallocation heuristics
    # (it measures the length of each box).
    # Instead we make a bare iterator and list() that; specific
    # incantation from Peter Otten.
    subboxes = list(iter(self.boxes))
    yield level, self, subboxes
    if limit is None or limit > 0:
      for subbox in subboxes:
        yield from subbox.walk(
            level=level + 1, limit=(None if limit is None else limit - 1)
        )

  def metatags(self) -> TagSet:
    ''' Return a `TagSet` containing direct metadata for this box.
        This default implementation returns an empty `TagSet`.
    '''
    return TagSet()

  def gather_metadata(self, prepath='') -> Iterable[Tuple[str, "Box", TagSet]]:
    ''' Walk the `Box` hierarchy looking for metadata.
        Yield `(box_path,Box,TagSet)` 3-tuples for each `Box`
        with a nonempty `.metatags`.
    '''
    path = f'{prepath}.{self.box_type_s}' if prepath else self.box_type_s
    tags = self.metatags()
    if tags:
      yield path, self, tags
    for subbox in self.boxes:
      yield from subbox.gather_metadata(path)

  def merged_metadata(self) -> TagSet:
    ''' Return a `TagSet` containing the merged metadata from this `Box` down.
    '''
    tags = TagSet()
    for path, box, tags in self.gather_metadata():
      for tag_name, tag_value in tags.items():
        merged_name = f'{path}.{tag_name}'
        assert merged_name not in tags
        tags[merged_name] = tag_value
    return tags

# patch us in
Box.FIELD_TYPES['parent'] = (False, (type(None), Box))
BoxBody.FIELD_TYPES['parent'] = Box

class ListOfBoxes(ListOfBinary, item_type=Box):
  ''' A `ListOfBinary` containing `Box`es.
  '''

  def __str__(self):
    last_box_type = None
    last_count = None
    boxgroups = []
    for box in self:
      box_type = box.box_type_s
      if last_box_type is None or last_box_type != box_type:
        if last_box_type is not None:
          boxgroups.append((last_box_type, last_count))
        last_box_type = box_type
        last_count = 1
      else:
        last_count += 1
    if last_box_type is not None:
      boxgroups.append((last_box_type, last_count))
    type_listing = ",".join(
        box_type_s if count == 1 else f'{box_type_s}[{count}]'
        for box_type_s, count in boxgroups
    )
    return f'{self.__class__.__name__}:{len(self)}:{type_listing}'

def add_body_subclass(superclass, box_type, section, desc):
  ''' Create and register a new `BoxBody` class that is simply a subclass of
      another.
      Return the new class.
  '''
  if isinstance(box_type, bytes):
    box_type = box_type.decode('ascii')
  classname = f'{box_type.upper()}BoxBody'
  box_type = box_type.encode('ascii')

  class _SubClass(
      superclass,
      bodyclass_name=classname,
      doc=f'A {box_type!r} {desc} box - ISO14496 section {section}.',
  ):

    def transcribe(self):
      ''' A stub transcribe method distinct from the parent.
      '''
      yield from super().transcribe()

  return _SubClass

class FullBoxBody(BoxBody):
  ''' A common extension of a basic `BoxBody`, with a version and flags field.
      ISO14496 section 4.2.
  '''

  FIELD_TYPES = dict(
      BoxBody.FIELD_TYPES,
      _version__Binary=UInt8,
      _flags0__Binary=UInt8,
      _flags1__Binary=UInt8,
      _flags2__Binary=UInt8,
  )

  def parse_fields(self, bfr: CornuCopyBuffer):
    super().parse_fields(bfr)
    self.parse_field_value('version', bfr, UInt8)
    self.parse_field_value('flags0', bfr, UInt8)
    self.parse_field_value('flags1', bfr, UInt8)
    self.parse_field_value('flags2', bfr, UInt8)

  @property
  def flags(self):
    ''' The flags value, computed from the 3 flag bytes.
    '''
    return (self.flags0 << 16) | (self.flags1 << 8) | self.flags2

@boxbodyclass
class FullBoxBody2(BoxBody):
  ''' A common extension of a basic `BoxBody`, with a version and flags field.
      ISO14496 section 4.2.
  '''
  version: UInt8
  flags0: UInt8
  flags1: UInt8
  flags2: UInt8

  @property
  def flags(self):
    ''' The flags value, computed from the 3 flag bytes.
    '''
    return (self.flags0 << 16) | (self.flags1 << 8) | self.flags2

class MDATBoxBody(BoxBody):
  ''' A Media Data Box - ISO14496 section 8.1.1.
  '''

  FIELD_TYPES = dict(BoxBody.FIELD_TYPES, data=(True, (type(None), list)))

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather all data to the end of the field.
    '''
    super().parse_fields(bfr)
    offset0 = bfr.offset
    if PARSE_MODE.discard_data:
      self.data = None
      bfr.skipto(bfr.end_offset)
    else:
      self.data = list(bfr)
    self.data_length = bfr.offset - offset0

  def transcribed_length(self):
    ''' Return the transcription length even if we didn't keep the data.
    '''
    return self.data_length

  def transcribe(self):
    ''' Transcribe the data.
        Raise an `AssertionError` if we skipped the data during the parse.
    '''
    if self.data is None:
      warning("self.data is None, self=%s", self)
    return self.data

class FREEBoxBody(BoxBody):
  ''' A 'free' or 'skip' box - ISO14496 section 8.1.2.
      Note the length and discard the data portion.
  '''

  FIELD_TYPES = dict(
      BoxBody.FIELD_TYPES,
      free_size=int,
  )

  BOX_TYPES = (b'free', b'skip')

  def parse_fields(self, bfr: CornuCopyBuffer, end_offset=Ellipsis, **kw):
    ''' Gather the `padding` field.
    '''
    super().parse_fields(bfr, **kw)
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

@boxbodyclass
class FTYPBoxBody(BoxBody):
  ''' An 'ftyp' File Type box - ISO14496 section 4.3.
      Decode the major_brand, minor_version and compatible_brands.
  '''
  major_brand: 4
  minor_version: UInt32BE
  brands_bs: ...

  @property
  def compatible_brands(self):
    ''' The compatible brands as a list of 4 byte bytes instances.
    '''
    return [
        self.brands_bs[offset:offset + 4]
        for offset in range(0, len(self.brands_bs), 4)
    ]

class PDINBoxBody(FullBoxBody2):
  ''' A 'pdin' Progressive Download Information box - ISO14496 section 8.1.3.
  '''

  # field names for the tuples in a PDINBoxBody
  PDInfo = BinaryStruct('PDInfo', '>LL', 'rate initial_delay')

  class PDInfoList(ListOfBinary, item_type=PDInfo):
    pass

  pdinfo: PDInfoList

@boxbodyclass
class ContainerBoxBody(BoxBody):
  ''' Common superclass of several things with `.boxes`.
  '''
  boxes: ListOfBoxes

@boxbodyclass
class MOOVBoxBody(ContainerBoxBody):
  ''' An 'moov' Movie box - ISO14496 section 8.2.1.
      Decode the contained boxes.
  '''

@boxbodyclass
class MVHDBoxBody(FullBoxBody2):
  ''' An 'mvhd' Movie Header box - ISO14496 section 8.2.2.
  '''
  creation_time: Union[TimeStamp32, TimeStamp64]
  modification_time: Union[TimeStamp32, TimeStamp64]
  timescale: UInt32BE
  duration: Union[UInt32BE, UInt64BE]
  rate_long: Int32BE
  volume_short: Int16BE
  reserved1_: 10  # 2-reserved, 2x4 reserved
  matrix: Matrix9Long
  predefined1_: 24  # 6x4 predefined
  next_track_id: UInt32BE

  @classmethod
  def parse_fields(cls, bfr: CornuCopyBuffer) -> Mapping[str, AbstractBinary]:
    # parse the fixed fields from the superclass, FullBoxBody2
    parse_fields = super().parse_fields
    superfields = super()._datafieldtypes
    field_values = parse_fields(bfr, superfields)
    ##field_values = super().parse_fields(bfr)
    version = field_values['version'].value
    # obtain box data after version and flags decode
    if version == 0:
      field_values.update(
          super().parse_fields(
              bfr,
              dict(
                  creation_time=TimeStamp32,
                  modification_time=TimeStamp32,
                  timescale=UInt32BE,
                  duration=UInt32BE,
              )
          )
      )
    elif version == 1:
      field_values.update(
          super().parse_fields(
              bfr,
              dict(
                  creation_time=TimeStamp64,
                  modification_time=TimeStamp64,
                  timescale=UInt32BE,
                  duration=UInt64BE,
              )
          )
      )
    else:
      raise ValueError(f'{cls.__name__}: unsupported {version=}')
    field_values.update(
        super().parse_fields(
            bfr,
            [
                'rate_long', 'volume_short', 'reserved1_', 'matrix',
                'predefined1_', 'next_track_id'
            ],
        )
    )
    return field_values

  @property
  def rate(self):
    ''' Rate field converted to float: 1.0 represents normal rate.
    '''
    rate_long = self.rate_long
    return (rate_long >> 16) + (rate_long & 0xffff) / 65536.0

  @property
  def volume(self):
    ''' Volume field converted to float: 1.0 represents full volume.
    '''
    volume_short = self.volume_short
    return (volume_short >> 8) + (volume_short & 0xff) / 256.0

add_body_subclass(ContainerBoxBody, 'trak', '8.3.1', 'Track')

@boxbodyclass
class TKHDBoxBody(FullBoxBody2):
  ''' A 'tkhd' Track Header box - ISO14496 section 8.2.2.
  '''

  TKHDMatrix = BinaryStruct(
      'TKHDMatrix', '>lllllllll', 'v0 v1 v2 v3 v4 v5 v6 v7 v8'
  )

  creation_time: Union[TimeStamp32, TimeStamp64]
  modification_time: Union[TimeStamp32, TimeStamp64]
  track_id: UInt32BE
  reserved1_: UInt32BE
  duration: Union[UInt32BE, UInt64BE]
  reserved2_: UInt32BE
  reserved3_: UInt32BE
  layer: Int16BE
  alternate_group: Int16BE
  volume: Int16BE
  reserved4_: UInt16BE
  matrix: TKHDMatrix
  width: UInt32BE
  height: UInt32BE

  @classmethod
  def parse_fields(cls, bfr: CornuCopyBuffer) -> Mapping[str, AbstractBinary]:
    # parse the fixed fields from the superclass, FullBoxBody2
    parse_fields = super().parse_fields
    superfields = super()._datafieldtypes
    field_values = parse_fields(bfr, superfields)
    ##field_values = super().parse_fields(bfr)
    version = field_values['version'].value
    # obtain box data after version and flags decode
    if version == 0:
      field_values.update(
          super().parse_fields(
              bfr,
              dict(
                  creation_time=TimeStamp32,
                  modification_time=TimeStamp32,
                  track_id=UInt32BE,
                  reserved1_=UInt32BE,
                  duration=UInt32BE,
              )
          )
      )
    elif version == 1:
      field_values.update(
          super().parse_fields(
              bfr,
              dict(
                  creation_time=TimeStamp64,
                  modification_time=TimeStamp64,
                  track_id=UInt32BE,
                  reserved1_=UInt32BE,
                  duration=UInt64BE,
              )
          )
      )
    else:
      raise ValueError(f'{cls.__name__}: unsupported {version=}')
    field_values.update(
        super().parse_fields(
            bfr,
            [
                'reserved2_', 'reserved3_', 'layer', 'alternate_group',
                'volume', 'reserved4_', 'matrix', 'width', 'height'
            ],
        )
    )
    return field_values

  @property
  def track_enabled(self):
    ''' Test flags bit 0, 0x1, track_enabled.
    '''
    return (self.flags & 0x1) != 0

  @property
  def track_in_movie(self):
    ''' Test flags bit 1, 0x2, track_in_movie.
    '''
    return (self.flags & 0x2) != 0

  @property
  def track_in_preview(self):
    ''' Test flags bit 2, 0x4, track_in_preview.
    '''
    return (self.flags & 0x4) != 0

  @property
  def track_size_is_aspect_ratio(self):
    ''' Test flags bit 3, 0x8, track_size_is_aspect_ratio.
    '''
    return (self.flags & 0x8) != 0

  @property
  def timescale(self):
    ''' The `timescale` comes from the movie header box (8.3.2.3).
    '''
    return self.ancestor('mvhd').timescale

##add_body_subclass(ContainerBoxBody, 'tref', '8.3.3', 'track Reference')

class TREFBoxBody(ContainerBoxBody):
  ''' Track Reference BoxBody, container for trackReferenceTypeBoxes - ISO14496 section 8.3.3.
  '''

class TrackReferenceTypeBoxBody(BoxBody):
  ''' A TrackReferenceTypeBoxBody contains references to other tracks - ISO14496 section 8.3.3.2.
  '''
  FIELD_TYPES = dict(BoxBody.FIELD_TYPES, track_ids=list)

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

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather the `track_ids` field.
    '''
    super().parse_fields(bfr)
    self.add_field('track_ids', list(UInt32BE.scan(bfr)))

add_body_subclass(ContainerBoxBody, 'trgr', '8.3.4', 'Track Group')

class TrackGroupTypeBoxBody(FullBoxBody):
  ''' A TrackGroupTypeBoxBody contains a track group id - ISO14496 section 8.3.3.2.
  '''

  def __init__(self, box_type, box_data):
    FullBoxBody.__init__(self, box_type, box_data)

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather the `track_group_id` field.
    '''
    super().parse_fields(bfr)
    self.parse_field('track_group_id', bfr, UInt32BE)

add_body_subclass(
    TrackGroupTypeBoxBody, 'msrc', '8.3.4.3',
    'Multi-source presentation Track Group'
)
add_body_subclass(ContainerBoxBody, 'mdia', '8.4.1', 'Media')

# TODO: as for MVHD
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
      pre_defined_=UInt16BE,
  )

  def parse_fields(self, bfr: CornuCopyBuffer):
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
      raise NotImplementedError(f'unsupported {self.version=}')
    self.parse_field('language_short', bfr, UInt16BE)
    self.parse_field('pre_defined_', bfr, UInt16BE)

  def transcribe(self):
    yield super().transcribe()
    yield self.creation_time
    yield self.modification_time
    yield self.timescale
    yield self.duration
    yield self.language_short
    yield self.pre_defined_

  @property
  def language(self):
    ''' The ISO 639‐2/T language code as decoded from the packed form.
    '''
    language_short = self.language_short.value
    return bytes(
        [
            x + 0x60 for x in (
                (language_short >> 10) & 0x1f, (language_short >> 5) & 0x1f,
                language_short & 0x1f
            )
        ]
    ).decode('ascii')

@boxbodyclass
class HDLRBoxBody(FullBoxBody2):
  ''' A HDLRBoxBody is a Handler Reference box - ISO14496 section 8.4.3.
  '''
  pre_defined_: UInt32BE
  handler_type_long: UInt32BE
  reserved1_: UInt32BE
  reserved2_: UInt32BE
  reserved3_: UInt32BE
  name: BinaryUTF8NUL

  @property
  def handler_type(self):
    ''' The handler_type as an ASCII string, its usual form.
    '''
    return bytes(self._data.handler_type_long).decode('ascii')

add_body_subclass(ContainerBoxBody, b'minf', '8.4.4', 'Media Information')
add_body_subclass(FullBoxBody, 'nmhd', '8.4.5.2', 'Null Media Header')

@boxbodyclass
class ELNGBoxBody(FullBoxBody2):
  ''' A `ELNGBoxBody` is a Extended Language Tag box - ISO14496 section 8.4.6.
  '''
  # extended language based on RFC4646
  extended_language: BinaryUTF8NUL

class EntryCountListOfBoxes(FullBoxBody2):
  ''' An intermediate `FullBoxBody` subclass which contains more boxes
      whose number if specified with a leading `entry_count`
      whose defaut type is `UInt32BE`.

      This is a common superclass of `_SampleTableContainerBoxBody` and 
  '''
  boxes: ListOfBoxes

  ENTRY_COUNT_TYPE = None

  def __init_subclass__(cls, count_type=UInt32BE, **ickw):
    super().__init_subclass__(**ickw)
    cls.ENTRY_COUNT_TYPE = count_type

  def __iter__(self):
    return iter(self.boxes)

  @property
  def entry_count(self):
    ''' The `entry_count` is the number of `Box`es.
    '''
    return len(self.boxes)

  @classmethod
  def parse_fields(cls, bfr: CornuCopyBuffer):
    ''' Gather the `entry_count` and `boxes`.
    '''
    # parse the fixed fields from the superclass, FullBoxBody2
    parse_fields = super().parse_fields
    superfields = super()._datafieldtypes
    field_values = parse_fields(bfr, superfields)
    entry_count = cls.ENTRY_COUNT_TYPE.parse_value(bfr)
    field_values.update(boxes=ListOfBoxes.parse(bfr, count=entry_count))
    self = cls(**field_values)
    return self

  def transcribe(self):
    yield super().transcribe()
    yield self.ENTRY_COUNT_TYPE(self.entry_count)
    yield self.boxes

class _SampleTableContainerBoxBody(EntryCountListOfBoxes):
  pass

@boxbodyclass
class _SampleEntry(BoxBody):
  ''' Superclass of Sample Entry boxes.
  '''
  reserved_: 6
  data_reference_index: UInt16BE

@boxbodyclass
class BTRTBoxBody(BoxBody):
  ''' BitRateBoxBody - section 8.5.2.2.
  '''
  bufferSizeDB: UInt32BE
  maxBitrate: UInt32BE
  avgBitRate: UInt32BE

@boxbodyclass
class STDPBoxBody(FullBoxBody2):
  ''' A `STDPBoxBody` is a DegradationPriorityBox - ISO14496 section 8.5.3.2.
  '''

  class PriorityList(ListOfBinary, item_type=UInt16BE):
    pass

  priority: PriorityList

@boxbodyclass
class STSDBoxBody(BoxBody):
  ''' A `STSDBoxBody` is a SampleDescriptionBoxBody - ISO14496 section 8.5.2.2.
  '''
  reserved_: 6  # 6 8-bit integers
  data_reference_index: UInt16BE

add_body_subclass(ContainerBoxBody, b'stbl', '8.5.1', 'Sample Table')

TTSB_Sample = namedtuple('TTSB_Sample', 'count delta')

# pylint: disable=too-many-arguments
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
  sample_type_v0 = BinaryStruct(
      sample_class_name + 'V0', struct_format_v0, sample_fields
  )
  sample_type_v1 = BinaryStruct(
      sample_class_name + 'V1', struct_format_v1, sample_fields
  )

  class _SpecificSampleBoxBody(
      FullBoxBody,
      bodyclass_name=class_name,
      doc=f'`Box` type {box_type!r} {desc} box - ISO14496 section {section}.',
  ):

    FIELD_TYPES = dict(
        FullBoxBody.FIELD_TYPES,
        entry_count=(False, UInt32BE),
        has_inferred_entry_count=bool,
        sample_type=(True, type),
        samples_count=int,
        samples_bs=bytes,
    )

    def parse_fields(self, bfr: CornuCopyBuffer):
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
        end_offset = bfr.end_offset
        entry_count = (end_offset - bfr.offset) // sample_size
      else:
        entry_count = UInt32BE.parse_value(bfr)
      # gather the sample data but do not bother to parse it yet
      # because that can be very expensive
      self.samples_count = entry_count
      self.samples_bs = bfr.take(entry_count * sample_size)

    def transcribe(self):
      ''' Transcribe the regular fields
          then transcribe the source data of the samples.
      '''
      yield super().transcribe()
      if not self.has_inferred_entry_count:
        yield UInt32BE.transcribe_value(self.samples_count)
      try:
        samples = self._samples
      except AttributeError:
        yield self.samples_bs
      else:
        yield from map(self.sample_type.transcribe_value, samples)

    @locked_property
    @pfx_method
    def samples(self, bfr: CornuCopyBuffer):
      ''' The `sample_data` decoded.
      '''
      bfr = CornuCopyBuffer.from_bytes(self.sample_bs)
      sample_type = self.sample_type
      decoded = []
      for _ in range(self.samples_count):
        decoded.append(sample_type.parse_value(bfr))
      assert bfr.at_eof()
      return decoded

  # we define these here because the names collide with the closure
  _SpecificSampleBoxBody.struct_format_v0 = struct_format_v0
  _SpecificSampleBoxBody.sample_type_v0 = sample_type_v0
  _SpecificSampleBoxBody.struct_format_v1 = struct_format_v1
  _SpecificSampleBoxBody.sample_type_v1 = sample_type_v1
  return _SpecificSampleBoxBody

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
  CSLGParamsLong = BinaryStruct('CSLGParamsLong', '>lllll', CSLG_PARAM_NAMES)
  CSLGParamsQuad = BinaryStruct('CSLGParamsLong', '>qqqqq', CSLG_PARAM_NAMES)

  def parse_fields(self, bfr: CornuCopyBuffer):
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
    self.parse_field('params', bfr, param_type)

  def __getattr__(self, attr):
    ''' Present the `params` attributes at the top level.
    '''
    try:
      return getattr(self.params, attr)
    except AttributeError:
      return super().__getattr__(attr)

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

add_body_subclass(ContainerBoxBody, b'edts', '8.6.5.1', 'Edit')

class ELSTBoxBody(FullBoxBody):
  ''' An 'elst' Edit List FullBoxBody - section 8.6.6.
  '''

  V0EditEntry = BinaryStruct(
      'ELSTBoxBody_V0EditEntry', '>Llhh',
      'segment_duration media_time media_rate_integer media_rate_fraction'
  )
  V1EditEntry = BinaryStruct(
      'ELSTBoxBody_V1EditEntry', '>Qqhh',
      'segment_duration media_time media_rate_integer media_rate_fraction'
  )

  @property
  def entry_class(self):
    ''' The class representing each entry.
    '''
    return self.V1EditEntry if self.version == 1 else self.V0EditEntry

  @property
  def entry_count(self):
    ''' The number of entries.
    '''
    return len(self.entries)

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Parse the fields of an `ELSTBoxBody`.
    '''
    super().parse_fields(bfr)
    assert self.version in (0, 1)
    entry_count = UInt32BE.parse_value(bfr)
    self.entries = list(self.entry_class.scan(bfr, count=entry_count))

  def transcribe(self):
    ''' Transcribe an `ELSTBoxBody`.
    '''
    yield super().transcribe()
    yield self.entry_count
    yield map(self.entry_class.transcribe, self.entries)

add_body_subclass(ContainerBoxBody, b'dinf', '8.7.1', 'Data Information')

class URL_BoxBody(FullBoxBody):
  ''' An 'url ' Data Entry URL BoxBody - section 8.7.2.1.
  '''

  FIELD_TYPES = dict(FullBoxBody.FIELD_TYPES, location=BinaryUTF8NUL)

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather the `location` field.
    '''
    super().parse_fields(bfr)
    self.parse_field('location', bfr, BinaryUTF8NUL)

class URN_BoxBody(FullBoxBody):
  ''' An 'urn ' Data Entry URL BoxBody - section 8.7.2.1.
  '''

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather the `name` and `location` fields.
    '''
    super().parse_fields(bfr)
    self.parse_field('name', bfr, BinaryUTF8NUL)
    self.parse_field('location', bfr, BinaryUTF8NUL)

  def transcribe(self):
    yield super().transcribe()
    yield self.name
    yield self.location

class STSZBoxBody(FullBoxBody):
  ''' A 'stsz' Sample Size box - section 8.7.3.2.
  '''

  FIELD_TYPES = dict(
      FullBoxBody.FIELD_TYPES,
      sample_size=UInt32BE,
      sample_count=UInt32BE,
      entry_sizes_bs=(False, (bytes,)),
  )

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather the `sample_size`, `sample_count`, and `entry_sizes` fields.
    '''
    super().parse_fields(bfr)
    self.parse_field('sample_size', bfr, UInt32BE)
    sample_size = self.sample_size.value
    self.parse_field('sample_count', bfr, UInt32BE)
    sample_count = self.sample_count.value
    if sample_size == 0:
      # a zero sample size means that each sample's individual size
      # is specified in `entry_sizes`
      self.entry_sizes_bs = bfr.take(sample_count * UInt32BE.length)

  def transcribe(self):
    ''' Transcribe the `STSZBoxBody`.
    '''
    yield super().transcribe()
    yield self.sample_size
    yield self.sample_count
    if self.sample_size == 0:
      try:
        entry_sizes = self._entry_sizes
      except AttributeError:
        yield self.entry_sizes_bs
      else:
        yield from map(UInt32BE.transcribe_value, entry_sizes)

  @locked_property
  @pfx_method
  def entry_sizes(self):
    ''' Parse the `UInt32BE` entry sizes from stashed buffer
        into a list of `int`s.
    '''
    XP("parse %d bytes from .entry_sizes_bs", len(self.entry_sizes_bs))
    bfr = CornuCopyBuffer.from_bytes(self.entry_sizes_bs)
    entry_sizes = []
    for _ in range(self.sample_count.value):
      entry_sizes.append(UInt32BE.parse_value(bfr))
    return entry_sizes

class STZ2BoxBody(FullBoxBody):
  ''' A 'stz2' Compact Sample Size box - section 8.7.3.3.
  '''

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather the `field_size`, `sample_count` and `entry_sizes` fields.
    '''
    super().parse_fields(bfr)
    self.reserved = bfr.take(3)
    self.field_size = UInt8.parse_value(bfr)
    self.sample_count = UInt32BE.parse_value(bfr)
    # TODO: defer the parse of entry_sizes
    if self.field_size == 4:
      # nybbles packed into bytes
      entry_sizes = []
      for i in range(self.sample_count):
        if i % 2 == 0:
          bs = bfr.take(1)
          entry_sizes.append(bs[0] >> 4)
        else:
          entry_sizes.append(bs[0] & 0x0f)
    elif self.field_size == 8:
      # unsigned byte values - store directly!
      entry_sizes = bfr.take(self.sample_count)
    elif self.field_size == 16:
      entry_sizes = list(UInt16BE.scan_values(bfr, count=self.sample_count))
    else:
      warning(
          "unhandled field_size=%d, not parsing entry_sizes", self.field_size
      )
      entry_sizes = None
    self.entry_sizes = entry_sizes

  def transcribe(self):
    ''' Transcribe the STZ2BoxBody.
    '''
    yield super().transcribe()
    yield self.reserved
    field_size = self.field_size
    yield bytes([field_size])
    yield UInt32BE.transcribe_value(self.sample_count)
    entry_sizes = self.entry_sizes
    if entry_sizes:
      if field_size == 4:
        b = None
        bs = []
        for i, n in entry_sizes:
          assert 0 < n < 16
          if i % 2 == 0:
            b = n << 4
          else:
            b |= n
            bs.append(b)
        if entry_sizes % 2 != 0:
          bs.append(b)
        yield bytes(bs)
      elif field_size == 8:
        assert isinstance(entry_sizes, bytes)
        yield entry_sizes
      elif field_size == 16:
        yield map(UInt16BE.transcribe_value, entry_sizes)
      else:
        raise ValueError(
            "unhandled field_size=%d, not transcribing entry_sizes"
        )

class STSCBoxBody(FullBoxBody):
  ''' 'stsc' (Sample Table box - section 8.7.4.1.
  '''

  FIELD_TYPES = dict(
      FullBoxBody.FIELD_TYPES,
      entry_count=int,
      entries_bs=bytes,
  )

  STSCEntry = BinaryStruct(
      'STSCEntry', '>LLL',
      'first_chunk samples_per_chunk sample_description_index'
  )

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather the `entry_count` and `entries` fields.
    '''
    super().parse_fields(bfr)
    self.entry_count = UInt32BE.parse_value(bfr)
    self.entries_bs = bfr.take(self.entry_count * STSCBoxBody.STSCEntry.length)

  def transcribe(self):
    yield super().transcribe()
    yield UInt32BE.transcribe_value(self.entry_count)
    try:
      entries = self._entries
    except AttributeError:
      yield self.entries_bs
    else:
      yield from map(STSCBoxBody.STSCEntry.transcribe_value, entries)

  @locked_property
  @pfx_method
  def entries(self, bfr: CornuCopyBuffer):
    ''' A list of `int`s parsed from the `STSCEntry` list.
    '''
    bfr = CornuCopyBuffer.from_bytes(self.entries_bs)
    entries = []
    for _ in range(self.entry_count):
      entries.append(STSCBoxBody.STSCEntry.parse_value(bfr))
    return entries

class STCOBoxBody(FullBoxBody):
  ''' A 'stco' Chunk Offset box - section 8.7.5.
  '''

  FIELD_TYPES = dict(
      FullBoxBody.FIELD_TYPES,
      entry_count=int,
      chunk_offsets_bs=bytes,
      ##chunk_offsets=ListField,
  )

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather the `entry_count` and `chunk_offsets` fields.
    '''
    super().parse_fields(bfr)
    self.entry_count = UInt32BE.parse_value(bfr)
    self.chunk_offsets_bs = bfr.take(self.entry_count * UInt32BE.length)

  def transcribe(self):
    yield super().transcribe()
    yield UInt32BE.transcribe_value(self.entry_count)
    try:
      chunk_offsets = self._chunk_offsets
    except AttributeError:
      yield self.chunk_offsets_bs
    else:
      yield from map(UInt32BE.transcribe_value, chunk_offsets)

  @locked_property
  @pfx_method
  def chunk_offsets(self, bfr: CornuCopyBuffer):
    ''' Parse the `UInt32BE` chunk offsets from stashed buffer.
    '''
    XP("decode .chunk_offsets_bs")
    bfr = CornuCopyBuffer.from_bytes(self.chunk_offsets_bs)
    chunk_offsets = []
    for _ in range(self.entry_count):
      chunk_offsets.append(UInt32BE.parse_value(bfr))
    return chunk_offsets

class CO64BoxBody(FullBoxBody):
  ''' A 'c064' Chunk Offset box - section 8.7.5.
  '''

  FIELD_TYPES = dict(
      FullBoxBody.FIELD_TYPES,
      entry_count=int,
      chunk_offsets_bs=bytes,
  )

  def parse_fields(self, bfr: CornuCopyBuffer):
    ''' Gather the `entry_count` and `chunk_offsets` fields.
    '''
    super().parse_fields(bfr)
    self.entry_count = UInt32BE.parse_value(bfr)
    self.chunk_offsets_bs = bfr.take(self.entry_count * UInt64BE.length)

  def transcribe(self):
    ''' Transcribe a `CO64BoxBody`.
    '''
    yield super().transcribe()
    yield UInt32BE.transcribe_value(self.entry_count)
    try:
      chunk_offsets = self._chunk_offsets
    except AttributeError:
      yield self.chunk_offsets_bs
    else:
      yield from map(UInt64BE.transcribe_value, chunk_offsets)

  ##@deferred_field
  ##def chunk_offsets(self,bfr:CornuCopyBuffer):
  ##  ''' Computed on demand list of chunk offsets.
  ##  '''
  ##  offsets = []
  ##  for _ in range(self.entry_count):
  ##    offsets.append(UInt64BE.from_buffer(bfr))
  ##  return offsets

@boxbodyclass
class DREFBoxBody(EntryCountListOfBoxes):
  ''' A 'dref' Data Reference box containing Data Entry boxes - section 8.7.2.1.
  '''

add_body_subclass(ContainerBoxBody, b'udta', '8.10.1', 'User Data')

class CPRTBoxBody(FullBoxBody2):
  ''' A 'cprt' Copyright box - section 8.10.2.
  '''
  language_packed: UInt16BE
  notice: UTF8or16Field

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
        (ord(ch1) - 0x60) & 0x1f, ((ord(ch2) - 0x60) & 0x1f) << 5,
        ((ord(ch3) - 0x60) & 0x1f) << 10
    )
    self.language_packed = packed

@boxbodyclass
class METABoxBody(FullBoxBody2):
  ''' A 'meta' Meta BoxBody - section 8.11.1.
  '''
  theHandler: Box
  boxes: ListOfBoxes

  def __iter__(self):
    return iter(self.boxes)

  @pfx_method
  def __getattr__(self, attr):
    ''' Attributes not found on `self` in the normal way
        are also looked up on the `.ILST` subbox
        if there is one.
    '''
    try:
      # direct attribute access
      return super().__getattr__(attr)
    except AttributeError:
      # otherwise dereference through the .ilst subbox if present
      ilst = super().__getattr__('ILST0')
      if ilst is not None:
        value = getattr(ilst, attr, None)
        if value is not None:
          return value
    raise AttributeError(f'{self.__class__.__name__}.{attr}')

class _attr_schema(namedtuple('_attr_schema', 'attribute_name schema_class')):
  ''' A `(attribute_name,schema_class)` 2-tuple
      associating a long `attribute_name` with an `AbstractBinary`.
  '''

  def __repr__(self):
    return f'schema({self.attribute_name}={self.schema_class.__name__})'

class _ILSTRawSchema(BinaryBytes):
  ''' All the bytes in an ILST.
  '''

_ILSTRawSchema.__name__ = 'ILSTRawSchema'

def ILSTRawSchema(attribute_name):
  ''' Attribute name and type for ILST raw schema.
  '''
  return _attr_schema(attribute_name, _ILSTRawSchema)

# class to decode bytes as UTF-8
class _ILSTTextSchema(
    pt_spec(
        (
            lambda bfr: bfr.take(...).decode('utf-8'),
            lambda txt: txt.encode('utf-8'),
        ),
        name='ILSTTextSchema',
        value_type=str,
    ),
    value_type=str,
):
  pass

def ILSTTextSchema(attribute_name):
  ''' Attribute name and type for ILST text schema.
  '''
  return _attr_schema(attribute_name, _ILSTTextSchema)

def ILSTUInt32BESchema(attribute_name):
  ''' Attribute name and type for ILST `UInt32BE` schema.
  '''
  return _attr_schema(attribute_name, UInt32BE)

def ILSTUInt8Schema(attribute_name):
  ''' Attribute name and type for ILST `UInt8BE` schema.
  '''
  return _attr_schema(attribute_name, UInt8)

# class to decode n/total as a pair of UInt32BE values
@binclass
class ILSTAofB:
  n: UInt32BE
  total: UInt32BE

  def __str__(self):
    total_s = "..." if self.total == 0 else str(self.total)
    return f'{self.n}/{total_s}'

  def __repr__(self):
    return f'{self.__class__.__name__}:{self}'

def ILSTAofBSchema(attribute_name):
  ''' Attribute name and type for ILST "A of B" schema.
  '''
  return _attr_schema(attribute_name, ILSTAofB)

# class to decode bytes as UTF-8 of ISO8601 datetime string
_ILSTISOFormatSchema = pt_spec(
    (
        lambda bfr: datetime.fromisoformat(bfr.take(...).decode('utf-8')),
        lambda dt: dt.isoformat(sep=' ', timespec='seconds').encode('utf-8'),
    ),
    name='ILSTISOFormatSchema',
    value_type=datetime,
    ##as_str=lambda self: self.value.isoformat(sep='T', timespec='seconds'),
    ##as_repr=lambda self: self.value.isoformat(sep='T', timespec='seconds'),
)

def ILSTISOFormatSchema(attribute_name):
  ''' Attribute name and type for ILST ISO format schema.
  '''
  return _attr_schema(attribute_name, _ILSTISOFormatSchema)

itunes_media_type = namedtuple('itunes_media_type', 'type stik')

def decode_itunes_date_field(data) -> datetime:
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

class _ILSTUTF8Text(BinarySingleValue, value_type=str):
  ''' A full-buffer piece of UTF-8 encoded text.
  '''

  @staticmethod
  def parse_value(bfr):
    ''' Read the entire buffer and decode it as UTF-8.
    '''
    bs = bfr.take(...)
    try:
      s = bs.decode('utf-8', errors='strict')
    except UnicodeDecodeError as e:
      warning(
          "_ILSTUTF8Text.parse_value(%r): %s, retrying with errors=replace",
          bs, e
      )
      s = bs.decode('utf-8', errors='replace')
    return s

  @staticmethod
  def transcribe_value(value):
    ''' Transcribe `value` in UTF-8.
    '''
    return value.encode('utf-8')

@boxbodyclass
class ILSTBoxBody(ContainerBoxBody):
  ''' Apple iTunes Information List, container for iTunes metadata fields.

      The basis of the format knowledge here comes from AtomicParsley's
      documentation here:

          http://atomicparsley.sourceforge.net/mpeg-4files.html

      and additional information from:

          https://github.com/sergiomb2/libmp4v2/wiki/iTunesMetadata
  '''

  # the schema names are available as attributes
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
      b'geID': ILSTUInt32BESchema('geID_unknown_geo_id_maybe'),
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

  # make a mapping of schema long attribute names to (schema_code,schema)
  SUBBOX_SCHEMA_BY_LONG_ATTRIBUTE = {
      schema.attribute_name: (schema_code, schema)
      for schema_code, schema in reversed(SUBBOX_SCHEMA.items())
  }

  @classmethod
  def parse_fields(cls, bfr: CornuCopyBuffer):
    ''' An ILST body is a list of `Box`es containering "data" `Box`es.
        The ILST member `Box`es' types are not related to the ISO14496 types.
        The meaning of the "data" subboxes depends on the ILST box type field.

        Therefore we always scan the subboxes as plain `BoxBody` boxes,
        then parse their meaning once loaded.
    '''
    # scan in the member Boxes, ignoring their box type fields
    subboxes = list(Box.scan(bfr, body_type_for=lambda _: BoxBody))
    # process each member by looking up its `box_type` in SUBSUBBOX_SCHEMA
    for subbox in subboxes:
      subbox_type = bytes(subbox.box_type)
      with Pfx("subbox %r", subbox_type):
        # first, parse the contained "data" subboxes
        with subbox.reparse_buffer() as subbfr:
          data_boxes = list(Box.scan(subbfr, body_type_for=lambda _: BoxBody))
        if subbox_type == b'----':
          # 3 boxes: mean, name, value
          #
          # The mean Box.
          mean_box = data_boxes.pop(0)
          assert mean_box.box_type == b'mean'
          subbox.add_field('mean', mean_box)
          with mean_box.reparse_buffer() as meanbfr:
            mean_box.parse_field('_n1', meanbfr, UInt32BE)
            mean_box.parse_field('text', meanbfr, _ILSTUTF8Text)
          mean_value = mean_box.text.value
          subsubbox_schema = cls.SUBSUBBOX_SCHEMA.get(mean_value, {})
          #
          # The name Box.
          name_box = data_boxes.pop(0)
          assert name_box.box_type == b'name'
          subbox.add_field('name', name_box)
          with name_box.reparse_buffer() as namebfr:
            name_box.parse_field('_n1', namebfr, UInt32BE)
            name_box.parse_field('text', namebfr, _ILSTUTF8Text)
          name_value = name_box.text.value
          #
          # The data Box.
          data_box = data_boxes.pop(0)
          assert data_box.box_type == b'data'
          subbox.add_field('data', data_box)
          with data_box.reparse_buffer() as databfr:
            data_box.parse_field('_n1', databfr, UInt32BE)
            data_box.parse_field('_n2', databfr, UInt32BE)
            data_box.parse_field('text', databfr, _ILSTUTF8Text)
          # decode the data value
          data_value = data_box.text.value
          decoder = subsubbox_schema.get(name_value)
          if decoder is not None:
            data_value = pfx_call(decoder, data_value)
          # annotate the subbox and the ilst
          attribute_name = f'{mean_box.text}.{name_box.text}'
          setattr(subbox, 'attribute_name', attribute_name)
          setattr(subbox, attribute_name, data_value)
        else:
          # Other boxes have a single data subbox.
          for i, data_box in enumerate(data_boxes):
            if data_box.box_type != b'data':
              warning(
                  "data_boxes[%d].box_type is not b'data': got %r", i,
                  data_box.box_type
              )
              value = value.value
              decoder = subsubbox_schema.get(name_box.text.value)
              if decoder is not None:
                value = decoder(value)
              # annotate the subbox and the ilst
              attribute_name = f'{mean_box.text}.{name_box.text}'
              setattr(subbox, attribute_name, value)
              tags.add(attribute_name, value)
        ### single data box
        ##elif not inner_boxes:
        ##  warning("no inner boxes, expected 1 data box")
        ##else:
        ##  data_box, = inner_boxes
        ##  with data_box.reparse_buffer() as databfr:
        ##    data_box.parse_field('_n1', databfr, UInt32BE)
        ##    data_box.parse_field('_n2', databfr, UInt32BE)
        ##    subbox_schema = cls.SUBBOX_SCHEMA.get(subbox_type)
        ##    if subbox_schema is None:
        ##      # no specific schema, just stash the bytes
        ##      bs = databfr.take(...)
        ##      warning("no schema, stashing bytes %s", cropped_repr(bs))
        ##      subbox.add_field(f'subbox__{subbox_type.decode("ascii")}', bs)
        ##    else:
        ##      attribute_name, binary_cls = subbox_schema
        ##      with Pfx("%s:%s", attribute_name, binary_cls):
        ##        try:
        ##          subbox.parse_field(attribute_name, databfr, binary_cls)
        ##        except (ValueError, TypeError) as e:
        ##          warning("decode fails: %s", e)
        ##        else:
        ##          data_attr = getattr(subbox, attribute_name)
        ##          tag_value = data_attr.value if is_single_value(
        ##              data_attr
        ##          ) else data_attr
        ##        if isinstance(tag_value, bytes):
        ##          # record bytes in base64 in the Tag
        ##          tag_value = b64encode(tag_value).decode('ascii')
        ##        setattr(subbox, 'attribute_name', attribute_name)
        ##        setattr(subbox, attribute_name, tag_value)
        ### Any trailing Boxes.
        ##if data_boxes:
        ##  subbox.add_field('extra_boxes', data_boxes)
        ##  warning("%d unexpected extra boxes: %r", len(data_boxes), data_boxes)
    return dict(boxes=subboxes)

  def __getattr__(self, attr):
    # see if this is a schema long name
    try:
      schema_code, schema = self.SUBBOX_SCHEMA_BY_LONG_ATTRIBUTE[attr]
    except KeyError:
      # not a long attribute name
      return super().__getattr__(attr)
    subbox_attr = schema_code.decode('iso8859-1').upper()
    with Pfx(
        "%s.%s: schema:%r: self.%s",
        self.__class__.__name__,
        attr,
        schema_code,
        subbox_attr,
    ):
      return getattr(self, subbox_attr)

  def metatags(self):
    for subbox in self.boxes:
      return TagSet(
          **{subbox.attribute_name: getattr(subbox, subbox.attribute_name)}
      )

OpColor = BinaryStruct('OpColor', '>HHH', 'red green blue')

@boxbodyclass
class VMHDBoxBody(FullBoxBody2):
  ''' A 'vmhd' Video Media Headerbox - section 12.1.2.
  '''
  OpColor = BinaryStruct('OpColor', '>HHH', 'red green blue')

  graphicsmode: UInt16BE
  opcolor: OpColor

@boxbodyclass
class SMHDBoxBody(FullBoxBody2):
  ''' A 'smhd' Sound Media Headerbox - section 12.2.2.
  '''
  balance: Int16BE
  reserved: UInt16BE

def parse_tags(path, tag_prefix=None):
  ''' Parse the tags from `path`.
      Yield `(box,tags)` for each subbox with tags.

      The optional `tag_prefix` parameter
      may be specified to prefix each tag name with a prefix.
      Other keyword arguments are passed to `parse()`
      (typical example: `discard_data=True`).
  '''
  with PARSE_MODE(discard_data=True):
    over_box = parse(path)
    for top_box in over_box:
      for box, tags in top_box.gather_metadata():
        if tags:
          if tag_prefix:
            new_tags = TagSet()
            new_tags.update(Tag(tag, prefix=tag_prefix) for tag in tags)
            tags = new_tags
          yield box, tags

if __name__ == '__main__':
  sys.exit(main(sys.argv))
  ##from cProfile import run
  ##run('main(sys.argv)', sort='ncalls')

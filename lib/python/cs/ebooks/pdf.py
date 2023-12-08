#!/usr/bin/env python3

''' Simple PDF parser/decoder with no rendering support at all.

    This is based on the PDF 32000-1:2008 document:
    https://web.archive.org/web/20081203002256/https://www.adobe.com/devnet/acrobat/pdfs/PDF32000_2008.pdf
'''

import binascii
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache, cached_property, partial
from getopt import GetoptError
from io import BytesIO
from itertools import chain
from math import floor
import os
from os.path import (
    basename, exists as existspath, join as joinpath, splitext
)
from pprint import pprint
import re
import sys
from tempfile import NamedTemporaryFile
from typing import Any, Callable, List, Mapping, Optional, Tuple, Union
from zipfile import ZipFile, ZIP_STORED
import zlib

from PIL import Image
from typeguard import typechecked

from cs.binary import AbstractBinary
from cs.buffer import CornuCopyBuffer
from cs.cmdutils import BaseCommand
from cs.deco import promote
from cs.lex import r
from cs.logutils import debug, error, warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.queues import IterableQueue
from cs.resources import RunState, uses_runstate
from cs.threads import bg

from .cbz import make_cbz

from pprint import pformat
from cs.debug import trace, s
from cs.x import X

pfx_open = partial(pfx_call, open)

DEFAULT_IMAGE_FILENAME_FORMAT = '{n:03d}.png'

def main(argv=None):
  return PDFCommand(argv).run()

class PDFCommand(BaseCommand):
  ''' Command line tool for doing things with PDF files.
  '''

  def cmd_extract_images(self, argv):
    ''' Usage: {cmd} pdf-files...
          Extract the images from the named page files.
    '''
    if not argv:
      raise GetoptError('missing pdf-files')
    runstate = self.options.runstate
    for pdf_filename in argv:
      runstate.raiseif()
      with Pfx(pdf_filename):
        pdf = PDFDocument.from_fspath(pdf_filename)
        print(' ', pdf.catalog)
        print(' ', pdf.pages)
        base, _ = splitext(basename(pdf_filename))
        for pagenum, imgnum, im in pdf.page_images():
          runstate.raiseif()
          width, height = im.size
          with Pfx("page %d, image %d, %dx%d", pagenum, imgnum, width, height):
            imgpath = f'{base}--{pagenum:02}--{imgnum:02}.png'
            print("{width}x{height}", imgpath)
            pfx_call(im.save, imgpath)

  cmd_xi = cmd_extract_images

  def cmd_make_cbz(self, argv):
    ''' Usage: {cmd} pdf-files...
          Extract the images from the named page files.
    '''
    if not argv:
      raise GetoptError('missing pdf-files')
    runstate = self.options.runstate
    for pdf_filename in argv:
      runstate.raiseif()
      with Pfx(pdf_filename):
        pdf = PDFDocument.from_fspath(pdf_filename)
        print(' ', pdf.catalog)
        print(' ', pdf.pages)
        base, _ = splitext(basename(pdf_filename))
        cbzpath = f'{base}.cbz'
        pdf.make_cbz(cbzpath)

  def cmd_scan(self, argv):
    ''' Usage: {cmd} pdf-files...
          Scan the PDF-data in pdf-files and report.
    '''
    if not argv:
      raise GetoptError('missing pdf-files')
    runstate = self.options.runstate
    for pdf_filename in argv:
      runstate.raiseif()
      with Pfx(pdf_filename):
        pdf = PDFDocument.from_fspath(pdf_filename)
        print(' ', pdf.catalog)
        print(' ', pdf.pages)
        for pagenum, page in enumerate(pdf.pages, 1):
          print(pagenum, '=============')
          print(s(page))
          print(dict(page.object))
          ##print("I15 =>", page['I15'])
          ##image_15 = page['I15']
          ##print(
          ##    "image 15 is_image", image_15.is_image(), "size",
          ##    image_15.image.size
          ##)
          ##image_15.image.show()
          content_obj = page.Contents.object
          print(content_obj)
          pprint(content_obj.context_dict)
          page_content_bs = content_obj.decoded_payload
          print("contents:", page_content_bs)
          resources = page.Resources
          print(s(resources))
          print(s(resources.object))
          print(dict(resources.object))

          def on_draw_object(obj):
            X("page %d: obj %s", pagenum, s(obj))
            X("  context dict %r", obj.context_dict)
            if obj.is_image():
              X("  width %r height %r", obj.Width, obj.Height)

          page.render(on_draw_object=on_draw_object)
          break
        break
        for (objnum, objgen), iobj in sorted(pdf.objmap.items()):
          runstate.raiseif()
          print(
              ' ', (
                  f'objmap[{objnum}]'
                  if objgen == 0 else f'objmap[num:{objnum},gen:{objgen}]'
              ), iobj
          )
          assert isinstance(iobj, IndirectObject)
          obj = iobj.object
          if isinstance(obj, Stream) and obj.is_image():
            print("   ", obj.image)
        break
        for token in pdf.tokens:
          if runstate.cancelled:
            return 1
          if isinstance(token, Comment):
            print('=>', r(token))
          elif isinstance(token, Stream):
            ##print('stream', len(token.payload))
            ##pprint(token.context_dict)
            decoded_bs = token.decoded_payload
            ##print('  =>', len(decoded_bs), 'bytes decoded')
            subtype = token.context_dict.get(b'Subtype')
            ##print("  subtype", subtype)
            if (subtype == b'Image' and (1 or token.filters)
                and (1 or token.filters[-1] == b'DCTDecode')
                and (1 or b'SMask' not in token.context_dict)):
              print("Image:")
              pprint(token.context_dict)
              im = token.image
              ##im.show()
              ##exit(1)
            else:
              print("skip stream subtype", repr(subtype))
              pprint(token.context_dict)
          ##break

# Binary regexps for PDF tokens.
# Most of these consist of a pair:
# a regexp FOO_re_bs to match the entire token
# and a regexp FOO_LEADIN_re_bs to match the required starting bytes.
EOL_re_bs = br'(\r?\n|\r(?!\n))'
WS_re_bs = br'[\x00\x09\x0a\x0c\x0d\x20]'
WSS_re_bs = WS_re_bs + b'+'
ARRAY_OPEN_re_bs = br'\['
ARRAY_OPEN_LEADIN_re_bs = br'\['
ARRAY_CLOSE_re_bs = br'\]'
ARRAY_CLOSE_LEADIN_re_bs = br'\]'
DICT_OPEN_re_bs = br'<<'
DICT_OPEN_LEADIN_re_bs = br'<<'
DICT_CLOSE_re_bs = br'>>'
DICT_CLOSE_LEADIN_re_bs = br'>>'
FLOAT_re_bs = br'[-+]?(\.\d+|\d+\.\d*)'
FLOAT_LEADIN_re_bs = br'[-+.\d]'
INT_re_bs = br'[-+]?\d+(?!\.)'
INT_LEADIN_re_bs = br'[-+\d]'
# I have not yet found a formal definition of a keyword :-(
KEYWORD_re_bs = br'[a-zA-Z][_\w]*'
KEYWORD_LEADIN_re_bs = br'[a-zA-Z]'
NAME_HASHHEX_re_bs = br'#[0-9a-fA-F][0-9a-fA-F]'
NAME_NONHASH_re_bs = br'[^/\[\]<>\(#\x00\x09\x0a\x0c\x0d\x20]'
NAME_re_bs = b''.join(
    (
        br'/(',
        br'(',
        NAME_HASHHEX_re_bs,
        br'|',
        NAME_NONHASH_re_bs,
        br')*',
        br')',
    )
)
NAME_LEADIN_re_bs = br'/'
COMMENT_re_bs = br'%[^\r\n]*' + EOL_re_bs
COMMENT_LEADIN_re_bs = br'%'
HEXSTRING_re_bs = br'<[0-9a-zA-F]+>'
HEXSTRING_LEADIN_re_bs = br'<'
# string bytes not needing escapes
STRING_NONSLOSH_re_bs = br'[^\\\(\)\r\n]'
# STRING slosh escapes
STRING_SLOSHED_re_bs = b''.join(
    (
        br'\\',
        br'(',
        br'[nrtbf()\\]',  # \t et al
        br'|',
        br'[0-7]{3}',  # \ooo
        br'|',
        EOL_re_bs,  # line extension
        br')',
    )
)
STRING_OPEN_re_bs = b''.join(
    (
        br'\(',
        br'(',
        STRING_NONSLOSH_re_bs,
        br'|',
        STRING_SLOSHED_re_bs,
        br')*',
    )
)
STRING_OPEN_LEADIN_re_bs = br'\('
STRING_CLOSE_re_bs = br'\)'
STRING_CLOSE_LEADIN_re_bs = br'\)'

@dataclass
class Reaction:
  ''' A regular expression based token recogniser.

      Fields:
      - `re`: an `re.Pattern` matching the whole token
      - `re_leading`: an `re.Pattern` matching the leading byte
      - `tokenise`: a function of an `re.Match` object matching a token,
        returning the token's native value, eg a `str`, `int` etc.
      - `min_len`: the minimum length of this token, default `1`
  '''

  re: re.Pattern
  re_leadin: re.Pattern
  tokenise: Callable[re.Match, Any] = lambda m: m.group()
  min_len: int = 1
  buf_inc: int = 16

  def match(self, buf: CornuCopyBuffer):
    ''' Match a token at the start of `buf`.
        Return the result of `self.tokenise(m)`
        where `m` is the `re.Match` object.
        Returns `None` if the leadin pattern does not match
        or the pattern cannot be matched before EOF.

        The optional parameter `preious_object` may be supplied
        as the previous semantic object from the PDF token stream.
        This intended is to support parsing `stream` objects
        which require access to a preceeding dictionary
        in order to know the payload length.

        This requires a match followed by at least one byte post
        the token or followed by EOF. The buffer is extended until
        this is achieved.  This implies that the tokens are
        distinguishable by their leadin patterns.
    '''
    # probe for the leading regexp
    m = self.re_leadin.match(buf.peek(self.min_len, short_ok=True))
    if m is None:
      # no leadin, do no further matching
      return None
    return self.match_re(buf, self.re)

  def match_re(
      self, buf: CornuCopyBuffer, R: re.Pattern = None, tokenise=None
  ):
    if R is None:
      R = self.re
    if tokenise is None:
      tokenise = self.tokenise
    while True:
      buflen = len(buf)
      m = R.match(buf.peek(buflen))
      if m is not None:
        # a match, which might be incomplete (eg an int)
        if m.end() < buflen:
          # must be complete because the match did not consume the buffer
          buf.take(m.end())
          return tokenise(m)
      buf.extend(self.buf_inc, short_ok=True)
      if buflen == len(buf):
        # EOF, no more data to match
        if m is not None:
          # must be a EOF, return the match
          buf.take(m.end())
          return tokenise(m)
        # no match possible
        return None

class _Token(bytes):
  ''' Base class for PDF tokens, a subtype of `bytes`.
      Where relevant the `bytes` value is the raw value
      and `bytes(token)` returns a transcription suitable for use in a PDF.
  '''

  def __repr__(self):
    return f'{self.__class__.__name__}:{bytes(self)!r}'

  @property
  def value(self):
    ''' The value of this object, by default `self`.
    '''
    return self

  @classmethod
  def promote(cls, name):
    ''' Promote `name` to a `cls`.
        Accepts `str`, `bytes` or `cls`.
    '''
    if isinstance(name, cls):
      return name
    if isinstance(name, str):
      return cls(name.encode('utf-8'))
    return cls(name)

class ArrayOpen(_Token):
  ''' A `bytes` instance representing a PDF array open.
  '''

class ArrayClose(_Token):
  ''' A `bytes` instance representing a PDF array close.
  '''

class Comment(_Token):
  ''' A `bytes` instance representing a PDF comment.
  '''

class DictOpen(_Token):
  ''' A `bytes` instance representing a PDF dictionary open.
  '''

class DictClose(_Token):
  ''' A `bytes` instance representing a PDF dictionary close.
  '''

class EndStream(_Token):
  ''' A `bytes` instance representing a PDF endstream.
  '''

class HexString(_Token):
  ''' A `bytes` instance representing a PDF hex string.
  '''

  def __bytes__(self):
    ''' Return a PDF transcription of the string.
    '''
    return b'<' + binascii.hexlify(self) + b'>'

class Keyword(_Token):
  ''' A `bytes` instance representing a PDF keyword.
  '''

def isnull(token):
  ''' Test whether `token` is the `null` keyword.
  '''
  return isinstance(token, Keyword) and token == b'null'

class Name(_Token):
  ''' A `bytes` instance representing a PDF name.
  '''

  def __bytes__(self):
    ''' Return a PDF transcription of the string.
    '''
    return br'/' + self

class String(_Token):
  ''' A `bytes` instance representing a PDF string.

      The `String`'s value is the decoded bytes.
      Note that `bytes(string)` is a transcription of the `String`
      for use in a PDF.
  '''

  STRING_NONSLOSH_bre = re.compile(STRING_NONSLOSH_re_bs + b'+')

  # mapping of byte values to slosh escapes
  SLOSH_MAP = {
      ord('\\'): b'\\\\',
      ord('\n'): br'\n',
      ord('\r'): br'\r',
      ord('\t'): br'\t',
      ord('('): br'\(',
      ord(')'): br'\)',
  }

  def sloshed(self):
    ''' Return the string in sloshed form with no nested parentheses.
    '''
    bss = []
    offset = 0
    while offset < len(self):
      m = self.STRING_NONSLOSH_bre.match(self, offset)
      if m:
        bss.append(m.group())
        offset = m.end()
      else:
        b = self[offset]
        try:
          bs = self.SLOSH_MAP[b]
        except KeyError:
          bs = ("\\03o" % b).encode('ascii')
        bss.append(bs)
        offset += 1
    return b''.join(bss)

  def __bytes__(self):
    ''' Return a PDF transcription of the string.
    '''
    return br'(' + self.sloshed() + br')'

class ArrayObject(list):
  ''' A PDF array.
  '''

  def __bytes__(self):
    return b''.join(
        b'[ ',
        *map(bytes, self),
        b' ]',
    )

class DictObject(dict):
  ''' A PDF dictionary.
  '''

  def __bytes__(self):
    return b''.join(
        b'<<\r\n',
        *chain(
            (b'  ', bytes(k), b'\r\n    ', bytes(v), b'\r\n')
            for k, v in sorted(self.items())
        ),
        b'>>',
    )

  @typechecked
  def __getattr__(self, key: str):
    ''' Provide access to the entries by name.
    '''
    try:
      return self[Name.promote(key)]
    except KeyError:
      raise AttributeError(
          f'{self.__class__.__name__}.{key}: keys={sorted(self.keys())}'
      )

class IntObject(int):

  def __bytes__(self):
    return str(self).encode('ascii')

class FloatObject(float):

  def __bytes__(self):
    return ("%f" % self).encode('ascii')

@dataclass
class IndirectObject:
  ''' An indirect object.
  '''

  number: int
  generation: int
  object: Any

  def __str__(self):
    return f'{self.__class__.__name__}:{self.generation}:{self.object.__class__.__name__}'

  def __bytes__(self):
    return b' '.join(
        str(self.number).encode('ascii'),
        str(self.generation).encode('ascii'),
        b'obj',
        bytes(self.object),
        b'endobj',
    )

@dataclass
class ObjectRef:
  ''' A reference to an `IndirectObject`.
  '''

  objmap: Mapping[Tuple[int, int], IndirectObject]
  number: int
  generation: int

  def __repr__(self):
    return f'{self.__class__.__name__}:{self.number},{self.generation}'

  def __bytes__(self):
    return b' '.join(
        str(self.number).encode('ascii'),
        str(self.generation).encode('ascii'),
        b'R',
    )

  @property
  def iobj(self):
    ''' The referenced `IndirectObject`.
    '''
    iobj = self.objmap.get((self.number, self.generation))
    if iobj is None:
      warning(
          "no object named %r: keys=%r", (self.number, self.generation),
          sorted(self.objmap.keys())
      )
      return None
    return iobj

  @property
  def object(self):
    ''' The `Object` referenced by the `IndirectObject`.
    '''
    iobj = self.iobj
    if iobj is None:
      return None
    return iobj.object

@dataclass
class ColorSpace:

  # mapping of color space names to classes instances
  SPACE_BY_NAME = {}

  family_name: str
  ncolors: int
  bits_per_component: int
  PIL_mode: str

  @classmethod
  @pfx_method
  def promote(cls, obj):
    if isinstance(obj, cls):
      return obj
    try:
      name = Name.promote(obj)
    except TypeError:
      pass
    else:
      return cls.SPACE_BY_NAME[name]
    if isinstance(obj, ObjectRef):
      return cls.promote(obj.object)
    if isinstance(obj, ArrayObject):
      family_name, spec = obj
      if isinstance(spec, ObjectRef):
        spec = spec.object
      assert isinstance(spec, Stream)
      cd = spec.context_dict
      ncolors = cd.N
      alternate = cd.Alternate
      length = cd.Length
      filter = cd.Filter
      alt_space = ColorSpace.promote(alternate)
      return ColorSpace(
          family_name=family_name,
          ncolors=ncolors,
          bits_per_component=alt_space.bits_per_component,
          PIL_mode=alt_space.PIL_mode,
      )
    raise TypeError(f'{cls.__name__}.promote(obj={s(obj)}): unsupported type')

ColorSpace.SPACE_BY_NAME[b'DeviceGray'] = ColorSpace(
    family_name='DeviceGray',
    ncolors=1,
    bits_per_component=8,
    PIL_mode='L',
)
ColorSpace.SPACE_BY_NAME[b'DeviceRGB'] = ColorSpace(
    family_name='DeviceRGB',
    ncolors=3,
    bits_per_component=8,
    PIL_mode='RGB',
)

@dataclass
class Stream:
  ''' A PDF Stream.
  '''

  context_dict: DictObject
  payload: bytes

  def __str__(self):
    return f'{self.__class__.__name__}:encoded_length={len(self.payload)}'

  def __repr__(self):
    return str(self)

  def __bytes__(self):
    return (
        bytes(self.context_dict) + b'\r\nstream\r\n' + self.payload +
        b'\r\nendstream\r\n'
    )

  def __getattr__(self, attr):
    ''' Try getting unknown attributes from the context dictionary.
    '''
    try:
      return getattr(self.context_dict, attr)
    except AttributeError:
      raise AttributeError(f'{self.__class__.__name__}.{attr}')

  @property
  def filters(self) -> List[bytes]:
    ''' The filters as a list of `bytes` instances.
    '''
    filters = self.context_dict.get(b'Filter')
    if filters is None:
      filters = []
    elif isinstance(filters, bytes):
      filters = [filters]
    else:
      assert isinstance(filters, list)
    return filters

  @cached_property
  def decoded_payload(self):
    filters = self.filters
    bs = self.payload
    for i, filt in enumerate(filters, 1):
      if filt == b'DCTDecode':
        bs, im = pfx_call(dctdecode_im, bs)
        if i == len(filters):
          # also stash as the cached Image
          self._image = im
      elif filt == b'FlateDecode':
        bs = pfx_call(flatedecode, bs)
      else:
        warning("stop at unimplemented filter %r", filt)
        break
    return bs

  @property
  def value(self):
    ''' The value of a stream is the decoded payload.
    '''
    return self.decoded_payload

  def is_image(self):
    ''' Does this `Stream` encode an image?
    '''
    return self.context_dict.get(b'Subtype') == b'Image'

  @cached_property
  def image(self):
    ''' A cached property holding a `PIL.Image` decoded from the `Stream`.
    '''
    return self.compute_image()

  @uses_runstate
  def compute_image(self, *, debug_tags=False, runstate: RunState):
    ''' Compute a PIL `Image` from the decoded payload.
    '''
    decoded_bs = self.decoded_payload
    decode_params = self.context_dict.get(b'DecodeParms', {})
    color_transform = decode_params.get(b'ColorTransform', 0)
    color_space = ColorSpace.promote(self.ColorSpace)
    bits_per_component = decode_params.get(b'BitsPerComponent')
    if not bits_per_component:
      bits_per_component = color_space.bits_per_component
    ncolors = decode_params.get(b'Colors')
    if not ncolors:
      ncolors = color_space.ncolors
    predictor = decode_params.get(b'Predictor', 0)
    width = self.Width
    height = self.Height
    PIL_mode = color_space.PIL_mode
    if predictor == 0:
      image_data = decoded_bs
    elif predictor >= 10:
      # Decode the data according to its predictor algorithm.
      #
      # I'm indebted to Mark Adler for explaining a lot of this to me here:
      # https://stackoverflow.com/questions/77120604/decoding-pdf-can-i-use-pil-pillow-to-access-the-png-predictor-algorithm-in-orde
      #
      # The algorithms are specified here:
      # https://www.w3.org/TR/png/#9Filters
      #
      # Note that statement "Unsigned arithmetic modulo 256 is used,
      # so that both the inputs and outputs fit into bytes." actually
      # applies only to the input and output values themselves. Doing
      # modulo 256 intermediate values leads to incorrect results.
      # Therefore it is only when storing the result in the deocded
      # row data that we apply a `% 256`.
      #
      # Split data into tagged rows:
      mv = memoryview(decoded_bs)
      tags = []
      rows = []
      # a row has a leading tag byte indicating the encoding and then pixels
      row_length = 1 + width * ncolors
      # dummy preceeding row filled with zeroes
      prev_row = bytes(width * ncolors)
      prev_offset = -row_length
      for row_index, offset in enumerate(range(
          0,
          height * row_length,
          row_length,
      )):
        runstate.raiseif()
        prev_offset = offset
        tag = decoded_bs[offset]
        tags.append(tag)
        row_data = mv[offset + 1:offset + row_length]
        # recon methods from tag values
        # detailed here:
        # https://www.w3.org/TR/png/#9Filters
        if tag == 0:
          # None: Recon(x) = Filt(x)
          # store row unchanged
          # debug_tags colour black
          pass
        else:
          # we will be modifying these data in place, so make a read/write copy
          row_data = bytearray(row_data)
          row_data0 = row_data
          if tag == 1:
            # Sub: Recon(x) = Filt(x) + Recon(a)
            # debug_tags colour red - looks correct
            for i, b in enumerate(row_data):
              recon_a = 0 if i < ncolors else row_data[i - ncolors]
              row_data[i] = (b + recon_a) % 256
          elif tag == 2:
            # Up: Recon(x) = Filt(x) + Recon(b)
            # debug_tags colour green
            for i, b in enumerate(row_data):
              recon_b = prev_row[i]
              row_data[i] = (b + recon_b) % 256
          elif tag == 3:
            # Average: Recon(x) = Filt(x) + floor((Recon(a) + Recon(b)) / 2)
            # debug_tags colour blue
            for i, b in enumerate(row_data):
              recon_a = 0 if i < ncolors else row_data[i - ncolors]
              recon_b = prev_row[i]
              row_data[i] = (b + floor((recon_a + recon_b) / 2)) % 256
          elif tag == 4:
            # Paeth: Recon(x) = Filt(x) + PaethPredictor(Recon(a), Recon(b), Recon(c))
            # debug_tags colour white
            for i, b in enumerate(row_data):
              recon_a = 0 if i < ncolors else row_data[i - ncolors]
              recon_b = prev_row[i]
              recon_c = 0 if i < ncolors else prev_row[i - ncolors]
              # Paeth predictor
              p = (recon_a + recon_b - recon_c)  ## % 256
              pa = abs(p - recon_a)
              pb = abs(p - recon_b)
              pc = abs(p - recon_c)
              if pa <= pb and pa <= pc:
                Pr = recon_a
              elif pb <= pc:
                Pr = recon_b
              else:
                Pr = recon_c
              row_data[i] = (b + Pr) % 256
          else:
            warning(
                "row %d: unsupported tag value %d, row unchanged",
                row_index,
                tag,
            )
        rows.append(row_data)
        prev_row = row_data
      if debug_tags:
        # colour the rows by tag for debugging
        # 0:black 1:red 2:green 3:blue 4:white
        tag_bs_map = {
            0: bytes((0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
            1: bytes((255, 0, 0, 255, 0, 0, 255, 0, 0, 255, 0, 0)),
            2: bytes((0, 255, 0, 0, 255, 0, 0, 255, 0, 0, 255, 0)),
            3: bytes((0, 0, 255, 0, 0, 255, 0, 0, 255, 0, 0, 255)),
            4: bytes(
                (255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255)
            ),
        }
        for tag, row_data in zip(tags, rows):
          tag_bs = tag_bs_map[tag]
          row_data[:len(tag_bs)] = tag_bs
      image_data = b''.join(rows)
    else:
      warning("unhandled DecodeParms[Predictor] value %r", predictor)
      image_data = decoded_bs
    im = Image.frombytes(PIL_mode, (width, height), image_data)
    return im

class StringOpen(_Token):
  ''' The opening section of a PDF string.
  '''

class StringClose(_Token):
  ''' The closing parenthesis section of a PDF string.
  '''

class StringParts(list):
  ''' The `bytes` components of a PDF string.
  '''

  def __bytes__(self):
    return b''.join(map(decode_pdf_simple_string, self))

class WhiteSpace(bytes):
  ''' A `bytes` instance representing PDF whitespace.
  '''

StringOpen_reaction = Reaction(
    re.compile(STRING_OPEN_re_bs),
    re.compile(STRING_OPEN_LEADIN_re_bs),
    lambda m: StringOpen(m.group()),
)
StringClose_reaction = Reaction(
    re.compile(STRING_CLOSE_re_bs),
    re.compile(STRING_CLOSE_LEADIN_re_bs),
    lambda m: StringClose(m.group()),
)

# regular expressions for tokens in recognition order
# for example the FLOAT_re must preceed the INT_re
tokenisers = [
    Reaction(
        re.compile(COMMENT_re_bs),
        re.compile(COMMENT_LEADIN_re_bs),
        lambda m: Comment(m.group(0)),
    ),
    Reaction(
        re.compile(KEYWORD_re_bs),
        re.compile(KEYWORD_LEADIN_re_bs),
        lambda m: Keyword(decode_pdf_name(m.group(0))),
    ),
    Reaction(
        re.compile(NAME_re_bs),
        re.compile(NAME_LEADIN_re_bs),
        lambda m: Name(decode_pdf_name(m.group(0)[1:])),
    ),
    Reaction(
        re.compile(WSS_re_bs),
        re.compile(WS_re_bs),
        lambda m: WhiteSpace(m.group(0)),
    ),
    Reaction(
        re.compile(ARRAY_OPEN_re_bs),
        re.compile(ARRAY_OPEN_LEADIN_re_bs),
        lambda m: ArrayOpen(m.group()),
    ),
    Reaction(
        re.compile(ARRAY_CLOSE_re_bs),
        re.compile(ARRAY_CLOSE_LEADIN_re_bs),
        lambda m: ArrayClose(m.group()),
    ),
    Reaction(
        re.compile(DICT_OPEN_re_bs),
        re.compile(DICT_OPEN_LEADIN_re_bs),
        lambda m: DictOpen(m.group()),
        2,
    ),
    Reaction(
        re.compile(DICT_CLOSE_re_bs),
        re.compile(DICT_CLOSE_LEADIN_re_bs),
        lambda m: DictClose(m.group()),
        2,
    ),
    Reaction(
        re.compile(INT_re_bs),
        re.compile(INT_LEADIN_re_bs),
        lambda m: IntObject(m.group()),
        1,
    ),
    Reaction(
        re.compile(FLOAT_re_bs),
        re.compile(FLOAT_LEADIN_re_bs),
        lambda m: FloatObject(m.group()),
        3,
    ),
    Reaction(
        re.compile(HEXSTRING_re_bs),
        re.compile(HEXSTRING_LEADIN_re_bs),
        lambda m: HexString(decode_pdf_hex(m.group(0)[1:-1])),
    ),
    StringOpen_reaction,
    StringClose_reaction,
]

@promote
def tokenise(buf: CornuCopyBuffer, debug_matches=False):
  ''' Scan `buf` and yield tokens.
  '''
  in_str = None
  in_str_stack = []
  while True:
    with Pfx("tokenise(%s)", buf):
      for reaction in ((StringOpen_reaction, StringClose_reaction)
                       if isinstance(in_str, StringParts) else tokenisers):
        token = reaction.match(buf)
        if token is not None:
          if debug_matches:
            debug("matched %s", r(token))
          break
      else:
        if buf.at_eof():
          # end parse loop
          if debug_matches:
            debug("EOF")
          break
        # nothing matched - yield the starting byte
        warning(
            "tokenise: no match at offset %d:%r..., taking the first byte",
            buf.offset,
            buf.peek(8, short_ok=True),
        )
        token = buf.take(1)
        break  ## debug
      if isinstance(token, StringOpen):
        in_str_stack.append(in_str)
        in_str = StringParts([token[1:]])
        continue
      if isinstance(token, StringClose):
        if isinstance(in_str, StringParts):
          token = String(bytes(in_str))
          in_str = in_str_stack.pop()
        else:
          warning("unexpected %r, in_str is %r", token, in_str)
      if in_str is None:
        yield token
  while in_str is not None:
    warning("tokenise(%s): unclosed %s at EOF", buf, r(in_str))
    try:
      in_str = in_str_stack.pop()
    except IndexError:
      break

@dataclass
class PDFDocument(AbstractBinary):
  ''' A PDF document.
  '''

  objmap: Mapping[Tuple[int, int], Any] = field(default_factory=dict)
  tokens: List = field(default_factory=list)
  values: List = field(default_factory=list)
  # mapping of object types to a list of objects
  by_obj_type: Mapping[bytes, List[Any]] = field(
      default_factory=lambda: defaultdict(list)
  )

  def __str__(self):
    return (
        f'{self.__class__.__name__}('
        f'objects[{len(self.objmap)}],t'
        f'okens[{len(self.tokens)}],'
        f'values[{len(self.values)}]'
        ')'
    )

  @classmethod
  def from_fspath(cls, pdfpath: str):
    ''' Parse the file at `pdfpath` and return a `PDFDocument`.
    '''
    with pfx_call(open, pdfpath, 'rb') as pdff:
      return cls.parse(buf=pdff)

  @property
  @pfx_method
  def catalog(self):
    ''' The `Catalog` `Dict`.
    '''
    catalogs = self.by_obj_type[Name(b'Catalog')]
    if not catalogs:
      warning("no catalogs in document")
      return None
    if len(catalogs) > 1:
      warning("%d catalogs in document", len(catalogs))
    return PDFCatalog(pdf=self, object=catalogs[0])

  @property
  @typechecked
  def pages(self) -> List['PDFPage']:
    ''' A list of the `Page` objects.
    '''
    catalog: 'PDFCatalog' = self.catalog
    if catalog is None:
      return [
          PDFPage(pdf=self, catalog=None, number=pagenum, object=pageobj) for
          pagenum, pageobj in enumerate(self.by_obj_type[Name(b'Page')], 1)
      ]
    return catalog.pages

  def page_images(self):
    ''' Return an iterable of `(pagenum,imgnum,im)`
        for each image drawn when rendering the PDF pages.
    '''
    Q = IterableQueue(name=str(self) + '.page_images')

    def generate_images():
      ''' Compute the PDF images and put them onto `Q`.
      '''

      def on_draw_object(obj):
        nonlocal imgnum
        if not obj.is_image():
          X("draw object nonimage: %s", s(obj))
          return
        imgnum += 1
        im = obj.image
        Q.put((pagenum, imgnum, im))

      try:

        for pagenum, page in enumerate(self.pages, 1):
          with Pfx("page %d", pagenum):
            imgnum = 0
            page.render(on_draw_object=on_draw_object)
      finally:
        Q.close()

    bg(generate_images, daemon=True)
    return Q

  def make_cbz(self, cbzpath):
    ''' Create a CBZ file at `cbzpath` containing the images rendered by the pages.
    '''
    base, _ = splitext(basename(cbzpath))
    with make_cbz(
        cbzpath,
        images=self.make_cbz_images(base),
    ):
      pass

  @pfx_method
  @uses_runstate
  def make_cbz_images(self, base: str, *, runstate: RunState):
    ''' A generator for the `images` parameter for `cs.ebooks.cbs.make_cbz`.
    '''
    for pagenum, imgnum, im in self.page_images():
      runstate.raiseif()
      width, height = im.size
      with Pfx("page %d, image %d, %dx%d", pagenum, imgnum, width, height):
        imgpath = f'{base}--{pagenum:02}--{imgnum:02}.png'
        with NamedTemporaryFile(suffix='.png') as T:
          pfx_call(im.save, T.name)
          yield T.name, basename(imgpath)

  @classmethod
  @promote
  def parse(cls, buf: CornuCopyBuffer) -> 'PDFDocument':
    ''' Scan `buf`, return a `PDFDocument`.
    '''
    tokens = []
    objmap: Mapping[Tuple[int, int], IndirectObject] = {}
    values = []
    by_obj_type = defaultdict(list)
    pdfdoc = cls(
        tokens=tokens, objmap=objmap, values=values, by_obj_type=by_obj_type
    )
    values_stack = []
    in_obj = None
    in_obj_stack = []
    tokens_it = tokenise(buf)
    for token in tokens_it:
      tokens.append(token)
      if not isinstance(token, (
          Comment,
          WhiteSpace,
          ArrayOpen,
          ArrayClose,
          DictOpen,
          DictClose,
      )):
        values.append(token)
      # number generation "obj" value "endobj"
      if isinstance(token, Keyword) and token == b'endobj':
        # define an indirect object
        number, generation, obj, objvalue, endobj = values[-5:]
        assert isinstance(obj, Keyword) and obj == b'obj'
        assert isinstance(number, int) and number > 0
        assert isinstance(generation, int) and generation >= 0
        assert isinstance(endobj, Keyword) and endobj == b'endobj'
        objkey = number, generation
        assert objkey not in objmap, "repeated obj definition %d %d" % (
            number, generation
        )
        objmap[objkey] = iobj = IndirectObject(
            number=number, generation=generation, object=objvalue
        )
        # replace the last 5 values with the indirect object
        values[-5:] = [iobj]
        objtype = None
        if isinstance(objvalue, DictObject):
          try:
            objtype = objvalue.Type
          except AttributeError:
            X("no /Type in %r", sorted(objvalue.keys()))
        elif isinstance(objvalue, Stream):
          try:
            objtype = objvalue.Type
          except AttributeError:
            pass
        if objtype is not None:
          by_obj_type[objtype].append(objvalue)
        continue
      # number generation "R"
      if isinstance(token, Keyword) and token == b'R':
        # an object reference
        number, generation, R = values[-3:]
        assert isinstance(number, int) and number > 0
        assert isinstance(generation, int) and generation >= 0
        assert isinstance(R, Keyword) and R == b'R'
        objref = ObjectRef(objmap, number, generation)
        # replace the last 3 values
        values[-3:] = [objref]
        generation = tokens.pop()
        continue
      if isinstance(token, ArrayOpen):
        in_obj_stack.append(in_obj)
        in_obj = ArrayObject()
        values_stack.append(values)
        values = []
        continue
      if isinstance(token, ArrayClose):
        if not isinstance(in_obj, ArrayObject):
          warning("ignoring unexpected %r, in_obj is %r", token, in_obj)
          continue
        in_obj.extend(values)
        values = values_stack.pop()
        values.append(in_obj)
        in_obj = in_obj_stack.pop()
        continue
      if isinstance(token, DictOpen):
        in_obj_stack.append(in_obj)
        in_obj = DictObject()
        values_stack.append(values)
        values = []
        continue
      if isinstance(token, DictClose):
        if not isinstance(in_obj, DictObject):
          warning("ignoring unexpected %r, in_obj is %r", token, in_obj)
          continue
        for i in range(0, len(values), 2):
          k = values[i]
          v = values[i + 1]
          if not isnull(v):
            in_obj[k] = v
        values = values_stack.pop()
        values.append(in_obj)
        in_obj = in_obj_stack.pop()
        continue
      if isinstance(token, Keyword) and token == b'stream':
        with Pfx("stream"):
          # consume the required EOL
          eol_bs = buf.peek(2)
          if eol_bs == b'\r\n':
            buf.take(2)
          elif eol_bs.startswith(b'\n'):
            buf.take(1)
          else:
            warning("missing EOL after stream")
          context_dict, stream = values[-2:]
          assert isinstance(context_dict, DictObject)
          length = context_dict.Length
          if isinstance(length, ObjectRef):
            length = length.object
          if length is not None:
            # gather up the payload
            assert isinstance(length,
                              int), "Length is not an int: %s" % (r(length),)
            assert length >= 0
            payload = buf.take(length)
          else:
            # weird PDF whose length cannot be resolved
            warning(
                "could not resolve stream length, falling back to looking for endstream"
            )
            peeklen = 24
            end_re = re.compile(b'\r?\nendstream[\r\n]')
            while True:
              bs = buf.peek(peeklen)
              m = end_re.search(bs)
              if m is None:
                if len(bs) < peeklen:
                  # EOF, abandon all hope
                  warning(
                      "no endstream found, keeping %d bytes as the payload",
                      len(bs)
                  )
                  payload = bs
                  break
                peeklen += 1024
                continue
              payload = buf.take(m.start())
              warning(
                  "found endstream, used %d bytes as the payload",
                  len(payload)
              )
              break
          newline_bs = buf.peek(2, short_ok=True)
          if newline_bs == b'\r\n':
            buf.take(2)
          elif newline_bs.startswith(b'\n'):
            buf.take(1)
          else:
            warning("expected EOL after payload")
          endstream = next(tokens_it)
          assert isinstance(endstream, Keyword) and endstream == b'endstream'
          assert tokens[-1] is token
          stream = Stream(context_dict, payload)
          tokens[-2:] = [stream]
          values[-2:] = [stream]
          continue
    return pdfdoc

  def transcribe(self):
    ''' Yield `bytes` instances transcribing the PDF document.
    '''
    yield from iter(self.tokens)

@dataclass
class PDFCatalog:
  pdf: PDFDocument
  object: DictObject

  def __str__(self):
    return f'{self.__class__.__name__}:{sorted(self.object.keys())}'

  def __repr__(self):
    return str(self)

  def __getattr__(self, attr):
    assert attr != 'pages'
    return getattr(self.object, attr)

  def __getitem__(self, index):
    ''' Indexing with an `int` returns the numbered page (starting at `0`).
        Indexing with a `str` or `Name` returns the named resource.
    '''
    if isinstance(index, int):
      return self.pages[index]
    resource = Name.promote(index)
    ref = self.Resources.object.XObject[resource]
    return ref.object

  @cached_property
  @typechecked
  def pages(self) -> List['PDFPage']:
    ''' The cached list of `PDFPage`s in this `PDFCatalog`.
    '''
    pages_objref = self.Pages
    assert isinstance(pages_objref, ObjectRef)
    assert isinstance(pages_objref.object, DictObject)
    kids = pages_objref.object.Kids
    return [
        PDFPage(pdf=self.pdf, catalog=self, number=pagenum, object=kid.object)
        for pagenum, kid in enumerate(kids, 1)
    ]

@dataclass
class PDFPage:
  ''' A page of a `PDFDocument`.
  '''
  pdf: PDFDocument
  catalog: PDFCatalog
  number: int
  object: DictObject

  def __str__(self):
    return f'{self.__class__.__name__}#{self.number}'

  def __repr__(self):
    return str(self)

  def __getattr__(self, attr):
    return getattr(self.object, attr)

  @promote
  def __getitem__(self, resource: Name):
    ''' Indexing returns the named resources `XObject` entry.
    '''
    xobjs = self.resources.XObject
    try:
      ref = xobjs[resource]
    except KeyError:
      return self.Parent[resource]
    return ref.object

  @property
  def resources(self):
    ''' The `Resources` dictionary.
    '''
    return self.Resources.object

  @pfx_method
  def render(self, *, on_draw_object=None):
    ''' Render this page.

        At present this just processes the content stream.
        TODO: apply the rendering to eg a `PIL.Image` or something,
        but with some sort of modality to support data extraction
        such as reporting the images and discarding any rendering.
    '''
    bs = self.Contents.object.decoded_payload
    buf = CornuCopyBuffer.promote(self.Contents.object.decoded_payload)
    values_stack = []
    gs = GraphicsState()
    gss = [gs]
    tokens_it = tokenise(buf)
    for token in tokens_it:
      with Pfx(s(token)):
        if isinstance(token, WhiteSpace):
          continue
        if isinstance(token, (int, float, Name)):
          values_stack.append(token)
        elif isinstance(token, Keyword):
          kw = token.decode('ascii')
          if kw == 'cm':
            # modify the CTM
            abcdef = values_stack[-6:]
            values_stack[-6:] = []
            gs.ctm = [
                abcdef[0],
                abcdef[1],
                0,
                abcdef[2],
                abcdef[3],
                0,
                abcdef[4],
                abcdef[5],
                1,
            ]
          elif kw == 'Do':
            # draw object
            obj_name: str = values_stack.pop()
            obj = self[obj_name]
            if on_draw_object:
              on_draw_object(obj)
            else:
              warning("do not know how to draw object: %s", s(obj))
          elif kw == 'd':
            # set the line dash pattern
            dash_array, dash_phase = values_stack[-2:]
            values_stack[-2:] = []
            gs.dash_pattern = DashPattern(array=dash_array, phase=dash_phase)
          elif kw == 'gs':
            # apply graphics state dictionary
            # TODO: unimplemented, see table 57
            extg_name: str = values_stack.pop()
            warning("TODO: look up graphics state dictionary and apply")
          elif kw == 'i':
            # flatness
            gs.flatness = float(values_stack.pop())
          elif kw == 'J':
            line_cap = int(values_stack.pop())
            if line_cap not in (0, 1, 2):
              warning(
                  "invalid line_cap, should be 0, 1 or 2, got %r", line_cap
              )
            gs.line_cap = line_cap
          elif kw == 'j':
            line_join = int(values_stack.pop())
            if line_join not in (0, 1):
              warning("invalid line_join, should be 0 or 1, got %r", line_join)
            gs.line_join = line_join
          elif kw == 'M':
            gs.miter_limit = float(values_stack.pop())
          elif kw == 'q':
            gs = GraphicsState(**gs.__dict__)
            gss.append(gs)
          elif kw == 'Q':
            gs = gss.pop()
          elif kw == 'ri':
            gs.rendering_intent = values_stack.pop()
          elif kw == 'w':
            line_width = float(values_stack.pop())
            gs.line_width = line_width
          else:
            warning("unimplemented keyword")
        else:
          raise TypeError(f'unsupported token: {s(token)}')
    if values_stack:
      warning(
          "%d left over content stream values: %r", len(values_stack),
          values_stack
      )

@dataclass
class DashPattern:
  ''' A dash pattern, section 8.4.3.6.
  '''
  array: list
  phase: int

@dataclass
class GraphicsState:
  ''' A representation of the graphics state described in section 8.4.
  '''

  # TODO: method to apply a Graphics State Parameter Dictionary
  # see section 8.4.5 and the "gs" operator in 8.4.4

  # fields from table 52: Device Independent Graphics State Parameters
  # current transformation matrix
  ctm: list = field(default_factory=list)
  clipping_Path: Any = None
  color_space: Union[str, list] = "DeviceGray"
  color: Any = (0, 0, 0)  # black
  text_state: Any = None
  line_width: float = 1.0
  line_cap: int = 0  # square butt caps
  line_join: int = 0  # mitered joins
  miter_limit: float = 10.0
  dash_pattern: DashPattern = field(default_factory=lambda: DashPattern([], 0))
  rendering_intent: str = "RelativeColorimetric"
  stroke_adjustment: bool = False
  blend_mode: Union[str, list] = "Normal"
  soft_mask: Optional[Union[str, dict]] = None
  alpha_constant: float = 1.0
  alpha_source: bool = False

  # fields from table 53: Device Dependent Graphics State Parameters
  overprint: bool = False
  overprint_mode: float = 0.0
  black_generation: Union[str, Callable] = "???"
  undercolor_removal: Union[str, Callable] = "???"
  transfer: Union[str, list, Callable] = "???"
  halftone: Union[DictObject, Stream, str] = "???"
  flatness: float = 1.0
  smoothness: float = 0.0

def decode_pdf_hex(bs: bytes):
  ''' Decode a PDF hex string body.
  '''
  if len(bs) % 2:
    bs += b'0'
  return binascii.unhexlify(bs)

def decode_pdf_name(bs: bytes):
  ''' Decode a PDF name.
  '''
  NAME_HASHHEX_bre = re.compile(NAME_HASHHEX_re_bs)
  NAME_NONHASH_bre = re.compile(NAME_NONHASH_re_bs + br'+')
  bss = []
  offset = 0
  while offset < len(bs):
    offset0 = offset
    m = NAME_HASHHEX_bre.match(bs)
    if m:
      bss.append(binascii.unhexlify(m.group()[1:]))
      offset = m.end()
    else:
      m = NAME_NONHASH_bre.match(bs)
      bss.append(m.group())
      offset = m.end()
    assert offset > offset0
  return b''.join(bss)

def decode_pdf_simple_string(bs: bytes):
  ''' Decode a PDF simple string, one with no internal brackets.
  '''
  STRING_NONSLOSH_bre = re.compile(STRING_NONSLOSH_re_bs + br'*')
  STRING_SLOSHED_bre = re.compile(STRING_SLOSHED_re_bs)
  bss = []
  offset = 0
  while offset < len(bs):
    offset0 = offset
    m = STRING_SLOSHED_bre.match(bs, offset)
    if m:
      offset = m.end()
      mbs = m.group()
      assert mbs.startswith(b'\\')
      if mbs == br'\n':
        mbs = b'\n'
      elif mbs == br'\r':
        mbs = b'\r'
      elif mbs == br'\t':
        mbs = b'\t'
      elif mbs == br'\b':
        mbs = b'\b'
      elif mbs == br'\f':
        mbs = b'\f'
      elif mbs == br'\(':
        mbs = b'('
      elif mbs == br'\)':
        mbs = b')'
      elif mbs == b'\\\\':
        mbs = b'\\'
      elif mbs in (b'\\\r', b'\\\r\n', b'\\\n'):
        mbs = b''
      else:
        # \ooo
        mbs = bytes((int(mbs[1:], 8),))
    else:
      m = STRING_NONSLOSH_bre.match(bs, offset)
      offset = m.end()
      mbs = m.group()
    bss.append(mbs)
    assert offset > offset0
  return b''.join(bss)

def dctdecode_im(bs):
  ''' Decode `bs` using the `DCTDecode` filter.
      Returns the decoded `bytes` and a PIL `Image`.
  '''
  im = Image.open(BytesIO(bs), formats=('JPEG',))
  return bytes(chain(*im.getdata())), im

def dctdecode(bs):
  ''' Decode `bs` using the `DCTDecode` filter.
      Returns the decoded `bytes`.
  '''
  decoded_bs, im = dctdecode_im(bs)
  return decoded_bs

def flatedecode(bs):
  ''' Decode `bs` using the `FlateDecode` filter.
  '''
  return zlib.decompress(bs)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

#!/usr/bin/python
# - Cameron Simpson <cs@cskk.id.au>
#

''' Support for ID3 tags. Mostly a convenience wrapper for Doug Zongker's pyid3lib:
    http://pyid3lib.sourceforge.net/
'''

from threading import RLock
from types import SimpleNamespace
from cs.binary import SimpleBinary, BinarySingleValue, UInt32BE, UInt16BE
from cs.buffer import CornuCopyBuffer
from cs.logutils import info, debug, warning
from cs.pfx import Pfx
from cs.tagset import TagSet
from cs.threads import locked, locked_property

DISTINFO = {
    'description':
    "support for ID3 tags",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires':
    ['cs.binary', 'cs.buffer', 'cs.logutils', 'cs.pfx', 'cs.threads'],
}

# mapping from frameids to nice names ("artist" etc)
# taken from the id3c2.3.0 specification at:
#  http://id3.org/id3v2.3.0#Text_information_frames
#  http://id3.org/id3v2.4.0-frames
FRAMEID_ATTRS = {
    'AENC': ('audio_encryption',),
    'APIC': ('attached_picture',),
    'ASPI': ('audio_seek_point_index',),
    'COMM': ('comments',),
    'COMR': ('commercial',),
    'ENCR': ('encryption_method_registration',),
    'EQU2': ('equalisation2',),
    'ETCO': ('event_timing_codes',),
    'GEOB': ('general_encapsulated_object',),
    'GRID': ('group_identification_registration',),
    'LINK': ('linked_information',),
    'MCDI': ('music_cd_identifier',),
    'MLLT': ('mpeg_location_lookup_table',),
    'OWNE': ('ownership',),
    'PRIV': ('private',),
    'PCNT': ('play_count',),
    'POPM': ('popularimeter',),
    'POSS': ('position_synchronisation',),
    'RBUF': ('recommended_buffer_size',),
    'RVA2': ('relative_volume_adjustment',),
    'RVRB': ('reverb',),
    'SEEK': ('seek',),
    'SIGN': ('signature',),
    'SYLT': ('sync_lyric_transcription',),
    'SYTC': ('sync_tempo_codes',),
    'TALB': ('album_title', 'album', 'title'),
    'TBPM': ('bpm', 'beats_per_minute'),  # integer
    'TCOM': ('composer',),
    'TCON': ('content_type',),  # integer
    'TCOP': ('copyright',),  # "YYYY ..."
    'TDAT': ('date',),  # DDMM
    'TDEN': ('encoding_time',),
    'TDLY': ('playlist_delay',),  # milliseconds
    'TDOR': ('original_release_time',),
    'TDRC': ('recording_time',),
    'TDRL': ('release_time',),
    'TDTG': ('tagging_time',),
    'TENC': ('encoded_by',),
    'TEXT': ('lyrics',),  # slash separated
    'TFLT': ('file_type',),
    'TIME': ('time',),  # HHMM
    'TIPL': ('involved_people',),
    'TIT1': ('content_group_description', 'genre'),
    'TIT2': (
        'song_title',
        'songname',
        'content_description',
    ),  # eg "adagio"
    'TIT3': ('subtitle', 'description_refinement'),  # eg "Op. 16"
    'TKEY': ('initial_key',),  # musical key
    'TLAN': ('languages',),
    'TLEN': ('length',),  # milliseconds
    'TMCL': ('musician_credits',),
    'TMED': ('media_type',),
    'TMOO': ('mood',),
    'TOAL': ('original_album',),
    'TOFN': ('original_filename',),
    'TOLY': ('original_lyricist',),
    'TOPE': ('original_performer', 'original_artist', 'artist'),
    'TORY': ('original_release_year',),  # YYYY
    'TOWN': ('owner', 'file_owner', 'licensee'),
    'TPE1': ('lead_performer', 'lead_artist', 'soloist',
             'performing_group'),  # slash separated
    'TPE2': ('band', 'orchestra', 'accompaniment'),
    'TPE3': ('conductor',),
    'TPE4': ('interpreted_by', 'remixed_by', 'modified_by'),
    'TPOS': ('part_of_set',),  # eg 1/2
    'TPRO': ('produced_notice',),
    'TPUB': ('publisher',),
    'TRCK': ('track_number', 'position'),
    'TRDA': ('recording_dates',),
    'TRSN': ('radio_station_name',),
    'TRSO': ('radio_station_owner',),
    'TSIZ': ('size',),  # in bytes, excluding ID3 tag
    'TSOA': ('album_sort_order',),
    'TSOP': ('performer_sort_order',),
    'TSOT': ('title_sort-order',),
    'TSRC': ('isrc',),  # International Standard
    # Recording Code (ISRC) (12 characters).
    'TSSE': ('software_settings',),  # audio encoder and its settings
    'TSST': ('set_subtitle',),
    'TXXX': ('user_defined_text',),
    'TYER': ('year',),  # YYYY
    'UFID': ('ufid', 'unique_file_identifier'),
    'USER': ('terms_of_use',),
    'USLT': ('unsync_lyric_transcription',),
    'WCOM': ('commercial_information_url',),
    'WCOP': ('copyright_information_url', 'legal_information_url'),
    'WOAF': ('official_audio_file_url',),
    'WOAR': ('official_artist_url', 'official_performer_url'),
    'WOAS': ('official_audio_source_url',),
    'WORS': ('official_radio_station_url',),
    'WPAY': ('payment_url',),
    'WPUB': ('publishers_official_url',),
    'WXXX': ('user_defined_url',),
}

ATTR_FRAMEID = {}

def _fill_attr_frameid():
  for frame_id, attrs in FRAMEID_ATTRS.items():
    for attr in attrs:
      assert attr not in ATTR_FRAMEID, (
          "attr %r for frame_id %r already mapped to %r" %
          (attr, frame_id, ATTR_FRAMEID[attr])
      )
      ATTR_FRAMEID[attr] = frame_id

_fill_attr_frameid()

def parse_padded_text(bfr, length, encoding='ascii'):
  ''' Fetch `length` bytes from `bfr`,
      strip trailing NULs,
      decode using `encoding` (default `'ascii'`),
      strip trailling whitepsace.
  '''
  bs = bfr.take(length)
  text = bs.rstrip(b'\0').decode(encoding).rstrip()
  return text

def padded_text(text, length, encoding='ascii'):
  ''' Encode `text` using `encoding`,
      crop to a maximum of `length` bytes,
      pad with NULs to `length` if necessary.
  '''
  bs = text.encode(encoding)
  if len(bs) < length:
    bs += bytes(length - len(bs))
  else:
    bs = bs[:length]
  return bs

# TODO: mapping of genre_ids
class ID3V1Frame(SimpleBinary):
  ''' An ID3V1 or ID3v1.1 data frame as described in wikipedia:
      https://en.wikipedia.org/wiki/ID3

      The following fields are defined:
      * `title`: up to 30 ASCII characters
      * `artist`: up to 30 ASCII characters
      * `album`: up to 30 ASCII characters
      * `year`: 4 digit ASCII year
      * `comment`: up to 30 ASCII bytes, NUL or whitespace padded,
        up to 28 ASCII bytes if `track` is nonzero
      * `track`: 0 for no track, a number from 1 through 255 otherwise;
        if nonzero then the comment field may only be up to 28 bytes long
      * `genre_id`: a number value from 0 to 255
  '''

  @classmethod
  def parse(cls, bfr):
    ''' Parse a 128 byte ID3V1 or ID3v1.1 record.
    '''
    self = cls()
    # pylint: disable=attribute-defined-outside-init
    offset0 = bfr.offset
    hdr = bfr.take(3)
    if hdr != b'TAG':
      raise ValueError("expected leading b'TAG'")
    self.title = parse_padded_text(bfr, 30)
    self.artist = parse_padded_text(bfr, 30)
    self.album = parse_padded_text(bfr, 30)
    year_s = parse_padded_text(bfr, 4)
    if year_s == '':
      self.year = None
    else:
      try:
        self.year = int(year_s)
      except ValueError as e:
        warning("invalid year %r: %s", year_s, e)
        self.year = None
    comment_bs = bfr.take(30)
    if comment_bs[-2] == 0:
      self.track = comment_bs[-1]
      comment_bs = comment_bs[:-2]
    else:
      self.track = 0
    self.comment = comment_bs.decode('ascii').rstrip()
    self.genre_id = bfr.byte0()
    assert bfr.offset - offset0 == 128
    return self

  def transcribe(self):
    ''' Transcribe the ID3V1 frame.
    '''
    yield b'TAG'
    yield padded_text(self.title, 30)
    yield padded_text(self.artist, 30)
    yield padded_text(self.album, 30)
    yield padded_text('' if self.year is None else str(self.year), 4)
    yield padded_text(self.comment, 28 if self.track > 0 else 30)
    if self.track > 0:
      yield bytes([0, self.track])
    yield bytes([self.genre_id])

  def tagset(self):
    ''' Return a `TagSet` with the ID3 tag information.
    '''
    return TagSet(
        {
            k: v
            for k, v in dict(
                title=self.title,
                artist=self.artist,
                album=self.album,
                year=self.year,
                comment=self.comment,
                track=self.track,
                genre_id=self.genre_id,
            ).items()
            if v is not None and not (isinstance(v, int) and v == 0)
        }
    )

class EnhancedTagFrame(SimpleBinary):
  ''' An Enhanced Tag.
  '''

  @classmethod
  def parse(cls, bfr):
    ''' Parse an enhanced tag.
    '''
    self = cls()
    # pylint: disable=attribute-defined-outside-init
    offset0 = bfr.offset
    hdr = bfr.peek(4)
    if hdr != b'TAG+':
      raise ValueError("expected leading b'TAG+'")
    bfr.take(4)
    self.title = parse_padded_text(bfr, 60)
    self.artist = parse_padded_text(bfr, 60)
    self.album = parse_padded_text(bfr, 60)
    self.speed_code = bfr.byte0()
    self.genre = parse_padded_text(bfr, 30)
    start_mmm, start_ss = map(bfr.take(6).decode('ascii').split(':'))
    self.start_s = start_mmm * 60 + start_ss
    end_mmm, end_ss = map(bfr.take(6).decode('ascii').split(':'))
    self.end_s = end_mmm * 60 + end_ss
    assert bfr.offset - offset0 == 227
    return self

  def transcribe(self):
    ''' Transcribe the enhanced tag.
    '''
    yield b'TAG+'
    yield padded_text(self.title, 60)
    yield padded_text(self.artist, 60)
    yield padded_text(self.album, 60)
    yield bytes([self.speed_code])
    yield padded_text(self.genre, 30)
    start_text = "%03d:%2d" % (self.start_time // 60, self.start_time % 60)
    assert len(start_text) == 6
    yield start_text
    end_text = "%03d:%2d" % (self.end_time // 60, self.end_time % 60)
    assert len(end_text) == 6
    yield end_text

class ID3V2Size(BinarySingleValue):
  ''' An ID3v2 size field,
      a big endian 4 byte field of 7-bit values, high bit 0.
  '''

  @staticmethod
  def check_size_bytes(size_bs):
    if any(map(lambda b: b & 0x80, size_bs)):
      raise ValueError(
          "invalid size bytes, some have the high bit set: %r" %
          (list(map(lambda b: "0x%02x" % b, size_bs)))
      )

  @classmethod
  def parse_value(cls, bfr):
    ''' Read an ID3V2 size field from `bfr`, return the size.
    '''
    size_bs = bfr.take(4)
    cls.check_size_bytes(size_bs)
    return size_bs[0] << 21 | size_bs[1] << 14 | size_bs[2] << 7 | size_bs[3]

  @classmethod
  def transcribe_value(cls, size):
    ''' Transcribe a size in ID3v2 format.
    '''
    if size < 0 or size >= 1 << 28:
      raise ValueError("size %d out of range" % (size,))
    size_bs = bytes(
        [
            (size >> 21) & 0x7f,
            (size >> 14) & 0x7f,
            (size >> 7) & 0x7f,
            size & 0x7f,
        ]
    )
    cls.check_size_bytes(size_bs)
    return size_bs

class ISO8859_1NULString(BinarySingleValue):
  ''' A NUL terminated string encoded with ISO8859-1.
  '''
  ENCODING_BYTE = bytes([0])
  ENCODING = 'iso8859-1'

  @classmethod
  def parse_value(cls, bfr):
    bs = []
    while not bfr.at_eof():
      b = bfr.byte0()
      if b == 0:
        break
      bs.append(b)
    s = bytes(bs).decode(cls.ENCODING)
    return s

  # pylint: disable=arguments-differ
  @classmethod
  def transcribe_value(cls, s):
    yield s.encode(cls.ENCODING)
    yield b'\0'

class UCS2NULString(BinarySingleValue):
  ''' A NUL terminated string encoded with UCS-2.

      We're cheating and using UTF16 for this.
  '''
  ENCODING_BYTE = bytes([1])
  DEFAULT_BOM = b'\xfe\xff'
  DEFAULT_ENCODING = 'utf-16-le'

  @classmethod
  def parse_value(cls, bfr):
    bs = []
    bom = bfr.take(2)
    if bom == b'\x00\x00':
      return ''
    if bom == b'\xff\xfe':
      encoding = 'utf-16-le'
    elif bom == b'\xff\xfe':
      encoding = 'utf-16-be'
    else:
      warning("no BOM, assuming %r", cls.DEFAULT_ENCODING)
      bs.extend(bom)
      encoding = cls.DEFAULT_ENCODING
    while not bfr.at_eof():
      b2 = bfr.take(2)
      if b2 == b'\0\0':
        break
      bs.extend(b2)
    # decode and strip the leading BOM
    s = bytes(bs).decode(encoding)
    return s

  # pylint: disable=arguments-differ
  @classmethod
  def transcribe_value(cls, s):
    ''' Transcribe text as UTF-16-LE with leading BOM and trailing NUL.
    '''
    # always use the same output encoding, avoids saving the BOM
    yield cls.DEFAULT_BOM
    yield s.encode(cls.DEFAULT_ENCODING)
    yield bytes([0, 0])

class TextEncodingClass(BinarySingleValue):
  ''' A trite class to parse the single byte text encoding field.
  '''

  @staticmethod
  def parse_value(bfr):
    encoding_b = bfr.byte0()
    if encoding_b == 0:
      return ISO8859_1NULString
    if encoding_b == 1:
      return UCS2NULString
    raise ValueError(
        "invalid text encoding byte %r, should be 0 or 1" % (encoding_b,)
    )

  @staticmethod
  def transcribe_value(text_class):
    return text_class.ENCODING_BYTE

class TextInformationFrameBody(SimpleBinary):
  ''' A text information frame.
  '''

  @classmethod
  def parse(cls, bfr):
    ''' Parse the text from the frame.
    '''
    self = cls()
    # pylint: disable=attribute-defined-outside-init
    self.text_class = TextEncodingClass.parse_value(bfr)
    self.value = self.text_class.parse_value(bfr)
    return self

  def transcribe(self):
    ''' Transcribe the frame.
    '''
    yield self.text_class.ENCODING_BYTE
    yield self.text_class.transcribe_value(self.value)

class ID3V22TagDataFrame(SimpleBinary):
  ''' An ID3 v2.2.0 tag data frame.

      Reference doc: https://id3.org/id3v2-00
  '''

  # tag ids and meanings from section 4 of the doc: Declared ID3v2 frames.
  TAG_ID_INFO = {
      b'BUF': (str, 'Recommended buffer size (section 4.19)'),
      b'CNT': (str, 'Play counter (section 4.17)'),
      b'COM': (str, 'Comments (section 4.11)'),
      b'CRA': (str, 'Audio encryption (section 4.21)'),
      b'CRM': (str, 'Encrypted meta frame (section 4.20)'),
      b'EQU': (str, 'Equalization (section 4.13)'),
      b'ETC': (str, 'Event timing codes (section 4.6)'),
      b'GEO': (str, 'General encapsulated object (section 4.16)'),
      b'IPL': (str, 'Involved people list (section 4.4)'),
      b'LNK': (str, 'Linked information (section 4.22)'),
      b'MCI': (str, 'Music CD Identifier (section 4.5)'),
      b'MLL': (str, 'MPEG location lookup table (section 4.7)'),
      b'PIC': (str, 'Attached picture (section 4.15)'),
      b'POP': (str, 'Popularimeter (section 4.18)'),
      b'REV': (str, 'Reverb (section 4.14)'),
      b'RVA': (str, 'Relative volume adjustment (section 4.12)'),
      b'SLT': (str, 'Synchronized lyric/text (section 4.10)'),
      b'STC': (str, 'Synced tempo codes (section 4.8)'),
      b'TAL': (str, 'Album/Movie/Show title (section 4.2.1)'),
      b'TBP': (str, 'BPM (Beats Per Minute) (section 4.2.1)'),
      b'TCM': (TextInformationFrameBody, 'Composer (section 4.2.1)'),
      b'TCO': (TextInformationFrameBody, 'Content type (section 4.2.1)'),
      b'TCR': (TextInformationFrameBody, 'Copyright message (section 4.2.1)'),
      b'TDA': (TextInformationFrameBody, 'Date (section 4.2.1)'),
      b'TDY': (TextInformationFrameBody, 'Playlist delay (section 4.2.1)'),
      b'TEN': (TextInformationFrameBody, 'Encoded by (section 4.2.1)'),
      b'TFT': (TextInformationFrameBody, 'File type (section 4.2.1)'),
      b'TIM': (TextInformationFrameBody, 'Time (section 4.2.1)'),
      b'TKE': (TextInformationFrameBody, 'Initial key (section 4.2.1)'),
      b'TLA': (TextInformationFrameBody, 'Language(s) (section 4.2.1)'),
      b'TLE': (TextInformationFrameBody, 'Length (section 4.2.1)'),
      b'TMT': (TextInformationFrameBody, 'Media type (section 4.2.1)'),
      b'TOA': (
          TextInformationFrameBody,
          'Original artist(s)/performer(s) (section 4.2.1)'
      ),
      b'TOF': (TextInformationFrameBody, 'Original filename (section 4.2.1)'),
      b'TOL': (
          TextInformationFrameBody,
          'Original Lyricist(s)/text writer(s) (section 4.2.1)'
      ),
      b'TOR':
      (TextInformationFrameBody, 'Original release year (section 4.2.1)'),
      b'TOT': (
          TextInformationFrameBody,
          'Original album/Movie/Show title (section 4.2.1)'
      ),
      b'TP1': (
          TextInformationFrameBody,
          'Lead artist(s)/Lead performer(s)/Soloist(s)/Performing group (section 4.2.1)'
      ),
      b'TP2': (
          TextInformationFrameBody,
          'Band/Orchestra/Accompaniment (section 4.2.1)'
      ),
      b'TP3': (
          TextInformationFrameBody,
          'Conductor/Performer refinement (section 4.2.1)'
      ),
      b'TP4': (
          TextInformationFrameBody,
          'Interpreted, remixed, or otherwise modified by (section 4.2.1)'
      ),
      b'TPA': (TextInformationFrameBody, 'Part of a set (section 4.2.1)'),
      b'TPB': (TextInformationFrameBody, 'Publisher (section 4.2.1)'),
      b'TRC': (
          TextInformationFrameBody,
          'ISRC (International Standard Recording Code) (section 4.2.1)'
      ),
      b'TRD': (TextInformationFrameBody, 'Recording dates (section 4.2.1)'),
      b'TRK': (
          TextInformationFrameBody,
          'Track number/Position in set (section 4.2.1)'
      ),
      b'TSI': (TextInformationFrameBody, 'Size (section 4.2.1)'),
      b'TSS': (
          TextInformationFrameBody,
          'Software/hardware and settings used for encoding (section 4.2.1)'
      ),
      b'TT1':
      (TextInformationFrameBody, 'Content group description (section 4.2.1)'),
      b'TT2': (
          TextInformationFrameBody,
          'Title/Songname/Content description (section 4.2.1)'
      ),
      b'TT3': (
          TextInformationFrameBody,
          'Subtitle/Description refinement (section 4.2.1)'
      ),
      b'TXT':
      (TextInformationFrameBody, 'Lyricist/text writer (section 4.2.1)'),
      b'TXX': (str, 'User defined text information frame (section 4.2.2)'),
      b'TYE': (TextInformationFrameBody, 'Year (section 4.2.1)'),
      b'UFI': (str, 'Unique file identifier (section 4.1)'),
      b'ULT': (str, 'Unsychronized lyric/text transcription (section 4.9)'),
      b'WAF': (str, 'Official audio file webpage (section 4.3.1)'),
      b'WAR': (str, 'Official artist/performer webpage (section 4.3.1)'),
      b'WAS': (str, 'Official audio source webpage (section 4.3.1)'),
      b'WCM': (str, 'Commercial information (section 4.3.1)'),
      b'WCP': (str, 'Copyright/Legal information (section 4.3.1)'),
      b'WPB': (str, 'Publishers official webpage (section 4.3.1)'),
      b'WXX': (str, 'User defined URL link frame (section 4.3.2)'),
  }

  @classmethod
  def tag_id_class(cls, tag_id):
    ''' Return the `AbstractBinary` subclass to decode the a tag body from its tag id.
        Return `None` for unrecognised ids.
    '''
    try:
      data_type, _ = cls.TAG_ID_INFO[tag_id]
    except KeyError:
      if b'T00' <= tag_id <= b'TZZ':
        data_type = TextInformationFrameBody
      else:
        data_type = None
    return data_type

  @classmethod
  def parse(cls, bfr):
    self = cls()
    # pylint: disable=attribute-defined-outside-init
    self.tag_id = bfr.take(3)
    with Pfx(self.tag_id):
      sz0, sz1, sz2 = bfr.take(3)
      size = sz0 << 16 | sz1 << 8 | sz2
      if size < 1:
        warning("size < 1")
      else:
        data_bs = bfr.take(size)
      if not data_bs or data_bs[0] == 0:
        # forbidden empty data or data zeroed out
        data_type = None
      else:
        data_type = self.tag_id_class(self.tag_id)
      if data_type is None:
        self.value = data_bs
      else:
        databfr = CornuCopyBuffer([data_bs])
        self.value = data_type.parse(databfr)
        if not databfr.at_eof():
          warning("unparsed data: %r" % (databfr.take(...),))
    return self

  def transcribe(self):
    assert isinstance(self.tag_id, bytes) and len(self.tag_id) == 3
    data_bs = bytes(self.value)
    size = len(data_bs)
    sz2 = size & 0xff
    size >>= 8
    sz1 = size & 0xff
    size >>= 8
    sz0 = size & 0xff
    size >>= 8
    if size:
      raise ValueError(
          "data too large for a 3 byte size field: %d bytes" % (len(data_bs),)
      )
    yield self.tag_id
    yield bytes([sz0, sz1, sz2])
    yield data_bs

class ID3V23TagDataFrame(SimpleBinary):
  ''' An ID3 v2.3.0 tag data frame.

      Reference doc: https://id3.org/id3v2.3.0
  '''

  # tag ids and meanings from section 4 of the doc: Declared ID3v2 frames
  # https://id3.org/id3v2.3.0#Declared_ID3v2_frames
  TAG_ID_INFO = {
      b'AENC': (str, '[#sec4.20|Audio encryption] (section 4.20)'),
      b'APIC': (str, '#sec4.15 Attached picture (section 4.15)'),
      b'COMM': (str, '#sec4.11 Comments (section 4.11)'),
      b'COMR': (str, '#sec4.25 Commercial frame (section 4.25)'),
      b'ENCR': (str, '#sec4.26 Encryption method registration (section 4.26)'),
      b'EQUA': (str, '#sec4.13 Equalization (section 4.13)'),
      b'ETCO': (str, '#sec4.6 Event timing codes (section 4.6)'),
      b'GEOB': (str, '#sec4.16 General encapsulated object (section 4.16)'),
      b'GRID':
      (str, '#sec4.27 Group identification registration (section 4.27)'),
      b'IPLS': (str, '#sec4.4 Involved people list (section 4.4)'),
      b'LINK': (str, '#sec4.21 Linked information (section 4.21)'),
      b'MCDI': (str, '#sec4.5 Music CD identifier (section 4.5)'),
      b'MLLT': (str, '#sec4.7 MPEG location lookup table (section 4.7)'),
      b'OWNE': (str, '#sec4.24 Ownership frame (section 4.24)'),
      b'PRIV': (str, '#sec4.28 Private frame (section 4.28)'),
      b'PCNT': (str, '#sec4.17 Play counter (section 4.17)'),
      b'POPM': (str, '#sec4.18 Popularimeter (section 4.18)'),
      b'POSS': (str, '#sec4.22 Position synchronisation frame (section 4.22)'),
      b'RBUF': (str, '#sec4.19 Recommended buffer size (section 4.19)'),
      b'RVAD': (str, '#sec4.12 Relative volume adjustment (section 4.12)'),
      b'RVRB': (str, '#sec4.14 Reverb (section 4.14)'),
      b'SYLT': (str, '#sec4.10 Synchronized lyric/text (section 4.10)'),
      b'SYTC': (str, '#sec4.8 Synchronized tempo codes (section 4.8)'),
      b'TALB': (
          TextInformationFrameBody,
          '#TALB Album/Movie/Show title (section 4.2.1)'
      ),
      b'TBPM': (
          TextInformationFrameBody,
          '#TBPM BPM (beats per minute) (section 4.2.1)'
      ),
      b'TCOM': (TextInformationFrameBody, '#TCOM Composer (section 4.2.1)'),
      b'TCON':
      (TextInformationFrameBody, '#TCON Content type (section 4.2.1)'),
      b'TCOP':
      (TextInformationFrameBody, '#TCOP Copyright message (section 4.2.1)'),
      b'TDAT': (TextInformationFrameBody, '#TDAT Date (section 4.2.1)'),
      b'TDLY':
      (TextInformationFrameBody, '#TDLY Playlist delay (section 4.2.1)'),
      b'TENC': (TextInformationFrameBody, '#TENC Encoded by (section 4.2.1)'),
      b'TEXT':
      (TextInformationFrameBody, '#TEXT Lyricist/Text writer (section 4.2.1)'),
      b'TFLT': (TextInformationFrameBody, '#TFLT File type (section 4.2.1)'),
      b'TIME': (TextInformationFrameBody, '#TIME Time (section 4.2.1)'),
      b'TIT1': (
          TextInformationFrameBody,
          '#TIT1 Content group description (section 4.2.1)'
      ),
      b'TIT2': (
          TextInformationFrameBody,
          '#TIT2 Title/songname/content description (section 4.2.1)'
      ),
      b'TIT3': (
          TextInformationFrameBody,
          '#TIT3 Subtitle/Description refinement (section 4.2.1)'
      ),
      b'TKEY': (TextInformationFrameBody, '#TKEY Initial key (section 4.2.1)'),
      b'TLAN': (TextInformationFrameBody, '#TLAN Language(s) (section 4.2.1)'),
      b'TLEN': (TextInformationFrameBody, '#TLEN Length (section 4.2.1)'),
      b'TMED': (TextInformationFrameBody, '#TMED Media type (section 4.2.1)'),
      b'TOAL': (
          TextInformationFrameBody,
          '#TOAL Original album/movie/show title (section 4.2.1)'
      ),
      b'TOFN':
      (TextInformationFrameBody, '#TOFN Original filename (section 4.2.1)'),
      b'TOLY': (
          TextInformationFrameBody,
          '#TOLY Original lyricist(s)/text writer(s) (section 4.2.1)'
      ),
      b'TOPE': (
          TextInformationFrameBody,
          '#TOPE Original artist(s)/performer(s) (section 4.2.1)'
      ),
      b'TORY': (
          TextInformationFrameBody,
          '#TORY Original release year (section 4.2.1)'
      ),
      b'TOWN':
      (TextInformationFrameBody, '#TOWN File owner/licensee (section 4.2.1)'),
      b'TPE1': (
          TextInformationFrameBody,
          '#TPE1 Lead performer(s)/Soloist(s) (section 4.2.1)'
      ),
      b'TPE2': (
          TextInformationFrameBody,
          '#TPE2 Band/orchestra/accompaniment (section 4.2.1)'
      ),
      b'TPE3': (
          TextInformationFrameBody,
          '#TPE3 Conductor/performer refinement (section 4.2.1)'
      ),
      b'TPE4': (
          TextInformationFrameBody,
          '#TPE4 Interpreted, remixed, or otherwise modified by (section 4.2.1)'
      ),
      b'TPOS':
      (TextInformationFrameBody, '#TPOS Part of a set (section 4.2.1)'),
      b'TPUB': (TextInformationFrameBody, '#TPUB Publisher (section 4.2.1)'),
      b'TRCK': (
          TextInformationFrameBody,
          '#TRCK Track number/Position in set (section 4.2.1)'
      ),
      b'TRDA':
      (TextInformationFrameBody, '#TRDA Recording dates (section 4.2.1)'),
      b'TRSN': (
          TextInformationFrameBody,
          '#TRSN Internet radio station name (section 4.2.1)'
      ),
      b'TRSO': (
          TextInformationFrameBody,
          '#TRSO Internet radio station owner (section 4.2.1)'
      ),
      b'TSIZ': (TextInformationFrameBody, '#TSIZ Size (section 4.2.1)'),
      b'TSRC': (
          TextInformationFrameBody,
          '#TSRC ISRC (international standard recording code) (section 4.2.1)'
      ),
      b'TSSE': (
          TextInformationFrameBody,
          '#TSEE Software/Hardware and settings used for encoding (section 4.2.1)'
      ),
      b'TYER': (TextInformationFrameBody, '#TYER Year (section 4.2.1)'),
      b'TXXX':
      (str, '#TXXX User defined text information frame (section 4.2.2)'),
      b'UFID': (str, '#sec4.1 Unique file identifier (section 4.1)'),
      b'USER': (str, '#sec4.23 Terms of use (section 4.23)'),
      b'USLT':
      (str, '#sec4.9 Unsychronized lyric/text transcription (section 4.9)'),
      b'WCOM': (str, '#WCOM Commercial information (section 4.3.1)'),
      b'WCOP': (str, '#WCOP Copyright/Legal information (section 4.3.1)'),
      b'WOAF': (str, '#WOAF Official audio file webpage (section 4.3.1)'),
      b'WOAR':
      (str, '#WOAR Official artist/performer webpage (section 4.3.1)'),
      b'WOAS': (str, '#WOAS Official audio source webpage (section 4.3.1)'),
      b'WORS':
      (str, '#WORS Official internet radio station homepage (section 4.3.1)'),
      b'WPAY': (str, '#WPAY Payment (section 4.3.1)'),
      b'WPUB': (str, '#WPUB Publishers official webpage (section 4.3.1)'),
      b'WXXX': (str, '#WXXX User defined URL link frame (section 4.3.2)'),
  }

  @classmethod
  def tag_id_class(cls, tag_id):
    ''' Return the `AbstractBinary` subclass to decode the a tag body from its tag id.
        Return `None` for unrecognised ids.
    '''
    try:
      data_type, _ = cls.TAG_ID_INFO[tag_id]
    except KeyError:
      if b'T00' <= tag_id <= b'TZZ':
        data_type = TextInformationFrameBody
      else:
        data_type = None
    return data_type

  @classmethod
  def parse(cls, bfr):
    self = cls()
    # pylint: disable=attribute-defined-outside-init
    self.tag_id = bfr.take(4)
    with Pfx(self.tag_id):
      size = UInt32BE.parse_value(bfr)
      self.flags = UInt16BE.parse_value(bfr)
      if size < 1:
        warning("size < 1")
      else:
        data_bs = bfr.take(size)
      data_type = self.tag_id_class(self.tag_id)
      if data_type is None:
        self.dataframe_body = data_bs
      else:
        databfr = CornuCopyBuffer([data_bs])
        self.datafrome_body = data_type.parse(databfr)
        if not databfr.at_eof():
          warning("unparsed data: %r" % (databfr.take(...),))
    return self

  def transcribe(self):
    assert isinstance(self.tag_id, bytes) and len(self.tag_id) == 4
    data_bs = bytes(self.datafrome_body)
    size = len(data_bs)
    yield self.tag_id
    yield UInt32BE.transcribe_value(size)
    yield UInt16BE.transcribe_value(self.flags)
    yield data_bs

class ID3V2Frame(SimpleBinary):
  ''' An ID3v2 frame, based on the document at:
      https://web.archive.org/web/20120527211939/http://www.unixgods.org/~tilo/ID3/docs/id3v2-00.html
  '''

  @classmethod
  def parse(cls, bfr):
    ''' Return an ID3v2 frame as described here:
    '''
    self = cls()
    # pylint: disable=attribute-defined-outside-init
    if bfr.peek(3, short_ok=True) != b'ID3':
      raise ValueError("expected b'ID3'")
    bfr.take(3)
    # the 2.0 part of ID3.2.0
    self.v1, self.v2 = bfr.take(2)
    self.flags = bfr.byte0()
    size = ID3V2Size.parse_value(bfr)
    data_bs = bfr.take(size)
    data_bfr = CornuCopyBuffer([data_bs])
    dataframe_class = {2: ID3V22TagDataFrame, 3: ID3V23TagDataFrame}[self.v1]
    self.tag_frames = list(dataframe_class.scan(data_bfr))
    return self

  def transcribe(self):
    ''' Transcribe the ID3v2 frame.
    '''
    yield b'ID3'
    yield bytes([self.v1, self.v2, self.flags])
    tag_frames_bss = list(map(bytes, self.tag_frames))
    size = sum(map(len, tag_frames_bss))
    yield ID3V2Size(size)
    yield tag_frames_bss

  def tagset(self):
    ''' Return a `TagSet` with the ID3 tag information.
    '''
    tags = TagSet()
    for tag_frame in self.tag_frames:
      tag_id = tag_frame.tag_id.decode('ascii').lower()
      tags.set(tag_id, tag_frame.datafrome_body.value)
      if tag_frame.flags != 0:
        tags.set(f"{tag_id}.flags", tag_frame.flags)
    return tags

class ID3V2Tags(SimpleNamespace):
  ''' An `ID3V2Tags` maps ID3V2 tag information as a `SimpleNamespace`.
  '''

  def __getattr__(self, attr):
    ''' Catch frame id attrs and their wordier versions.
    '''
    if attr in FRAMEID_ATTRS:
      raise AttributeError(type(self).__name__ + '.' + attr)
    try:
      frame_id = ATTR_FRAMEID[attr]
    except KeyError:
      # pylint: disable=raise-missing-from
      raise AttributeError(type(self).__name__ + '.' + attr)
    return getattr(self, frame_id)

  def __setattr__(self, attr, value):
    ''' Map attributes to frame ids, set the corresponding `__dict__` entry.
    '''
    if attr in FRAMEID_ATTRS:
      self.__dict__[attr] = value
    else:
      try:
        frame_id = ATTR_FRAMEID[attr]
      except KeyError:
        # pylint: disable=raise-missing-from
        raise AttributeError(type(self).__name__ + '.' + attr)
      self.__dict__[frame_id] = value

class ID3(SimpleNamespace):
  ''' Wrapper for pyid3lib.tag.

      OBSOLETE.
      Going away when I'm sure the other classes cover all this stuff off.
  '''

  # mapping from frameids to nice names ("artist" etc)
  # taken from the id3c2.3.0 specification at:
  #  http://id3.org/id3v2.3.0#Text_information_frames
  #  http://id3.org/id3v2.4.0-frames
  frameids_to_names = {
      'AENC': ('audio_encryption',),
      'APIC': ('attached_picture',),
      'ASPI': ('audio_seek_point_index',),
      'COMM': ('comments',),
      'COMR': ('commercial',),
      'ENCR': ('encryption_method_registration',),
      'EQU2': ('equalisation2',),
      'ETCO': ('event_timing_codes',),
      'GEOB': ('general_encapsulated_object',),
      'GRID': ('group_identification_registration',),
      'LINK': ('linked_information',),
      'MCDI': ('music_cd_identifier',),
      'MLLT': ('mpeg_location_lookup_table',),
      'OWNE': ('ownership',),
      'PRIV': ('private',),
      'PCNT': ('play_count',),
      'POPM': ('popularimeter',),
      'POSS': ('position_synchronisation',),
      'RBUF': ('recommended_buffer_size',),
      'RVA2': ('relative_volume_adjustment',),
      'RVRB': ('reverb',),
      'SEEK': ('seek',),
      'SIGN': ('signature',),
      'SYLT': ('sync_lyric_transcription',),
      'SYTC': ('sync_tempo_codes',),
      'TALB': ('album_title', 'album', 'title'),
      'TBPM': ('bpm', 'beats_per_minute'),  # integer
      'TCOM': ('composer',),
      'TCON': ('content_type',),  # integer
      'TCOP': ('copyright',),  # "YYYY ..."
      'TDAT': ('date',),  # DDMM
      'TDEN': ('encoding_time',),
      'TDLY': ('playlist_delay',),  # milliseconds
      'TDOR': ('original_release_time',),
      'TDRC': ('recording_time',),
      'TDRL': ('release_time',),
      'TDTG': ('tagging_time',),
      'TENC': ('encoded_by',),
      'TEXT': ('lyrics',),  # slash separated
      'TFLT': ('file_type',),
      'TIME': ('time',),  # HHMM
      'TIPL': ('involved_people',),
      'TIT1': ('content_group_description', 'genre'),
      'TIT2': (
          'song_title',
          'songname',
          'content_description',
      ),  # eg "adagio"
      'TIT3': ('subtitle', 'description_refinement'),  # eg "Op. 16"
      'TKEY': ('initial_key',),  # musical key
      'TLAN': ('languages',),
      'TLEN': ('length',),  # milliseconds
      'TMCL': ('musician_credits',),
      'TMED': ('media_type',),
      'TMOO': ('mood',),
      'TOAL': ('original_album',),
      'TOFN': ('original_filename',),
      'TOLY': ('original_lyricist',),
      'TOPE': ('original_performer', 'original_artist', 'artist'),
      'TORY': ('original_release_year',),  # YYYY
      'TOWN': ('owner', 'file_owner', 'licensee'),
      'TPE1': ('lead_performer', 'lead_artist', 'soloist',
               'performing_group'),  # slash separated
      'TPE2': ('band', 'orchestra', 'accompaniment'),
      'TPE3': ('conductor',),
      'TPE4': ('interpreted_by', 'remixed_by', 'modified_by'),
      'TPOS': ('part_of_set',),  # eg 1/2
      'TPRO': ('produced_notice',),
      'TPUB': ('publisher',),
      'TRCK': ('track_number', 'position'),
      'TRDA': ('recording_dates',),
      'TRSN': ('radio_station_name',),
      'TRSO': ('radio_station_owner',),
      'TSIZ': ('size',),  # in bytes, excluding ID3 tag
      'TSOA': ('album_sort_order',),
      'TSOP': ('performer_sort_order',),
      'TSOT': ('title_sort-order',),
      'TSRC': ('isrc',),  # International Standard
      # Recording Code (ISRC) (12 characters).
      'TSSE': ('software_settings',),  # audio encoder and its settings
      'TSST': ('set_subtitle',),
      'TXXX': ('user_defined_text',),
      'TYER': ('year',),  # YYYY
      'UFID': ('ufid', 'unique_file_identifier'),
      'USER': ('terms_of_use',),
      'USLT': ('unsync_lyric_transcription',),
      'WCOM': ('commercial_information_url',),
      'WCOP': ('copyright_information_url', 'legal_information_url'),
      'WOAF': ('official_audio_file_url',),
      'WOAR': ('official_artist_url', 'official_performer_url'),
      'WOAS': ('official_audio_source_url',),
      'WORS': ('official_radio_station_url',),
      'WPAY': ('payment_url',),
      'WPUB': ('publishers_official_url',),
      'WXXX': ('user_defined_url',),
  }

  names_to_frameids = {}
  for frameid, names in frameids_to_names.items():
    for name in names:
      if name in names_to_frameids:
        warning(
            "name %r already associated with frameid %r,"
            " discarding mapping to frameid %r", name, names_to_frameids[name],
            frameid
        )
      else:
        names_to_frameids[name] = frameid

  def __init__(self, pathname):
    import pyid3lib
    self.__dict__['pathname'] = pathname
    self.__dict__['modified'] = False
    self._lock = RLock()

  @locked_property
  def tag(self):
    ''' The tag mapping from `self.pathname`.
    '''
    import pyid3lib
    return pyid3lib.tag(self.pathname)

  @staticmethod
  def _valid_frameid(frameid):
    ''' Test lexical validity of a `frameid`.
    '''
    return (
        len(frameid) == 4 and frameid[0].isupper() and all(
            frameid_char.isupper() or frameid_char.isdigit()
            for frameid_char in frameid[1:]
        )
    )

  @staticmethod
  def _frame(frameid, text, textenc=0):
    ''' Construct a new frame with the specified `frameid`,
        `text` and optional `textenc` (default 0).
    '''
    if not ID3._valid_frameid(frameid):
      raise ValueError(
          "invalid frameid, expected UPPER+3*(UPPER|DIGIT), got: %r" %
          (frameid,)
      )
    return {'text': text, 'textenc': textenc, 'frameid': frameid}

  @locked
  def _update_frame(self, frameid, newtext):
    ''' Set frame identified by `frameid` to have text `newtext`.
        Set self.modified to True if `newtext` is different from any existing text.
    '''
    frame = self.get_frame(frameid)
    if frame is None:
      info("%s: NEW %r", frameid, newtext)
      frame = self._frame(frameid, newtext)
      self.tag.append(frame)
      self.modified = True
    else:
      oldtext = frame['text']
      if oldtext == newtext:
        debug("%s: UNCHANGED %r", frameid, oldtext)
        return
      info("%s: UPDATE %r => %r", frameid, oldtext, newtext)
      frame['text'] = newtext
      self.tag[self.tag.index(frameid)] = frame
      self.modified = True

  @locked
  def flush(self):
    ''' Update the ID3 tags in the file if modified, otherwise no-op.
        Clears the modified flag.
    '''
    if self.modified:
      self.tag.update()
      self.modified = False

  def get_frame(self, frameid):
    ''' Return the frame with the specified `frameid`, or None.
    '''
    try:
      framendx = self.tag.index(frameid)
    except ValueError:
      return None
    return self.tag[framendx]

  def __getitem__(self, key):
    ''' Fetch the text of the specified frame.
    '''
    if self._valid_frameid(key):
      frameid = key
      frame = self.get_frame(frameid)
      if frame is None:
        raise KeyError(".%s: no such frame" % (frameid,))
      return frame['text']
    frameid = ID3.names_to_frameids.get(key)
    debug("names_to_frameids.get(%r) ==> %r", key, frameid)
    if frameid is None:
      raise KeyError(".%s: no mapping to a frameid" % (key,))
    return self[frameid]

  def __getattr__(self, attr):
    if attr.startswith('_'):
      raise AttributeError(".%s missing" % (attr,))
    try:
      return self[attr]
    except KeyError:
      return ''

  def __setitem__(self, key, value):
    ''' Set a frame text to `value`.
    '''
    debug("%s: SET TO %r", key, value)
    if self._valid_frameid(key):
      self._update_frame(key, value)
    else:
      frameid = ID3.names_to_frameids.get(key)
      if frameid is None:
        raise KeyError(".%s: no mapping to a frameid" % (key,))
      self[frameid] = value

  def __setattr__(self, attr, value):
    if attr in self.__dict__ or attr.startswith('_'):
      if (attr in ID3.frameids_to_names or attr in ID3.names_to_frameids):
        warning(".%s: local to object, shadows id3 tag", attr)
      self.__dict__[attr] = value
      return
    self[attr] = value

  def clean(self, attr):
    ''' Strip NULs and leading and trailing whitespace.
    '''
    self[attr] = self[attr].rstrip('\0').strip()

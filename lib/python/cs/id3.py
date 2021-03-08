#!/usr/bin/python
# - Cameron Simpson <cs@cskk.id.au>
#

''' Support for ID3 tags. Mostly a convenience wrapper for Doug Zongker's pyid3lib:
    http://pyid3lib.sourceforge.net/
'''

from threading import RLock
from types import SimpleNamespace
from cs.logutils import info, debug, warning
from cs.threads import locked, locked_property

DISTINFO = {
    'description':
    "support for ID3 tags, mostly a convenience wrapper for Doug Zongker's pyid3lib",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.logutils', 'cs.threads'],
}

class ID3(SimpleNamespace):
  ''' Wrapper for pyid3lib.tag.
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

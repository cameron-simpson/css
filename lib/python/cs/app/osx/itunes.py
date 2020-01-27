#!/usr/bin/env python3
#
# Convenience iTunes related functions.
# - Cameron Simpson <cs@cskk.id.au> 05jan2013
#

''' Convenience iTunes related functions.
'''

from base64 import b64decode
from datetime import datetime
try:
  from lxml import etree
except ImportError:
  import xml.etree.ElementTree as etree
import os
from os.path import join as joinpath
from threading import RLock
from cs.logutils import setup_logging, warning
from cs.mappings import named_column_tuples, dicts_to_namedtuples
from cs.pfx import Pfx, pfx_method
from cs.threads import locked_property
from cs.x import X

ITUNES_LIBRARY_PATH_ENVVAR = 'ITUNES_LIBRARY_PATH'

class ITunesISODateTime(datetime):
  ''' A `datetime` subclass using the iTunes exported XML date format.
  '''

  ISOFORMAT = '%Y-%m-%dT%H:%M:%SZ'

  def __str__(self):
    return self.strftime(self.ISOFORMAT)

  __repr__ = __str__

  @classmethod
  def from_itunes_date(cls, date_text):
    ''' Prepare `ITunesISODateTime` from its text transcription.
    '''
    return cls.strptime(date_text, cls.ISOFORMAT)

class ITunes:
  ''' Access to an iTunes library.
  '''

  EXPORTED_XML_SUBPATH = 'iTunes Library.xml'

  @pfx_method
  def __init__(self, path=None):
    if path is None:
      path = os.environ.get(ITUNES_LIBRARY_PATH_ENVVAR)
      if path is None:
        raise ValueError(
            f"no path specified and no ${ITUNES_LIBRARY_PATH_ENVVAR} environment variable"
        )
    self.path = path
    self.exported_xml_path = joinpath(path, self.EXPORTED_XML_SUBPATH)
    self._parsed_export = None
    self._lock = RLock()

  @locked_property
  @pfx_method
  def exported_library(self):
    ''' The root of the ElementTree parsed from the iTunes export XML file.
    '''
    lib_xml = self.parsed_export.getroot()[0]
    parsed = self.parse_dict(
        lib_xml,
        key_parsers={
            'Playlists': self.parse_playlists,
            'Tracks': self.parse_tracks,
        }
    )
    library, = dicts_to_namedtuples(
        (parsed,),
        type(self).__name__ + '_Library_XML'
    )
    return library

  @property
  def tv_shows(self):
    ''' Iterable of the TV Show tracks.
    '''
    for track in self.exported_library.tracks.values():
      if track.tv_show:
        yield track

  @locked_property
  def parsed_export(self):
    ''' The ElementTree parsed from the iTunes export XML file.
    '''
    parsed = self._parsed_export
    if parsed is None:
      parsed = self._parsed_export = etree.parse(self.exported_xml_path)
    return parsed

  @staticmethod
  def parse_data(data_elem):
    ''' Parse a `<data>` tag, which contained unpadded base64 encoded data.
    '''
    b64text = ''.join(data_elem.text.split())
    padding = 4 - len(b64text) % 4
    if padding < 4:
      b64text += '=' * padding
    ##X("b64text=%r", b64text)
    return b64decode(b64text)

  def parse_dict(
      self, dict_elem, key_parsers=None, tag_parsers=None, parse_key=None
  ):
    ''' Deconstruct a `<dict>` XML element containing
        a flat sequence of `<key>text</key>` and value tags
        into a dict.
    '''
    if key_parsers is None:
      key_parsers = {}
    if tag_parsers is None:
      tag_parsers = {}
    d = {}
    elements = iter(dict_elem)
    for key_elem in elements:
      key_name = key_elem.text.strip() if key_elem.text else ''
      key_parser = key_parsers.get(key_name)
      with Pfx("<%s>%s</%s>", key_elem.tag, key_name, key_elem.tag):
        value_elem = next(elements)
        value_tag = value_elem.tag
        with Pfx("<%s>%s</%s>", value_tag,
                 value_elem.text.strip() if value_elem.text else '',
                 value_tag):
          value_parser = tag_parsers.get(value_tag)
          if key_parser:
            with Pfx("key_parsers[%r]:%s(%r)", key_name, key_parser,
                     value_elem.text):
              try:
                value = key_parser(value_elem)
              except ValueError as e:
                warning("parse failed: %s", e)
                continue
          elif value_parser:
            with Pfx("tag_parsers[%s]:%s(%r)", value_tag, value_parser,
                     value_elem.text):
              try:
                value = value_parser(value_elem)
              except ValueError as e:
                warning("parse failed: %s", e)
                continue
          elif value_tag == 'true':
            value = True
          elif value_tag == 'false':
            value = False
          elif value_tag == 'integer':
            value = int(value_elem.text)
          elif value_tag == 'string':
            value = value_elem.text
          elif value_tag == 'data':
            value = self.parse_data(value_elem)
          elif value_tag == 'date':
            value = ITunesISODateTime.from_itunes_date(value_elem.text)
          else:
            warning("unhandled value: <%s>", value_tag)
            continue
      key = key_name if parse_key is None else parse_key(key_name)
      d[key] = value
    return d

  def parse_array_of_dict(self, array, class_name, **kw):
    ''' Yield `namedtuple`s from an `<array>` of `<dict>`s.

        Keyword arguments are passed to `parse_dict`.
    '''
    yield from dicts_to_namedtuples(
        (self.parse_dict(array_elem, **kw) for array_elem in array),
        class_name=class_name
    )

  def parse_dict_of_dict(
      self, dict_elem, class_name, parse_key=None, inner_parse_key=None, **kw
  ):
    ''' Yield `(key,namedtuple)` from a `<dict>` of `<key><dict>`s.

        Keyword arguments are passed to the internal `parse_dict`
        use for the internal `<dict>` elements.
        `inner_parse_key` is passed to `parse_dict` as `parse_key`.
    '''
    # patch `inner_parse_key` to internal parse_dict call
    if inner_parse_key is not None:
      kw['parse_key'] = inner_parse_key
    # dissect the `<key><dict>` pairs into a list of keys and parsed `dict`s
    ks = []
    ds = []
    elements = iter(dict_elem)
    for key_elem in elements:
      key_name = key_elem.text
      ks.append(key_name if parse_key is None else parse_key(key_name))
      inner_dict_elem = next(elements)
      ds.append(self.parse_dict(inner_dict_elem, **kw))
    # assemble into a `dict` mapping `key` to `namedtuple`
    return dict(zip(ks, dicts_to_namedtuples(ds, class_name)))

  def parse_playlists(self, array):
    ''' Parse the `'Playlists'` array of playlist definitions
        into a `dict` mapping `playlist_id` to `Playlist`s.
    '''
    return {
        pl.playlist_id: pl
        for pl in self.parse_array_of_dict(
            array,
            class_name='Playlist',
            key_parsers={'Playlist Items': self.parse_playlist_items}
        )
    }

  def parse_playlist_items(self, array):
    ''' Parse the `'Playlist Items'` array of playlist items.
    '''
    return self.parse_array_of_dict(
        array,
        class_name='PlaylistItem',
        tag_parsers={'data': self.parse_data}
    )

  def parse_tracks(self, tracks_dict):
    ''' Parse the `'tracks'` array of playlist definitions.
    '''
    return self.parse_dict_of_dict(
        tracks_dict,
        class_name='Track',
        parse_key=int,
    )

def read_playlist(path):
  ''' Read an iTunes playlist file, return a list of entry objects.
  '''
  with open(path, encoding='utf-16le', newline='\r') as plf:
    # skip the BOM
    plf.seek(2)

    def preprocess(context, row):
      ''' Convert some row columns before assimilation.
      '''
      if context.index > 0:
        for i, attr in enumerate(context.cls.attrs_):
          if attr in (
              'bit_rate',
              'disc_count',
              'disc_number',
              'my_rating',
              'plays',
              'sample_rate',
              'size',
              'time',
              'track_count',
              'track_number',
              'year',
          ):
            row[i] = int(row[i]) if row[i] else None
          elif attr in (
              'date_added',
              'date_modified',
              'last_played',
          ):
            row[i] = playlist_date(row[i]) if row[i] else None
      X("row = %r", row)
      return row

    _, entries = named_column_tuples(
        [line[-1].split('\t') for line in plf],
        class_name='ApplePlaylistEntry',
        preprocess=preprocess
    )
    entries = list(entries)
  return entries

def playlist_date(s):
  ''' Parse a date time field from an Apple playlist file.
  '''
  return datetime.strptime(s, '%d/%m/%y %I:%S %p')

if __name__ == '__main__':
  import sys
  for argv_path in sys.argv[1:]:
    print(argv_path)
    for item in read_playlist(argv_path):
      print(item)

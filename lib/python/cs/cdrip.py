#!/usr/bin/env python3

''' A tool for working with audio Compact Discs (CDs),
    uses the discid and musicbrainzngs modules.
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property
from getopt import GetoptError
import os
from os.path import (
    dirname,
    exists as existspath,
    expanduser,
    isdir as isdirpath,
    join as joinpath,
)
from pprint import pprint
import sys
import time
from typing import List, Optional, Union
from uuid import UUID

import discid
from discid.disc import DiscError
from icontract import require
import musicbrainzngs
from typeguard import typechecked

from cs.cmdutils import BaseCommand, popopts
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.excutils import unattributable
from cs.ffmpegutils import convert as ffconvert, MetaData as FFMetaData
from cs.fileutils import atomic_filename
from cs.fs import needdir, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.lex import cutsuffix, is_identifier, printt, r
from cs.logutils import error, warning, info, debug
from cs.mappings import AttrableMapping
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.psutils import run
from cs.queues import ListQueue
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.seq import unrepeated
from cs.sqltags import (
    BaseSQLTagsCommand,
    SQLTags,
    SQLTagSet,
    SQLTagsCommandsMixin,
    FIND_OUTPUT_FORMAT_DEFAULT,
)
from cs.tagset import HasTags, TagSet, UsesTagSets
from cs.upd import run_task, print

__version__ = '20201004-dev'

musicbrainzngs.set_useragent(__name__, __version__, os.environ['EMAIL'])

CDRIP_DEV_ENVVAR = 'CDRIP_DEV'
CDRIP_DEV_DEFAULT = 'default'

CDRIP_DIR_ENVVAR = 'CDRIP_DIR'
CDRIP_DIR_DEFAULT = '~/var/cdrip'

CDRIP_CODECS_ENVVAR = 'CDRIP_CODECS'
CDRIP_CODECS_DEFAULT = 'wav,flac,aac,mp3'

MBDB_PATH_ENVVAR = 'MUSICBRAINZ_SQLTAGS'
MBDB_PATH_DEFAULT = '~/var/cache/mbdb.sqlite'

def main(argv=None):
  ''' Call the command line main programme.
  '''
  return CDRipCommand(argv).run()

def probe_disc(device, mbdb, disc_id=None):
  ''' Probe MusicBrainz about the disc in `device`.
  '''
  print("probe_disc: device", device, "mbdb", mbdb)
  if disc_id is None:
    dev_info = discid.read(device=device)
    disc_id = dev_info.id
  print("probe_disc: disc_id", disc_id)
  if disc_id in mbdb.discs:
    disc = mbdb.discs[disc_id]
    mbdb.stale(disc)
    mbdb.refresh(
        disc,
        recurse=True,
    )
    return
  print("  missing disc_id", disc_id)
  ##includes = ['artists', 'recordings']
  includes = ['artist-credits']
  get_type = 'releases'
  id_name = 'discid'
  record_key = 'disc'
  with stackattrs(mbdb, dev_info=dev_info):
    A = mbdb.query(
        get_type,
        disc_id,
        includes,
        id_name,
        record_key,
        toc=dev_info.toc_string,
    )
  releases = A['release-list']
  for release in releases:
    print(
        release['id'], release['title'], "by", release['artist-credit-phrase']
    )
    print(" ", release['release-event-list'])
    for medium in release['medium-list']:
      print("  medium")
      for track in medium['track-list']:
        print("    track", track['number'], track['recording']['title'])
  release = pick(
      releases,
      as_str=(
          lambda rel:
          f"{rel['id']} {rel['title']} by {rel['artist-credit-phrase']}"
      )
  )

def pick(items, as_str=None):
  ''' Interactively pick a item from a `items`.
  '''
  items = list(items)
  assert len(items) > 1
  if as_str is None:
    as_str = repr
  show_items = True
  while True:
    if show_items:
      for i, item in enumerate(items, 1):
        print(i, as_str(item))
        show_items = False
    answer = input(
        f"Select item from 1 to {len(items)}) (? to list items again) "
    ).strip()
    if answer == '?':
      show_items = True
    else:
      try:
        i = int(answer)
      except ValueError:
        print("Not an integer.")
      else:
        if i < 1 or i > len(items):
          print(f"Out of range, expected a value from 1 to {len(items)}.")
        else:
          return items[i - 1]

# pylint: disable=too-many-locals,too-many-branches,too-many-statements
@uses_fstags
def rip(
    device,
    mbdb,
    *,
    output_dirpath,
    disc_id=None,
    audio_outputs=('wav', 'flac', 'aac', 'mp3'),
    fstags: FSTags,
    no_action=False,
    split_by_codec=False,
):
  ''' Pull audio from `device` and save in `output_dirpath`.
  '''
  if not isdirpath(output_dirpath):
    raise ValueError(f'not a directory: {output_dirpath!r}')
  dev_info = discid.read(device=device)
  mb_toc = dev_info.toc_string
  with stackattrs(mbdb, dev_info=dev_info):
    if disc_id is None:
      disc_id = dev_info.id
    elif disc_id != dev_info.id:
      warning("disc_id:%r != dev_info.id:%r", disc_id, dev_info.id)
    disc = mbdb.discs[disc_id]
    if disc_id == dev_info.id:
      disc.mb_toc = mb_toc
    recordings = disc.recordings
    disc_tags = disc.disc_tags()
    # filesystem paths
    artist_part = disc_tags.disc_artist_credit
    disc_part = disc_tags.disc_title

    def fmtpath(acodec, ext):
      ''' Compute the output filesystem path.
      '''
      return joinpath(
          output_dirpath,
          acodec if split_by_codec else '',
          " ".join(artist_part.replace(os.sep, ' - ').split()),
          " ".join(disc_part.replace(os.sep, ' - ').split()),
          f'{track_part}.{ext}'.replace(os.sep, '-'),
      )

    for track_index in range(len(recordings)):
      track_number = track_index + 1
      with Pfx("track %d", track_number):
        track_tags = disc.track_tags(track_index)
        # filesystem paths
        track_part = (
            f"{track_tags.track_number:02}"
            f" - {track_tags.track_title}"
            f" -- {track_tags.track_artist_credit}"
        )
        for acodec in 'wav', 'flac', 'aac', 'mp3':
          # skip unmentioned codec except for "wav"
          if acodec != 'wav' and (acodec not in audio_outputs):
            continue
          if acodec == 'aac':
            # to provide metadata we embed AAC audio in an MP4 container
            # named .m4a to happy iTunes
            ext = 'm4a'
            fmt = 'mp4'
          else:
            fmt = ext = acodec
          fmt_filename = fmtpath(acodec, ext)
          ffmetadata = FFMetaData(
              fmt,
              album=disc_tags.disc_title,
              album_artist=disc_tags.disc_artist_credit,
              disc=f'{disc_tags.disc_number}/{disc_tags.disc_total}',
              track=f'{track_tags.track_number}/{track_tags.track_total}',
              title=track_tags.track_title,
              artist=track_tags.track_artist_credit,
              ##author=track_tags.track_artist_credit,
          )
          with Pfx(shortpath(fmt_filename)):
            if existspath(fmt_filename):
              info("using existing %s file: %r", fmt.upper(), fmt_filename)
              argv = None
            else:
              fmt_dirpath = dirname(fmt_filename)
              needdir(fmt_dirpath, use_makedirs=True)
              fstags[fmt_dirpath].update(disc_tags)
              if fmt == 'wav':
                # rip from CD
                argv = rip_to_wav(
                    device, track_number, fmt_filename, no_action=no_action
                )
              else:
                # use ffmpeg to convert from the WAV file
                wav_filename = fmtpath('wav', 'wav')
                with atomic_filename(fmt_filename, placeholder=True) as T:
                  argv = ffconvert(
                      wav_filename,
                      dstpath=T.name,
                      dstfmt=fmt,
                      acodec=acodec,
                      doit=not no_action,
                      metadata=ffmetadata,
                      overwrite=True,
                  )
                  print("CONVERTED:", *argv)
            if no_action:
              print("fstags[%r].update(%s)" % (fmt_filename, track_tags))
            else:
              fstags[fmt_filename].conversion_command = argv
              fstags[fmt_filename].update(track_tags)

def rip_to_wav(device, tracknum, wav_filename, no_action=False):
  ''' Rip a track from the CDROM device to a WAV file.
  '''
  with atomic_filename(wav_filename) as T:
    argv = ['cdparanoia', '-d', device, '-w', str(tracknum), T.name]
    run(argv, doit=not no_action, quiet=False, check=True)
  return argv

# pylint: disable=too-many-ancestors
class _MBEntity(HasTags):
  ''' A `HasTags` subclass for MB entities.
      This exists as a search root for the subclass `.TYPE_SUBNAME` attribute.

      All the state is proxied through the `.tags`, which is an `SQLTagSet`.
      Instances are constructed via `MBDB.mbentity(SQLTagSet)`,
      which also sets the `.mbdb` and `.tags_db` on the instance.
  '''

  MB_QUERY_PREFIX = 'musicbrainzngs.api.query'
  MB_QUERY_PREFIX_ = f'{MB_QUERY_PREFIX}.'
  MB_QUERY_RESULT_TAG_NAME = f'{MB_QUERY_PREFIX}.result'
  MB_QUERY_TIME_TAG_NAME = f'{MB_QUERY_PREFIX}.time'

  @property
  def query_result(self):
    ''' The Musicbrainz query result, fetching it if necessary.
    '''
    mb_result = self.get(self.MB_QUERY_RESULT_TAG_NAME)
    if not mb_result:
      self.refresh(refetch=True)
      try:
        mb_result = self[self.MB_QUERY_RESULT_TAG_NAME]
      except KeyError as e:
        raise AttributeError(f'no {self.MB_QUERY_RESULT_TAG_NAME}: {e}') from e
    typename, db_id = self.name.split('.', 1)
    self.mbdb.apply_dict(self, mb_result)
    return mb_result

  def dump(self, keys=None, **kw):
    if keys is None:
      keys = sorted(
          k for k in self.keys() if (
              not k.startswith(self.MB_QUERY_PREFIX_)
              and not k.endswith('_relation')
          )
      )
    return super().dump(keys=keys, **kw)

  def refresh(self, **mbdb_refresh_kw):
    ''' Refresh the MBDB entry for this `TagSet`.
    '''
    return self.mbdb.refresh(self, **mbdb_refresh_kw)

  @property
  def mbdb(self):
    ''' Use the shared `SQLTags`.
    '''
    return self.tags_db

  @property
  def mbkey(self):
    ''' The MusicBrainz id, typically a UUID or discid.
    '''
    return self.tags.type_key.replace('+', '.')

  @property
  def mbtype(self):
    ''' The MusicBrainz type, eg "release".
    '''
    return self.tags.type_subname

  @property
  def mbtime(self):
    ''' The timestamp of the most recent .refresh() API call, or `None`.
    '''
    return self.tags.get(self.MB_QUERY_TIME_TAG_NAME)

  @property
  def ontology(self):
    ''' The `TagsOntology` for this entity.
    '''
    return self.mbdb.ontology

  @require(lambda type_name: is_identifier(type_name))  # pylint: disable=unnecessary-lambda
  @typechecked
  def resolve_id(
      self,
      type_name: str,
      id: Union[str, dict],  # pylint: disable=redefined-builtin
  ) -> '_MBEntity':
    ''' Fetch the object `{type_name}.{id}`.
    '''
    if type_name == 'disc':
      try:
        UUID(id)
      except ValueError:
        pass
      else:
        raise RuntimeError("type_name=%r, id=%r is UUID" % (type_name, id))
    if isinstance(id, dict):
      id = id["id"]
    return self.mbdb[type_name, id]

  @cached_property
  def artist_credit_v(self):
    ''' A list of `str|MBArtist` from `self.tags.artist_credit`.
        This falls back to `self.tags.artist` if there are no `artist_credit`.
    '''
    artists = []
    for ac in self.tags.get('artist_credit') or self.tags.get('artist', []):
      if isinstance(ac, str):
        artists.append(ac)
      else:
        artist_info = None
        for ack, acv in ac.items():
          if ack == 'artist':
            artist_info = acv
          else:
            warning(
                "self.tags.artist_credit: unexpected key %r in %r", ack, ac
            )
        assert artist_info is not None
        assert isinstance(artist_info, str)
        UUID(artist_info)
        artists.append(self.mbdb['artist', artist_info])
    return artists

  @property
  def artists(self):
    ''' A list of the `MBArtist`s from `self.tags.artist_credit`.
    '''
    return [
        artist for artist in self.artist_credit_v
        if not isinstance(artist, str)
    ]

  def artist_names(self):
    ''' A list of the artist names from `self.tags.artist_credit`.
    '''
    return [artist.fullname for artist in self.artists]

  @property
  def artist_credit(self) -> str:
    '''A credit string computed from `self.tags.artist_credit`.
    '''
    strs = []
    sep = ''
    for artist in self.artist_credit_v:
      if isinstance(artist, str):
        strs.append(artist)
        sep = ''
      else:
        fn = artist.fullname
        strs.append(sep)
        strs.append(fn)
        sep = ', '
    return ''.join(strs)

class MBArea(_MBEntity):
  ''' A Musicbrainz area entry.
  '''
  TYPE_SUBNAME = 'area'

class MBArtist(_MBEntity):
  ''' A Musicbrainz artist entry.
  '''

  TYPE_SUBNAME = 'artist'

class MBDisc(_MBEntity):
  ''' A Musicbrainz disc entry.
  '''

  TYPE_SUBNAME = 'disc'

  @property
  def discid(self):
    ''' The disc id to be used in lookups.
        For most discs it is `self.mbkey`, but if our discid is unknown
        and another is in the database, the `use_discid` tag will supply
        that discid.
    '''
    return getattr(self, 'use_discid', self.mbkey)

  @property
  @unattributable
  def title(self):
    ''' The medium title or failing that the release title.
    '''
    return self.medium_title or self.release['title']

  @property
  def release_list(self):
    ''' The query result `"release-list"` list.
    '''
    return self.query_result.get('release-list', [])

  @cached_property
  def releases(self):
    ''' A cached list of entries from `release_list` matching the `disc_id`. '''
    releases = []
    discid = self.mbkey
    for release_entry in self.release_list:
      for medium in release_entry['medium-list']:
        for disc_entry in medium['disc-list']:
          if disc_entry['id'] == discid:
            release = self.resolve_id('release', release_entry['id'])
            if release is None:
              warning("no release found for id %r", release)
            else:
              releases.append(release)
    return releases

  @cached_property
  def release(self):
    ''' The first release containing this disc found in the releases from Musicbrainz, or `None`.
    '''
    releases = self.releases
    if not releases:
      # fall back to the first release
      warning(
          "%s: no matching releases, falling back to the first nonmatching release",
          self.name
      )
      all_releases = self.release_list
      if not all_releases:
        warning("%s: no nonmatching relases", self.name)
        return None
      return self.resolve_id('release', all_releases[0]['id'])
    return releases[0]

  @property
  def release_title(self):
    ''' The release title.
    '''
    return self.release.title

  @cached_property
  @unattributable
  def mb_info(self):
    ''' Salient data from the MusicbrainzNG API response.
    '''
    discid = self.mbkey
    release = self.release
    if release is None:
      raise AttributeError(f'no release for discid:{discid!r}')
    release_entry = release.query_result
    media = release_entry['medium-list']
    medium_count = len(media)
    for medium in media:
      for pos, disc_entry in enumerate(medium['disc-list'], 1):
        if disc_entry['id'] == discid:
          mb_info = AttrableMapping(
              disc_entry=disc_entry,
              disc_pos=pos,
              medium=medium,
              medium_count=medium_count,
          )
          return mb_info
    # gather discids for inclusion in the exception message
    discids = set()
    for medium in media:
      for disc_entry in medium['disc-list']:
        discids.add(disc_entry['id'])
    raise AttributeError(
        f'no medium+disc found for discid:{discid!r}: saw {sorted(discids)!r}'
    )

  @property
  @unattributable
  def medium(self):
    '''The recording's medium.'''
    return self.mb_info.medium

  @property
  @typechecked
  def medium_position(self) -> int:
    '''The position of this recording's medium eg disc 1 of 2.'''
    return int(self.medium['position'])

  @property
  @typechecked
  def medium_count(self) -> int:
    '''The position of this recording's medium eg disc 1 of 2.'''
    return self.mb_info.medium_count

  @property
  @unattributable
  def medium_title(self):
    ''' The medium title.
    '''
    return self.medium.get('title')

  @cached_property
  def recordings(self):
    ''' Return a list of `MBRecording` instances.
    '''
    recordings = []
    for track_rec in self.medium['track-list']:
      recording = self.resolve_id('recording', track_rec['recording']['id'])
      recordings.append(recording)
    return recordings

  @property
  def disc_title(self):
    ''' The per-disc title, used as the subdirectory name when ripping.
        This is:

            release-title[ (n of m)][ - disc-title]

        The `(n of m)` suffix is appended if there is more than one
        medium in the release.
        The `disc-title` suffix is appended if the per-disc title is
        not the same as the release title.
    '''
    disc_title = self.release_title
    if self.medium_count > 1:
      disc_title += f" ({self.medium_position} of {self.medium_count})"
    if self.title != self.release_title:
      disc_title += f' - {self.title}'
    return disc_title

  def disc_tags(self):
    ''' Return a `TagSet` for the disc.
    '''
    release = self.release
    return TagSet(
        disc_id=self.mbkey,
        disc_artist_credit='' if release is None else release.artist_credit,
        disc_title=self.title,
        disc_number=self.medium_position,
        disc_total=self.medium_count,
    )

  @require(
      lambda self, track_index: track_index >= 0 and track_index <
      len(self.recordings)
  )
  @typechecked
  def track_tags(self, track_index: int) -> TagSet:
    ''' Return a `TagSet` for track `tracknum` (counting from 0).
    '''
    recording = self.recordings[track_index]
    return TagSet(
        track_number=track_index + 1,
        track_total=int(self.medium['track-count']),
        track_artist_credit=recording.artist_credit,
        track_title=recording.title,
    )

class MBRecording(_MBEntity):
  ''' A Musicbrainz recording entry, a single track.
  '''

  TYPE_SUBNAME = 'recording'

  @property
  def title(self):
    ''' The recording title.
    '''
    try:
      title = self['title']
    except KeyError:
      try:
        title = self.query_result['title']
      except KeyError as e:
        raise AttributeError("no .title: {e}") from e
    return title

class MBTrack(_MBEntity):
  ''' A Musicbrainz track entry, a recording on a disc.
  '''

  TYPE_SUBNAME = 'track'

class MBRelease(_MBEntity):
  ''' A Musicbrainz recording entry, a single track.
  '''

  TYPE_SUBNAME = 'release'

class MBReleaseGroup(_MBEntity):
  ''' A Musicbrainz release group, associated with a recording.
  '''
  TYPE_SUBNAME = 'release_group'

class MBLabel(_MBEntity):
  ''' A Musicbrainz label.
  '''
  TYPE_SUBNAME = 'label'

class MBSQLTags(SQLTags):
  ''' Musicbrainz flavoured `SQLTags`; it just has custom values for the default db location.
  '''

  DBURL_ENVVAR = MBDB_PATH_ENVVAR
  DBURL_DEFAULT = MBDB_PATH_DEFAULT

class MBDB(UsesTagSets, MultiOpenMixin, RunStateMixin):
  ''' An interface to MusicBrainz with a local `SQLTags` cache.
  '''

  TYPE_ZONE = 'mbdb'
  HasTagsClass = _MBEntity
  TagSetsClass = MBSQLTags

  # Mapping of MusicbrainzNG tag names whose type is not themselves.
  TYPE_NAME_REMAP = {
      'artist-credit': 'artist',
      ##'begin-area': 'area',
      ##'end-area': 'area',
      ##'label-info': 'label',
      ##'medium': 'disc',
      'release-event': 'event',
      ##'release-group': 'release',
      'track': 'recording',
  }

  # Mapping of query type names to default includes,
  # overrides the fallback to musicbrainzngs.VALID_INCLUDES.
  QUERY_TYPENAME_INCLUDES = {
      ##'area': ['annotation', 'aliases'],
      ##'artist': ['annotation', 'aliases'],
      'releases': ['artists', 'recordings'],
      ##'releases': [],
  }
  # List of includes only available if logged in.
  # We drop these if we're not logged in.
  QUERY_INCLUDES_NEED_LOGIN = ['user-tags', 'user-ratings']

  def __init__(self, mbdb_path=None):
    UsesTagSets.__init__(self, tagsets=MBSQLTags(mbdb_path))
    RunStateMixin.__init__(self)
    # can be overlaid with discid.read of the current CDROM
    self.dev_info = None

  def __str__(self):
    return f'{self.__class__.__name__}({self.tagsets})'

  @contextmanager
  def startup_shutdown(self):
    ''' Context manager for open/close.
    '''
    with self.tagsets:
      yield

  def __getitem__(self, index) -> _MBEntity:
    ''' Fetch an `_MBEntity` from an `(mbtype,mbkey)` 2-tuple.
    '''
    try:
      mbtype, key = index
    except ValueError:
      pass
    else:
      # UUIDs do not contain . or +
      # discids may contain . and should not contain +
      # the sqltags type_key part should not contain a .
      # so we replace . with +
      # discid stuff:
      # https://github.com/metabrainz/libdiscid/blob/192edd70f17661f1a13ac3b349a2a2d96f5f0351/src/base64.c#L85
      # this is amazingly ill specified AFAICT
      index = (
          mbtype.replace('-', '_') if isinstance(mbtype, str) else mbtype,
          key.replace('.', '+'),
      )
    return super().__getitem__(index)

  # pylint: disable=too-many-arguments
  @pfx_method
  def query(
      self,
      typename,
      db_id,
      id_name='id',
      *,
      includes=None,
      record_key=None,
      **getter_kw
  ) -> dict:
    ''' Fetch data from the Musicbrainz API.
    '''
    logged_in = False
    getter_name = f'get_{typename}_by_{id_name}'
    if typename == 'releases':
      assert getter_name == 'get_releases_by_discid'
    if record_key is None:
      record_key = typename
    try:
      getter = getattr(musicbrainzngs, getter_name)
    except AttributeError:
      error(
          "no musicbrainzngs.%s: %r", getter_name,
          sorted(
              gname for gname in dir(musicbrainzngs)
              if gname.startswith('get_')
          )
      )
      return {}
    if includes is None:
      try:
        includes = self.QUERY_TYPENAME_INCLUDES[typename]
      except KeyError:
        includes_map = (
            musicbrainzngs.VALID_INCLUDES
            if logged_in else musicbrainzngs.VALID_BROWSE_INCLUDES
        )
        include_map_key = 'release' if typename == 'releases' else typename
        includes = list(includes_map.get(include_map_key, ()))
    if not logged_in:
      if typename.startswith('collection'):
        warning("typename=%r: need to be logged in for collections", typename)
        return {}
      if any(map(lambda inc: inc in self.QUERY_INCLUDES_NEED_LOGIN, includes)):
        debug(
            "includes contains some of %r, dropping because not logged in",
            self.QUERY_INCLUDES_NEED_LOGIN
        )
        includes = [
            inc for inc in includes
            if inc not in self.QUERY_INCLUDES_NEED_LOGIN
        ]
    if (typename == 'releases' and 'toc' not in getter_kw
        and self.dev_info is not None and self.dev_info.id == db_id):
      getter_kw.update(toc=self.dev_info.toc_string)
    assert ' ' not in db_id, "db_id:%r contains a space" % (db_id,)
    ##warning(
    ##    "QUERY typename=%r db_id=%r includes=%r ...", typename, db_id, includes
    ##)
    if typename == 'releases':
      try:
        UUID(db_id)
      except ValueError:
        pass
      else:
        raise RuntimeError(
            "query(%r,%r,...): using a UUID" % (typename, db_id)
        )
    with run_task(f'musicbrainzngs.{getter_name}({db_id=},...)',
                  report_print=True):
      try:
        mb_info = pfx_call(getter, db_id, includes=includes, **getter_kw)
      except musicbrainzngs.musicbrainz.MusicBrainzError as e:
        if e.cause.code == 404:
          warning("not found: %s(%s): %s", getter_name, r(db_id), e)
          if typename == 'recording':
            raise
          return {}
        warning("help(%s):\n%s", getter_name, getter.__doc__)
        help(getter)
        raise
        ##return {}
    # we expect the response to have a single entry for the record type requested
    if record_key in mb_info:
      other_keys = sorted(k for k in mb_info.keys() if k != record_key)
      if other_keys:
        warning(
            "mb_info contains %r, discarding other keys: %r",
            record_key,
            other_keys,
        )
      mb_info = mb_info[record_key]
    else:
      warning(
          "no entry named %r, returning entire mb_info, keys=%r", record_key,
          sorted(mb_info.keys())
      )
    return mb_info

  def stale(self, te):
    ''' Make this entry stale by scrubbing the query time attribute.
    '''
    if te.MB_QUERY_TIME_TAG_NAME in te:
      del te[te.MB_QUERY_TIME_TAG_NAME]

  # pylint: disable=too-many-branches,too-many-statements
  @require(lambda mbe: '.' in mbe.tags.name)
  @typechecked
  def refresh(
      self,
      mbe: _MBEntity,
      refetch: bool = True,  ##False,
      recurse: Union[bool, int] = False,
      no_apply=False,
  ) -> dict:
    ''' Query MusicBrainz about the entity `mbe`, fill recursively.
        Return the query result of `mbe`.

        Parameters:
        * `mbe`: the MuscBrainzNG entity to refresh
        * `refetch`: query MuscBrainzNG even if not stale
        * `recurse`: limit recursive refresh of related entities;
          if `True`, refresh all related entities,
          if `False`, refresh no related entities,
          if an `int`, refresh up to this many related entities
        * `no_apply`: if true (default `False`) do not apply query results to the database
    '''
    mbe0 = mbe
    with run_task("refresh %s" % mbe0.name) as proxy:
      q = ListQueue([mbe0])
      for mbe in unrepeated(q, signature=lambda mbe: mbe.name):
        with Pfx("refresh mbe %r", mbe.name):
          if self.runstate.cancelled:
            break
          with proxy.extend_prefix(": " + mbe.name):
            if '.' not in mbe.name:
              warning("refresh: skip %r, not dotted", mbe.name)
              continue
            mbtype = mbe.mbtype
            mbkey = mbe.mbkey
            if mbtype in ('cdstub',):
              warning("no refresh for mbtype=%r", mbtype)
              continue
            if (refetch or mbe.MB_QUERY_RESULT_TAG_NAME not in mbe
                or mbe.MB_QUERY_TIME_TAG_NAME not in mbe
                or not mbe[mbe.MB_QUERY_RESULT_TAG_NAME]):
              # refresh or missing or stale
              query_get_type = mbtype
              id_name = 'id'
              record_key = None
              if mbtype == 'disc':
                # we use get_releases_by_discid() for discs
                query_get_type = 'releases'
                id_name = 'discid'
                record_key = 'disc'
              with stackattrs(
                  proxy,
                  text=f'query({query_get_type!r},{mbkey!r},...)',
              ):
                try:
                  A = self.query(
                      query_get_type, mbkey, id_name, record_key=record_key
                  )
                except (musicbrainzngs.musicbrainz.MusicBrainzError,
                        musicbrainzngs.musicbrainz.ResponseError) as e:
                  warning("%s: not refreshed: %s", type(e).__name__, e)
                  raise
                  ##A = mbe.get(mbe.MB_QUERY_RESULT_TAG_NAME, {})
                else:
                  # record the full response data for forensics
                  mbe[f'{mbe.MB_QUERY_PREFIX}.get_type'] = query_get_type
                  ##mbe[f'{mbe.MB_QUERY_PREFIX.includes'] = includes
                  mbe[mbe.MB_QUERY_RESULT_TAG_NAME] = A
                  mbe[mbe.MB_QUERY_TIME_TAG_NAME] = time.time()
                  if not no_apply:
                    self.apply_dict(mbe, A)
            else:
              # use the caches result from the database
              A = mbe[mbe.MB_QUERY_PREFIX + 'result']
            # cap recursion
            if isinstance(recurse, bool):
              if not recurse:
                break
            elif isinstance(recurse, int):
              recurse -= 1
              if recurse < 1:
                break
            else:
              raise TypeError(f'wrong type for recurse {r(recurse)}')
      return mbe0.get(mbe0.MB_QUERY_RESULT_TAG_NAME, {})

  @classmethod
  def key_type_name(cls, k):
    ''' Derive a type name from a MusicBrainzng key name.
        Return `(type_name,suffix)`.

        A key such as `'disc-list'` will return `('disc','list')`.
        A key such as `'recording'` will return `('recording',None)`.
    '''
    # NB: ordering matters
    for suffix in 'relation-list', 'count', 'list', 'relation':
      _suffix = '-' + suffix
      type_name = cutsuffix(k, _suffix)
      if type_name is not k:
        break
    else:
      type_name = k
      suffix = None
    type_name = cls.TYPE_NAME_REMAP.get(type_name, type_name)
    return type_name, suffix

  @typechecked
  def apply_dict(
      self,
      mbe: _MBEntity,
      d: dict,
      *,
      q: Optional[ListQueue] = None,
      seen: Optional[set] = None,
  ):
    ''' Apply an `'id'`-ed dict from MusicbrainzNG query result `d` to `mde`.

        Parameters:
        * `type_name`: the entity type, eg `'disc'`
        * `id`: the entity identifying value, typically a discid or a UUID
        * `d`: the `dict` to apply to the entity
        * `q`: optional queue onto which to put related entities
    '''
    sig = mbe.name
    if seen is None:
      seen = set()
    elif sig in seen:
      return
    seen.add(sig)
    d = dict(d)  # make a copy because we will be modifying it
    # check the id if present
    if 'id' in d:
      assert d['id'] == mbe.mbkey, f'{mbe.mbkey=} != {d["id"]=}'
      d.pop('id')
    counts = {}  # sanity check of foo-count against foo-list
    # scan the mapping, recognise contents
    for k, v in sorted(d.items()):
      with Pfx("%s=%s", k, r(v, 20)):
        # derive tag_name and field role (None, count, list)
        k_type_name, suffix = self.key_type_name(k)
        tag_name = k_type_name.replace('-', '_')
        # note expected counts
        if suffix == 'count':
          assert isinstance(v, int)
          counts[tag_name] = v
          continue
        if suffix == 'list':
          # this is a list of object attributes
          # apply members
          assert isinstance(v, list)
          flat_v = []
          for i, list_entry in enumerate(v):
            if isinstance(list_entry, (int, str)):
              flat_v.append(list_entry)
              continue
            if not isinstance(list_entry, dict):
              warning("skip entry %s", r(list_entry))
              flat_v.append(list_entry)
              continue
            try:
              entry_id = list_entry['id']
            except KeyError:
              # no list_entry['id']
              # we expect this entry to be a mapping of types to id-based records
              # { 'mbtype1':{'id':'id1','a':1,'b':2,...},
              #   'mbtype2':{'id':'id2','a':3,'b':4,...},
              # }
              #
              # Example:
              # {'area': {'id': '489ce91b-6658-3307-9877-795b68554c98',
              #           'iso-3166-1-code-list': ['US'],
              #           'name': 'United States',
              #           'sort-name': 'United States'},
              #  'date': '1999-04-20'}
              #
              flat_entry = {}
              for le_key, le_value in list_entry.items():
                if isinstance(le_value, dict) and 'id' in le_value:
                  # an le_value["id"] is there, like the "area" above
                  le_id = le_value["id"]
                  submbe = self[le_key, le_id]
                  self.apply_dict(submbe, le_value, q=q, seen=seen)
                  flat_entry[le_key] = le_id
                else:
                  flat_entry[le_key] = le_value
              flat_v.append(flat_entry)
            else:
              # this entry is the id and its attributes
              # {'id':'...','a':1,'b':2,...}
              submbe = self[k_type_name, entry_id]
              self.apply_dict(submbe, list_entry, q=q, seen=seen)
              flat_v.append(entry_id)
          v = flat_v
        if tag_name == 'name':
          tag_name = 'fullname'
        elif tag_name == 'fullname':
          warning(f'unexpected "fullname": {tag_name=}')
        # fold a dict value down to its key,
        # applying the dict
        v = self._fold_value(k_type_name, v, q=q, seen=seen)
        mbe.tags[tag_name] = v
    # sanity check the accumulated counts
    for k, c in counts.items():
      with Pfx("counts[%r]=%d", k, c):
        if k in mbe:
          assert len(mbe[k]) == c

  @typechecked
  def _fold_value(
      self,
      type_name: str,
      v,
      *,
      q=None,
      seen: set,
  ):
    ''' Fold `v` recursively,
        replacing `'id'`-ed `dict`s with their identifier
        and applying their values to the corresponding entity.
    '''
    if isinstance(v, dict):
      if 'id' in v:
        # {'id':.., ...}
        v = self._fold_id_dict(type_name, v, q=q, seen=seen)
      else:
        v = dict(v)
        for k, subv in list(v.items()):
          type_name, _ = self.key_type_name(k)
          v[k] = self._fold_value(
              type_name,
              subv,
              q=q,
              seen=seen
          )
    elif isinstance(v, list):
      v = list(v)
      for i, subv in enumerate(v):
        with Pfx("[%d]=%s", i, r(subv, 20)):
          v[i] = self._fold_value(type_name, subv, q=q, seen=seen)
    elif isinstance(v, str):
      if type_name not in ('name', 'title'):
        # folder integer strings to integers
        try:
          i = int(v)
        except ValueError:
          pass
        else:
          if str(i) == v:
            v = i
    # TODO: date => date? etc?
    else:
      assert isinstance(v, (int, float))
    return v

  @typechecked
  def _fold_id_dict(
      self,
      type_name: str,
      d: dict,
      *,
      q=None,
      seen: set,
  ):
    ''' Apply `d` (a `dict`) to the entity identified by `(type_name,d['id'])`,
        return `d['id']`.

        This is used to replace identified records in a MusicbrainzNG query result
        with their identifier.
    '''
    key = d['id']  # pylint: disable=redefined-builtin
    assert isinstance(key, str) and key, (
        "expected d['id'] to be a nonempty string, got: %s" % (r(key),)
    )
    mbe = self[type_name, key]
    self.apply_dict(mbe, d, q=q, seen=seen)
    return key

  def _tagif(self, tags, name, value):
    ''' Apply a new `Tag(name,value)` to `tags` if `value` is not `None`.
    '''
    if value is not None:
      tags.set(name, value)

class CDRipCommand(BaseCommand, SQLTagsCommandsMixin):
  ''' 'cdrip' command line.
      Environment:
        {CDRIP_DEV_ENVVAR}            Default CDROM device.
                             default {CDRIP_DEV_DEFAULT!r}.
        {CDRIP_DIR_ENVVAR}            Default output directory path,
                             default {CDRIP_DIR_DEFAULT!r}.
        {MBDB_PATH_ENVVAR}  Default location of MusicBrainz SQLTags cache,
                             default {MBDB_PATH_DEFAULT!r}.
  '''

  SUBCOMMAND_ARGV_DEFAULT = 'rip'

  @dataclass
  class Options(BaseSQLTagsCommand.Options):
    ''' Options for `CDRipCommand`.
    '''

    force: bool = False
    device: str = field(
        default_factory=lambda: os.environ.get(CDRIP_DEV_ENVVAR) or
        CDRIP_DEV_DEFAULT
    )
    dirpath: str = field(
        default_factory=lambda: os.environ.get(CDRIP_DIR_ENVVAR) or
        expanduser(CDRIP_DIR_DEFAULT)
    )
    mbdb_path: Optional[str] = None
    codecs_spec: str = field(
        default_factory=lambda: os.environ.
        get(CDRIP_CODECS_ENVVAR, CDRIP_CODECS_DEFAULT)
    )

    @property
    def codecs(self) -> List[str]:
      '''A list of the codec names to produce.'''
      return self.codecs_spec.replace(',', ' ').split()

    COMMON_OPT_SPECS = dict(
        **BaseSQLTagsCommand.Options.COMMON_OPT_SPECS,
        d_=('dirpath', 'Specify the output directory path.'),
        D_=(
            'device',
            '''Device to access. This may be omitted or "" or "default"
               for the default device as determined by the discid module.''',
        ),
        F_=('codec_spec', 'Specify the output codecs.'),
    )

  @contextmanager
  def run_context(self):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    with super().run_context():
      options = self.options
      fstags = FSTags()
      mbdb = MBDB(mbdb_path=options.mbdb_path)
      with fstags:
        with mbdb:
          mbdb_attrs = {}
          if self.subcmd not in ('mbq',):
            try:
              dev_info = pfx_call(discid.read, device=self.device_id)
            except discid.disc.DiscError as e:
              warning("no disc information: %s", e)
            else:
              mbdb_attrs.update(dev_info=dev_info)
          with stackattrs(mbdb, **mbdb_attrs):
            with stackattrs(
                options,
                fstags=fstags,
                mbdb=mbdb,
            ):
              yield

  @property
  @fmtdoc
  def device_id(self):
    '''
    The CD device id to use.
    This is `self.options.device` unless that is `CDRIP_DEV_DEFAULT` ({CDRIP_DEV_DEFAULT!r})
    in which case the value from `discid.get_default_device()` is used.
    '''
    return (
        discid.get_default_device()
        if self.options.device == CDRIP_DEV_DEFAULT else self.options.device
    )

  def device_info(self, device_id=None):
    ''' Return the device info from device `device_id`
        as from `discid.read(device_id)`.
        The default device comes from `self.device_id`.
    '''
    if device_id is None:
      device_id = self.device_id
    return pfx_call(discid.read, device=device_id)

  @typechecked
  def popdisc(self, argv, default=None, *, device_id=None) -> "MBDisc":
    ''' Pop an `MBDisc` from `argv`.
        If `argv` is empty and `default` is `None`, raise `IndexError`
        otherwise use `default`.
        A value of `"."` obtains the discid from the current device
        (or `device_id` if provided).
    '''
    if argv:
      disc_id = argv.pop(0)
    elif default is None:
      raise GetoptError("missing disc_id")
    else:
      disc_id = default
    dev_info = None
    if disc_id == '.':
      dev_info = pfx_call(self.device_info, device_id)
      disc_id = dev_info.id
    disc = self.options.mbdb['disc', disc_id]
    if dev_info is not None:
      disc.mb_toc = dev_info.toc_string
    return disc

  @popopts
  def cmd_disc(self, argv):
    ''' Usage: {cmd} {{.|discid}} {{tag[=value]|-tag}}...
          Tag the disc identified by discid.
          If discid is "." the discid is derived from the CD device.
          Typical example:
            disc . use_discid=some-other-discid
          to alias this disc with another in the database.
    '''
    if not argv:
      raise GetoptError('missing discid')
    disc = self.popdisc(argv, '.')
    if not argv:
      disc.dump()
      return
    try:
      tag_choices = self.parse_tag_choices(argv)
    except ValueError as e:
      raise GetoptError(str(e)) from e
    for tag_choice in tag_choices:
      if tag_choice.choice:
        if tag_choice.tag not in disc:
          disc.set(tag_choice.tag)
      elif tag_choice.tag in disc:
        disc.discard(tag_choice.tag)

  @popopts(
      a=(
          'all_fields',
          ''' Dump all tags. By default the Musicbrainz API fields
              and *_relation fields are suppressed.''',
      ),
      R=('do_refresh', 'Explicitly refresh the entity before dumping it.'),
  )
  def cmd_dump(self, argv):
    ''' Usage: {cmd} [-a] [-R] [entity...]
          Dump each entity.
          If no entities are supplied, dump the entity for the disc in the CD drive.
    '''
    options = self.options
    mbdb = options.mbdb
    sqltags = mbdb.sqltags
    all_fields = options.all_fields
    do_refresh = options.do_refresh
    if not argv:
      if mbdb.dev_info:
        argv = [f'disc.{mbdb.dev_info.id}']
      else:
        raise GetoptError("missing entities and no CD in the drive")
    q = ListQueue(argv)
    for name in q:
      with Pfx(name):
        if name.endswith('.') and is_identifier(name[:-1]):
          q.prepend(sqltags.keys(prefix=name[:-1]))
          continue
        if name not in sqltags:
          warning("unknown")
          continue
        te = sqltags[name]
        if do_refresh:
          mbdb.refresh(te, refetch=options.force, recurse=True)
        te.dump(compact=True, keys=sorted(te.keys()) if all_fields else None)

  @popopts
  def cmd_edit(self, argv):
    ''' Usage: edit criteria...
          Edit the entities specified by criteria.
    '''
    options = self.options
    mbdb = options.mbdb
    badopts = False
    tag_criteria, argv = self.parse_tagset_criteria(argv)
    if not tag_criteria:
      warning("missing tag criteria")
      badopts = True
    if argv:
      warning("remaining unparsed arguments: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    tes = list(mbdb.sqltags.find(tag_criteria))
    changed_tes = SQLTagSet.edit_tagsets(tes)  # verbose=state.verbose
    for te in changed_tes:
      print("changed", repr(te.name or te.id))

  @popopts
  def cmd_meta(self, argv):
    ''' Usage: {cmd} entity...
          Print the metadata about entity, where entity has the form
          *type_name*`.`*uuid* such as "artist"
          and a Musicbrainz UUID for that type.
    '''
    options = self.options
    mbdb = options.mbdb
    if not argv:
      raise GetoptError("missing metanames")
    for metaname in argv:
      with Pfx("metaname %r", metaname):
        metadata = mbdb.ontology[metaname]
        print(' ', metaname, metadata)

  @popopts
  def cmd_eject(self, argv):
    ''' Usage: {cmd}
          Eject the disc.
    '''
    if argv:
      raise GetoptError("extra arguments")
    return os.system('eject')

  @popopts
  def cmd_mbq(self, argv):
    ''' Usage: {cmd} type.id
          Do a MusicBrainzNG API query, report the result.
    '''
    if not argv:
      raise GetoptError('missing type:id')
    type_and_id = argv.pop(0)
    if argv:
      raise GetoptError(
          f'extra arguments after type:id {type_and_id!r}: {argv!r}'
      )
    mbdb = self.options.mbdb
    qtype, qid = type_and_id.split('.', 1)
    result = pfx_call(mbdb.query, qtype, qid)
    pprint(result)
    assert 'medium-list' in result
    mbe = mbdb[qtype, qid]
    mbdb.apply_dict(mbe, result)
    printt(
        ["Final MBE", mbe.name],
        *(
            [f'  {tag_name}', tag_value]
            for tag_name, tag_value in sorted(mbe.items())
            if not tag_name.startswith('musicbrainzngs.api.query.')
        ),
    )

  @popopts
  def cmd_probe(self, argv):
    ''' Usage: {cmd} [disc_id]
          Probe Musicbrainz about the current disc.
          disc_id   Optional disc id to query instead of obtaining
                    one from the current inserted disc.
    '''
    disc = self.popdisc(argv, '.')
    if argv:
      raise GetoptError("extra arguments after disc_id: %r" % (argv,))
    options = self.options
    try:
      pfx_call(probe_disc, self.device_id, options.mbdb, disc_id=disc.mbkey)
    except DiscError as e:
      error("%s", e)
      return 1
    return 0

  # pylint: disable=too-many-locals
  @popopts
  def cmd_rip(self, argv):
    ''' Usage: {cmd} [-F codecs] [-n] [disc_id]
          Pull the audio into a subdirectory of the current directory.
          -F codecs Specify the formats/codecs to produce.
          -n        No action; recite planned actions.
    '''
    options = self.options
    fstags = options.fstags
    dirpath = options.dirpath
    options.popopts(argv, F_='codecs_spec', n='dry_run')
    disc = self.popdisc(argv, '.')
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    try:
      rip(
          self.device_id,
          options.mbdb,
          output_dirpath=dirpath,
          audio_outputs=options.codecs,
          disc_id=disc.mbkey,
          fstags=fstags,
          no_action=options.dry_run,
          split_by_codec=True,
      )
    except discid.disc.DiscError as e:
      error("disc error: %s", e)
      return 1
    os.system("eject")
    return 0

  @popopts
  def cmd_toc(self, argv):
    ''' Usage: {cmd} [disc_id]
          Print a table of contents for the current disc.
    '''
    disc = self.popdisc(argv, '.')
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    options = self.options
    ##MB = options.mbdb
    ##with stackattrs(MB, dev_info=dev_info):
    with Pfx("discid %s", disc.mbkey):
      disc_tags = disc.disc_tags()
      print(disc_tags.disc_title)
      print(disc_tags.disc_artist_credit)
      for track_index, recording in enumerate(disc.recordings):
        track_tags = disc.track_tags(track_index)
        track_title = (
            f"{track_tags.track_number:02}"
            f" - {track_tags.track_title}"
            f" -- {track_tags.track_artist_credit}"
        )
        print(track_title)
    return 0

if __name__ == '__main__':
  sys.exit(main(sys.argv))

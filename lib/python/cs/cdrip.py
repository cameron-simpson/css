#!/usr/bin/env python3
#

''' A tool for working with audio Compact Discs (CDs),
    uses the discid and musicbrainzngs modules.
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property
from getopt import getopt, GetoptError
import os
from os.path import (
    dirname,
    exists as existspath,
    expanduser,
    isdir as isdirpath,
    join as joinpath,
)
from signal import SIGINT, SIGTERM
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
from cs.context import stackattrs, stack_signals
from cs.deco import fmtdoc
from cs.excutils import unattributable
from cs.ffmpegutils import convert as ffconvert, MetaData as FFMetaData
from cs.fileutils import atomic_filename
from cs.fs import needdir, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.lex import cutsuffix, is_identifier, r
from cs.logutils import error, warning, info, debug
from cs.mappings import AttrableMapping
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.psutils import run
from cs.queues import ListQueue
from cs.resources import MultiOpenMixin, RunState, uses_runstate, RunStateMixin
from cs.seq import unrepeated
from cs.sqltags import SQLTags, SQLTagSet, SQLTagsCommandsMixin, FIND_OUTPUT_FORMAT_DEFAULT
from cs.tagset import TagSet, TagsOntology
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
  class Options(BaseCommand.Options):
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
        **BaseCommand.Options.COMMON_OPT_SPECS,
        d_=('dirpath', 'Specify the output directory path.'),
        D_=(
            'device',
            '''Device to access. This may be omitted or "" or "default"
               for the default device as determined by the discid module.''',
        ),
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
                sqltags=mbdb.sqltags,
                verbose=True,
            ):

              @uses_runstate
              def on_signal(sig, frame, *, runstate: RunState):
                ''' Note signal and cancel the `RunState`.
                '''
                warning("signal %s at %s, cancelling runstate", sig, frame)
                runstate.cancel()

              with stack_signals([SIGINT, SIGTERM], on_signal):
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
      raise IndexError("missing disc_id")
    else:
      disc_id = default
    dev_info = None
    if disc_id == '.':
      dev_info = pfx_call(self.device_info, device_id)
      disc_id = dev_info.id
    disc = self.options.mbdb.discs[disc_id]
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
      else:
        if tag_choice.tag in disc:
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
    tes = list(mbdb.find(tag_criteria))
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
class _MBTagSet(SQLTagSet):
  ''' An `SQLTagSet` subclass for MB entities.
  '''

  MB_QUERY_PREFIX = 'musicbrainzngs.api.query.'
  MB_QUERY_RESULT_TAG_NAME = MB_QUERY_PREFIX + 'result'
  MB_QUERY_TIME_TAG_NAME = MB_QUERY_PREFIX + 'time'

  def __repr__(self):
    return "%s:%s:%r" % (type(self).__name__, self.name, self.as_dict())

  @property
  def query_result(self):
    ''' The Musicbrainz query result, fetching it if necessary. '''
    mb_result = self.get(self.MB_QUERY_RESULT_TAG_NAME)
    if not mb_result:
      self.refresh(refetch=True)
      try:
        mb_result = self[self.MB_QUERY_RESULT_TAG_NAME]
      except KeyError as e:
        raise AttributeError(f'no {self.MB_QUERY_RESULT_TAG_NAME}: {e}') from e
    typename, db_id = self.name.split('.', 1)
    self.sqltags.mbdb.apply_dict(typename, db_id, mb_result, seen=set())
    return mb_result

  def dump(self, keys=None, **kw):
    if keys is None:
      keys = sorted(
          k for k in self.keys() if (
              not k.startswith(self.MB_QUERY_PREFIX)
              and not k.endswith('_relation')
          )
      )
    return super().dump(keys=keys, **kw)

  @property
  def mbdb(self):
    ''' The associated `MBDB`.
    '''
    return self.sqltags.mbdb

  def refresh(self, **kw):
    ''' Refresh the MBDB entry for this `TagSet`.
    '''
    return self.mbdb.refresh(self, **kw)

  @property
  def mbtype(self):
    ''' The MusicBrainz type (usually a UUID or discid).
        Returns `None` for noncompound names.
    '''
    try:
      type_name, _ = self.name.split('.', 1)
    except ValueError:
      return None
    return type_name

  @property
  def mbkey(self):
    ''' The MusicBrainz key (usually a UUID or discid).
    '''
    with Pfx("%s.mbkey: split(.,1)", self.name):
      _, mbid = self.name.split('.', 1)
    return mbid

  @property
  def mbtime(self):
    ''' The timestamp of the most recent .refresh() API call, or `None`.
    '''
    if self.MB_QUERY_TIME_TAG_NAME not in self:
      return None
    return self[self.MB_QUERY_TIME_TAG_NAME]

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
  ) -> '_MBTagSet':
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
    te_name = f"{type_name}.{id}"
    te = self.sqltags[te_name]
    return te

class MBArtist(_MBTagSet):
  ''' A Musicbrainz artist entry.
  '''

class MBHasArtistsMixin:
  ''' A mixin for `_MBTagSet`s with artists.
      This depends on the class specific property `artist_refs`
      which is a list of Musicbrainzng artist references
      used to construct artist credits:
      either a `str` of literal interpolated text
      or a `dict` with an `"artist"` entry which is an artist id.
  '''

  @property
  def artist_refs(self):
    ''' Stub method to return a list of artist references.
        This is overridden by subclasses which use the mixin.
    '''
    raise RuntimeError(f'no .artist_refs property on type {type(self)!r}')

  @cached_property
  @typechecked
  def artists(self) -> List[Union[MBArtist, str]]:
    '''A list of `MBArtist`s, possibly interspersed with `str`.'''
    artists = []
    for artist_ref in self.artist_refs:
      with Pfx("artist_ref %s", r(artist_ref)):
        if isinstance(artist_ref, str):
          artists.append(artist_ref)
          continue
        artist_id = artist_ref['artist']
        artist = self.resolve_id('artist', artist_id)
        artists.append(artist)
    return artists

  @property
  def artist_credit(self) -> str:
    '''A credit string computes from `self.artists`.'''
    strs = []
    sep = ''
    for artist in self.artists:
      if isinstance(artist, str):
        strs.append(artist)
        sep = ''
      else:
        try:
          name = artist['name_']
        except KeyError:
          warning("no ['name_']: artist keys = %r", sorted(artist.keys()))
        else:
          strs.append(sep)
          strs.append(name)
          sep = ', '
    return ''.join(strs)

  @property
  def artist_names(self) -> List[str]:
    '''A list of the artist names.'''
    names = []
    for artist in self.artists:
      if isinstance(artist, str):
        continue
      try:
        name = artist['name_']
      except KeyError:
        warning("no ['name_']: artist keys = %r", sorted(artist.keys()))
      else:
        names.append(name)
    return names

class MBDisc(MBHasArtistsMixin, _MBTagSet):
  ''' A Musicbrainz disc entry.
  '''

  @property
  def discid(self):
    ''' The disc id to be used in lookups.
        For most discs with is `self.mbkey`, but if our discid is unknown
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
  @unattributable
  def artist_refs(self):
    # we get the artist refs from the release
    return self.release.artist

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
    discid = self.discid
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

class MBRecording(MBHasArtistsMixin, _MBTagSet):
  ''' A Musicbrainz recording entry, a single track.
  '''

  @property
  def artist_refs(self):
    return self.query_result['artist-credit']

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

class MBRelease(MBHasArtistsMixin, _MBTagSet):
  ''' A Musicbrainz recording entry, a single track.
  '''

  @property
  def artist_refs(self):
    return self.query_result['artist-credit']

class MBSQLTags(SQLTags):
  ''' Musicbrainz `SQLTags` with special `TagSetClass`.
  '''

  TAGSETCLASS_DEFAULT = _MBTagSet

  # map 'foo' from 'foo.bah' to a particular TagSet subclass
  TAGSETCLASS_PREFIX_MAPPING = {
      'artist': MBArtist,
      'disc': MBDisc,
      'recording': MBRecording,
      'release': MBRelease,
  }

  def default_factory(
      self,
      name: Optional[str] = None,
      skip_refresh=None,
      **kw,
  ):
    if skip_refresh is None:
      skip_refresh = '.' not in name
    return super().default_factory(name, skip_refresh=skip_refresh, **kw)

  @classmethod
  def infer_db_url(cls):
    return super().infer_db_url(
        envvar=MBDB_PATH_ENVVAR, default_path=MBDB_PATH_DEFAULT
    )

  @pfx_method
  def __getitem__(self, index):
    assert not index.startswith('releases.')
    if isinstance(index, str) and index.startswith('disc.'):
      discid = index[5:]
      try:
        UUID(discid)
      except ValueError:
        pass
      else:
        raise RuntimeError(
            "%s.__getitem__(%r): discid is a UUID, should not be!" %
            (type(self).__name__, index)
        )
    return super().__getitem__(index)

class MBDB(MultiOpenMixin, RunStateMixin):
  ''' An interface to MusicBrainz with a local `TagsOntology(SQLTags)` cache.
  '''

  # Mapping of Tag names whose type is not themselves.
  # TODO: get this from the ontology type?
  TYPE_NAME_REMAP = {
      'artist-credit': 'artist',
      'begin-area': 'area',
      'end-area': 'area',
      'label-info': 'label',
      'medium': 'disc',
      'release-event': 'event',
      'release-group': 'release',
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
    RunStateMixin.__init__(self)
    # can be overlaid with discid.read of the current CDROM
    self.dev_info = None
    sqltags = self.sqltags = MBSQLTags(mbdb_path)
    sqltags.mbdb = self
    with sqltags:
      ont = self.ontology = TagsOntology(sqltags)
      self.artists = sqltags.subdomain('artist')
      ont['artists'].update(type='list', member_type='artist')
      self.discs = sqltags.subdomain('disc')
      ont['discs'].update(type='list', member_type='disc')
      self.recordings = sqltags.subdomain('recording')
      ont['recordings'].update(type='list', member_type='recording')

  def __str__(self):
    return f'{self.__class__.__name__}({self.sqltags})'

  @contextmanager
  def startup_shutdown(self):
    ''' Context manager for open/close.
    '''
    with self.sqltags:
      yield

  def find(self, criteria):
    ''' Find entities in the cache database.
    '''
    return self.sqltags.find(criteria)

  def __getitem__(self, index):
    ''' Fetching via the MBDB triggers a refresh.
    '''
    te = self.sqltags[index]
    if '.' in te.name:
      te.refresh()
    return te

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
  @require(lambda te0: '.' in te0.name)
  @typechecked
  def refresh(
      self,
      te0: _MBTagSet,
      refetch: bool = True,  ##False,
      recurse: Union[bool, int] = False,
      no_apply=None,
  ) -> dict:
    ''' Query MusicBrainz about the entity `te`, fill recursively.
        Return the query result of `te`.
    '''
    with run_task("refresh %s" % te0.name) as proxy:
      q = ListQueue([te0])
      for te in unrepeated(q, signature=lambda te: te.name):
        with Pfx("refresh te=%s", te.name):
          if self.runstate.cancelled:
            break
          with proxy.extend_prefix(": " + te.name):
            if '.' not in te.name:
              warning("refresh: skip %r, not dotted", te.name)
              continue
            with Pfx("refresh te %s", te.name):
              mbtype = te.mbtype
              mbkey = te.mbkey
              if mbtype is None:
                warning("no MBTYPE, not refreshing")
                continue
              if mbtype in ('cdstub',):
                warning("no refresh for mbtype=%r", mbtype)
                continue
              q_result_tag = te.MB_QUERY_RESULT_TAG_NAME
              q_time_tag = te.MB_QUERY_TIME_TAG_NAME
              if (refetch or q_result_tag not in te or q_time_tag not in te
                  or not te[q_result_tag]):
                get_type = mbtype
                id_name = 'id'
                record_key = None
                if mbtype == 'disc':
                  # we use get_releases_by_discid() for discs
                  get_type = 'releases'
                  id_name = 'discid'
                  record_key = 'disc'
                  if no_apply is None:
                    no_apply = True
                else:
                  if no_apply is None:
                    no_apply = False
                with stackattrs(
                    proxy,
                    text=("query(%r,%r,...)" % (get_type, mbkey)),
                ):
                  try:
                    A = self.query(
                        get_type, mbkey, id_name, record_key=record_key
                    )
                  except (musicbrainzngs.musicbrainz.MusicBrainzError,
                          musicbrainzngs.musicbrainz.ResponseError) as e:
                    warning("%s: not refreshed: %s", type(e).__name__, e)
                    raise
                    ##A = te.get(te.MB_QUERY_RESULT_TAG_NAME, {})
                  else:
                    te[q_time_tag] = time.time()
                    # record the full response data for forensics
                    te[te.MB_QUERY_PREFIX + 'get_type'] = get_type
                    ##te[te.MB_QUERY_PREFIX + 'includes'] = includes
                    te[te.MB_QUERY_PREFIX + 'result'] = A
                    if not no_apply:
                      self.apply_dict(mbtype, mbkey, A, seen=set())
                    self.sqltags.flush()
              else:
                A = te[te.MB_QUERY_PREFIX + 'result']
              ##self.apply_dict(mbtype, mbkey, A, seen=set())  # q=q
              self.sqltags.flush()
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
      return te0.get(te0.MB_QUERY_RESULT_TAG_NAME, {})

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
      type_name: str,
      id: str,  # pylint: disable=redefined-builtin
      d: dict,
      *,
      get_te=None,
      q: Optional[ListQueue] = None,
      seen: set,
  ):
    ''' Apply an `'id'`-ed dict from MusicbrainzNG query result `d`
        associated with its `type_name` and `id` value
        to the corresponding entity obtained by `get_te(type_name,id)`.

        Parameters:
        * `type_name`: the entity type, eg `'disc'`
        * `id`: the entity identifying value, typically a discid or a UUID
        * `d`: the `dict` to apply to the entity
        * `get_te`: optional entity fetch function;
          the default calls `self.sqltags[f"{type_name}.{id}"]`
        * `q`: optional queue onto which to put related entities
    '''
    sig = type_name, id
    if sig in seen:
      return
    seen.add(sig)
    if get_te is None:
      # pylint: disable=unnecessary-lambda-assignment
      get_te = lambda type_name, id: self.sqltags[f"{type_name}.{id}"]
    if 'id' in d:
      assert d['id'] == id, "id=%s but d['id']=%s" % (r(id), r(d['id']))
    te = get_te(type_name, id)
    counts = {}  # sanity check of foo-count against foo-list
    # scan the mapping, recognise contents
    for k, v in d.items():
      with Pfx("%s=%s", k, r(v, 20)):
        # skip the id field, already checked
        if k == 'id':
          continue
        # derive tag_name and field role (None, count, list)
        k_type_name, suffix = self.key_type_name(k)
        # note expected counts
        if suffix == 'count':
          assert isinstance(v, int)
          counts[k_type_name] = v
          continue
        if suffix == 'list':
          if k_type_name in ('offset',):
            continue
          # apply members
          assert isinstance(v, list)
          for list_entry in v:
            if not isinstance(list_entry, dict):
              continue
            try:
              entry_id = list_entry['id']
            except KeyError:
              for le_key, le_value in list_entry.items():
                if isinstance(le_value, dict) and 'id' in le_value:
                  self.apply_dict(
                      le_key, le_value['id'], le_value, q=q, seen=seen
                  )
              continue
            self.apply_dict(k_type_name, entry_id, list_entry, q=q, seen=seen)
          continue
        if suffix in ('relation', 'relation-list'):
          continue
        tag_name = k_type_name.replace('-', '_')
        if tag_name == 'name':
          tag_name = 'name_'
        tag_value = te.get(tag_name, '')
        if not tag_value:
          v = self._fold_value(k_type_name, v, get_te=get_te, q=q, seen=seen)
          # apply the folded value
          te.set(tag_name, v)
    for k, c in counts.items():
      with Pfx("counts[%r]=%d", k, c):
        if k in te:
          assert len(te[k]) == c

  @typechecked
  def _fold_value(
      self,
      type_name: str,
      v,
      *,
      get_te=None,
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
        v = self._fold_id_dict(type_name, v, get_te=get_te, q=q, seen=seen)
      else:
        v = dict(v)
        for k, subv in list(v.items()):
          type_name, _ = self.key_type_name(k)
          v[k] = self._fold_value(
              ##type_name, subv, get_te=get_te, q=q, seen=seen
              type_name,
              subv,
              get_te=get_te,
              q=q,
              seen=seen
          )
    elif isinstance(v, list):
      v = list(v)
      for i, subv in enumerate(v):
        with Pfx("[%d]=%s", i, r(subv, 20)):
          v[i] = self._fold_value(
              type_name, subv, get_te=get_te, q=q, seen=seen
          )
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
      get_te=None,
      q=None,
      seen: set,
  ):
    ''' Apply `d` (a `dict`) to the entity identified by `(type_name,d['id'])`,
        return `d['id']`.

        This is used to replace identified records in a MusicbrainzNG query result
        with their identifier.

        If `q` is not `None`, queue `get_te(type_name, id)` for processing
        by the enclosing `refresh()`.
    '''
    id = d['id']  # pylint: disable=redefined-builtin
    assert isinstance(id, str) and id, (
        "expected d['id'] to be a nonempty string, got: %s" % (r(id),)
    )
    self.apply_dict(type_name, id, d, get_te=get_te, q=q, seen=seen)
    return id

  def _tagif(self, tags, name, value):
    ''' Apply a new `Tag(name,value)` to `tags` if `value` is not `None`.
    '''
    with self.sqltags:
      if value is not None:
        tags.set(name, value)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

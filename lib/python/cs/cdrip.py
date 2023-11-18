#!/usr/bin/env python3
#

''' A tool for working with audio Compact Discs (CDs),
    uses the discid and musicbrainzngs modules.
'''

# Extract discid and track info from a CD as a preliminary to
# constructing a FreeDB CDDB entry. Used by cdsubmit.
# Rework of cddiscinfo in Python, since the Perl libraries aren't
# working any more; update to work on OSX and use MusicBrainz.
#	- Cameron Simpson <cs@cskk.id.au> 31mar2016
#

from contextlib import contextmanager
from dataclasses import dataclass, field
from getopt import getopt, GetoptError
import os
from os.path import (
    dirname,
    exists as existspath,
    expanduser,
    isdir as isdirpath,
    join as joinpath,
)
from pprint import pformat, pprint
from signal import SIGINT, SIGTERM
import sys
import time
from typing import Optional, Union
from uuid import UUID

import discid
from discid.disc import DiscError
from icontract import require
import musicbrainzngs
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import stackattrs, stack_signals
from cs.deco import cachedmethod, fmtdoc
from cs.fileutils import atomic_filename
from cs.fs import needdir
from cs.fstags import FSTags
from cs.lex import cutsuffix, is_identifier, r
from cs.logutils import error, warning, info, debug
from cs.mappings import AttrableMapping
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.psutils import run
from cs.queues import ListQueue
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.seq import unrepeated
from cs.sqltags import SQLTags, SQLTagSet, SQLTagsCommand
from cs.tagset import TagSet, TagsOntology
from cs.upd import run_task, print

__version__ = '20201004-dev'

musicbrainzngs.set_useragent(__name__, __version__, os.environ['EMAIL'])

CDRIP_DEV_ENVVAR = 'CDRIP_DEV'
CDRIP_DEV_DEFAULT = 'default'

CDRIP_DIR_ENVVAR = 'CDRIP_DIR'
CDRIP_DIR_DEFAULT = '~/var/cdrip'

MBDB_PATH_ENVVAR = 'MUSICBRAINZ_SQLTAGS'
MBDB_PATH_DEFAULT = '~/var/cache/mbdb.sqlite'

def main(argv=None):
  ''' Call the command line main programme.
  '''
  return CDRipCommand(argv).run()

class CDRipCommand(BaseCommand):
  ''' 'cdrip' command line.
  '''

  GETOPT_SPEC = 'd:D:fM:'

  USAGE_FORMAT = r'''Usage: {cmd} [options...] subcommand...
    -d output_dir Specify the output directory path.
    -D device     Device to access. This may be omitted or "default" or
                  "" for the default device as determined by the discid module.
    -f            Force. Read disc and consult Musicbrainz even if a toc file exists.
    -M mbdb_path  Specify the location of the MusicBrainz SQLTags cache.

  Environment:
    {CDRIP_DEV_ENVVAR}            Default CDROM device.
                         default {CDRIP_DEV_DEFAULT}.
    {CDRIP_DIR_ENVVAR}            Default output directory path.,
                         default {CDRIP_DIR_DEFAULT}.
    {MBDB_PATH_ENVVAR}  Default location of MusicBrainz SQLTags cache,
                         default {MBDB_PATH_DEFAULT}.'''

  USAGE_KEYWORDS = {
      'CDRIP_DEV_ENVVAR': CDRIP_DEV_ENVVAR,
      'CDRIP_DEV_DEFAULT': CDRIP_DEV_DEFAULT,
      'CDRIP_DIR_ENVVAR': CDRIP_DIR_ENVVAR,
      'CDRIP_DIR_DEFAULT': CDRIP_DIR_DEFAULT,
      'MBDB_PATH_ENVVAR': MBDB_PATH_ENVVAR,
      'MBDB_PATH_DEFAULT': MBDB_PATH_DEFAULT,
  }

  SUBCOMMAND_ARGV_DEFAULT = 'rip'

  @dataclass
  class Options(BaseCommand.Options):
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

  def apply_opts(self, opts):
    ''' Apply the command line options.
    '''
    options = self.options
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-d':
          options.dirpath = val
        elif opt == '-D':
          options.device = val
        elif opt == '-f':
          options.force = True
        elif opt == '-M':
          options.mbdb_path = val
        else:
          raise GetoptError("unimplemented option")
    if not isdirpath(options.dirpath):
      raise GetoptError(
          "output directory: not a directory: %r" % (options.dirpath,)
      )

  @contextmanager
  def run_context(self):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    with super().run_context():
      options = self.options
      fstags = FSTags()
      mbdb = MBDB(mbdb_path=options.mbdb_path, runstate=options.runstate)
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
            with stackattrs(options, fstags=fstags, mbdb=mbdb,
                            sqltags=mbdb.sqltags, verbose=True):

              def on_signal(sig, frame):
                ''' Note signal and cancel the `RunState`.
                '''
                warning("signal %s at %s, cancelling runstate", sig, frame)
                options.runstate.cancel()

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

  cmd_dbshell = SQLTagsCommand.cmd_dbshell

  def cmd_dump(self, argv):
    ''' Usage: {cmd} [-a] [-R] [entity...]
          Dump each entity.
          -a    Dump all tags. By default the Musicbrainz API fields
                and *_relation fields are suppressed.
          -R    Explicitly refresh the entity before dumping it.
          If no entities are supplied, dump the entity for the disc in the CD drive.
    '''
    options = self.options
    mbdb = options.mbdb
    sqltags = mbdb.sqltags
    all_fields = False
    do_refresh = False
    force_refresh = False
    opts, argv = getopt(argv, 'aR')
    for opt, _ in opts:
      with Pfx(opt):
        if opt == '-a':
          all_fields = True
        elif opt == '-R':
          do_refresh = True
        else:
          raise RuntimeError("unimplemented option")
    if not argv:
      if mbdb.dev_info:
        argv = ['disc.' + mbdb.dev_info.id]
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

  def cmd_edit(self, argv):
    ''' Usage: edit criteria...
          Edit the entities specified by criteria.
    '''
    options = self.options
    mbdb = options.mbdb
    badopts = False
    tag_criteria, argv = SQLTagsCommand.parse_tagset_criteria(argv)
    if not tag_criteria:
      warning("missing tag criteria")
      badopts = True
    if argv:
      warning("remaining unparsed arguments: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    tes = list(mbdb.find(tag_criteria))
    changed_tes = SQLTagSet.edit_entities(tes)  # verbose=state.verbose
    for te in changed_tes:
      print("changed", repr(te.name or te.id))

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

  def cmd_probe(self, argv):
    ''' Usage: {cmd} [disc_id]
          Probe Musicbrainz about the current disc.
          disc_id   Optional disc id to query instead of obtaining
                    one from the current inserted disc.
    '''
    disc_id = None
    if argv:
      disc_id = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments after disc_id: %r" % (argv,))
    options = self.options
    try:
      probe_disc(self.device_id, options.mbdb, disc_id=disc_id)
    except DiscError as e:
      error("%s", e)
      return 1
    return 0

  # pylint: disable=too-many-locals
  def cmd_rip(self, argv):
    ''' Usage: {cmd} [-n] [disc_id]
          Pull the audio into a subdirectory of the current directory.
          -n  No action; recite planned actions.
    '''
    options = self.options
    fstags = options.fstags
    dirpath = options.dirpath
    no_action = False
    disc_id = None
    if argv and argv[0] == '-n':
      no_action = True
      argv.pop(0)
    if argv:
      disc_id = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    try:
      rip(
          self.device_id,
          options.mbdb,
          output_dirpath=dirpath,
          disc_id=disc_id,
          fstags=fstags,
          no_action=no_action,
          split_by_format=True,
      )
    except discid.disc.DiscError as e:
      error("disc error: %s", e)
      return 1
    return 0

  def cmd_toc(self, argv):
    ''' Usage: {cmd} [disc_id]
          Print a table of contents for the current disc.
    '''
    disc_id = None
    if argv:
      disc_id = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    options = self.options
    MB = options.mbdb
    if disc_id is None:
      try:
        dev_info = discid.read(device=self.device_id)
      except discid.disc.DiscError as e:
        error("disc error: %s", e)
        return 1
      disc_id = dev_info.id
    else:
      dev_info = None
    with stackattrs(MB, dev_info=dev_info):
      with Pfx("discid %s", disc_id):
        disc = MB.discs[disc_id]
        print(disc.title)
        print(", ".join(disc.artist_names))
        for tracknum, recording in enumerate(disc.recordings, 1):
          print(
              tracknum, recording.title, '--',
              ", ".join(recording.artist_names)
          )
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
    print("  already present:", disc)
    print(type(disc))
    mbdb.stale(disc)
    mbdb.refresh(
        disc,
        recurse=True,
    )
    return
  print("  missing disc_id", disc_id)
  includes = ['artist-credits']
  get_type = 'releases'
  id_name = 'discid'
  record_key = 'disc'
  with stackattrs(mbdb, dev_info=dev_info):
    query_time = time.time()
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
def rip(
    device,
    mbdb,
    *,
    output_dirpath,
    disc_id=None,
    fstags=None,
    no_action=False,
    split_by_format=False,
):
  ''' Pull audio from `device` and save in `output_dirpath`.
  '''
  if not isdirpath(output_dirpath):
    raise ValueError(f'not a directory: {output_dirpath!r}')
  if disc_id is None:
    dev_info = discid.read(device=device)
    disc_id = dev_info.id
  if fstags is None:
    fstags = FSTags()
  with stackattrs(mbdb, dev_info=dev_info):
    with Pfx("MB: discid %s", disc_id, print=True):
      X("mbdb.discs = %s", mbdb.discs)
      disc = mbdb.discs[disc_id]
    X("disc = %r", disc)
    release = disc.release
    title = disc.title or "UNTITLED"
    artist_credit = ", ".join(disc.artist_names or "NO_ARTISTS")
    recordings = disc.recordings
    level1 = artist_credit
    level2 = disc.title or "UNTITLED"
    if release.medium_count > 1:
      level2 += f" ({disc.medium_position} of {disc.medium_count})"
    disc_subpath = joinpath(
        level1.replace(os.sep, ':'),
        level2.replace(os.sep, ':'),
    )
    for tracknum, recording in enumerate(recordings, 1):
      with Pfx("track %d", tracknum):
        recording_md = disc.ontology.metadata('recording', recording.id)
        track_fstags = TagSet(
            discid=disc.mbkey,
            artists=recording.artist_names,
            title=recording.title,
            track=tracknum
        )
        track_artists = recording.artist_credit
        track_base = f"{tracknum:02} - {recording.title} -- {track_artists}".replace(
            os.sep, '-'
        )
        wav_filename = joinpath(
            output_dirpath,
            'wav' if split_by_format else '',
            disc_subpath,
            track_base + '.wav',
        )
        mp3_filename = joinpath(
            output_dirpath,
            'mp3' if split_by_format else '',
            disc_subpath,
            track_base + '.mp3',
        )
        if existspath(mp3_filename):
          warning("MP3 file already exists, skipping track: %r", mp3_filename)
          continue
        if existspath(wav_filename):
          info("using existing WAV file: %r", wav_filename)
        else:
          no_action or needdir(dirname(wav_filename), use_makedirs=True)
          fstags[dirname(wav_filename)].update(
              discid=disc.id,
              title=disc.title,
              artists=disc.artist_names,
          )
          with atomic_filename(wav_filename) as T:
            argv = ['cdparanoia', '-d', device, '-w', str(tracknum), T.name]
            run(argv, doit=not no_action, quiet=False, check=True)
          if no_action:
            os.unlink(wav_filename)
        if no_action:
          print("fstags[%r].update(%s)" % (wav_filename, track_fstags))
        else:
          fstags[wav_filename].update(track_fstags)
          fstags[wav_filename].rip_command = argv
        no_action or needdir(dirname(mp3_filename), use_makedirs=True)
        fstags[dirname(mp3_filename)].update(
            discid=disc.id,
            title=disc.title,
            artists=disc.artist_names,
        )
        with atomic_filename(mp3_filename) as T:
          argv = [
              'lame',
              '-q',
              '7',
              '-V',
              '0',
              '--tt',
              recording.title or "UNTITLED",
              '--ta',
              track_artists or "NO ARTISTS",
              '--tl',
              level2,
              ## '--ty',recording year
              '--tn',
              str(tracknum),
              ## '--tg', recording genre
              ## '--ti', album cover filename
              wav_filename,
              T.name,
          ]
          run(argv, doit=not no_action, quiet=False, check=True)
        if no_action:
          os.unlink(mp3_filename)
        if no_action:
          print("fstags[%r].update(%s)" % (mp3_filename, track_fstags))
        else:
          fstags[mp3_filename].conversion_command = argv
          fstags[mp3_filename].update(track_fstags)
  if not no_action:
    run(['ls', '-la', dirname(mp3_filename)])  # pylint: disable=subprocess-run-check
    os.system("eject")

# pylint: disable=too-many-ancestors
class _MBTagSet(SQLTagSet):
  ''' An `SQLTagSet` subclass for MB entities.
  '''

  MB_QUERY_PREFIX = 'musicbrainzngs.api.query.'
  MB_QUERY_RESULT_TAG_NAME = MB_QUERY_PREFIX + 'result'
  MB_QUERY_TIME_TAG_NAME = MB_QUERY_PREFIX + 'time'

  def __repr__(self):
    return "%s:%s:%r" % (type(self).__name__, self.name, self.as_dict())

  def __getattr__(self, attr):
    try:
      return super().__getattr__(attr)
    except AttributeError:
      # no direct tag or other attribute, look in the MB query result
      mb_result = self.query_result
      try:
        value = mb_result[attr.replace('_', '-')]
        return value
      except KeyError as e:
        raise AttributeError("%s: no .%s attribute" % (self.name, attr))

  @property
  def mbdb(self):
    ''' The associated `MBDB`.
    '''
    return self.sqltags.mbdb

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

  # pylint: disable=too-many-branches,too-many-statements
  def refresh(self, force=False):
    ''' Query MusicBrainz, fill in tags.

        This method has a fair bit of entity type specific knowledge.
    '''
    onttype = self.mbtype
    if onttype is None:
      ##warning("%s: no MBTYPE, not refreshing", self)
      return
    if not force and self.MB_QUERY_TIME_TAG_NAME in self:
      return
    mbkey = self.mbkey
    get_type = onttype
    id_name = 'id'
    record_key = None
    includes = None
    if onttype == 'artist':
      includes = ['annotation']
    elif onttype == 'disc':
      includes = ['artists', 'recordings']
      get_type = 'releases'
      id_name = 'discid'
      record_key = 'disc'
    elif onttype == 'recording':
      includes = ['artists', 'tags']
    A = self.mbdb.query(get_type, mbkey, includes, id_name, record_key)
    self[self.MB_QUERY_TIME_TAG_NAME] = time.time()
    # record the full response data for forensics
    self[self.MB_QUERY_PREFIX + 'get_type'] = get_type
    self[self.MB_QUERY_PREFIX + 'includes'] = includes
    self[self.MB_QUERY_PREFIX + 'result'] = A
    # modify A for discs
    if onttype == 'disc':
      # drill down to the release and medium containing the disc id
      # replace A with a dict with selected values
      found_medium = None
      found_release = None
      for release in A['release-list']:
        if found_medium:
          break
        for medium in release['medium-list']:
          if found_medium:
            break
          for disc in medium['disc-list']:
            if found_medium:
              break
            if disc['id'] == mbkey:
              # matched disc
              found_medium = medium
              found_release = release
      assert found_medium
      A = {
          'title':
          found_release.get('title'),
          'medium-count':
          found_release['medium-count'],
          'medium-position':
          found_medium['position'],
          'artist-credit':
          found_release['artist-credit'],
          'recordings': [
              track['recording']['id']
              for track in found_medium.get('track-list')
          ],
      }

    # store salient fields
    k_tag_map = {
        'name': onttype + '_name',
        'artist-credit': 'artists',
    }
    k_tag_map_reverse = {v: k for k, v in k_tag_map.items()}
    for k, v in A.items():
      with Pfx("%s=%r", k, v):
        if k == 'id':
          assert v == mbkey, "A[%r]=%r != mbkey %r" % (k, v, mbkey)
          continue
        if k in ('begin-area', 'life-span', 'release-count'):
          continue
        if k in k_tag_map_reverse:
          warning(
              "SKIP %r=%r, would be overridden by k_tag_map_reverse[%r]=%r", k,
              v, k, k_tag_map_reverse[k]
          )
          continue
        tag_name = k_tag_map.get(k, k.replace('-', '_'))
        if k in ('artist-credit-phrase', 'disambiguation', 'offset-list',
                 'medium-count', 'medium-position', 'name', 'sort-name',
                 'recordings', 'title', 'type'):
          tag_value = v
        elif k in ('length', 'sectors'):
          tag_value = int(v)
        elif k == 'artist-credit':
          # list of artist ids
          tag_name = 'artists'
          artist_uuids = []
          for credit in v:
            if isinstance(credit, str):
              try:
                uu = UUID(credit)
              except ValueError as e:
                warning("discarding credit %r, not a UUID: %s", credit, e)
              else:
                artist_uuids.append(str(uu))
            else:
              artist_uuids.append(credit['artist']['id'])
          tag_value = artist_uuids
        elif k == 'tag-list':
          # list of unique tag strings
          tag_value = list(set(map(lambda tag_dict: tag_dict['name'], v)))
        else:
          ##warning("SKIP unhandled A record %r", k)
          ##print(pformat(v))
          continue
        self[tag_name] = tag_value

class MBArtist(_MBTagSet):
  ''' A Musicbrainz artist entry.
  '''

class MBDisc(_MBTagSet):
  ''' A Musicbrainz disc entry.
  '''

class MBRecording(_MBTagSet):
  ''' A Musicbrainz recording entry.
  '''

class MBSQLTags(SQLTags):
  ''' Musicbrainz `SQLTags` with special `TagSetClass`.
  '''

  TAGSETCLASS_DEFAULT = _MBTagSet

  # map 'foo' from 'foo.bah' to a particular TagSet subclass
  TAGSETCLASS_PREFIX_MAPPING = {
      'artist': MBArtist,
      'disc': MBDisc,
      'recording': MBRecording,
  }

  @fmtdoc
  def __init__(self, mbdb_path=None):
    ''' Initialise the `MBSQLTags` instance,
        computing the default `mbdb_path` if required.

        `mbdb_path` is provided as `db_url` to the `SQLTags` superclass
        initialiser.
        If not specified it is obtained from the environment variable
        {MBDB_PATH_ENVVAR}, falling back to `{MBDB_PATH_DEFAULT!r}`.
    '''
    if mbdb_path is None:
      mbdb_path = os.environ.get(MBDB_PATH_ENVVAR)
      if mbdb_path is None:
        mbdb_path = expanduser(MBDB_PATH_DEFAULT)
    super().__init__(db_url=mbdb_path)
    self.mbdb_path = mbdb_path

  def default_factory(self, index):
    ''' The default factory runs the `SQLTags` default factory and then does an MB refresh.
    '''
    te = super().default_factory(index)
    te.refresh()
    return te

  def get(self, key, default=None):
    ''' Run the default `.get()` and the do an MB refresh.
    '''
    te = super().get(key, default=default)
    if te is not default:
      te.refresh()
    return te

class MBDB(MultiOpenMixin):
  ''' An interface to MusicBrainz with a local `TagsOntology(SQLTags)` cache.
  '''

  def __init__(self, mbdb_path=None):
    sqltags = self.sqltags = MBSQLTags(mbdb_path=mbdb_path)
    sqltags.mbdb = self
    with sqltags:
      ont = self.ontology = TagsOntology(sqltags)
      self.artists = sqltags.subdomain('artist')
      ont['artists'].update(type='list', member_type='artist')
      self.discs = sqltags.subdomain('disc')
      ont['discs'].update(type='list', member_type='disc')
      self.recordings = sqltags.subdomain('recording')
      ont['recordings'].update(type='list', member_type='recording')

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

  @staticmethod
  def query(typename, db_id, includes=None, id_name='id', record_key=None):
    ''' Fetch data from the Musicbrainz API.
    '''
    getter_name = f'get_{typename}_by_{id_name}'
    if record_key is None:
      record_key = typename
    try:
      getter = getattr(musicbrainzngs, getter_name)
    except AttributeError:
      warning(
          "no musicbrainzngs.%s: %s", getter_name,
          pformat(dir(musicbrainzngs))
      )
      raise
    if includes is None:
      includes = ['tags']
      warning(
          "query(%r,..): no includes specified, using %r", typename, includes
      )
      help(getter)
    with Pfx("%s(%r,includes=%r)", getter_name, db_id, includes, print=True):
      try:
        mb_info = getter(db_id, includes=includes)
      except musicbrainzngs.InvalidIncludeError as e:
        warning("BAD INCLUDES: %s", e)
        warning("help(%s):\n%s", getter_name, getter.__doc__)
        raise
      except musicbrainzngs.ResponseError as e:
        warning("RESPONSE ERROR: %s", e)
        warning("help(%s):\n%s", getter_name, getter.__doc__)
        raise
      mb_info = mb_info[record_key]
    return mb_info

  def _tagif(self, tags, name, value):
    ''' Apply a new `Tag(name,value)` to `tags` if `value` is not `None`.
    '''
    with self.sqltags:
      if value is not None:
        tags.set(name, value)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

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
from getopt import GetoptError
import os
from os.path import (
    exists as existspath,
    expanduser,
    expandvars,
    isdir as isdirpath,
    join as joinpath,
)
from pprint import pformat
import subprocess
import sys
from tempfile import NamedTemporaryFile
import discid
import musicbrainzngs
from typeguard import typechecked
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.fstags import FSTags
from cs.logutils import warning, info
from cs.pfx import Pfx, pfx_method
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags, SQLTagSet, SQLTagsCommand
from cs.tagset import TagSet, TagsOntology

__version__ = '20201004-dev'

musicbrainzngs.set_useragent(__name__, __version__, os.environ['EMAIL'])

DEFAULT_CDRIP_DIR = '~/var/cdrip'

MBDB_PATH_ENVVAR = 'MUSICBRAINZ_SQLTAGS'
MBDB_PATH_DEFAULT = '~/var/cache/mbdb.sqlite'

def main(argv=None):
  ''' Call the command line main programme.
  '''
  return CDRipCommand(argv).run()

class CDRipCommand(BaseCommand):
  ''' 'cdrip' command line.
  '''

  GETOPT_SPEC = 'D:fM:'

  USAGE_FORMAT = r'''Usage: {cmd} [options...] subcommand...
    -D device     Device to access. This may be omitted or "default" or
                  "" for the default device as determined by the discid module.
                  The environment variable $CDRIP_DEV may override the default.
    -f            Force. Read disc and consult Musicbrainz even if a toc file exists.
    -M mbdb_path  Specify the location of the MusicBrainz SQLTags cache.

  Environment:
    CDRIP_DEV            Default CDROM device.
    CDRIP_DIR            Default output directory.
    {MBDB_PATH_ENVVAR}  Default location of MusicBrainz SQLTags cache,
                         default {MBDB_PATH_DEFAULT}.'''

  USAGE_KEYWORDS = {
      'MBDB_PATH_ENVVAR': MBDB_PATH_ENVVAR,
      'MBDB_PATH_DEFAULT': MBDB_PATH_DEFAULT,
  }

  def apply_defaults(self):
    ''' Set up the default values in `options`.
    '''
    options = self.options
    options.force = False
    options.device = os.environ.get('CDRIP_DEV', "default")
    options.dirpath = os.environ.get('CDRIP_DIR', ".")
    options.mbdb_path = None

  def apply_opts(self, opts):
    ''' Apply the command line options.
    '''
    options = self.options
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-D':
          options.device = val
        elif opt == '-f':
          options.force = True
        if opt == '-M':
          options.mbdb_path = val
        else:
          raise GetoptError("unimplemented option")

  @contextmanager
  def run_context(self):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    fstags = FSTags()
    mbdb = MBDB(mbdb_path=self.options.mbdb_path)
    with fstags:
      with mbdb:
        with stackattrs(self.options, fstags=fstags, mbdb=mbdb, verbose=True):
          yield

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

  # pylint: disable=too-many-locals
  def cmd_rip(self, argv):
    ''' Usage: {cmd} [disc_id]
          Pull the audio into a subdirectory of the current directory.
    '''
    options = self.options
    fstags = options.fstags
    dirpath = options.dirpath
    disc_id = None
    if argv:
      disc_id = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    rip(
        options.device,
        options.mbdb,
        output_dirpath=dirpath,
        disc_id=disc_id,
        fstags=fstags
    )

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
      dev_info = discid.read(device=options.device)
      disc_id = dev_info.id
    with Pfx("discid %s", disc_id):
      disc = MB.discs[disc_id]
      print(disc.title)
      print(", ".join(disc.artist_names()))
      for tracknum, recording in enumerate(disc.recordings(), 1):
        print(
            tracknum, recording.title, '--',
            ", ".join(recording.artist_names())
        )

# pylint: disable=too-many-locals
def rip(device, mbdb, *, output_dirpath, disc_id=None, fstags=None):
  ''' Pull audio from `device` and save in `output_dirpath`.
  '''
  if disc_id is None:
    dev_info = discid.read(device=device)
    disc_id = dev_info.id
  if fstags is None:
    fstags = FSTags()
  with Pfx("MB: discid %s", disc_id, print=True):
    disc = mbdb.discs[disc_id]
  level1 = ", ".join(disc.artist_names()).replace(os.sep, '_') or "NO_ARTISTS"
  level2 = disc.title or "UNTITLED"
  if disc.medium_count > 1:
    level2 += f" ({disc.medium_position} of {disc.medium_count})"
  subdir = joinpath(output_dirpath, level1, level2)
  if not isdirpath(subdir):
    with Pfx("makedirs(%r)", subdir, print=True):
      os.makedirs(subdir)
  fstags[subdir].update(
      TagSet(discid=disc.id, title=disc.title, artists=disc.artist_names())
  )
  for tracknum, recording in enumerate(disc.recordings(), 1):
    track_tags = TagSet(
        discid=disc['musicbrainz.disc_id'],
        artists=recording.artist_names(),
        title=recording.title,
        track=tracknum
    )
    track_artists = ", ".join(recording.artist_names())
    track_base = f"{tracknum:02} - {recording.title} -- {track_artists}"
    wav_filename = joinpath(subdir, track_base + '.wav')
    mp3_filename = joinpath(subdir, track_base + '.mp3')
    if existspath(mp3_filename):
      warning("MP3 file already exists, skipping track: %r", mp3_filename)
    else:
      with NamedTemporaryFile(dir=subdir,
                              prefix=f"cdparanoia--track{tracknum}--",
                              suffix='.wav') as T:
        if existspath(wav_filename):
          info("using existing WAV file: %r", wav_filename)
        else:
          argv = ['cdparanoia', '-d', '1', '-w', str(tracknum), T.name]
          with Pfx("+ %r", argv, print=True):
            subprocess.run(argv, stdin=subprocess.DEVNULL, check=True)
          with Pfx("%r => %r", T.name, wav_filename, print=True):
            os.link(T.name, wav_filename)
      fstags[wav_filename].update(track_tags)
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
          mp3_filename
      ]
      with Pfx("+ %r", argv, print=True):
        subprocess.run(argv, stdin=subprocess.DEVNULL, check=True)
    fstags[mp3_filename].update(track_tags)
  os.system("eject")

# pylint: disable=too-many-ancestors
class MBTagSet(SQLTagSet):
  ''' An `SQLTagSet` subclass for MB entities.
  '''

  @property
  def mbdb(self):
    ''' The associated `MBDB`.
    '''
    return self.sqltags.mbdb

  @property
  def ontology(self):
    ''' The `TagsOntology` for this entity.
    '''
    return self.mbdb.ontology

  def artists(self):
    ''' Return a list of the artists' metadata.
    '''
    return self.tag_metadata('artists', convert=str)

  def recordings(self):
    ''' Return a list of the recordings.
    '''
    return self.tag_metadata('recordings', convert=str)

  def artist_names(self):
    ''' Return a list of artist names.
    '''
    return [artist.artist_name for artist in self.artists()]

class MBSQLTags(SQLTags):
  ''' Musicbrainz `SQLTags` with special `TagSetClass`.
  '''

  TagSetClass = MBTagSet

  @fmtdoc
  def __init__(self, mbdb_path=None):
    ''' Initialise the MBSQLTags instance,
        computing the default `mbdb_path` if required.

        `mbdb_path` is provided as `db_url` to the SQLTags superclass
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

  @pfx_method
  def default_factory(self, name: str, *, unixtime=None):
    te = super().default_factory(name, unixtime=unixtime)
    assert te.name == name
    mbdb = te.sqltags.mbdb
    if name.startswith('meta.'):
      try:
        _, typename, _ = name.split('.', 2)
      except ValueError:
        pass
      else:
        fill_in = getattr(mbdb, '_fill_in_' + typename, None)
        if fill_in:
          fill_in(te)
        else:
          warning("no fill_in for typename=%r", typename)
    return te

class MBDB(MultiOpenMixin):
  ''' An interface to MusicBrainz with a local `SQLTags` cache.
  '''

  VARIOUS_ARTISTS_ID = '89ad4ac3-39f7-470e-963a-56509c546377'

  def __init__(self, mbdb_path=None):
    sqltags = self.sqltags = MBSQLTags(mbdb_path=mbdb_path)
    sqltags.mbdb = self
    with sqltags:
      sqltags.init()
      ont = self.ontology = TagsOntology(sqltags)
      self.artists = sqltags.subdomain('meta.artist')
      ont['type.artists'].update(type='list', member_type='artist')
      self.discs = sqltags.subdomain('meta.disc')
      ont['type.discs'].update(type='list', member_type='disc')
      self.recordings = sqltags.subdomain('meta.recording')
      ont['type.recordings'].update(type='list', member_type='recording')

  def startup(self):
    ''' Start up the `MBDB`: open the `SQLTags`.
    '''
    self.sqltags.open()

  def shutdown(self):
    ''' Shut down the `MBDB`: close the `SQLTags`.
    '''
    self.sqltags.close()

  def find(self, criteria):
    ''' Find entities in the cache database.
    '''
    return self.sqltags.find(criteria)

  @staticmethod
  def _get(typename, db_id, includes, id_name='id', record_key=None):
    ''' Fetch data from the Musicbrainz API.
    '''
    if record_key is None:
      record_key = typename
    getter_name = f'get_{typename}_by_{id_name}'
    try:
      getter = getattr(musicbrainzngs, getter_name)
    except AttributeError:
      warning(
          "no musicbrainzngs.%s: %s", getter_name,
          pformat(dir(musicbrainzngs))
      )
      raise
    with Pfx("%s(%r,includes=%r)", getter_name, db_id, includes):
      try:
        mb_info = getter(db_id, includes=includes)
      except musicbrainzngs.InvalidIncludeError:
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

  @staticmethod
  def tag_from_tag_list(tags, mb_dict):
    ''' Set `tags.tag_list` from `mb_dict['tag-list'].
    '''
    tags.set(
        'tags', [tag_elem['name'] for tag_elem in mb_dict.get('tag-list', ())]
    )

  @staticmethod
  def tag_artists_from_credits(tags, mb_dict):
    ''' Set `tags.artists` from `mb_dict['artist-credit']`.
    '''
    artist_credits = mb_dict.get('artist-credit')
    if artist_credits is not None:
      tags.set(
          'artists', [
              credit['artist']['id']
              for credit in artist_credits
              if not isinstance(credit, str)
          ]
      )

  @typechecked
  def _fill_in_artist(self, mb_tags: MBTagSet, force=False):
    assert mb_tags.name.startswith('meta.artist.')
    artist_id = mb_tags.name.split('.', 2)[-1]
    mb_tags['musicbrainz.artist_id'] = artist_id
    A = None
    if artist_id == self.VARIOUS_ARTISTS_ID:
      A = {
          'name': 'Various Artists',
      }
    else:
      includes = []
      for cached in 'tags', :
        if force or cached not in mb_tags:
          includes.append(cached)
      if includes:
        A = self._get('artist', artist_id, includes)
    if A is not None:
      self._tagif(mb_tags, 'artist_name', A.get('name'))
      self._tagif(mb_tags, 'sort_name', A.get('sort-name'))
      self.tag_from_tag_list(mb_tags, A)
    return mb_tags

  # pylint: disable=too-many-branches,too-many-locals
  @typechecked
  def _fill_in_disc(self, mb_tags: MBTagSet, force=False):
    ''' Return the `disc.`*disc_id* entry.
        Update from MB as required before return.
    '''
    ##force = True
    assert mb_tags.name.startswith('meta.disc.')
    disc_id = mb_tags.name.split('.', 2)[-1]
    disc_tags = self.discs[disc_id]
    disc_tags['musicbrainz.disc_id'] = disc_id
    includes = []
    for cached in 'artists', 'recordings':
      if force or cached not in disc_tags:
        includes.append(cached)
    if includes:
      D = self._get('releases', disc_id, includes, 'discid', 'disc')
      assert D['id'] == disc_id
      found_medium = None
      found_release = None
      for release in D['release-list']:
        if found_medium:
          break
        for medium in release['medium-list']:
          if found_medium:
            break
          for disc in medium['disc-list']:
            if found_medium:
              break
            if disc['id'] == disc_id:
              # matched disc
              found_medium = medium
              found_release = release
      assert found_medium
      medium_count = found_release['medium-count']
      medium_position = found_medium['position']
      self._tagif(disc_tags, 'title', found_release.get('title'))
      self._tagif(disc_tags, 'medium_count', medium_count)
      self._tagif(disc_tags, 'medium_position', medium_position)
      self.tag_artists_from_credits(disc_tags, found_release)
      if 'recordings' in includes:
        track_list = found_medium.get('track-list')
        if not track_list:
          warning('no medium[track-list]')
        else:
          disc_tags.set(
              'recordings', [track['recording']['id'] for track in track_list]
          )
    return disc_tags

  @typechecked
  def _fill_in_recording(self, mb_tags: MBTagSet, force=False):
    ''' Return the recording for `recording_id`.
    '''
    ##force = True
    assert mb_tags.name.startswith('meta.recording.')
    recording_id = mb_tags.name.split('.', 2)[-1]
    mb_tags['musicbrainz.recording_id'] = recording_id
    includes = []
    for cached in 'artists', 'tags':
      if force or cached not in mb_tags:
        includes.append(cached)
    if includes:
      R = self._get('recording', recording_id, includes)
      self._tagif(mb_tags, 'title', R.get('title'))
      self.tag_from_tag_list(mb_tags, R)
      self.tag_artists_from_credits(mb_tags, R)
    return mb_tags

if __name__ == '__main__':
  sys.exit(main(sys.argv))

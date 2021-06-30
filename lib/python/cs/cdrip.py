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
    isdir as isdirpath,
    join as joinpath,
)
from pprint import pformat
import subprocess
import sys
from tempfile import NamedTemporaryFile
from uuid import UUID
import discid
import musicbrainzngs
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.fstags import FSTags
from cs.lex import cutprefix
from cs.logutils import error, warning, info
from cs.pfx import Pfx
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags, SQLTagSet, SQLTagsCommand
from cs.tagset import TagSet, TagsOntology

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

  def apply_defaults(self):
    ''' Set up the default values in `options`.
    '''
    options = self.options
    options.force = False
    options.device = os.environ.get(CDRIP_DEV_ENVVAR) or CDRIP_DEV_DEFAULT
    options.dirpath = os.environ.get(CDRIP_DIR_ENVVAR
                                     ) or expanduser(CDRIP_DIR_DEFAULT)
    options.mbdb_path = None

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

  def cmd_meta(self, argv):
    ''' Usage: {cmd} metaname...
          Print the metadata about metaname, where metaname name the form
          type_name.uuid being an ontology type such as "artist"
          and a Musicbrainz UUID for that type.
    '''
    options = self.options
    mbdb = options.mbdb
    if not argv:
      raise GetoptError("missing metanames")
    for metaname in argv:
      with Pfx("metaname %r", metaname):
        ontkey = 'meta.' + metaname
        metadata = mbdb.ontology[ontkey]
        print(' ', metadata)

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
          options.device,
          options.mbdb,
          output_dirpath=dirpath,
          disc_id=disc_id,
          fstags=fstags,
          no_action=no_action,
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
        dev_info = discid.read(device=options.device)
      except discid.disc.DiscError as e:
        error("disc error: %s", e)
        return 1
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
    return 0

# pylint: disable=too-many-locals,too-many-branches,too-many-statements
def rip(
    device,
    mbdb,
    *,
    output_dirpath,
    disc_id=None,
    fstags=None,
    no_action=False
):
  ''' Pull audio from `device` and save in `output_dirpath`.
  '''
  if disc_id is None:
    dev_info = discid.read(device=device)
    disc_id = dev_info.id
  if fstags is None:
    fstags = FSTags()
  with Pfx("MB: discid %s", disc_id, print=True):
    disc = mbdb.discs[disc_id]
  level1 = ", ".join(disc.artist_names).replace(os.sep, '_') or "NO_ARTISTS"
  level2 = disc.title or "UNTITLED"
  if disc.medium_count > 1:
    level2 += f" ({disc.medium_position} of {disc.medium_count})"
  subdir = joinpath(output_dirpath, level1, level2)
  if not isdirpath(subdir):
    with Pfx("makedirs(%r)", subdir, print=True):
      os.makedirs(subdir)
  fstags[subdir].update(
      TagSet(discid=disc.id, title=disc.title, artists=disc.artist_names)
  )
  for tracknum, recording_id in enumerate(disc.recordings, 1):
    recording = disc.ontology.metadata('recording', recording_id)
    track_fstags = TagSet(
        discid=disc.mbkey,
        artists=recording.artist_names,
        title=recording.title,
        track=tracknum
    )
    track_artists = ", ".join(recording.artist_names)
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
          if no_action:
            print(*argv)
          else:
            with Pfx("+ %r", argv, print=True):
              subprocess.run(argv, stdin=subprocess.DEVNULL, check=True)
            with Pfx("%r => %r", T.name, wav_filename, print=True):
              os.link(T.name, wav_filename)
      if no_action:
        print("fstags[%r].update(%s)" % (wav_filename, track_fstags))
      else:
        fstags[wav_filename].update(track_fstags)
        fstags[wav_filename].rip_command = argv
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
      if no_action:
        print(*argv)
      else:
        with Pfx("+ %r", argv, print=True):
          subprocess.run(argv, stdin=subprocess.DEVNULL, check=True)
    if no_action:
      print("fstags[%r].update(%s)" % (mp3_filename, track_fstags))
    else:
      fstags[mp3_filename].update(track_fstags)
      fstags[mp3_filename].conversion_command = argv
  if not no_action:
    os.system("eject")

# pylint: disable=too-many-ancestors
class _MBTagSet(SQLTagSet):
  ''' An `SQLTagSet` subclass for MB entities.
  '''

  @property
  def mbdb(self):
    ''' The associated `MBDB`.
    '''
    return self.sqltags.mbdb

  @property
  def mbkey(self):
    ''' The MusicBrainz key (usually a UUID or discid).
    '''
    return self.name.split('.', 2)[2]

  @property
  def type_name(self):
    ''' The ontology type. Eg `'artist'` if `name==`meta.artist.foo`.
        This is `None` if `self.name` is not a `meta.`*type_name*`.` name.
    '''
    try:
      ontish, onttype, _ = self.name.split('.', 2)
    except ValueError:
      return None
    if ontish != 'meta':
      return None
    return onttype

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
    if not force:
      onttype = self.type_name
      if onttype in ('artist',):
        if getattr(self, onttype + '_name', None):
          return
      elif onttype in ('disc', 'recording'):
        if self.title:
          return
    mbkey = self.mbkey
    onttype = self.type_name
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
    # record the full response data for forensics
    self[f'musicbrainzngs.{get_type}_by_{id_name}__{"_".join(includes)}'] = A
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
          warning("SKIP unhandled A record %r", k)
          print(pformat(v))
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

  # map 'foo' frm 'meta.foo.bah' to a particular TagSet subclass
  TAGSET_CLASSES = {
      'artist': MBArtist,
      'disc': MBDisc,
      'recording': MBRecording,
  }

  def TagSetClass(self, *, name, **kw):
    ''' Instead of a fixed class we use a factory to construct a
        type specific instance.
    '''
    cls = None
    meta1 = cutprefix(name, 'meta.')
    if meta1 is not name:
      type_name, _ = meta1.split('.', 1)
      cls = self.TAGSET_CLASSES[type_name]
    if cls is None:
      cls = super().TagSetClass
    te = cls(name=name, **kw)
    return te

  TagSetClass.singleton_also_by = _MBTagSet.singleton_also_by

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

class MBDB(MultiOpenMixin):
  ''' An interface to MusicBrainz with a local `TagsOntology(SQLTags)` cache.
  '''

  def __init__(self, mbdb_path=None):
    sqltags = self.sqltags = MBSQLTags(mbdb_path=mbdb_path)
    sqltags.mbdb = self
    with sqltags:
      ont = self.ontology = TagsOntology(sqltags)
      self.artists = sqltags.subdomain('meta.artist')
      ont['type.artists'].update(type='list', member_type='artist')
      self.discs = sqltags.subdomain('meta.disc')
      ont['type.discs'].update(type='list', member_type='disc')
      self.recordings = sqltags.subdomain('meta.recording')
      ont['type.recordings'].update(type='list', member_type='recording')

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

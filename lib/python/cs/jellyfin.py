#!/usr/bin/env python3

''' Utility stuff for working with Jellyfin (jellyfin.org).
'''

from dataclasses import dataclass
from functools import cached_property
from getopt import GetoptError
from os.path import basename, exists as existspath, expanduser, splitext
from typing import Mapping, Optional

from lxml.builder import ElementMaker
from lxml.etree import tostring as xml_tostring
from typeguard import typechecked

from cs.cmdutils import BaseCommand, popopts, vprint
from cs.deco import uses_verbose
from cs.fileutils import atomic_filename
from cs.fs import HasFSPath, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.logutils import warning
from cs.pfx import Pfx, pfx
from cs.mediainfo import SeriesEpisodeInfo
from cs.tagset import TagSet

from cs.debug import trace, pprint, printt

class JellyfinMedia(HasFSPath):
  ''' A representation of a Jellyfin compatible media directory.
  '''

  MOVIES_SUBDIR = 'Movies'
  SHOWS_SUBDIR = 'Shows'

  MDPROVIDER_BY_ZONE_TYPE = {
      'tmdb.movie': 'tmdb',
      'tvdb.series': 'tvdb',
      'imdb.movie': 'imdb',
  }

  def __init__(self, mediadir: str):
    self.fspath = mediadir

  @staticmethod
  @trace(retval=True)
  def parse_filename(filename):
    base = basename(filename)
    tags = TagSet()
    base, ext = splitext(base)
    title_part, *flags = base.split('.')

  @uses_fstags
  def pathto_series_episode(
      self,
      srcfspath: str,
      *,
      fstags: FSTags,
      title: Optional[str] = None,
      year: Optional[int] = None,
      metadata_ids: Optional[Mapping[str, str]] = None,
  ):
    ''' Compute the filesystem path to be used for an original media file `srcfspath`.
    '''
    sei = SeriesEpisodeInfo.from_str(basename(srcfspath))
    tags = fstags[srcfspath]
    year = tags.get('year')
    series_title = tags.get('series_title', sei.series)
    episode_title = tags.get('episode_title', sei.episode_title)
    dir_title = series_title
    # append (year)
    if year:
      dir_title += f' ({year})'
    # append [metaid-id]
    for tag in tags:
      with Pfx(tag):
        if tag.name.endswith('.id'):
          zone = tag.name.removesuffix('.id')
          zone_id = tag.value
          subtype, type_id = zone_id.rsplit('.', 1)
          zone_type = f'{zone}.{subtype}'
          try:
            provider = self.MDPROVIDER_BY_ZONE_TYPE[zone_type]
          except KeyError:
            warning(f'no MDPROVIDER_BY_ZONE_TYPE[{zone_type=}]')
            continue
          dir_title += f' [{zone}.{subtype}-{type_id}]'
    print(f'{sei=}')
    breakpoint()
    filename = f'{dir_title} S{sei.season:02}E{sei.episode:02}'
    if sei.episode_title:
      filename += f' {episode_title}'
    if sei.episode_part:
      filename += f' part {sei.episode_part}'
    return f'{dir_title}/{filename}'

class JellfyfinCommand(BaseCommand):
  ''' Operations to help with jellyfin.
  '''

  @dataclass
  class Options(BaseCommand.Options):
    mediapath: str = expanduser('~/var/jellyfin/media')

    @cached_property
    def jellyfin(self) -> JellyfinMedia:
      return JellyfinMedia(self.mediapath)

  def cmd_file(self, argv):
    jf = self.options.jellyfin
    for filename in argv:
      print(filename)
      print(jf.pathto_series_episode(filename))

  @popopts(f=('force', 'Force: overwrite existing .nfo files.'))
  @uses_fstags
  def cmd_nfo(self, argv, fstags: FSTags):
    ''' Usage: {cmd} mediapaths...
          Create .nfo files for the named media files if missing.
          See: https://jellyfin.org/docs/general/server/metadata/nfo/
    '''
    if not argv:
      raise GetoptError("missing filenames")
    xit = 0
    for fspath in argv:
      vprint(fspath)
      with Pfx(fspath):
        try:
          nfopath = make_nfo(
              fspath, fstags=fstags, exists_ok=self.options.force
          )
        except OSError as e:
          warning("%s", e)
          xit = 1
        else:
          vprint(" ", nfopath)
    return xit

def nfopath(mediapath):
  ''' Return the filesystem path for the `.nfo` file for `mediapath`.
  '''
  base, ext = splitext(mediapath)
  return f'{base}.nfo'

@pfx
@uses_fstags
@uses_verbose
def make_nfo(
    mediapath, *, fstags: FSTags, verbose: bool, exists_ok=False
) -> str:
  ''' Create the `.nfo` file for `mediapath` if missing.
      Return the path of the NFO file.
  '''
  if verbose:
    print(shortpath(mediapath, collapseuser=True, foldsymlinks=True))
  if not existspath(mediapath):
    raise FileNotFoundError(mediapath)
  ftags = fstags[mediapath]
  if verbose:
    print("  fstags:")
    ftags.printt(indent=4)
  nfpath = nfopath(mediapath)
  if not exists_ok and existspath(nfpath):
    vprint(nfpath, "already exists")
    return nfpath
  itags = ftags.infer_tags()
  if verbose:
    print("  inferred:")
    itags.printt(indent=4)
  # make sure existing tags override inferences
  itags.update(ftags)
  if 'tv.season' in itags:
    xml = nfo_tv(itags)
  else:
    xml = nfo_movie(itags)
  with atomic_filename(nfpath, mode="w", exists_ok=exists_ok) as f:
    print('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', file=f)
    print(xml_tostring(xml, encoding='unicode', pretty_print=True), file=f)
  return nfpath

@typechecked
def nfo_movie(tags: TagSet):
  ''' NFO `<movie>` XML tag with fields as described at:
      https://jellyfin.org/docs/general/server/metadata/nfo/
  '''
  E = ElementMaker()  ## version="1.0", encoding="UTF-8", standalone="yes")
  return E.movie(
      *[
          getattr(E, tag)(str(value)) for tag, value in dict(
              plot=tags.get('synopsis'),
              # airsafter_season
              # airsbefore_episode
              # airsbefore_season
              # displayepisode - which episode this special airs before
              # displayseason - season the special aired in
              # trailer - YouTube URL in the Kodi format
              # rating
              year=tags.get('year'),
              # sorttitle
              # mpaa
              # aspectratio
              # dateadded - in UTC
              # collectionnumber - TMDb collection id
              # set - collection name, only for movies
              imdbid=tags.get('id.imdb.movie'),
              # imdbid - for all other media types
              # tvdbid
              # tmdbid
              # language
              title=tags.get('title'),
              tvdbid=tags.get('id.tvdb.movie'),
          ).items() if value is not None and value != ''
      ],
      *(E.director(director) for director in tags.get('director', ())),
      *(E.writer(writer) for writer in tags.get('writer', ())),
      *(E.credits(credit) for credit in tags.get('credits', ())),
  )

@typechecked
def nfo_tv(tags: TagSet):
  ''' NFO `<tvshow>` XML tag with fields as described at:
      https://jellyfin.org/docs/general/server/metadata/nfo/
  '''
  E = ElementMaker()  ## version="1.0", encoding="UTF-8", standalone="yes")
  return E.tvshow(
      *[
          getattr(E, tag)(str(value)) for tag, value in dict(
              plot=tags.get('synopsis'),
              seasonnumber=tags.get('tv.season'),
              showtitle=tags.get('tv.episode_title'),
              episode=tags.get('tv.episode'),
              season=tags.get('tv.season'),
              # airsafter_season
              # airsbefore_episode
              # airsbefore_season
              # displayepisode - which episode this special airs before
              # displayseason - season the special aired in
              # trailer - YouTube URL in the Kodi format
              # rating
              # year
              # sorttitle
              # mpaa
              # aspectratio
              # dateadded - in UTC
              # collectionnumber - TMDb collection id
              # set - collection name, only for movies
              imdb_id=tags.get('id.imdb.tvseries'),
              # imdbid - for all other media types
              # tvdbid
              # tmdbid
              # language
              title=tags.get('title'),
              tvdbid=tags.get('id.tvdb.show'),
          ).items() if value is not None and value != ''
      ],
      *(E.director(director) for director in tags.get('director', ())),
      *(E.writer(writer) for writer in tags.get('writer', ())),
      *(E.credits(credit) for credit in tags.get('credits', ())),
  )

if __name__ == '__main__':
  import sys
  JellfyfinCommand(sys.argv).run()

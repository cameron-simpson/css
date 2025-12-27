#!/usr/bin/env python3
#
# Playon facilities. - Cameron Simpson <cs@cskk.id.au>
#

''' PlayOn facilities, primarily access to the download API.
    Includes a nice command line tool.
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from getopt import GetoptError
from netrc import netrc
import os
from os import environ
from os.path import (
    basename, exists as existspath, realpath, samefile, splitext
)
from pprint import pformat, pprint
import re
import sys
from threading import Semaphore
import time
from typing import Any, Mapping, Optional
from urllib.parse import unquote as unpercent

from icontract import require
import requests
from typeguard import typechecked

from cs.cmdutils import BaseCommand, popopts
from cs.context import stackattrs
from cs.deco import fmtdoc, Promotable, uses_quiet, uses_verbose
from cs.fileutils import atomic_filename
from cs.fstags import FSTags, uses_fstags
from cs.lex import (
    cutprefix,
    cutsuffix,
    format_attribute,
    get_prefix_n,
    get_suffix_part,
    printt,
)
from cs.logutils import warning
from cs.mediainfo import SeriesEpisodeInfo
from cs.pfx import Pfx, pfx_method, pfx_call
from cs.progress import progressbar
from cs.resources import RunState, uses_runstate
from cs.result import bg as bg_result, report as report_results, CancellationError
from cs.rfc2616 import content_length
from cs.service_api import HTTPServiceAPI, RequestsNoAuth
from cs.sqltags import SQLTags
from cs.tagset import HasTags
from cs.threads import monitor, bg as bg_thread
from cs.units import BINARY_BYTES_SCALE
from cs.upd import print, run_task  # pylint: disable=redefined-builtin

__version__ = '20241007-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: Utilities",
    ],
    'entry_points': {
        'console_scripts': {
            'playon': 'cs.app.playon:main'
        },
    },
    'install_requires': [
        'cs.cmdutils',
        'cs.context',
        'cs.deco',
        'cs.fileutils>=atomic_filename',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.mediainfo',
        'cs.pfx>=pfx_call',
        'cs.progress',
        'cs.resources',
        'cs.result',
        'cs.service_api',
        'cs.sqltags',
        'cs.tagset',
        'cs.threads',
        'cs.units',
        'cs.upd',
        'icontract',
        'requests',
        'typeguard',
    ],
}

PLAYON_DBURL_ENVVAR = 'PLAYON_TAGS_DBURL'
PLAYON_DBURL_DEFAULT = '~/var/playon.sqlite'

FILENAME_FORMAT_ENVVAR = 'PLAYON_FILENAME_FORMAT'
DEFAULT_FILENAME_FORMAT = (
    '{series_prefix}{series_episode_name}--{resolution}--{playon.ProviderID}--playon--{playon.ID}'
)

# default "ls" output format
LS_FORMAT = (
    '{playon.ID} {playon.HumanSize} {resolution}'
    ' {nice_name} {playon.ProviderID} {status:upper}'
)

# default "queue" output format
QUEUE_FORMAT = '{playon.ID} {playon.Series} {playon.Name} {playon.ProviderID}'

# download parallelism
DEFAULT_DL_PARALLELISM = 2

def main(argv=None):
  ''' Playon command line mode;
      see the `PlayOnCommand` class below.
  '''
  return PlayOnCommand(argv).run()

class PlayOnCommand(BaseCommand):
  ''' Playon command line implementation.
  '''
  USAGE_KEYWORDS = {
      'DEFAULT_DL_PARALLELISM': DEFAULT_DL_PARALLELISM,
      'DEFAULT_FILENAME_FORMAT': DEFAULT_FILENAME_FORMAT,
      'FILENAME_FORMAT_ENVVAR': FILENAME_FORMAT_ENVVAR,
      'LS_FORMAT': LS_FORMAT,
      'PLAYON_DBURL_ENVVAR': PLAYON_DBURL_ENVVAR,
      'PLAYON_DBURL_DEFAULT': PLAYON_DBURL_DEFAULT,
      'QUEUE_FORMAT': QUEUE_FORMAT,
  }

  USAGE_FORMAT = r'''Usage: {cmd} subcommand [args...]

    Environment:
      PLAYON_USER               PlayOn login name, default from $EMAIL.
      PLAYON_PASSWORD           PlayOn password.
                                This is obtained from .netrc if omitted.
      {FILENAME_FORMAT_ENVVAR}  Format string for downloaded filenames.
                                Default: {DEFAULT_FILENAME_FORMAT}
      {PLAYON_DBURL_ENVVAR:17}         Location of state tags database.
                                Default: {PLAYON_DBURL_DEFAULT}

    Recording specification:
      an int        The specific recording id.
      all           All known recordings.
      downloaded    Recordings already downloaded.
      expired       Recording which are no longer available.
      pending       Recordings not already downloaded.
      /regexp       Recordings whose Series or Name match the regexp,
                    case insensitive.
  '''

  @dataclass
  class Options(BaseCommand.Options):
    INFO_SKIP_NAMES = (*BaseCommand.Options.INFO_SKIP_NAMES, 'password')
    user: Optional[str] = field(
        default_factory=lambda: environ.
        get('PLAYON_USER', environ.get('EMAIL'))
    )
    password: Optional[str] = field(
        default_factory=lambda: environ.get('PLAYON_PASSWORD')
    )
    dl_jobs: int = DEFAULT_DL_PARALLELISM
    filename_format: str = field(
        default_factory=lambda: environ.
        get('FILENAME_FORMAT_ENVVAR', DEFAULT_FILENAME_FORMAT)
    )
    ls_format: str = LS_FORMAT
    queue_format: str = QUEUE_FORMAT

  @contextmanager
  def run_context(self):
    ''' Prepare the `PlayOnAPI` around each command invocation.
    '''
    with super().run_context():
      options = self.options
      runstate = options.runstate
      api = PlayOnAPI(options.user, options.password)
      with api:
        with stackattrs(options, api=api):
          # preload all the recordings from the db
          ##list(sqltags.recordings())
          # if there are unexpired stale entries or no unexpired entries,
          # refresh them
          self._refresh_sqltags_data(api)
          runstate.raiseif()
          yield

  @popopts
  def cmd_account(self, argv):
    ''' Usage: {cmd}
          Report account state.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    api = self.options.api
    for k, v in sorted(api.account().items()):
      print(k, pformat(v))

  @popopts
  def cmd_api(self, argv):
    ''' Usage: {cmd} suburl
          GET suburl via the API, print result.
    '''
    if not argv:
      raise GetoptError("missing suburl")
    suburl = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    api = self.options.api
    result = api.suburl_data(suburl)
    pprint(result)

  @popopts
  def cmd_cds(self, argv):
    ''' Usage: {cmd} suburl
          GET suburl via the content delivery API, print result.
          Example subpaths:
            content
            content/provider-name
    '''
    if not argv:
      raise GetoptError("missing suburl")
    suburl = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    api = self.options.api
    login_state = api.login_state
    pprint(login_state)
    result = api.cdsurl_data(suburl)
    pprint(result)

  @popopts(o_='filename_format')
  @uses_fstags
  def cmd_rename(self, argv, *, fstags: FSTags):
    ''' Usage: {cmd} [-o filename_format] filenames...
          Rename the filenames according to their fstags.
          -n    No action, dry run.
          -o filename_format
                Format for the new filename, default {DEFAULT_FILENAME_FORMAT!r}.
    '''
    options = self.options
    api = options.api
    doit = options.doit
    filename_format = options.filename_format
    if not argv:
      raise GetoptError("missing filenames")
    xit = 0
    for fspath in argv:
      with Pfx(fspath):
        if not existspath(fspath):
          warning("does not exist")
          xit = 1
          continue
        _, ext = splitext(basename(fspath))
        try:
          recording = fstags[fspath].zone_entity('playon')
        except KeyError as e:
          warning("no playon zone key, skipping: %s", e)
          continue
        new_filename = recording.filename(filename_format)
        new_pfx, new_ext = splitext(new_filename)
        new_filename = new_pfx + ext
        if new_filename in (fspath, basename(fspath)):
          continue
        with Pfx("-> %s", new_filename):
          if existspath(new_filename):
            warning("already exists")
            if not samefile(fspath, new_filename):
              xit = 1
            continue
          print("mv", fspath, new_filename)
          if doit:
            fstags.mv(fspath, new_filename)
            fstags[new_filename].update(recording)
    return xit

  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
  @popopts(j_=('dl_jobs', 'Concurrent download jobs.', int))
  def cmd_dl(self, argv):
    ''' Usage: {cmd} [recordings...]
          Download the specified recordings, default "pending".
    '''
    options = self.options
    dl_jobs = options.dl_jobs
    no_download = options.dry_run
    if not argv:
      argv = ['pending']
    api = options.api
    filename_format = options.filename_format
    runstate = options.runstate
    sem = Semaphore(dl_jobs)

    @typechecked
    def _dl(dl_id: int, sem):
      try:
        with api:
          filename = api[dl_id].filename(filename_format)
          try:
            api.download(dl_id, filename=filename, runstate=runstate)
          except ValueError as e:
            warning("download fails: %s", e)
            return None
          return filename
      finally:
        sem.release()

    xit = 0
    Rs = []
    for arg in argv:
      with Pfx(arg):
        recording_ids = api.recording_ids_from_str(arg)
        if not recording_ids:
          if sys.stderr.isatty():
            warning("no recording ids")
          continue
        for dl_id in recording_ids:
          recording = api[dl_id]
          with Pfx(recording.name):
            citation = recording.nice_name()
            if recording.is_expired():
              warning("expired, skipping %r", citation)
              continue
            if not recording.is_available():
              warning("not yet available, skipping %r", citation)
              continue
            if recording.is_downloaded():
              warning(
                  "already downloaded %r to %r", citation,
                  recording.download_path
              )
            if no_download:
              recording.ls()
            else:
              sem.acquire()  # pylint: disable=consider-using-with
              Rs.append(
                  bg_result(
                      _dl,
                      dl_id,
                      sem,
                      _extra=dict(dl_id=dl_id),
                      _name=f'playon dl {dl_id} {recording.name}',
                  )
              )

    if Rs:
      for R in report_results(Rs):
        dl_id = R.extra['dl_id']
        recording = api[dl_id]
        try:
          dl = R()
        except CancellationError as e:
          warning("%s: download interrupted: %s", recording.nice_name(), e)
          xit = 1
        else:
          if not dl:
            warning("FAILED download of %d", dl_id)
            xit = 1

    return xit

  @uses_runstate
  def _refresh_sqltags_data(self, api, runstate: RunState, max_age=None):
    ''' Refresh the queue and recordings if any unexpired records are stale
        or if all records are expired.
    '''
    recordings = set(api.fetch_recordings())
    need_refresh = (
        # any current recordings whose state is stale
        any(recording.is_stale(max_age=max_age) for recording in recordings) or
        # no recording is current
        all(recording.is_expired() for recording in recordings)
    )
    if need_refresh:
      with run_task("refresh queue and recordings"):
        Ts = [runstate.bg(api.queue), runstate.bg(api.recordings)]
        for T in Ts:
          T.join()

  @popopts
  def cmd_downloaded(self, argv, locale='en_US'):
    ''' Usage: {cmd} recordings...
          Mark the specified recordings as downloaded and no longer pending.
    '''
    if not argv:
      raise GetoptError("missing recordings")
    api = self.options.api
    xit = 0
    for spec in argv:
      with Pfx(spec):
        recording_ids = api.recording_ids_from_str(spec)
        if not recording_ids:
          warning("no recording ids")
          xit = 1
          continue
        for dl_id in recording_ids:
          with Pfx("%s", dl_id):
            recording = api[dl_id]
            print(dl_id, '+ downloaded')
            recording.add("downloaded")
    return xit

  @popopts(l='long_mode')
  def cmd_feature(self, argv, locale='en_US'):
    ''' Usage: {cmd} [feature_id]
          List features.
    '''
    options = self.options
    long_mode = options.long_mode
    if argv:
      feature_id = argv.pop(0)
    else:
      feature_id = None
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    api = self.options.api
    for feature in sorted(api.features(), key=lambda svc: svc['playon.ID']):
      playon = feature.subtags('playon', as_tagset=True)
      if feature_id is not None and playon.ID != feature_id:
        print("skip", playon.ID)
        continue
      print(playon.ID)
      if feature_id is None and not long_mode:
        continue
      for tag in playon:
        print(" ", tag)

  @popopts(
      l=('long_mode', 'Long listing: list tags below each entry.'),
      o_=(
          'ls_format',
          ''' Format string for each entry. Default format:
              {LS_FORMAT}
          ''',
      ),
  )
  def cmd_ls(self, argv):
    ''' Usage: {cmd} [recordings...]
          List available downloads.
    '''
    options = self.options
    options.api.ls(
        argv or ['available'],
        format=options.ls_format,
        long_mode=options.long_mode,
    )

  @popopts
  def cmd_poll(self, argv):
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    pprint(self.options.api.notifications())

  @popopts(
      l=('long_mode', 'Long listing: list tags below each entry.'),
      o_=(
          'queue_format',
          ''' Format string for each entry. Default format:
              {QUEUE_FORMAT}
          ''',
      ),
  )
  def cmd_queue(self, argv):
    ''' Usage: {cmd} [recordings...]
          List queued recordings.
    '''
    options = self.options
    options.api.ls(
        argv or ['available'],
        format=options.ls_format,
        long_mode=options.long_mode,
    )

  cmd_q = cmd_queue

  @popopts
  def cmd_refresh(self, argv):
    ''' Usage: {cmd} [queue] [recordings]
          Update the db state from the PlayOn service.
    '''
    api = self.options.api
    if not argv:
      argv = ['queue', 'recordings']
    xit = 0
    Ts = []
    for state in argv:
      with Pfx(state):
        if state == 'queue':
          print("refresh queue...")
          Ts.append(bg_thread(api.queue))
        elif state == 'recordings':
          print("refresh recordings...")
          Ts.append(bg_thread(api.recordings))
        else:
          warning("unsupported update target")
          xit = 1
    print("wait for API...")
    for T in Ts:
      T.join()
    return xit

  @popopts
  def cmd_service(self, argv, locale='en_US'):
    ''' Usage: {cmd} [service_id]
          List services.
    '''
    if argv:
      service_id = argv.pop(0)
    else:
      service_id = None
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    api = self.options.api
    for service in sorted(api.services(), key=lambda svc: svc['playon.ID']):
      playon = service.subtags('playon')
      if service_id is not None and playon.ID != service_id:
        print("skip", playon.ID)
        continue
      login_meta = playon.LoginMetadata
      print(playon.ID, playon.Name, login_meta['URL'] if login_meta else {})
      if service_id is None:
        continue
      for tag in playon:
        print(" ", tag)

class _PlayOnEntity(HasTags):
  ''' The base class of the entity subclasses.
      This exists as a search root for the subclass `.TYPE_SUBNAME` attribute.
  '''

class LoginState(_PlayOnEntity):

  TYPE_SUBNAME = 'login.state'

  @property
  def expiry(self):
    ''' Expiry unixtime of the login state information.
        `-1` if the `'exp'` field is not present.
    '''
    return self.tags.get('exp') or -1

class Recording(_PlayOnEntity):
  ''' A PlayOn recording.
  '''

  TYPE_SUBNAME = 'recording'

  # recording data are considered stale after 10 minutes
  STALE_AGE = 600

  RECORDING_QUALITY = {
      1: '720p',
      2: '1080p',
  }

  @format_attribute
  def resolution(self):
    ''' The recording resolution derived from the quality
        via the `Recording.RECORDING_QUALITY` mapping.
    '''
    quality = self.tags.get('playon.Quality')
    if quality is None:
      return None
    return self.RECORDING_QUALITY.get(quality, quality)

  @format_attribute
  def recording_id(self):
    ''' The recording id or `None`.
    '''
    return self.get('playon.ID')

  @cached_property
  def sei(self):
    ''' A `PlayonSeriesEpisodeInfo` inferred from this `Recording`.
    '''
    return PlayonSeriesEpisodeInfo.from_Recording(self)

  @format_attribute
  def nice_name(self):
    ''' A nice name for the recording: the PlayOn series and name,
        omitting the series if that is `None`.
    '''
    sei = self.sei
    if sei.series:
      citation = f'{sei.series} - s{sei.season:02d}e{sei.episode:02d} - {sei.episode_title}'
      if sei.episode_part:
        citation += f' - pt{sei.episode_part:02d}'
    else:
      citation = sei.episode_title or self['playon.Name']
    return citation

  @format_attribute
  def status(self):
    ''' Return a short status string.
    '''
    for status_label in 'queued', 'expired', 'downloaded', 'pending':
      if getattr(self, f'is_{status_label}')():
        return status_label
    raise RuntimeError("cannot infer a status string: %s" % (self,))

  @format_attribute
  def series_prefix(self):
    ''' Return a series prefix for recording containing the series name
        and season and episode, or `''`.
    '''
    sei = self.sei
    sep = '--'
    parts = []
    if sei.series:
      parts.append(sei.series)
      se_parts = []
      if sei.season:
        se_parts.append(f's{sei.season:02d}')
      if sei.episode is not None:
        se_parts.append(f'e{sei.episode:02d}')
      if se_parts:
        parts.append(''.join(se_parts))
    if not parts:
      return ''
    return sep.join(parts) + sep

  @format_attribute
  def series_episode_name(self):
    sei = self.sei
    name = sei.episode_title
    if sei.episode_part:
      name += f'--pt{sei.episode_part:02d}'
    return name.strip()

  @format_attribute
  def is_available(self):
    ''' Is a recording available for download?
    '''
    return not self.is_expired() and not self.is_queued()

  @format_attribute
  def is_queued(self):
    ''' Is a recording still in the queue?
    '''
    return 'playon.Created' not in self

  @format_attribute
  def is_downloaded(self):
    ''' Test whether this recording has been downloaded
        based on the presence of a `download_path` `Tag`
        or a true `downloaded` `Tag`.
    '''
    return self.get('download_path') is not None or 'downloaded' in self

  @format_attribute
  def is_pending(self):
    ''' A pending download: available and not already downloaded.
    '''
    return self.is_available() and not self.is_downloaded()

  @format_attribute
  def is_expired(self):
    ''' Test whether this recording is expired,
        which implies that it is no longer available for download.
    '''
    expires = self.get('playon.Expires')
    if not expires:
      return True
    return PlayOnAPI.from_playon_date(expires).timestamp() < time.time()

  @format_attribute
  def is_stale(self, max_age=None):
    ''' Override for `TagSet.is_stale()` which considers expired
        records not stale because they can never be refrehed from the
        service.
    '''
    if self.is_expired():
      # an expired recording will never become stale
      return False
    last_updated = self.get('last_updated')
    if last_updated is None:
      return True
    return last_updated + (max_age or self.STALE_AGE) < time.time()

  @fmtdoc
  def filename(self, filename_format=None) -> str:
    ''' Return the computed filename per `filename_format`,
        default from `DEFAULT_FILENAME_FORMAT`: `{DEFAULT_FILENAME_FORMAT!r}`.
    '''
    if filename_format is None:
      filename_format = DEFAULT_FILENAME_FORMAT
    filename = self.format_as(filename_format)
    filename = (
        filename.lower().replace(' - ', '--').replace(' ', '-')
        .replace('_', ':').replace(os.sep, ':') + '.'
    )
    filename = re.sub('---+', '--', filename)
    return filename

  def ls(self, *, format=None, long_mode=False, print_func=None):
    ''' List a recording.
    '''
    if format is None:
      format = PlayOnCommand.LS_FORMAT
    if print_func is None:
      print_func = print
    print_func(self.format_as(format))
    if long_mode:
      printt(
          *([f'  {tag.name}', tag.value] for tag in sorted(self.tags)),
          print_func=print_func,
      )

@dataclass
class PlayonSeriesEpisodeInfo(SeriesEpisodeInfo, Promotable):
  ''' A `SeriesEpisodeInfo` with a `from_Recording()` factory method to build
      one from a PlayOn `Recording` instead or other mapping with `playon.*` keys.
  '''

  @classmethod
  def from_Recording(cls, R: Mapping[str, Any]):
    ''' Infer series episode information from a `Recording`
        or any mapping with ".playon.*" keys.
    '''
    # get a basic SEI from the title
    episode_title = R.get('playon.Name')
    playon_series = R.get('playon.Series')
    playon_season = R.get('playon.Season')
    playon_episode = R.get('playon.Episode')
    # now override various fields from the playon tags
    ###############################################################
    # match a Playon browse path like "... | The Flash | Season 9"
    browse_path = R['playon.BrowsePath']
    browse_re_s = r'\|\s+(?P<series_s>[^|\s][^|]*[^|\s])\s+\|\s+season\s+(?P<season_s>\d+)$'
    m = re.search(
        browse_re_s,
        browse_path,
        re.I,
    )
    browse_series = m and m.group('series_s')
    browse_season = m and int(m.group('season_s'))
    # ignore the series "None", still unsure if this is some furphy
    # from a genuine None value
    if playon_series and playon_series.lower() == 'none':
      playon_series = None
    # sometimes the series is prepended to the episode title
    if playon_series:
      episode_title = cutprefix(episode_title, f'{playon_series} - ')
    # strip the trailing part info eg ": Part One"
    part_suffix, episode_part = get_suffix_part(episode_title)
    if part_suffix:
      episode_title = cutsuffix(episode_title, part_suffix)
    # strip leading "sSSeEE - " prefix
    spfx, episode_title_season, offset = get_prefix_n(
        episode_title.lower(), 's', n=playon_season
    )
    epfx, episode_title_episode, offset = get_prefix_n(
        episode_title.lower(), 'e', n=playon_episode, offset=offset
    )
    if offset > 0:
      # strip the sSSeEE and any spaces or dashes which follow it
      episode_title = episode_title[offset:].lstrip(' -')
    # fall back from provided stuff to inferred stuff
    return cls(
        series=playon_series or browse_series,
        season=playon_season or episode_title_season or browse_season,
        episode=playon_episode or episode_title_episode,
        episode_title=episode_title,
        episode_part=episode_part,
    )

# pylint: disable=too-many-ancestors
class PlayOnSQLTags(SQLTags):
  ''' PlayOn flavoured `SQLTags`; it just has custom values for the default db location.
  '''

  DBURL_ENVVAR = PLAYON_DBURL_ENVVAR
  DBURL_DEFAULT = PLAYON_DBURL_DEFAULT

# pylint: disable=too-many-instance-attributes
@monitor
class PlayOnAPI(HTTPServiceAPI):
  ''' Access to the PlayOn API.
  '''

  TYPE_ZONE = 'playon'
  HasTagsClass = _PlayOnEntity
  TagSetsClass = PlayOnSQLTags

  API_HOSTNAME = 'api.playonrecorder.com'
  API_BASE = f'https://{API_HOSTNAME}/v3/'
  API_AUTH_GRACETIME = 30

  CDS_HOSTNAME = 'cds.playonrecorder.com'
  CDS_BASE = f'https://{CDS_HOSTNAME}/api/v6/'

  CDS_HOSTNAME_LOCAL = 'cds-au.playonrecorder.com'
  CDS_BASE_LOCAL = f'https://{CDS_HOSTNAME_LOCAL}/api/v6/'

  def __init__(self, login=None, password=None, **kw):
    super().__init__(**kw)
    if login:
      self.login_userid = login
    self._password = password

  @cached_property
  @typechecked
  def login_userid(self) -> str:
    ''' The default `login_userid` comes from the netrc entry for `self.API_HOSTNAME`.
    '''
    N = netrc()
    with Pfx(".netrc host %r", self.API_HOSTNAME):
      entry = N.hosts.get(self.API_HOSTNAME)
    if entry is None:
      raise AttributeError(
          f'{self.__class__.__name__}.login_userid'
          f': no netrc entry for {self.API_HOSTNAME=}'
      )
    return entry[0]

  @pfx_method
  def login(self):
    ''' Perform a login, return the resulting `dict`.
        *Does not* update the state of `self`.
    '''
    login = self.login_userid
    password = self._password
    if not login or not password:
      N = netrc()
      netrc_hosts = []
      if login:
        assert login is not None and login != 'None', "login=%r" % login
        netrc_host = f"{login}:{self.API_HOSTNAME}"
        netrc_hosts.append(netrc_host)
        with Pfx(".netrc host %r", netrc_host):
          entry = N.hosts.get(netrc_host)
      else:
        entry = None
      if not entry:
        netrc_hosts.append(self.API_HOSTNAME)
        with Pfx(".netrc host %r", self.API_HOSTNAME):
          entry = N.hosts.get(self.API_HOSTNAME)
      if not entry:
        raise ValueError("no netrc entry for %r" % (netrc_hosts,))
      n_login, _, n_password = entry
      if login is None:
        login = n_login
      elif n_login and login != n_login:
        raise ValueError(
            "netrc: supplied login:%r != netrc login:%r" % (login, n_login)
        )
      password = n_password
    return self.suburl_data(
        'login',
        _method='POST',
        headers={'x-mmt-app': 'web'},
        params=dict(email=login, password=password)
    )

  @property
  def login_expiry(self):
    ''' Expiry UNIX time for the login state.
    '''
    return self.login_state_mapping['exp']

  # UNUSED
  @property
  @pfx_method
  def auth_token(self):
    ''' An auth token obtained from the login state.
    '''
    return self.login_state['auth_token']

  @property
  def jwt(self):
    ''' The JWT token.
    '''
    return self.login_state['token']

  # UNUSED
  def renew_jwt(self):
    at = self.auth_token
    data = self.suburl_data(
        'login/at', _method='POST', params=dict(auth_token=at)
    )
    self._jwt = data['token']

  @staticmethod
  def from_playon_date(date_s) -> datetime:
    ''' Return a timezone aware datetime from a PlayOn date/time value;
        The PlayOn API seems to use UTC date strings.
    '''
    for time_format in "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S":
      try:
        return datetime.strptime(date_s,
                                 time_format).replace(tzinfo=timezone.utc)
      except ValueError:
        pass
    raise ValueError(f'failed to parse PlayOn time string {date_s=}')

  @typechecked
  def __getitem__(self, index: tuple | int) -> HasTags:
    ''' If `index` is an `int` return the associated `Recording`.
        Otherwise `index` should be a `tuple`, returns the associated `HasTags`.
    '''
    if isinstance(index, int):
      index = Recording, index
    return super().__getitem__(index)

  def suburl(
      self, suburl, *, api_version=None, headers=None, _base_url=None, **kw
  ):
    ''' Override `HTTPServiceAPI.suburl` with default
        `headers={'Authorization':self.jwt}`.
    '''
    if _base_url is None:
      _base_url = None if api_version is None else f'api/v{api_version}'
    if headers is None:
      headers = dict(Authorization=self.jwt)
    return super().suburl(suburl, _base_url=_base_url, headers=headers, **kw)

  def suburl_data(self, suburl, *, raw=False, **kw):
    ''' Call `suburl` and return the `'data'` component on success.

        Parameters:
        * `suburl`: the API subURL designating the endpoint.
        * `raw`: if true, return the whole decoded JSON result;
          the default is `False`, returning `'success'` in the
          result keys and returning `result['data']`
        Other keyword arguments are passed to the `HTTPServiceAPI.json` method.
    '''
    result = self.json(suburl, **kw)
    if raw:
      return result
    ok = result.get('success')
    if not ok:
      raise ValueError("failed: %r" % (result,))
    return result['data']

  @pfx_method
  def account(self):
    ''' Return account information.
    '''
    return self.suburl_data('account')

  @pfx_method
  def notifications(self):
    ''' Return the notifications.
    '''
    return self.suburl_data('notification')

  def cdsurl_data(self, suburl, _method='GET', headers=None, **kw):
    ''' Wrapper for `suburl_data` using `CDS_BASE` as the base URL.
    '''
    return self.suburl_data(
        suburl,
        _base_url=self.CDS_BASE,
        _method=_method,
        headers=headers,
        raw=True,
        **kw
    )

  @pfx_method
  def queue(self):
    ''' Return the `TagSet` instances for the queued recordings.
    '''
    data = self.suburl_data('queue')
    entries = data['entries']
    return self._entry_entities(
        entries, Recording, dict(
            Episode=int,
            ReleaseYear=int,
            Season=int,
        )
    )

  @pfx_method
  def fetch_recordings(self) -> set[Recording]:
    ''' Return a set of the `Recording` instances for the available recordings.
        This makes an API request.
    '''
    data = self.suburl_data('library/all')
    entries = data['entries']
    return self._entry_entities(
        entries, Recording, dict(
            Episode=int,
            ReleaseYear=int,
            Season=int,
        )
    )

  def recordings(self):
    ''' Yield the `Recording`s.

        Note that this includes both recorded and queued items.
    '''
    return self.find(f'name~{self.TYPE_ZONE}.recording.*')

  def __iter__(self):
    ''' Iteration iterates over the recordins.
    '''
    return iter(self.recordings())

  # pylint: disable=too-many-branches
  @pfx_method
  def recording_ids_from_str(self, arg):
    ''' Convert a string to a list of recording ids.
    '''
    with Pfx(arg):
      recordings = []
      if arg == 'all':
        recordings.extend(iter(self))
      elif arg == 'available':
        recordings.extend(
            recording for recording in self if recording.is_available()
        )
      elif arg == 'downloaded':
        recordings.extend(
            recording for recording in self if recording.is_downloaded()
        )
      elif arg == 'expired':
        recordings.extend(
            recording for recording in self if recording.is_expired()
        )
      elif arg == 'pending':
        recordings.extend(
            recording for recording in self
            if not recording.is_downloaded() and recording.is_available()
        )
      elif arg == 'queued':
        recordings.extend(
            recording for recording in self if recording.is_queued()
        )
      elif arg.startswith('/'):
        # match regexp against playon.Series or playon.Name
        r_text = arg[1:]
        if r_text.endswith('/'):
          r_text = r_text[:-1]
        r = pfx_call(re.compile, r_text, re.I)
        for recording in self:
          name = recording.get('playon.Name') or ''
          series = recording.get('playon.Series') or ''
          if r.search(series) or r.search(name):
            recordings.append(recording)
      else:
        # integer recording id
        try:
          dl_id = int(arg)
        except ValueError:
          warning(
              "unsupported word, expected one of all, available, downloaded, expired, pending, queues or a /search"
          )
        else:
          recordings.append(self[dl_id])
      return list(
          filter(
              lambda dl_id: dl_id is not None,
              map(lambda recording: recording.get('playon.ID'), recordings)
          )
      )

  available = recordings

  @pfx_method
  @require(
      lambda entity_type: (
          isinstance(entity_type, type) and
          issubclass(entity_type, _PlayOnEntity)
      ) or entity_type in ('feature', 'recording', 'service')
  )
  def _entry_entities(
      self,
      entries,
      entity_type: type | str,
      conversions: Optional[dict] = None
  ) -> set[_PlayOnEntity]:
    ''' Return a `set` of `HasTags` instances from PlayOn data entries.
    '''
    now = time.time()
    entities = set()
    for entry in entries:
      entry_id = entry['ID']
      with Pfx(entry_id):
        # pylint: disable=use-dict-literal
        if conversions:
          for e_field, conv in sorted(conversions.items()):
            try:
              value = entry[e_field]
            except KeyError:
              pass
            else:
              with Pfx("%s=%r", e_field, value):
                if value is None:
                  del entry[e_field]
                else:
                  try:
                    value2 = conv(value)
                  except ValueError as e:
                    warning("%r: %s", value, e)
                  else:
                    entry[e_field] = value2
        entity = self[entity_type, entry_id]
        entity.tags.update(entry, prefix='playon')
        entity.tags.update(dict(last_updated=now))
        entities.add(entity)
    return entities

  @pfx_method
  def _services_from_entries(self, entries):
    ''' Return the service `TagSet` instances from PlayOn data entries.
    '''
    return self._entry_entities(entries, 'service')

  @pfx_method
  def services(self):
    ''' Fetch the list of services.
    '''
    entries = self.cdsurl_data('content')
    return self._services_from_entries(entries)

  def service(self, service_id: str):
    ''' Return the service `SQLTags` instance for `service_id`.
    '''
    return self['service', service_id]

  @pfx_method
  def features(self):
    ''' Fetch the list of featured shows.
    '''
    entries = self.cdsurl_data('content/featured')
    return self._entry_entities(entries, 'feature')

  def feature(self, feature_id):
    ''' Return the feature `SQLTags` instance for `feature_id`.
    '''
    return self['feature', feature_id]

  def featured_image_url(self, feature_name: str):
    ''' URL of the image for a featured show. '''
    return self.suburl(
        self, f'content/featured/{feature_name}/image', api_version=9
    )

  def service_image_url(self, service_id, large=True):
    return self.suburl(
        self,
        f'content/{service_id}/image?size={"large" if large else "small"}',
        _base_url=self.CDS_HOSTNAME,
        api_version=9,
    )

  def ls(self, recording_specs, *, format: str, long_mode=False):
    ''' List the specified recordings.
    '''
    for spec in recording_specs:
      with Pfx(spec):
        recording_ids = self.recording_ids_from_str(spec)
        if not recording_ids:
          warning("no recording ids")
          continue
        for dl_id in sorted(recording_ids):
          recording = self[dl_id]
          with Pfx(recording.name):
            recording.ls(format=format, long_mode=long_mode)

  # pylint: disable=too-many-locals
  @pfx_method
  @uses_quiet
  @uses_verbose
  @uses_runstate
  @typechecked
  def download(
      self,
      download_id: int,
      filename=None,
      *,
      quiet: bool,
      runstate: RunState,
      verbose: bool,
  ):
    ''' Download the file with `download_id` to `filename_basis`.
        Return the `TagSet` for the recording.

        The default `filename` is the basename of the filename
        from the download.
        If the filename is supplied with a trailing dot (`'.'`)
        then the file extension will be taken from the filename
        of the download URL.
    '''
    dl_data = self.suburl_data(f'library/{download_id}/download')
    dl_url = dl_data['url']
    dl_basename = unpercent(basename(dl_url))
    if filename is None:
      filename = dl_basename
    elif filename.endswith('.'):
      _, dl_ext = splitext(dl_basename)
      filename = filename[:-1] + dl_ext
    if existspath(filename):
      warning(
          "SKIPPING download of %r: already exists, just tagging", filename
      )
      dl_rsp = None
    else:
      dl_cookies = dl_data['data']
      jar = self.cookies
      for ck_name in 'CloudFront-Expires', 'CloudFront-Key-Pair-Id', 'CloudFront-Signature':
        jar.set(
            ck_name,
            str(dl_cookies[ck_name]),
            domain='playonrecorder.com',
            secure=True,
        )
      dl_rsp = requests.get(
          dl_url, auth=RequestsNoAuth(), cookies=jar, stream=True
      )
      dl_length = content_length(dl_rsp.headers)
      assert dl_length is not None
      with pfx_call(atomic_filename, filename, mode='xb') as f:
        for chunk in progressbar(
            dl_rsp.iter_content(chunk_size=131072),
            label=filename,
            total=dl_length,
            units_scale=BINARY_BYTES_SCALE,
            itemlenfunc=len,
            report_print=not quiet if sys.stdout.isatty() else verbose,
        ):
          runstate.raiseif()
          offset = 0
          length = len(chunk)
          while offset < length:
            with Pfx("write %d bytes", length - offset):
              written = f.write(chunk[offset:])
              if written < 1:
                warning("fewer than 1 bytes written: %s", written)
              else:
                offset += written
                assert offset <= length
          assert offset == length
    fullpath = realpath(filename)
    recording = self[download_id]
    if dl_rsp is not None:
      recording['download_path'] = fullpath
    tagged = self.fstags[fullpath]
    # add the Series/Season/Episode info
    tagged.update(recording.sei.as_dict())
    # add a reference to the recording
    recording.type_reference_apply_to(tagged)
    return recording

if __name__ == '__main__':
  sys.exit(main(sys.argv))

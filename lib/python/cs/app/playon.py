#!/usr/bin/env python3
#
# Playon facilities. - Cameron Simpson <cs@cskk.id.au>
#

''' PlayOn facilities, primarily access to the download API.
    Includes a nice command line tool.
'''

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from getopt import getopt, GetoptError
from netrc import netrc
import os
from os import environ
from os.path import (
    basename, exists as pathexists, expanduser, realpath, splitext
)
from pprint import pformat
import re
import sys
from threading import RLock, Semaphore
import time
from urllib.parse import unquote as unpercent
import requests
from typeguard import typechecked
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.fstags import FSTags
from cs.fileutils import atomic_filename
from cs.lex import has_format_attributes, format_attribute
from cs.logutils import warning
from cs.pfx import Pfx, pfx_method, pfx_call
from cs.progress import progressbar
from cs.resources import MultiOpenMixin
from cs.result import bg as bg_result, report as report_results
from cs.sqltags import SQLTags, SQLTagSet
from cs.threads import monitor, bg as bg_thread
from cs.units import BINARY_BYTES_SCALE
from cs.upd import print  # pylint: disable=redefined-builtin

__version__ = '20211212-post'

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
        'console_scripts': ['playon = cs.app.playon:main'],
    },
    'install_requires': [
        'cs.cmdutils',
        'cs.context',
        'cs.deco',
        'cs.fileutils>=atomic_filename',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.pfx>=pfx_call',
        'cs.progress',
        'cs.resources',
        'cs.result',
        'cs.sqltags',
        'cs.threads',
        'cs.units',
        'cs.upd',
        'requests',
        'typeguard',
    ],
}

DBURL_ENVVAR = 'PLAYON_TAGS_DBURL'
DBURL_DEFAULT = '~/var/playon.sqlite'

FILENAME_FORMAT_ENVVAR = 'PLAYON_FILENAME_FORMAT'
DEFAULT_FILENAME_FORMAT = (
    '{playon.Series}--{playon.Name}--{resolution}--{playon.ProviderID}--playon--{playon.ID}'
)

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

  # default "ls" output format
  LS_FORMAT = (
      '{playon.ID} {playon.HumanSize} {resolution}'
      ' {playon.Series} {playon.Name} {playon.ProviderID} {status:upper}'
  )

  # default "queue" output format
  QUEUE_FORMAT = '{playon.ID} {playon.Series} {playon.Name} {playon.ProviderID}'

  USAGE_KEYWORDS = {
      'DEFAULT_DL_PARALLELISM': DEFAULT_DL_PARALLELISM,
      'DEFAULT_FILENAME_FORMAT': DEFAULT_FILENAME_FORMAT,
      'FILENAME_FORMAT_ENVVAR': FILENAME_FORMAT_ENVVAR,
      'LS_FORMAT': LS_FORMAT,
      'DBURL_ENVVAR': DBURL_ENVVAR,
      'DBURL_DEFAULT': DBURL_DEFAULT,
      'QUEUE_FORMAT': QUEUE_FORMAT,
  }

  USAGE_FORMAT = r'''Usage: {cmd} subcommand [args...]

    Environment:
      PLAYON_USER               PlayOn login name, default from $EMAIL.
      PLAYON_PASSWORD           PlayOn password.
                                This is obtained from .netrc if omitted.
      {FILENAME_FORMAT_ENVVAR}  Format string for downloaded filenames.
                                Default: {DEFAULT_FILENAME_FORMAT}
      {DBURL_ENVVAR:17}         Location of state tags database.
                                Default: {DBURL_DEFAULT}

    Recording specification:
      an int        The specific recording id.
      all           All known recordings.
      downloaded    Recordings already downloaded.
      expired       Recording which are no longer available.
      pending       Recordings not already downloaded.
      /regexp       Recordings whose Series or Name match the regexp,
                    case insensitive.
  '''

  def apply_defaults(self):
    options = self.options
    options.user = environ.get('PLAYON_USER', environ.get('EMAIL'))
    options.password = environ.get('PLAYON_PASSWORD')
    options.filename_format = environ.get(
        'PLAYON_FILENAME_FORMAT', DEFAULT_FILENAME_FORMAT
    )

  @contextmanager
  def run_context(self):
    ''' Prepare the `PlayOnAPI` around each command invocation.
    '''
    options = self.options
    sqltags = PlayOnSQLTags()
    api = PlayOnAPI(options.user, options.password, sqltags)
    with sqltags:
      with stackattrs(options, api=api, sqltags=sqltags):
        with api:
          # preload all the recordings from the db
          list(sqltags.recordings())
          # if there are unexpired stale entries or no unexpired entries,
          # refresh them
          self._refresh_sqltags_data(api, sqltags)
          yield

  def cmd_account(self, argv):
    ''' Usage: {cmd}
          Report account state.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    api = self.options.api
    for k, v in sorted(api.account().items()):
      print(k, pformat(v))

  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
  def cmd_dl(self, argv):
    ''' Usage: {cmd} [-j jobs] [-n] [recordings...]
          Download the specified recordings, default "pending".
          -j jobs   Run this many downloads in parallel.
                    The default is {DEFAULT_DL_PARALLELISM}.
          -n        No download. List the specified recordings.
    '''
    options = self.options
    sqltags = options.sqltags
    dl_jobs = DEFAULT_DL_PARALLELISM
    no_download = False
    opts, argv = getopt(argv, 'j:n')
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-j':
          dl_jobs = int(val)
          if dl_jobs < 1:
            raise GetoptError(f"invalid jobs, should be >= 1, got: {dl_jobs}")
        elif opt == '-n':
          no_download = True
        else:
          raise RuntimeError("unhandled option")
    if not argv:
      argv = ['pending']
    api = options.api
    filename_format = options.filename_format
    sem = Semaphore(dl_jobs)

    @typechecked
    def _dl(dl_id: int, sem):
      try:
        with sqltags:
          filename = api[dl_id].format_as(filename_format)
          filename = (
              filename.lower().replace(' - ', '--').replace('_', ':')
              .replace(' ', '-').replace(os.sep, ':') + '.'
          )
          try:
            api.download(dl_id, filename=filename)
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
        recording_ids = sqltags.recording_ids_from_str(arg)
        if not recording_ids:
          warning("no recording ids")
          xit = 1
          continue
        for dl_id in recording_ids:
          recording = sqltags[dl_id]
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
              Rs.append(bg_result(_dl, dl_id, sem, _extra=dict(dl_id=dl_id)))

    if Rs:
      for R in report_results(Rs):
        dl_id = R.extra['dl_id']
        recording = sqltags[dl_id]
        if not R():
          print("FAILED", dl_id)
          xit = 1

    return xit

  @staticmethod
  def _refresh_sqltags_data(api, sqltags, max_age=None):
    ''' Refresh the queue and recordings if any unexpired records are stale
        or if all records are expired.
    '''
    need_refresh = False
    recordings = set(sqltags.recordings())
    stale_map = {
        recording.recording_id(): recording
        for recording in recordings
        if not recording.is_expired() and recording.is_stale(max_age=max_age)
    }
    if stale_map:
      need_refresh = True
    if need_refresh:
      print("refresh queue and recordings...")
      Ts = [bg_thread(api.queue), bg_thread(api.recordings)]
      for T in Ts:
        T.join()

  @staticmethod
  def _list(argv, options, default_argv, default_format):
    ''' Inner workings of "ls" and "queue".

        Usage: {ls|queue} [-l] [-o format] [recordings...]
          List available downloads.
          -l        Long listing: list tags below each entry.
          -o format Format string for each entry.
    '''
    sqltags = options.sqltags
    long_mode = False
    listing_format = default_format
    opts, argv = getopt(argv, 'lo:', '')
    for opt, val in opts:
      if opt == '-l':
        long_mode = True
      elif opt == '-o':
        listing_format = val
      else:
        raise RuntimeError("unhandled option: %r" % (opt,))
    if not argv:
      argv = list(default_argv)
    xit = 0
    for arg in argv:
      with Pfx(arg):
        recording_ids = sqltags.recording_ids_from_str(arg)
        if not recording_ids:
          warning("no recording ids")
          xit = 1
          continue
        for dl_id in recording_ids:
          recording = sqltags[dl_id]
          with Pfx(recording.name):
            recording.ls(ls_format=listing_format, long_mode=long_mode)
    return xit

  def cmd_ls(self, argv):
    ''' Usage: {cmd} [-l] [recordings...]
          List available downloads.
          -l        Long listing: list tags below each entry.
          -o format Format string for each entry.
          Default format: {LS_FORMAT}
    '''
    return self._list(argv, self.options, ['available'], self.LS_FORMAT)

  def cmd_queue(self, argv):
    ''' Usage: {cmd} [-l] [recordings...]
          List queued recordings.
          -l        Long listing: list tags below each entry.
          -o format Format string for each entry.
          Default format: {QUEUE_FORMAT}
    '''
    return self._list(argv, self.options, ['queued'], self.QUEUE_FORMAT)

  cmd_q = cmd_queue

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

  def cmd_service(self, argv, locale='en_US'):
    ''' Usage: {cmd} [service_id]
          List services.
    '''
    if argv:
      service_id = argv.pop(0)
    else:
      service_id = None
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    api = self.options.api
    for service in sorted(api.services(), key=lambda svc: svc['playon.ID']):
      playon = service.subtags('playon')
      if service_id is not None and playon.ID != service_id:
        print("skip", playon.ID)
        continue
      print(playon.ID, playon.Name, playon.LoginMetadata["URL"])
      if service_id is None:
        continue
      for tag in playon:
        print(" ", tag)

# pylint: disable=too-few-public-methods
class _RequestsNoAuth(requests.auth.AuthBase):
  ''' The API has a distinct login call, avoid basic auth from netrc etc.
  '''

  def __call__(self, r):
    return r

# pylint: disable=too-many-ancestors
@has_format_attributes
class Recording(SQLTagSet):
  ''' An `SQLTagSet` with knowledge about PlayOn recordings.
  '''

  # recording data stale after 10 minutes
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
    quality = self.get('playon.Quality')
    return self.RECORDING_QUALITY.get(quality, quality)

  @format_attribute
  def recording_id(self):
    ''' The recording id or `None`.
    '''
    return self.get('playon.ID')

  @format_attribute
  def nice_name(self):
    ''' A nice name for the recording: the PlayOn series and name,
        omitting the series if `None`.
    '''
    playon_tags = self.subtags('playon')
    citation = playon_tags.Name
    if playon_tags.Series and playon_tags.Series != 'none':
      citation = playon_tags.Series + " - " + citation
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
        based on the presence of a `download_path` `Tag`.
    '''
    return self.download_path is not None

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
    ''' Test whether this entry is stale
        i.e. the time since `self.last_updated` exceeds `max_age` seconds
        (default from `self.STALE_AGE`).
        Note that expired recordings are never stale
        because they can no longer be queried from the API.
    '''
    if max_age is None:
      max_age = self.STALE_AGE
    if self.is_expired():
      # expired recording will never become unstale
      return False
    if max_age <= 0:
      return True
    last_updated = self.last_updated
    if not last_updated:
      return True
    return time.time() >= last_updated + max_age

  def ls(self, ls_format=None, long_mode=False, print_func=None):
    ''' List a recording.
    '''
    if ls_format is None:
      ls_format = PlayOnCommand.LS_FORMAT
    if print_func is None:
      print_func = print
    print_func(self.format_as(ls_format))
    if long_mode:
      for tag in sorted(self):
        print_func(" ", tag)

# pylint: disable=too-many-ancestors
class PlayOnSQLTags(SQLTags):
  ''' `SQLTags` subclass with PlayOn related methods.
  '''

  STATEDBPATH = '~/var/playon.sqlite'

  # map 'foo' from 'foo.bah' to a particular TagSet subclass
  TAGSETCLASS_PREFIX_MAPPING = {
      'recording': Recording,
  }

  def __init__(self, dbpath=None):
    if dbpath is None:
      dbpath = expanduser(self.STATEDBPATH)
    super().__init__(db_url=dbpath)

  @staticmethod
  @fmtdoc
  def infer_db_url(envvar=None, default_path=None):
    ''' Infer the database URL.

        Parameters:
        * `envvar`: environment variable to specify a default,
          default from `DBURL_ENVVAR` (`{DBURL_ENVVAR}`).
    '''
    if envvar is None:
      envvar = DBURL_ENVVAR
    if default_path is None:
      default_path = DBURL_DEFAULT
    return super().infer_db_url(envvar=envvar, default_path=default_path)

  def __getitem__(self, index):
    if isinstance(index, int):
      index = f'recording.{index}'
    return super().__getitem__(index)

  def recordings(self):
    ''' Yield recording `TagSet`s, those named `"recording.*"`.

        Note that this includes both recorded and queued items.
    '''
    return self.find('name~recording.*')

  __iter__ = recordings

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
        with Pfx("re.compile(%r, re.I)", r_text):
          r = re.compile(r_text, re.I)
        for recording in self:
          pl_tags = recording.subtags('playon')
          if (pl_tags.Series and r.search(pl_tags.Series)
              or pl_tags.Name and r.search(pl_tags.Name)):
            recordings.append(recording)
      else:
        # integer recording id
        try:
          dl_id = int(arg)
        except ValueError:
          warning("unsupported word")
        else:
          recordings.append(self[dl_id])
      return list(
          filter(
              lambda dl_id: dl_id is not None,
              map(lambda recording: recording.get('playon.ID'), recordings)
          )
      )

# pylint: disable=too-many-instance-attributes
@monitor
class PlayOnAPI(MultiOpenMixin):
  ''' Access to the PlayOn API.
  '''

  API_HOSTNAME = 'api.playonrecorder.com'
  API_BASE = f'https://{API_HOSTNAME}/v3/'
  API_AUTH_GRACETIME = 30

  CDS_HOSTNAME = 'cds.playonrecorder.com'
  CDS_BASE = f'https://{CDS_HOSTNAME}/api/v6/'

  def __init__(self, login, password, sqltags=None):
    if sqltags is None:
      sqltags = PlayOnSQLTags()
    self._lock = RLock()
    self._auth_token = None
    self._login = login
    self._password = password
    self._login_state = None
    self._jwt = None
    self._cookies = {}
    self._storage = defaultdict(str)
    self.sqltags = sqltags
    self._fstags = FSTags()

  @contextmanager
  def startup_shutdown(self):
    ''' Start up: open and init the `SQLTags`, open the `FSTags`.
    '''
    sqltags = self.sqltags
    with sqltags:
      sqltags.init()
      with self._fstags:
        yield

  @property
  @pfx_method
  def auth_token(self):
    ''' An auth token obtained from the login state.
    '''
    return self.login_state['auth_token']

  @property
  def login_state(self):
    ''' The login state, a `dict`. Performs a login if necessary.
    '''
    with self._lock:
      state = self._login_state
      if not state or time.time() + self.API_AUTH_GRACETIME >= state['exp']:
        self._login_state = None
        self._jwt = None
        # not logged in or login about to expire
        state = self._login_state = self._dologin()
        self._jwt = state['token']
    return state

  @pfx_method
  def _dologin(self):
    ''' Perform a login, return the resulting `dict`.
        Does not update the state of `self`.
    '''
    login = self._login
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
  def jwt(self):
    ''' The JWT token.
    '''
    # ensure logged in with current tokens
    self.login_state  # pylint: disable=pointless-statement
    return self._jwt

  def _renew_jwt(self):
    at = self.auth_token
    data = self.suburl_data(
        'login/at', _method='POST', params=dict(auth_token=at)
    )
    self._jwt = data['token']

  @staticmethod
  def from_playon_date(date_s):
    ''' The PlayOn API seems to use UTC date strings.
    '''
    return datetime.strptime(date_s,
                             "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

  @typechecked
  def __getitem__(self, download_id: int):
    ''' Return the recording `TagSet` associated with the recording `download_id`.
    '''
    return self.sqltags[download_id]

  @staticmethod
  def suburl_request(base_url, method, suburl):
    ''' Return a curried `requests` method
        to fetch `API_BASE/suburl`.
    '''
    url = base_url + suburl
    rqm = partial(
        {
            'GET': requests.get,
            'POST': requests.post,
            'HEAD': requests.head,
        }[method],
        url,
        auth=_RequestsNoAuth(),
    )
    return rqm

  def suburl_data(
      self,
      suburl,
      _base_url=None,
      _method='GET',
      headers=None,
      raw=False,
      **kw
  ):
    ''' Call `suburl` and return the `'data'` component on success.

        Parameters:
        * `suburl`: the API subURL designating the endpoint.
        * `_method`: optional HTTP method, default `'GET'`.
        * `headers`: hreaders to accompany the request;
          default `{'Authorization':self.jwt}`.
        Other keyword arguments are passed to the `requests` method
        used to perform the HTTP call.
    '''
    if _base_url is None:
      _base_url = self.API_BASE
    if headers is None:
      headers = dict(Authorization=self.jwt)
    rqm = self.suburl_request(_base_url, _method, suburl)
    result = rqm(headers=headers, **kw).json()
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
  def _recordings_from_entries(self, entries):
    ''' Return the recording `TagSet` instances from PlayOn data entries.
    '''
    with self.sqltags:
      now = time.time()
      recordings = set()
      for entry in entries:
        entry_id = entry['ID']
        with Pfx(entry_id):
          for field, conv in sorted(dict(
              Episode=int,
              ReleaseYear=int,
              Season=int,
              ##Created=self.from_playon_date,
              ##Expires=self.from_playon_date,
              ##Updated=self.from_playon_date,
          ).items()):
            try:
              value = entry[field]
            except KeyError:
              pass
            else:
              with Pfx("%s=%r", field, value):
                if value is None:
                  del entry[field]
                else:
                  try:
                    value2 = conv(value)
                  except ValueError as e:
                    warning("%r: %s", value, e)
                  else:
                    entry[field] = value2
          recording = self[entry_id]
          recording.update(entry, prefix='playon')
          recording.update(dict(last_updated=now))
          recordings.add(recording)
      return recordings

  @pfx_method
  def queue(self):
    ''' Return the `TagSet` instances for the queued recordings.
    '''
    data = self.suburl_data('queue')
    entries = data['entries']
    return self._recordings_from_entries(entries)

  @pfx_method
  def recordings(self):
    ''' Return the `TagSet` instances for the available recordings.
    '''
    data = self.suburl_data('library/all')
    entries = data['entries']
    return self._recordings_from_entries(entries)

  @pfx_method
  def _services_from_entries(self, entries):
    ''' Return the service `TagSet` instances from PlayOn data entries.
    '''
    with self.sqltags:
      now = time.time()
      services = set()
      for entry in entries:
        entry_id = entry['ID']
        with Pfx(entry_id):
          for field, conv in sorted(dict(
              ##Created=self.from_playon_date,
              ##Expires=self.from_playon_date,
              ##Updated=self.from_playon_date,
          ).items()):
            try:
              value = entry[field]
            except KeyError:
              pass
            else:
              with Pfx("%s=%r", field, value):
                if value is None:
                  del entry[field]
                else:
                  try:
                    value2 = conv(value)
                  except ValueError as e:
                    warning("%r: %s", value, e)
                  else:
                    entry[field] = value2
          service = self.service(entry_id)
          service.update(entry, prefix='playon')
          service.update(dict(last_updated=now))
          services.add(service)
      return services

  @pfx_method
  def services(self):
    ''' Fetch the list of services.
    '''
    entries = self.cdsurl_data('content')
    return self._services_from_entries(entries)

  def service(self, service_id):
    ''' Return the service `SQLTags` instance for `service_id`.
    '''
    return self.sqltags[f'service.{service_id}']

  # pylint: disable=too-many-locals
  @pfx_method
  @typechecked
  def download(self, download_id: int, filename=None):
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
    if pathexists(filename):
      warning(
          "SKIPPING download of %r: already exists, just tagging", filename
      )
      dl_rsp = None
    else:
      dl_cookies = dl_data['data']
      jar = requests.cookies.RequestsCookieJar()
      for ck_name in 'CloudFront-Expires', 'CloudFront-Key-Pair-Id', 'CloudFront-Signature':
        jar.set(
            ck_name,
            str(dl_cookies[ck_name]),
            domain='playonrecorder.com',
            secure=True,
        )
      dl_rsp = requests.get(
          dl_url, auth=_RequestsNoAuth(), cookies=jar, stream=True
      )
      dl_length = int(dl_rsp.headers['Content-Length'])
      with pfx_call(atomic_filename, filename, mode='wb',
                    placeholder=True) as f:
        for chunk in progressbar(
            dl_rsp.iter_content(chunk_size=131072),
            label=filename,
            total=dl_length,
            units_scale=BINARY_BYTES_SCALE,
            itemlenfunc=len,
            report_print=True,
        ):
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
      recording.set('download_path', fullpath)
    # apply the SQLTagSet to the FSTags TagSet
    self._fstags[fullpath].update(recording.subtags('playon'), prefix='playon')
    return recording

if __name__ == '__main__':
  sys.exit(main(sys.argv))

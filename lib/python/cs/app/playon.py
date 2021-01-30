#!/usr/bin/env python3
#
# Playon facilities. - Cameron Simpson <cs@cskk.id.au>
#

''' Playon facilities.
'''

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from getopt import getopt, GetoptError
from netrc import netrc
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
from cs.fstags import FSTags
from cs.logutils import warning
from cs.pfx import Pfx, pfx_method
from cs.progress import progressbar
from cs.resources import MultiOpenMixin
from cs.result import bg as bg_result, report as report_results
from cs.sqltags import SQLTags, SQLTagSet
from cs.threads import monitor, bg as bg_thread
from cs.units import BINARY_BYTES_SCALE
from cs.upd import print  # pylint: disable=redefined-builtin

DEFAULT_FILENAME_FORMAT = (
    '{playon.Series}--{playon.Name}--{playon.ProviderID}--playon--{playon.ID}'
)

def main(argv=None):
  ''' Playon command line mode.
  '''
  return PlayOnCommand(argv).run()

class PlayOnCommand(BaseCommand):
  ''' Playon command line implementation.
  '''

  # default "ls" output format
  LS_FORMAT = '{playon.ID} {playon.HumanSize} {playon.Series} {playon.Name} {playon.ProviderID}'

  # default "queue" output format
  QUEUE_FORMAT = '{playon.ID} {playon.Series} {playon.Name} {playon.ProviderID}'

  USAGE_KEYWORDS = {
      'DEFAULT_FILENAME_FORMAT': DEFAULT_FILENAME_FORMAT,
      'LS_FORMAT': LS_FORMAT,
      'QUEUE_FORMAT': QUEUE_FORMAT,
  }

  USAGE_FORMAT = r'''Usage: {cmd} subcommand [args...]

    Environment:
      PLAYON_USER               PlayOn login name.
      PLAYON_PASSWORD           PlayOn password.
                                This is obtained from .netrc if omitted.
      PLAYON_FILENAME_FORMAT    Format string for downloaded filenames.
                                Default: {DEFAULT_FILENAME_FORMAT}

    Recording specification:
      an int        The specific recording id.
      all           All known recordings.
      downloaded    Recordings already downloaded.
      pending       Recordings not already downloaded.
      /regexp       Recordings whose Series or Name match the regexp,
                    case insensitive.
  '''

  def apply_defaults(self):
    options = self.options
    options.user = environ.get('PLAYON_USER')
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
    with stackattrs(options, api=api, sqltags=sqltags):
      with api:
        # preload all the recordings from the db
        all_recordings = list(sqltags.recordings())
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

  def cmd_dl(self, argv):
    ''' Usage: {cmd} [-n] [recordings...]
          Download the specified recordings, default "pending".
          -n  No download. List the specified recordings.
    '''
    options = self.options
    sqltags = options.sqltags
    no_download = False
    if argv and argv[0] == '-n':
      argv.pop(0)
      no_download = True
    if not argv:
      argv = ['pending']
    api = options.api
    filename_format = options.filename_format
    sem = Semaphore(2)

    @typechecked
    def _dl(dl_id: int, sem):
      try:
        with sqltags.sql_session():
          filename = api[dl_id].format_as(filename_format)
          filename = (
              filename.lower().replace(' - ',
                                       '--').replace('_',
                                                     ':').replace(' ', '-') +
              '.'
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
          te = sqltags[dl_id]
          with Pfx(te.name):
            if te.is_expired():
              warning("expired, skipping")
              continue
            if te.is_downloaded():
              warning("already downloaded to %r", te.download_path)
            if no_download:
              te.ls()
            else:
              sem.acquire()
              Rs.append(bg_result(_dl, dl_id, sem, _extra=dict(dl_id=dl_id)))

    if Rs:
      for R in report_results(Rs):
        dl_id = R.extra['dl_id']
        te = sqltags[dl_id]
        if R():
          print("OK ", dl_id, te.download_path)
        else:
          print("BAD", dl_id)
          xit = 1

    return xit

  @staticmethod
  def _refresh_sqltags_data(api, sqltags, max_age=None):
    ''' Refresh the queue and recordings if any unexpired records are stale
        or if all records are expired.
    '''
    tes = set(sqltags.recordings())
    if (any(map(
        lambda te: not te.is_expired() and te.is_stale(max_age=max_age), tes))
        or all(map(lambda te: te.is_expired(), tes))):
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
          te = sqltags[dl_id]
          with Pfx(te.name):
            te.ls(ls_format=listing_format, long_mode=long_mode)
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

  def cmd_update(self, argv):
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

# pylint: disable=too-few-public-methods
class _RequestsNoAuth(requests.auth.AuthBase):
  ''' The API has a distinct login call, avoid basic auth from netrc etc.
  '''

  def __call__(self, r):
    return r

class PlayOnSQLTagSet(SQLTagSet):
  ''' An `SQLTagSet` with some special methods.
  '''

  # recording data stale after 10 minutes
  STALE_AGE = 600

  def recording_id(self):
    ''' The recording id or `None`.
    '''
    return self.get('playon.ID')

  @property
  def status(self):
    ''' A short status string.
    '''
    if self.is_queued():
      return 'QUEUED'
    if self.is_expired():
      return 'EXPIRED'
    if self.is_downloaded():
      return 'DOWNLOADED'
    return 'PENDING'

  def is_available(self):
    ''' Is a recording available for download?
    '''
    return 'playon.Created' in self and not self.is_expired()

  def is_queued(self):
    ''' Is a recording still in the queue?
    '''
    return 'playon.Created' not in self

  def is_downloaded(self):
    ''' Test whether this recording has been downloaded
        based on the presence of a `download_path` `Tag`.
    '''
    return self.download_path is not None

  def is_expired(self):
    ''' Test whether this recording is expired,
        should imply no longer available for download.
    '''
    expires = self.get('playon.Expires')
    if not expires:
      return False
    return PlayOnAPI.from_playon_date(expires).timestamp() < time.time()

  def is_stale(self, max_age=None):
    ''' Test whether this entry is stale
        i.e. the time since `self.last_updated` exceeds `max_age` seconds,
        default from `self.STALE_AGE`.
    '''
    if max_age is None:
      max_age = self.STALE_AGE
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
    print_func(ls_format.format_map(self.ns()), f'{self.status}')
    if long_mode:
      for tag in sorted(self):
        print_func(" ", tag)

class PlayOnSQLTags(SQLTags):
  ''' `SQLTags` subclass with PlayOn related methods.
  '''

  STATEDBPATH = '~/var/playon.sqlite'

  TagSetClass = PlayOnSQLTagSet

  def __init__(self, dbpath=None):
    if dbpath is None:
      dbpath = expanduser(self.STATEDBPATH)
    super().__init__(db_url=dbpath)

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

  @pfx_method
  def recording_ids_from_str(self, arg):
    ''' Convert a string to a list of recording ids.
    '''
    with Pfx(arg):
      tes = []
      if arg == 'all':
        tes.extend(iter(self))
      elif arg == 'available':
        tes.extend(te for te in self if te.is_available())
      elif arg == 'downloaded':
        tes.extend(te for te in self if te.is_downloaded())
      elif arg == 'pending':
        tes.extend(
            te for te in self if not te.is_downloaded() and te.is_available()
        )
      elif arg == 'queued':
        tes.extend(te for te in self if te.is_queued())
      elif arg.startswith('/'):
        # match regexp against playon.Series or playon.Name
        r_text = arg[1:]
        if r_text.endswith('/'):
          r_text = r_text[:-1]
        with Pfx("re.compile(%r, re.I)", r_text):
          r = re.compile(r_text, re.I)
        for te in self:
          pl_tags = te.subtags('playon')
          if (pl_tags.Series and r.search(pl_tags.Series)
              or pl_tags.Name and r.search(pl_tags.Name)):
            tes.append(te)
      else:
        # integer recording id
        try:
          dl_id = int(arg)
        except ValueError:
          warning("unsupported word")
        else:
          tes.append(self[dl_id])
      return list(
          filter(
              lambda dl_id: dl_id is not None,
              map(lambda te: te.get('playon.ID'), tes)
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

  def startup(self):
    ''' Start up: open and init the `SQLTags`, open the `FSTags`.
    '''
    sqltags = self.sqltags
    sqltags.open()
    sqltags.init()
    self._fstags.open()

  def shutdown(self):
    ''' Shutdown: close the `SQLTags`, close the `FSTags`.
    '''
    self._fstags.close()
    self.sqltags.close()

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
      if login:
        entry = N.hosts.get(f"{login}:{self.API_HOSTNAME}")
      else:
        entry = None
      if not entry:
        entry = N.hosts.get(self.API_HOSTNAME)
      if not entry:
        raise ValueError("no netrc entry")
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
    ''' The PlayOnAPI seems to use UTC date strings.
    '''
    return datetime.strptime(date_s,
                             "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

  @typechecked
  def __getitem__(self, download_id: int):
    ''' Return the `TagSet` associated with the recording `download_id`.
    '''
    return self.sqltags[download_id]

  def suburl_request(self, method, suburl):
    ''' Return a curried `requests` method
        to fetch `API_BASE/suburl`.
    '''
    url = self.API_BASE + suburl
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

  def suburl_data(self, suburl, _method='GET', headers=None, **kw):
    ''' Call `suburl` and return the `'data'` component on success.

        Parameters:
        * `suburl`: the API subURL designating the endpoint.
        * `_method`: optional HTTP method, default `'GET'`.
        * `headers`: hreaders to accompany the request;
          default `{'Authorization':self.jwt}`.
        Other keyword arguments are passed to the `requests` method
        used to perform the HTTP call.
    '''
    if headers is None:
      headers = dict(Authorization=self.jwt)
    rqm = self.suburl_request(_method, suburl)
    result = rqm(headers=headers, **kw).json()
    ok = result.get('success')
    if not ok:
      raise ValueError("failed: %r" % (result,))
    return result['data']

  @pfx_method
  def account(self):
    ''' Return account information.
    '''
    return self.suburl_data('account')

  def _entities_from_entries(self, entries):
    ''' Return the `TagSet` instances from PlayOn data entries.
    '''
    with self.sqltags.sql_session():
      now = time.time()
      tes = set()
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
          te = self[entry_id]
          te.update(entry, prefix='playon')
          te.update(dict(last_updated=now))
          tes.add(te)
      return tes

  @pfx_method
  def queue(self):
    ''' Return the `TagSet` instances for the queued recordings.
    '''
    data = self.suburl_data('queue')
    entries = data['entries']
    return self._entities_from_entries(entries)

  @pfx_method
  def recordings(self):
    ''' Return the `TagSet` instances for the available recordings.
    '''
    data = self.suburl_data('library/all')
    entries = data['entries']
    return self._entities_from_entries(entries)

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
      with Pfx("open(%r,'wb')"):
        with open(filename, 'wb') as f:
          for chunk in progressbar(
              dl_rsp.iter_content(chunk_size=131072),
              label=filename,
              total=dl_length,
              units_scale=BINARY_BYTES_SCALE,
              itemlenfunc=len,
          ):
            offset = 0
            length = len(chunk)
            while length > 0:
              with Pfx("write %d bytes", length):
                written = f.write(chunk[offset:length])
                if written < 1:
                  warning("write %d bytes")
                else:
                  offset += written
                  length -= written
    fullpath = realpath(filename)
    te = self[download_id]
    if dl_rsp is not None:
      te.set('download_path', fullpath)
    # apply the SQLTagSet to the FSTags TagSet
    self._fstags[fullpath].update(te.subtags('playon'), prefix='playon')
    return te

if __name__ == '__main__':
  sys.exit(main(sys.argv))

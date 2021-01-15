#!/usr/bin/env python3
#
# Playon facilities. - Cameron Simpson <cs@cskk.id.au>
#

''' Playon facilities.
'''

from collections import defaultdict
from contextlib import contextmanager
##from datetime import datetime
from functools import partial
from getopt import GetoptError
from netrc import netrc
from os import environ
from os.path import (
    basename, exists as pathexists, expanduser, realpath, splitext
)
from pprint import pformat
import sys
import time
from urllib.parse import unquote as unpercent
import requests
from typeguard import typechecked
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import decorator
from cs.fstags import FSTags
from cs.logutils import warning
from cs.pfx import Pfx, pfx_method
from cs.progress import progressbar
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags
from cs.units import BINARY_BYTES_SCALE
from cs.upd import print  # pylint: disable=redefined-builtin

DEFAULT_FILENAME_FORMAT = (
    '{playon.Series}--{playon.Name}--{playon.ProviderID}--playon--{playon.ID}'
)

def main(argv=None):
  ''' Playon command line mode.
  '''
  return PlayOnCommand().run(argv)

class PlayOnCommand(BaseCommand):
  ''' Playon command line implementation.
  '''

  USAGE_KEYWORDS = {
      'DEFAULT_FILENAME_FORMAT': DEFAULT_FILENAME_FORMAT,
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

  @staticmethod
  def apply_defaults(options):
    options.user = environ.get('PLAYON_USER')
    options.password = environ.get('PLAYON_PASSWORD')
    options.filename_format = environ.get(
        'PLAYON_FILENAME_FORMAT', DEFAULT_FILENAME_FORMAT
    )

  @staticmethod
  @contextmanager
  def run_context(argv, options):
    ''' Prepare the `PlayOnAPI` around each command invocation.
    '''
    sqltags = PlayOnSQLTags()
    api = PlayOnAPI(options.user, options.password, sqltags)
    with stackattrs(options, api=api, sqltags=sqltags):
      with api:
        yield

  @staticmethod
  def cmd_account(argv, options):
    ''' Usage: {cmd}
          Report account state.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    api = options.api
    for k, v in sorted(api.account().items()):
      print(k, pformat(v))

  @staticmethod
  def cmd_dl(argv, options):
    ''' Usage: {cmd} [recording_ids...]
          Download the specified recording_ids.
          The default is "pending", meaning all recordings not
          previously downloaded.
    '''
    if not argv:
      argv = ['pending']
    api = options.api
    filename_format = options.filename_format

    @typechecked
    def _dl(dl_id: int):
      filename = api[dl_id].format_as(filename_format)
      filename = (
          filename.lower().replace(' - ',
                                   '--').replace('_', ':').replace(' ', '-') +
          '.'
      )
      try:
        api.download(dl_id, filename=filename)
      except ValueError as e:
        warning("download fails: %s", e)
        return None
      return filename

    available = None
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
            if te.is_downloaded():
              warning("already downloaded to %r", te.download_path)
            if not _dl(dl_id):
              xit = 1
    return xit

  @staticmethod
  def cmd_ls(argv, options):
    ''' Usage: {cmd} [-l]
          List available downloads.
          -l  Long format.
    '''
    long_format = False
    if argv and argv[0] == '-l':
      argv.pop(0)
      long_format = True
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    api = options.api
    for te in api.recordings():
      entry = te.subtags('playon')
      print(int(entry.ID), entry.HumanSize, entry.Series, entry.Name)
      if long_format:
        for tag in sorted(te):
          print(" ", tag)

  @staticmethod
  def cmd_queue(argv, options):
    ''' Usage: {cmd} [-l]
          List the recording queue.
          -l  Long format.
    '''
    long_format = False
    if argv and argv[0] == '-l':
      argv.pop(0)
      long_format = True
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    api = options.api
    for entry in api.queue():
      print(pformat(entry))

# pylint: disable=too-few-public-methods
class _RequestsNoAuth(requests.auth.AuthBase):
  ''' The API has a distinct login call, avoid basic auth from netrc etc.
  '''

  def __call__(self, r):
    return r

class PlayOnSQLTagSet(SQLTagSet):
  ''' An `SQLTagSet` with some special methods.
  '''

  def recording_id(self):
    ''' The recording id or `None`.
    '''
    return self.get('playon.ID')

  def is_downloaded(self):
    return self.download_path is not None

  def is_expired(self):
    return False

class PlayOnSQLTags(SQLTags):

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
    return map(lambda k: self[k], self.keys(prefix='recording.'))

  __iter__ = recordings

  @pfx_method
  def recording_ids_from_str(self, arg):
    ''' Convert a string to a list of recording ids.
    '''
    with Pfx(arg):
      tes = []
      if arg == 'all':
        tes.extend(iter(self))
      elif arg == 'downloaded':
        tes.extend(te for te in self if 'download_path' in te)
      elif arg == 'pending':
        tes.extend(te for te in self if 'download_path' not in te)
      elif arg.startswith('/'):
        # match regexp against playon.Series or playon.Name
        r_text = arg[1:]
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
class PlayOnAPI(MultiOpenMixin):
  ''' Access to the PlayOn API.
  '''

  API_HOSTNAME = 'api.playonrecorder.com'
  API_BASE = f'https://{API_HOSTNAME}/v3/'
  API_AUTH_GRACETIME = 30
  STATEDBPATH = '~/var/playon.sqlite'

  def __init__(self, login, password):
    self._auth_token = None
    self._login = login
    self._password = password
    self._login_state = None
    self._jwt = None
    self._cookies = {}
    self._storage = defaultdict(str)
    self.sqltags = SQLTags(expanduser(self.STATEDBPATH))
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
    data = self.suburl_data('login/at', _method='POST',params=dict(auth_token=at))
    self._jwt = data['token']

  @typechecked
  def __getitem__(self, download_id: int):
    ''' Return the `TagSet` associated with the recording `download_id`.
    '''
    return self.sqltags[f'recording.{download_id}']

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

    def request(*a, **kw):
      with Pfx("%s %r", method, url):
        return rqm(*a, **kw)

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

  @pfx_method
  def queue(self):
    ''' Return the recording queue entries, a list of `dict`s.
    '''
    data = self.suburl('queue')
    entries = data['entries']
    assert len(entries) == data['total_entries'], \
        "len(entries)=%d but result.data.total_entries=%r" % (
            len(entries), data['total_entries']
        )
    return entries

  @pfx_method
  def recordings(self):
    ''' Return the `TagSet` instances for the available recordings.
    '''
    data = self.suburl_data('library/all')
    entries = data['entries']
    tes = set()
    for entry in entries:
      entry_id = entry['ID']
      with Pfx(entry_id):
        for field, conv in sorted(dict(
            Episode=int,
            ReleaseYear=int,
            Season=int,
            ##Created=datetime.fromisoformat,
            ##Expires=datetime.fromisoformat,
            ##Updated=datetime.fromisoformat,
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
        tes.add(te)
    return tes

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
      dlrq = None
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
      dlrq = requests.get(
          dl_url, auth=_RequestsNoAuth(), cookies=jar, stream=True
      )
      dl_length = int(dlrq.headers['Content-Length'])
      with Pfx("open(%r,'wb')"):
        with open(filename, 'wb') as f:
          for chunk in progressbar(
              dlrq.iter_content(chunk_size=131072),
              label=filename,
              total=dl_length,
              units_scale=BINARY_BYTES_SCALE,
              itemlenfunc=len,
          ):
            with Pfx("write %d bytes", len(chunk)):
              f.write(chunk)
    fullpath = realpath(filename)
    te = self[download_id]
    if dlrq is not None:
      te.set('download_path', fullpath)
    fse = self._fstags[fullpath].update(te.subtags('playon'), prefix='playon')
    return te

if __name__ == '__main__':
  sys.exit(main(sys.argv))

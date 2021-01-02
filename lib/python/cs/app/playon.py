#!/usr/bin/env python3
#
# Playon facilities. - Cameron Simpson <cs@cskk.id.au>
#

''' Playon facilities.
'''

from collections import defaultdict
from contextlib import contextmanager
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
from cs.x import Y as X

def main(argv=None):
  ''' Playon command line mode.
  '''
  return PlayOnCommand().run(argv)

class PlayOnCommand(BaseCommand):
  ''' Playon command line implementation.
  '''

  @staticmethod
  def apply_defaults(options):
    options.user = environ.get('PLAYON_USER')
    options.password = environ.get('PLAYON_PASSWORD')

  @staticmethod
  @contextmanager
  def run_context(argv, options):
    ''' Prepare the `PlayOnAPI` around each command invocation.
    '''
    api = PlayOnAPI(options.user, options.password)
    with stackattrs(options, api=api):
      with api:
        yield

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

    @typechecked
    def _dl(dl_id: int):
      filename = api[dl_id].format_as(
          '{playon.Series}--{playon.Name}--{playon.ProviderID}--playon--{playon.ID}.'
      ).lower().replace(' - ', '--').replace('_', ':').replace(' ', '-')
      try:
        api.download(dl_id, filename=filename)
      except ValueError as e:
        warning("download fails: %s", e)
        return False
      return True

    available = None
    xit = 0
    for dlrq in argv:
      with Pfx(dlrq):
        if dlrq == 'pending':
          if available is None:
            available = api.recordings()
          tes = [te for te in available if 'download_path' not in te]
          if not tes:
            warning("no undownloaded recordings")
          else:
            for te in tes:
              dl_id = te['playon.ID']
              with Pfx(dl_id):
                filename = _dl(dl_id)
                if filename:
                  te.download_path = filename
                else:
                  xit = 1
        else:
          try:
            dl_id = int(dlrq)
          except ValueError:
            warning("not an int")
            xit = 2
          else:
            filename = _dl(dl_id)
            if filename:
              te = api[dl_id]
              te.download_path = filename
            else:
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

# pylint: disable=too-few-public-methods
class _RequestsNoAuth(requests.auth.AuthBase):
  ''' The API has a distinct login call, avoid basic auth from netrc etc.
  '''

  def __call__(self, r):
    return r

@decorator
def _api_call(func, suburl, method='GET'):
  ''' Decorator for API call methods requiring the `suburl`
      and optional `method` (default `'GET'`).

      Returns `func(self,requests.method,url,*a,**kw)`.
  '''

  def prep_call(self, *a, **kw):
    ''' Prepare the API call and pass to `func`.
    '''
    url = self.API_BASE + suburl
    with Pfx("%s %r", method, url):
      return func(
          self,
          partial(
              {
                  'GET': requests.get,
                  'POST': requests.post,
                  'HEAD': requests.head,
              }[method],
              url,
              auth=_RequestsNoAuth(),
          ),
          *a,
          **kw,
      )

  return prep_call

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
    self._sqltags = SQLTags(expanduser(self.STATEDBPATH))
    self._fstags = FSTags()

  def startup(self):
    ''' Start up: open and init the `SQLTags`, open the `FSTags`.
    '''
    sqltags = self._sqltags
    sqltags.open()
    sqltags.init()
    self._fstags.open()

  def shutdown(self):
    ''' Shutdown: close the `SQLTags`, close the `FSTags`.
    '''
    self._fstags.close()
    self._sqltags.close()

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
  @_api_call('login', 'POST')
  def _dologin(self, rqm):
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
    result = rqm(
        headers={
            'x-mmt-app': 'web'
        },
        params=dict(email=login, password=password),
    ).json()
    ok = result.get('success')
    if not ok:
      raise ValueError("login failed: %r" % (result,))
    return result['data']

  @property
  def jwt(self):
    ''' The JWT token.
    '''
    # ensure logged in with current tokens
    self.login_state  # pylint: disable=pointless-statement
    return self._jwt

  @_api_call('login/at', 'POST')
  def _renew_jwt(self, rqm):
    at = self.auth_token
    result = rqm(params=dict(auth_token=at)).json()
    ok = result.get('success')
    if not ok:
      raise ValueError("failed: %r" % (result,))
    self._jwt = result['data']['token']

  @typechecked
  def __getitem__(self, download_id: int):
    ''' Return the `TagSet` associated with `download_id`.
    '''
    return self._sqltags[f'recording.{download_id}']

  @_api_call('library/all')
  def recordings(self, rqm):
    ''' Return the `TagSet` instances for the available recordings.
    '''
    result = rqm(headers=dict(Authorization=self.jwt)).json()
    ok = result.get('success')
    if not ok:
      raise ValueError("failed: %r" % (result,))
    entries = result['data']['entries']
    tes = set()
    for entry in entries:
      te = self[entry['ID']]
      te.update(entry, prefix='playon')
      tes.add(te)
    return tes

  # pylint: disable=too-many-locals
  @pfx_method
  @typechecked
  def download(self, download_id: int, filename=None):
    ''' Download th file with `download_id` to `filename`.
        The default `filename` is the basename of the filename
        from the download.
        If the filename is supplied with a trailing dot (`'.'`)
        then the file extension will be taken from the filename
        of the download.
    '''
    rq = requests.get(
        f'{self.API_BASE}library/{download_id}/download',
        auth=_RequestsNoAuth(),
        headers=dict(Authorization=self.jwt),
    )
    result = rq.json()
    ok = result.get('success')
    if not ok:
      raise ValueError("failed: %r" % (result,))
    dl_url = result['data']['url']
    if filename is None:
      filename = unpercent(basename(dl_url))
    elif filename.endswith('.'):
      _, dl_ext = splitext(basename(dl_url))
      filename = filename[:-1] + dl_ext
    if pathexists(filename):
      warning("SKIPPING download of %r: already exists, just tagging", filename)
      dlrq = None
    else:
      dl_cookies = result['data']['data']
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
      with open(filename, 'wb') as f:
        for chunk in progressbar(
            dlrq.iter_content(chunk_size=131072),
            label=filename,
            total=dl_length,
            units_scale=BINARY_BYTES_SCALE,
            itemlenfunc=len,
        ):
          f.write(chunk)
    fullpath = realpath(filename)
    te = self[download_id]
    if dlrq is not None:
      te.set('downloaded_path', fullpath)
    pl_tags = te.subtags('playon')
    fse = self._fstags[fullpath]
    fse.update(pl_tags, prefix='playon')
    return te

if __name__ == '__main__':
  sys.exit(main(sys.argv))

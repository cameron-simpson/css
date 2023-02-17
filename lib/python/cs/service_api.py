#!/usr/bin/env python3

''' ServiceAPI, a base class for APIs which talk to a service,
    typically a web service via HTTP.

    An instance of a `ServiceAPI` embodies some basic features
    that feel common to web based services:
    - a notion of a login
    - local state, an `SQLTags` for data about entities of the service
    - downloads, if that is a thing, with `FSTags` for file annotations
'''

from contextlib import contextmanager
from json import JSONDecodeError
from threading import RLock
import time
from typing import Mapping, Set

from icontract import require
import requests

from cs.context import stackattrs
from cs.deco import promote
from cs.fstags import FSTags
from cs.logutils import warning
from cs.pfx import pfx_call
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags, SQLTagSet
from cs.upd import uses_upd

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.cmdutils>=20210404',
        'cs.context',
        'cs.deco',
        'cs.fileutils',
        'cs.fs>=HasFSPath',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.resources',
        'cs.tagset>=TagSet_is_stale',
        'cs.threads',
        'cs.upd',
        'icontract',
        'typeguard',
    ],
}

class ServiceAPI(MultiOpenMixin):
  ''' `SewrviceAPI` base class for other APIs talking to services.
  '''

  API_AUTH_GRACETIME = None

  @promote
  def __init__(self, *, sqltags: SQLTags):
    self.sqltags = sqltags
    self.fstags = None
    self._lock = RLock()
    self.login_state_mapping = None

  @contextmanager
  def startup_shutdown(self):
    ''' Start up: open and init the `SQLTags`, open the `FSTags`.
    '''
    sqltags = self.sqltags
    fstags = FSTags()
    with sqltags:
      sqltags.init()
      with fstags:
        with stackattrs(self, fstags=fstags):
          yield

  def login(self) -> Mapping:
    ''' Do a login: authenticate to the service, return a mapping of related information.

        Not all services require this and we expect such subclasses
        to avoid use of login-based methods.
    '''
    raise NotImplementedError

  @property
  def login_expiry(self):
    ''' Expiry UNIX time for the login state.
        This implementation returns `None`.
    '''
    return None

  @property
  def login_state(self, do_refresh=False) -> SQLTagSet:
    ''' The login state, a mapping. Performs a login if necessary.
    '''
    with self._lock:
      state = self.sqltags['login.state']
      if do_refresh or not state or (
          self.API_AUTH_GRACETIME is not None
          and time.time() + self.API_AUTH_GRACETIME >= state.expiry):
        for k, v in self.login().items():
          if k not in ('id', 'name'):
            state[k] = v
    return state

  def available(self) -> Set[SQLTagSet]:
    ''' Return a set of the `SQLTagSet` instances representing available
        items at the service, for example purchased books
        available to your login.
    '''
    raise NotImplementedError

class HTTPServiceAPI(ServiceAPI):
  ''' `HTTPServiceAPI` base class for other APIs talking to HTTP services.

      Subclasses must define:
      * `API_BASE`: the base URL of API calls.
        For example, the `PlayOnAPI` defines this as `f'https://{API_HOSTNAME}/v3/'`.
  '''

  def __init__(self, api_hostname=None, *, default_headers=None, **kw):
    if api_hostname is None:
      api_hostname = self.API_HOSTNAME
    else:
      self.API_HOSTNAME = api_hostname
      self.API_BASE = f'https://{api_hostname}/'
    if default_headers is None:
      default_headers = {}
    super().__init__(**kw)
    session = self.session = requests.Session()
    # mapping of method names to requests convenience calls
    self.REQUESTS_METHOD_CALLS = {
        'GET': session.get,
        'POST': session.post,
        'HEAD': session.head,
    }
    self.cookies = session.cookies
    self.default_headers = default_headers

  @uses_upd
  @require(lambda suburl: not suburl.startswith('/'))
  def suburl(
      self,
      suburl,
      *,
      _base_url=None,
      _method='GET',
      _no_raise_for_status=False,
      cookies=None,
      headers=None,
      upd,
      **rqkw,
  ):
    ''' Request `suburl` from the service, by default using a `GET`.
        The `suburl` must be a URL subpath not commencing with `'/'`.

        Keyword parameters:
        * `_base_url`: the base request domain, default from `self.API_BASE`
        * `_method`: the request method, default `'GET'`
        * `_no_raise_for_status`: do not raise an HTTP error if the
          response status is not 200, default `False` (raise if not 200)
        * `cookies`: optional cookie jar, default from `self.cookies`
        Other keyword parameters are passed to the requests method.
    '''
    rqm = self.REQUESTS_METHOD_CALLS[_method]
    if _base_url is None:
      _base_url = self.API_BASE
    if cookies is None:
      cookies = self.cookies
    url = _base_url + suburl
    rq_headers = {}
    rq_headers.update(self.default_headers)
    if headers is not None:
      rq_headers.update(headers)
    with upd.run_task(f'{_method} {url}'):
      rsp = pfx_call(rqm, url, cookies=cookies, headers=rq_headers, **rqkw)
    if not _no_raise_for_status:
      rsp.raise_for_status()
    return rsp

  def json(self, suburl, _response_encoding=None, **kw):
    ''' Request `suburl` from the service, by default using a `GET`.
        Return the result decoded as JSON.

        Parameters are as for `HTTPServiceAPI.suburl`.
    '''
    rsp = self.suburl(suburl, **kw)
    if _response_encoding is not None:
      rsp.encoding = _response_encoding
    try:
      return rsp.json()
    except JSONDecodeError as e:
      warning("response is not JSON: %s\n%r", e, rsp)
      raise

# pylint: disable=too-few-public-methods
class RequestsNoAuth(requests.auth.AuthBase):
  ''' This is a special purpose subclass of `requests.auth.AuthBase`
      to apply no authorisation at all.
      This is for services with their own special purpose authorisation
      and avoids things like automatic netrc based auth.
  '''

  def __call__(self, r):
    return r

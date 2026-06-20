#!/usr/bin/env python3

''' ServiceAPI, a base class for APIs which talk to a service,
    typically a web service via HTTP.

    An instance of a `ServiceAPI` embodies some basic features
    that feel common to web based services:
    - a notion of a login
    - local state, an `SQLTags` for data about entities of the service
    - downloads, if that is a thing, with `FSTags` for file annotations
'''

from collections import namedtuple
from contextlib import contextmanager
from functools import cached_property, partial
from json import JSONDecodeError
from netrc import netrc
from threading import RLock, Semaphore
import time
from typing import Mapping, Set, Union

from icontract import require
import requests
from requests import Response
try:
  from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError
except ImportError:
  RequestsJSONDecodeError = JSONDecodeError

from cs.context import contextif
from cs.deco import uses_verbose
from cs.fstags import FSTags, uses_fstags
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call
from cs.resources import MultiOpenMixin, RunState, uses_runstate
from cs.sqltags import SQLTagSet, UsesSQLTags
from cs.tagset import HasTags, UsesTagSets
from cs.threads import pmap
from cs.upd import run_task

__version__ = '20260531-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.fstags',
        'cs.logutils',
        'cs.pfx',
        'cs.resources',
        'cs.tagset',
        'cs.sqltags',
        'cs.upd',
        'icontract',
        'requests',
    ],
}

class LoginPasswordCredentials(namedtuple('LoginPasswordCredentials',
                                          'login password')):
  ''' A credentials class for login/password authentication.

      Its default `from_str(credname)` factory accesses the netrc(5) file.
  '''

  @classmethod
  def from_netrc(cls, host, netrc_path=None):
    ''' Create an instance from a netrc(5) `host`.
        Raise `KeyError` if `host` is unknown.
    '''
    N = netrc(netrc_path)
    login_account_password = N.authenticators(host)
    if login_account_password is None:
      raise KeyError(f'no entry for {host=}')
    return cls(
        login=login_account_password[0], password=login_account_password[2]
    )

  @classmethod
  def from_str(self, credname):
    return self.from_netrc(credname)

class UsesCredentials:
  ''' A mixin for accessing credentials.
  '''
  CREDENTIALS_CLASS = LoginPasswordCredentials

  def credentials(self, credname):
    ''' Return the credentials for `credname`.
    '''
    return self.CREDENTIALS_CLASS.from_str(credname)

  @classmethod
  def default_credentials(cls):
    ''' Return the default credentials for `cls.API_HOSTNAME`.
    '''
    return cls.CREDENTIALS_CLASS.from_str(cls.API_HOSTNAME)

class ServiceAPI(MultiOpenMixin, UsesCredentials, UsesSQLTags):
  ''' `SewrviceAPI` base class for other APIs talking to services.
  '''

  API_AUTH_GRACETIME = None
  API_RETRY_COUNT = 3  # number of request attempts
  API_RETRY_DELAY = 5  # interval between request retries
  API_CONCURRENCY_LIMIT = None

  @uses_fstags
  def __init__(
      self,
      *,
      fstags: FSTags,
      concurrency=None,
      tagsets: UsesTagSets = None,
  ):
    ''' Initialise a `ServiceAPI` instance.

        The `concurrency` parameter may take the following values:
        * `None`: API calls are serialised, setting `.concurrency_sem=None`
          and `.pmap=partial(pmap,concurrent=1)`
        * an `int`: API calls are constrained by `.concurrency_sem`,
          set to a `Semaphore` with this initial value, and `.pmap=pmap`
        * a `Semaphore`: API calls are constrained by `.concurrency_sem`,
          set to this `Semaphore`, and `.pmap=pmap`
    '''
    if concurrency is None:
      concurrency = self.API_CONCURRENCY_LIMIT
    if concurrency is None:
      concurrency_sem = Semaphore(1)
      _pmap = partial(pmap, concurrent=1)
    elif isinstance(concurrency, int):
      concurrency_sem = Semaphore(concurrency)
      if concurrency == 1:
        _pmap = pmap
      else:
        _pmap = pmap
    else:
      concurrency_sem = concurrency
      _pmap = pmap
    self.concurrency = concurrency
    self.concurrency_sem = concurrency_sem
    self.pmap = _pmap
    super().__init__(tagsets=tagsets)
    self.fstags = fstags
    self._lock = RLock()
    self.login_state_mapping = None

  @contextmanager
  def startup_shutdown(self):
    ''' Open/close the `FSTags` and `UsesTagSets`.
    '''
    with self.tagsets:
      with self.fstags:
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

  def get_login_state(self, do_refresh=False) -> HasTags:
    ''' The login state, a `HasTags`, stored as `login.state.`*login_userid*.
        This performs a login if necessary or if `do_refresh` is true
        (default `False`).
    '''
    with self._lock:
      state = self['login.state', self.login_userid.replace('.', '_')]
      if do_refresh or (self.API_AUTH_GRACETIME is not None and
                        time.time() + self.API_AUTH_GRACETIME >= state.expiry):
        for k, v in self.login().items():
          if k not in ('id', 'name'):
            state[k] = v
    return state

  @cached_property
  def login_state(self) -> HasTags:
    ''' The login state, a mapping. Performs a login if necessary.
    '''
    return self.get_login_state()

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

  def __init__(
      self,
      api_hostname=None,
      *,
      default_headers=None,
      mode='response',
      check_json=None,
      **service_api_kw,
  ):
    if api_hostname is None:
      api_hostname = type(self).API_HOSTNAME
    else:
      self.API_HOSTNAME = api_hostname
      self.API_BASE = f'https://{api_hostname}/'
    if default_headers is None:
      default_headers = {}
    super().__init__(**service_api_kw)
    session = self.session = requests.Session()
    # mapping of method names to requests convenience calls
    self.REQUESTS_METHOD_CALLS = {
        'GET': session.get,
        'POST': session.post,
        'HEAD': session.head,
    }
    self.cookies = session.cookies
    self.default_headers = default_headers
    self.mode = mode
    self.check_json = check_json

  def response_as_json(self, rsp: Response) -> dict:
    ''' Return `rsp.json()`.
    '''
    try:
      js = rsp.json()
    except (JSONDecodeError, RequestsJSONDecodeError) as e:
      warning("response is not JSON: %s\n%r", e, rsp)
      raise
    if self.check_json:
      for field, value in self.check_json.items():
        js_value = js.get(field)
        if js_value != value:
          warning(f'expected {field}={value!r}, got {js_value!r} from {js!r}')
    return js

  def response_as_json_data(self, rsp: Response) -> dict:
    ''' Return the `"data"` element from a JSON response.
    '''
    js = self.response_as_json(rsp)
    return js["data"]

  @uses_runstate
  @uses_verbose
  @require(lambda suburl: not suburl.startswith('/'))
  def suburl(
      self,
      suburl,
      *,
      base_url=None,
      method='GET',
      mode=None,
      check=True,
      cookies=None,
      headers=None,
      runstate: RunState,
      verbose: bool,
      **rqkw,
  ) -> Union[Response, dict]:
    ''' Request `suburl` from the service, by default using a `GET`.
        The `suburl` must be a URL subpath not commencing with `'/'`.

        Return:
        - `mode(Response)` if `mode` is callable
        - the `Response` if `mode=="response"`
        - the `Response.json()` if `mode=="json"`
        - the `Response.json()["data"]` if `mode=="data"`

        Keyword parameters:
        * `base_url`: the base request domain, default from `self.API_BASE`
        * `method`: optional request method, default `'GET'`
        * `check`: if true, raise an HTTP error if the response
          status is not 200; default `True`
        * `cookies`: optional cookie jar, default from `self.cookies`
        * `mode`: optional result mode, default from `self.mode`
        Other keyword parameters are passed to the requests method.
    '''
    rqm = self.REQUESTS_METHOD_CALLS[method]
    if base_url is None:
      base_url = self.API_BASE
    if cookies is None:
      cookies = self.cookies
    if mode is None:
      mode = self.mode
    url = base_url + suburl
    with Pfx('%s %s', method, url):
      rq_headers = {}
      rq_headers.update(self.default_headers)
      if headers is not None:
        rq_headers.update(headers)
      with run_task(
          f'{method} {url}',
          report_print=verbose,
          tick_deferred=True,
      ) as proxy:
        for retry in range(self.API_RETRY_COUNT, 0, -1):
          runstate.raiseif()
          with contextif(self.concurrency_sem):
            try:
              with proxy.ticker():
                rsp = pfx_call(
                    rqm,
                    url,
                    cookies=cookies,
                    headers=rq_headers,
                    **rqkw,
                )
                break
            except requests.ConnectionError as e:
              if retry <= 1:
                # last retry
                raise
              warning("%s, retrying in %ds", e, self.API_RETRY_DELAY)
              runstate.sleep(self.API_RETRY_DELAY)
      if check:
        if rsp.status_code != 200:
          print(dir(rsp))
          warning(f'{rsp.status_code} {rsp.reason}')
        rsp.raise_for_status()
      if callable(mode):
        return mode(rsp)
      if mode == 'response':
        return rsp
      if mode == 'json':
        return self.response_as_json(rsp)
      if mode == 'data':
        return self.response_as_json_data(rsp)
      raise ValueError(
          f'unsupported {mode=}, expected "data", "json", "response" or a callable'
      )

  def get(self, suburl, **kw) -> Response:
    ''' Call `slef.suburl` with `method="GET"`.
    '''
    return self, suburl(suburl, method='GET', **kw)

  def post(self, suburl, **kw) -> Response:
    ''' Call `slef.suburl` with `method="POST"`.
    '''
    return self.suburl(suburl, method='POST', **kw)

  def __truediv__(self, suburl):
    return self.suburl(suburl)

# pylint: disable=too-few-public-methods
class RequestsNoAuth(requests.auth.AuthBase):
  ''' This is a special purpose subclass of `requests.auth.AuthBase`
      to apply no authorisation at all.
      This is for services with their own special purpose authorisation
      and avoids things like automatic netrc based auth.
  '''

  def __call__(self, r):
    return r

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
from threading import RLock
import time
from typing import Mapping

import requests

from cs.context import stackattrs
from cs.deco import promote
from cs.fstags import FSTags
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags

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
  def login_state(self):
    ''' The login state, a mapping. Performs a login if necessary.
    '''
    with self._lock:
      state = self.login_state_mapping
      if not state or (
          self.API_AUTH_GRACETIME is not None
          and time.time() + self.API_AUTH_GRACETIME >= self.login_expiry):
        self.login_state_mapping = None
        # not logged in or login about to expire
        state = self.login_state_mapping = self.login()
    return state

class HTTPServiceAPI(ServiceAPI):
  ''' `HTTPSewrviceAPI` base class for other APIs talking to HTTP services.
  '''

  def __init__(self, **kw):
    super().__init__(**kw)
    self._cookies = requests.cookies.RequestsCookieJar()

# pylint: disable=too-few-public-methods
class RequestsNoAuth(requests.auth.AuthBase):
  ''' This is a special purpose subclass of `requests.auth.AuthBase`
      to apply no authorisation at all.
      This is for services with their own special purpose authorisation
      and avoids things like automatic netrc based auth.
  '''

  def __call__(self, r):
    return r

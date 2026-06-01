#!/usr/bin/env python3

''' Assorted cookie related facilities.
'''

from collections import namedtuple
from http.cookies import Morsel, SimpleCookie
from os.path import basename
import shutil
import sqlite3
from tempfile import NamedTemporaryFile
import time
from typing import Any, List

# column names from the firefox sqlite3 database
FIREFOX_COOKIE_SQL_COLUMNS = (
    'id',
    'originAttributes',
    'name',
    'value',
    'host',
    'path',
    'expiry',
    'lastAccessed',
    'creationTime',
    'isSecure',
    'isHttpOnly',
    'inBrowserElement',
    'sameSite',
    ##'rawSameSite',
    'schemeMap',
    'isPartitionedAttributeSet',
)

def morsel(name: str, value: Any, **morsel_kw) -> Morsel:
  ''' A factory to make a new `http.cookies.Morsel` from scratch.
  '''
  # this idiotic ritual is due to how the http.cookies module works
  cookies = SimpleCookie()
  cookies[name] = value
  morsel = cookies[name]
  for k, v in morsel_kw.items():
    morsel[k] = v
  expires = morsel.get('expires')
  if expires:
    morsel['max-age'] = expires - time.time()
  return morsel

class FirefoxCookie(namedtuple('FirefoxCookie', FIREFOX_COOKIE_SQL_COLUMNS)):
  ''' A `namedtuple` with attributes for the columns of the Firefox
      `cookies.sqlite` database's `moz_cookies` table.
  '''

  def as_Morsel(self) -> Morsel:
    ''' Return `self` as an `http.cookies.Morsel`.
    '''
    return morsel(
        self.name,
        self.value,
        domain=self.host,
        expires=self.expiry,
        httponly=self.isHttpOnly,
        path=self.path,
        samesite=self.sameSite,
        secure=self.isSecure,
    )

  def add_to_jar(self, jar):
    ''' Add this cookie to a `CookieJar`.
    '''
    jar.set(
        self.name,
        self.value,
        domain=self.host,
        expires=self.expiry,
        ##httponly=self.isHttpOnly,
        path=self.path,
        ##samesite=self.sameSite,
        secure=self.isSecure,
    )

def read_firefox_cookies(cookie_dbpath) -> List[FirefoxCookie]:
  ''' Read the current cookie values from `cookie_dbpath`,
      return a list of `FirefoxCookie` instances.

      Because firefox keeps the db locked we copy it and read from the copy.
  '''
  with NamedTemporaryFile(suffix=f'--{basename(cookie_dbpath)}') as tf:
    shutil.copyfile(cookie_dbpath, tf.name)
    with sqlite3.connect(tf.name) as con:
      con.row_factory = sqlite3.Row
      rows = [
          FirefoxCookie(**row) for row in con.execute(
              f'select {",".join(FIREFOX_COOKIE_SQL_COLUMNS)} from moz_cookies'
          )
      ]
  return rows

if __name__ == '__main__':
  import sys
  import requests
  with requests.Session() as session:
    cookies = session.cookies
    ffcookies = read_firefox_cookies(sys.argv[1])
    for ffc in ffcookies:
      print("FF:", ffc)
      m = ffc.as_Morsel()
      print("Morsel:", dict(m))

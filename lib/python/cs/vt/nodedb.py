#!/usr/bin/python

''' Hooks for attaching storage to a NodeDB.
'''

from .dir import Dirent, decode_Dirent, resolve

def register_with(nodedb, scheme=__name__):
  ''' Register the transcriptions with the supplied NodeDB.
  '''
  nodedb.register_attr_type(Dirent, scheme + '.Dirent',
                            dirent_totext, dirent_fromtext,
                            dirent_tobytes, dirent_frombytes)

def dirent_totext(D):
  return D.textencode()

def dirent_fromtext(value):
  D, name = resolve(value)
  if name is not None:
    D = D[name]
  return D

def dirent_tobytes(D):
  return D.encode()

def dirent_frombytes(value):
  return decode_Dirent(value, justone=True)

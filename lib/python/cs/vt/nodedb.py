#!/usr/bin/python

''' Hooks for attaching cs.vt storage to a NodeDB.
'''

import cs.vt.dir

def register_with(nodedb, scheme='cs.vt'):
  ''' Register the cs.vt transcriptions with the supplied NodeDB.
  '''
  nodedb.register_attr_type(cs.vt.dir.Dirent, scheme + '.Dirent',
                            dirent_totext, dirent_fromtext,
                            dirent_tobytes, dirent_frombytes)

def dirent_totext(D):
  return D.textencode()

def dirent_fromtext(value):
  D, name = cs.vt.dir.resolve(value)
  if name is not None:
    D = D[name]
  return D

def dirent_tobytes(D):
  return D.encode()

def dirent_frombytes(value):
  return cs.vt.dir.decode_Dirent(value, justone=True)

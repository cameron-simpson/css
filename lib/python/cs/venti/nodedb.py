#!/usr/bin/python

''' Hooks for attaching cs.venti storage to a NodeDB.
'''

from cs.venti import texthexify, untexthexify
import cs.venti.dir

def register_with(nodedb, scheme='cs.venti'):
  ''' Register the cs.venti transcriptions with the supplied NodeDB.
  '''
  nodedb.register_attr_type(cs.venti.dir.Dirent, scheme+'.Dirent',
                            dirent_totext, dirent_fromtext,
                            dirent_tobytes, dirent_frombytes)

def dirent_totext(D):
  return D.textEncode()

def dirent_fromtext(value):
  D, name = cs.venti.dir.resolve(value)
  if name is not None:
    D = D[name]
  return D

def dirent_tobytes(D):
  return D.encode()

def dirent_frombytes(value):
  return cs.venti.dir.decodeDirent(value, justone=True)

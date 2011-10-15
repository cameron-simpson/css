#!/usr/bin/python

import email.message
import mailbox
import os
import os.path
import socket
from tempfile import NamedTemporaryFile
from StringIO import StringIO
from thread import allocate_lock
import unittest

def ismaildir(path):
  ''' Test if 'path' points at a Maildir directory.
  '''
  for subdir in ('new','cur','tmp'):
    if not os.path.isdir(os.path.join(path,subdir)):
      return False
  return True

class Maildir(mailbox.Maildir):
  ''' A faster Maildir interface.
      Trust os.listdir, don't fsync, etc.
  '''

  def __init__(self, dir):
    if not ismaildir(dir):
      raise ValueError, "not a Maildir: %s" % (dir,)
    self.dir = dir
    self._hostpart = socket.gethostname() \
                          .replace('/', '_') \
                          .replace(':', '_')
    self._pid = os.getpid()
    self._lock = allocate_lock
    self.scan()

  def scan(self):
    ''' Rescan the maildir to rebuild the index.
    '''
    msgmap = {}
    for subdir in 'new', 'cur':
      subdirpath = os.path.join(self.dir, subdir)
      for msgbase in os.listdir(subdirpath):
        if msgbase.startswith('.'):
          continue
        try:
          key, info = msgbase.split(':', 1)
        except ValueError:
          key, info = msgbase, ''
        msgmap[key] = (subdir, msgbase)
    self.msgmap = msgmap

  def list_folders(self):
    for fbase in os.listdir(self.dir):
      if fbase.startswith('.'):
        if fbase != '.' and fbase != '..' \
           and ismaildir(os.path.join(self.dir, fbase)):
          return fbase[1:]

  def get_folder(folder):
    folderdir = os.path.join(self.dir, folder)
    if folder != '.' and ismaildir(folderdir):
      return Maildir(folderdir)
    raise mailbox.NoSuchMailboxError, folderdir

  def add_folder(folder):
    folderpath = os.path.join(self.dir, folder)
    os.mkdir(folderpath)
    for subdir in 'tmp', 'new', 'cur':
      os.mkdir(os.path.join(folderdir, subdir))
    return Maildir(folderdir)

  def remove_folder(self, folder):
    F = self.get_folder(folder)
    for key in F.iterkeys():
      raise mailbox.NotEmptyError, "not an empty Maildir"
    folderpath = os.path.join(self.dir, folder)
    for subdir in 'tmp', 'new', 'cur':
      os.rmdir(os.path.join(folderdir, subdir))
    os.rmdir(folderpath)

  def newkey(self):
    ''' Allocate a new key.
    '''
    now=time.time()
    secs=int(now)
    subsecs=now-secs
    while True:
      key = '%d.#%dM%dP%d' % (secs, seq(), subsecs * 1e6, self._pid)
      if key not in self.msgmap:
        break
    assert self.validkey(key), "invalid new key: %s" % (key,)
    return key

  def validkey(self, key):
    return len(key) > 0 \
       and not key.startswith('.') \
       and ':' not in key \
       and '/' not in key

  def save_filepath(self, filepath, key=None, nolink=False):
    ''' Save a file into the Maildir.
        By default a hardlink is attempted unless `nolink` is supplied true.
        Return the key for the saved message.
    '''
    if key is None:
      key = self.newkey()
    if not self.validkey(key):
      raise ValueError, "invalid key: %s" % (key,)
    if key in self.msgmap:
      raise ValueError, "key already in Maildir: %s" % (key,)
    tmppath = os.path.join(self.dir, 'tmp', key)
    if os.path.exists(tmppath):
      raise ValueError, "temp file already in Maildir: %s" % (tmppath,)
    if not nolink:
      try:
        os.link(filepath, tmppath)
      except OSError:
        shutils.copyfile(filepath, tmppath)
    else:
      shutils.copyfile(filepath, tmppath)
    newpath = os.path.join(self.dir, 'new', key)
    try:
      os.rename(tmppath, newpath)
    except:
      os.unlink(tmppath)
      raise
    self.msgmap[key] = ('new', key)
    return key

  def save_file(self, fp, key=None):
    ''' Save the contents of the file-like object `fp` into the Maildir.
        Return the key for the saved message.
    '''
    with NamedTemporaryFile('w', dir=os.path.join(self.dir, 'tmp')) as T:
      T.write(fp.read())
    return self.save_filepath(T.name, key=key)

  def open(self, key, mode='r'):
    ''' Open the file storing the message specified by `key`.
    '''
    subdir, msgbase = self.msgmap[key]
    return open(os.path.join(self.dir, subdir, msgbase), mode=mode)

  def get_file(key):
    return self.open(key, mode='rb')

  def add(self, message, key=None):
    if type(message) in (str, unicode):
      return self.save_filepath(message, key=key)
    if isinstance(message, email.message.Message):
      with NamedTemporaryFile('w', dir=os.path.join(self.dir, 'tmp')) as T:
        T.write(message.as_string())
      key = self.save_filepath(T.name, key=key)
      os.remove(T.name)
      return key
    raise ValueError, "unsupported message type: %s" % (type(message),)

  def remove(self, key):
    subdir, msgbase = self.msgmap[key]
    os.remove(os.path.join(self.dir, subdir, msgbase))
    del self.msgmap[key]
  discard = remove
  __delitem__ = remove

  def __contains__(self, key):
    return key in self.msgmap
  has_key = __contains__

  def __len__(self):
    return len(self.items())

  def clear(self):
    for key in self.keys():
      del self[key]

  def pop(self, key, *args):
    try:
      message = self[key]
    except KeyError:
      if len(args) > 0:
        return args[0]
      raise
    del self[key]
    return message

  def popitem(self):
    for key in self.iterkeys():
      return self.pop(key)
    raise KeyError, "empty Maildir"

  def update(self, arg):
    try:
      km = arg.iteritems()
    except AttributeError:
      km = iter(arg)
    for key, message in km:
      self[key] = message

  def __getitem__(self, key):
    with self.open(key) as mfp:
      return mailbox.Message(mfp)
  get_message = __getitem__

  def get_string(self, key):
    return self[key].as_string()

  def get(self, key, default=None):
    try:
      return self[key]
    except KeyError:
      return default

  def __setitem__(self, key, message):
    remove(key)
    self.add(message, key=key)

  def iterkeys(self):
    return self.msgmap.iterkeys()

  def keys(self):
    return self.msgmap.keys()

  def itervalues(self):
    for key in self.iterkeys():
      return self[key]
  __iter__ = itervalues

  def values(self):
    return list(self.iterkeys())

  def iteritems(self):
    for key in self.iterkeys():
      return key, self[key]

  def items(self):
    return list(self.iteritems())

  def flush(self):
    pass

  def lock(self):
    self._lock.acquire()

  def unlock(self):
    self._lock.release()

  def close(self):
    pass

class TestMaildir(unittest.TestCase):

  def test00basic(self):
    M = Maildir(os.path.join(os.environ['HOME'], 'ZZM'))
    print M.keys()

if __name__ == '__main__':
  unittest.main()

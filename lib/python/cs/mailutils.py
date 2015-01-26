#!/usr/bin/python
#
# Convenience functions and classes to work with email.
#       - Cameron Simpson <cs@zip.com.au>
#

DISTINFO = {
    'description': "functions and classes to work with email",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.fileutils', 'cs.logutils', 'cs.threads', 'cs.seq', 'cs.py3'],
}

import email.message
import email.parser
from email.utils import getaddresses
from itertools import chain
import mailbox
import os
import os.path
import sys
import socket
import shutil
from tempfile import NamedTemporaryFile
from threading import Lock
import time
from cs.fileutils import Pathname, shortpath as _shortpath
from cs.logutils import Pfx, info, warning, debug, D
from cs.threads import locked_property
from cs.seq import seq
from cs.py3 import StringIO, StringTypes

SHORTPATH_PREFIXES = ( ('$MAILDIR/', '+'), ('$HOME/', '~/') )

def shortpath(path, environ=None):
  return _shortpath(path, environ=environ, prefixes=SHORTPATH_PREFIXES)

def Message(msgfile, headersonly=False):
  ''' Factory function to accept a file or filename and return an email.message.Message.
  '''
  if isinstance(msgfile, StringTypes):
    # msgfile presumed to be filename
    pathname = msgfile
    with Pfx(pathname):
      with open(pathname) as mfp:
        M = Message(mfp, headersonly=headersonly)
        M.pathname = pathname
        return M
  # msgfile presumed to be file-like object
  return email.parser.Parser().parse(msgfile, headersonly=headersonly)

def message_addresses(M, header_names):
  ''' Yield (realname, address) pairs from all the named headers.
  '''
  for header_name in header_names:
    hdrs = M.get_all(header_name, ())
    for hdr in hdrs:
      for realname, address in getaddresses( (hdr,) ):
        if len(address) == 0:
          debug("message_addresses(M, %r): header_name %r: hdr=%r: getaddresses() => (%r, %r): DISCARDED",
                  header_names, header_name, hdr, realname, address)
        else:
          yield realname, address

def modify_header(M, hdr, new_value, always=False):
  ''' Modify a Message `M` to change the value of the named header `hdr` to the new value `new_value`.
      If `new_value` differs from the existing value or if `always`
      is true, save the old value as X-Old-`hdr`.
      Return a Boolean indicating whether the headers were modified.
  '''
  modified = False
  old_value = M.get(hdr, '')
  if always or old_value != new_value:
    modified = True
    old_hdr = 'X-Old-' + hdr
    for old_value in M.get_all(hdr, ()):
      M.add_header("X-Old-" + hdr, old_value)
    del M[hdr]
    M[hdr] = new_value
  return modified

def ismhdir(path):
  ''' Test if `path` points at an MH directory.
  '''
  return os.path.isfile(os.path.join(path, '.mh_sequences'))

def ismaildir(path):
  ''' Test if `path` points at a Maildir directory.
  '''
  for subdir in ('new', 'cur', 'tmp'):
    if not os.path.isdir(os.path.join(path,subdir)):
      return False
  return True

def ismbox(path):
  ''' Open path and check that its first line begins with "From ".
  '''
  fp = None
  try:
    fp = open(path)
    from_ = fp.read(5)
  except IOError:
    if fp is not None:
      fp.close()
    return False
  else:
    fp.close()
    return from_ == 'From '

def make_maildir(path):
  ''' Create a new maildir at `path`.
      The path must not already exist.
  '''
  info("make_maildir %s", path)
  made = []
  os.mkdir(path)
  made.append(path)
  for subdir in 'tmp', 'new', 'cur':
    subdirpath = os.path.join(path, subdir)
    try:
      os.mkdir(subdirpath)
    except OSError:
      for dir in reversed(made):
        os.rmdir(dir)
      raise
    made.append(subdirpath)

class Maildir(mailbox.Maildir):
  ''' A faster Maildir interface.
      Trust os.listdir, don't fsync, etc.
  '''

  def __init__(self, dir, create=False):
    if not ismaildir(dir):
      if not create:
        raise ValueError("not a Maildir: %s" % (dir,))
      make_maildir(dir)
    self.dir = Pathname(dir)
    self._msgmap = None
    self._pid = None
    self._hostpart = None
    self._lock = Lock()
    self.flush()

  def __str__(self):
    return "<%s %s>" % (self.__class__.__name__, self.dir)

  @property
  def shortname(self):
    return shortpath(self.dir)

  def flush(self):
    ''' Forget state.
    '''
    self._msgmap = None

  @locked_property
  def pid(self):
    return os.getpid()

  @locked_property
  def hostpart(self):
    return socket.gethostname().replace('/', '_').replace(':', '_')

  @locked_property
  def msgmap(self):
    ''' Scan the maildir, return key->message-info mapping.
    '''
    debug("compute msgmap for %s", self.dir)
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
    return msgmap

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
    raise mailbox.NoSuchMailboxError(folderdir)

  def add_folder(folder):
    folderdir = os.path.join(self.dir, folder)
    make_maildir(folderdir)
    return get_folder(folder)

  def remove_folder(self, folder):
    F = self.get_folder(folder)
    for key in F.keys():
      raise mailbox.NotEmptyError("not an empty Maildir")
    folderpath = os.path.join(self.dir, folder)
    for subdir in 'tmp', 'new', 'cur':
      os.rmdir(os.path.join(folderdir, subdir))
    os.rmdir(folderpath)

  def newkey(self):
    ''' Allocate a new key.
    '''
    now = time.time()
    secs = int(now)
    subsecs = now-secs
    key = '%d.#%dM%dP%d' % (secs, seq(), subsecs * 1e6, self.pid)
    assert self.validkey(key), "invalid new key: %s" % (key,)
    return key

  def validkey(self, key):
    return len(key) > 0 \
       and not key.startswith('.') \
       and ':' not in key \
       and '/' not in key

  def save_filepath(self, filepath, key=None, nolink=False, flags=''):
    ''' Save the file specified by `filepath` into the Maildir.
        By default a hardlink is attempted unless `nolink` is supplied true.
        The optional `flags` is a string consisting of flag letters listed at:
          http://cr.yp.to/proto/maildir.html
        Return the key for the saved message.
    '''
    with Pfx("save_filepath(%s)", filepath):
      if key is None:
        key = self.newkey()
        debug("new key = %s", key)
      elif not self.validkey(key):
        raise ValueError("invalid key: %s" % (key,))
        if key in self.msgmap:
          raise ValueError("key already in Maildir: %s" % (key,))
      tmppath = os.path.join(self.dir, 'tmp', key)
      if os.path.exists(tmppath):
        raise ValueError("temp file already in Maildir: %s" % (tmppath,))
      if not nolink:
        try:
          debug("hardlink %s => %s", filepath, tmppath)
          os.link(filepath, tmppath)
        except OSError:
          debug("copyfile %s => %s", filepath, tmppath)
          shutil.copyfile(filepath, tmppath)
      else:
        debug("copyfile %s => %s", filepath, tmppath)
        shutil.copyfile(filepath, tmppath)
      newbase = key
      if flags:
        newbase += ':2,' + ''.join(sorted(flags))
      newpath = os.path.join(self.dir, 'new', newbase)
      try:
        debug("rename %s => %s", tmppath, newpath)
        os.rename(tmppath, newpath)
      except:
        debug("unlink %s", tmppath)
        os.unlink(tmppath)
        raise
      self.msgmap[key] = ('new', newbase)
      return key

  def save_file(self, fp, key=None, flags=''):
    ''' Save the contents of the file-like object `fp` into the Maildir.
        Return the key for the saved message.
    '''
    with NamedTemporaryFile('w', dir=os.path.join(self.dir, 'tmp')) as T:
      debug("create new file %s for key %s", T.name, key)
      T.write(fp.read())
      T.flush()
      return self.save_filepath(T.name, key=key, flags=flags)

  def save_message(self, M, key=None, flags=''):
    ''' Save the contents of the Message `M` into the Maildir.
        Return the key for the saved message.
    '''
    return self.save_file(StringIO(str(M)), key=key, flags=flags)

  def keypath(self, key):
    subdir, msgbase = self.msgmap[key]
    return Pathname(os.path.join(self.dir, subdir, msgbase))

  def open(self, key, mode='r'):
    ''' Open the file storing the message specified by `key`.
    '''
    return open(self.keypath(key), mode=mode)

  def get_file(key):
    return self.open(key, mode='rb')

  def add(self, message, key=None):
    ''' Add a message to the Maildir.
        `message` may be an email.message.Message instance or a path to a file.
    '''
    if type(message) in (str, unicode):
      return self.save_filepath(message, key=key)
    if isinstance(message, email.message.Message):
      with NamedTemporaryFile('w', dir=os.path.join(self.dir, 'tmp')) as T:
        T.write(message.as_string())
        T.flush()
        key = self.save_filepath(T.name, key=key)
      return key
    raise ValueError("unsupported message type: %s" % (type(message),))

  def remove(self, key):
    subdir, msgbase = self.msgmap[key]
    msgpath = os.path.join(self.dir, subdir, msgbase)
    debug("%s: remove key %s: %s", self, key, msgpath)
    try:
      os.remove(msgpath)
    except OSError as e:
      warning("%s: remove key %s: %s: %s", self, key, msgpath, e)
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
    for key in self.keys():
      return self.pop(key)
    raise KeyError("empty Maildir")

  def update(self, arg):
    try:
      km = arg.items()
    except AttributeError:
      km = iter(arg)
    for key, message in km:
      self[key] = message

  def __getitem__(self, key):
    ''' Return a mailbox.Message loaded from the message with key `key`.
	The Message's .pathname property contains .keypath(key),
	the pathname to the message file.
    '''
    return Message(self.keypath(key))
  get_message = __getitem__

  def get_headers(self, key):
    ''' Return the headers of the specified message as
    '''
    with self.open(key) as mfp:
      return Message(mfp, headersonly=True)

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

  def keys(self, flush=False):
    if flush:
      self.flush()
    return self.msgmap.keys()

  def itervalues(self):
    for key in self.iterkeys():
      return self[key]
  __iter__ = itervalues

  def values(self):
    return list(self.itervalues())

  def iteritems(self):
    for key in self.iterkeys():
      return key, self[key]

  def items(self):
    return list(self.iteritems())

  def iterheaders(self):
    ''' Yield (key, headers) from the Maildir.
    '''
    for key in self.iterkeys():
      return key, self.get_headers(key)

  def lock(self):
    self._lock.acquire()

  def unlock(self):
    self._lock.release()

  def close(self):
    pass

  def as_mbox(self, fp, keys=None):
    ''' Transcribe the contents of this maildir in UNIX mbox format to the
        file `fp`.
        The optional iterable `keys` designates the messages to transcribe.
        The default is to transcribe all messages.
    '''
    if keys is None:
      keys = self.iterkeys()
    for key in keys:
      with Pfx(key):
        message = self[key]
        text = message.as_string(unixfrom=True)
        fp.write(text)
        fp.write('\n')

if __name__ == '__main__':
  import cs.mailutils_tests
  cs.mailutils_tests.selftest(sys.argv)

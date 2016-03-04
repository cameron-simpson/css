#!/usr/bin/python
#
# Access Amazon AWS services.
# Uses boto underneath, but boto does not feel awfully pythonic.
# In any case, this exercise will give me convenient AWS access and
# an avenue to learn the boto interfaces.
#       - Cameron Simpson <cs@zip.com.au> 17nov2012
#

from __future__ import print_function
import sys
from collections import namedtuple
from contextlib import contextmanager
import hashlib
import logging
import magic
from threading import RLock, Thread
from time import sleep
import os
import os.path
from os.path import basename, abspath, normpath, join as joinpath
import stat
import mimetypes
from getopt import getopt, GetoptError
import boto3
from botocore.exceptions import ClientError
from cs.env import envsub
from cs.excutils import logexc
from cs.fileutils import chunks_of
from cs.later import Later
from cs.logutils import setup_logging, D, X, XP, error, warning, info, Pfx
from cs.obj import O, O_str
from cs.queues import IterableQueue
from cs.resources import Pool
from cs.threads import locked_property
from cs.upd import Upd

USAGE = r'''Usage: %s [-L location (ignored, fixme)] command [args...]
  s3 [bucket_name]'''

S3_MAX_DELETE_OBJECTS = 1000

# will be magic.Magic(mime=True)
MAGIC = None

# will be cs.upd.Upd
UPD = None

def main(argv, stdout=None, stderr=None):
  global MAGIC, UPD
  if stdout is None:
    stdout = sys.stdout
  if stderr is None:
    stderr = sys.stderr

  cmd=os.path.basename(argv.pop(0))
  setup_logging(cmd, level=logging.WARNING)
  UPD = Upd(stdout)

  location = None

  badopts = False

  try:
    opts, argv = getopt(argv, 'L:')
  except GetoptError as e:
    error("bad option: %s", e)
    badopts = True
    opts = ()

  for opt, val in opts:
    if opt == '-L':
      location = val
    else:
      error("unimplemented option: %s", opt)
      badopts = True

  if not badopts:
    for mtpath in ( '/etc/mime.types',
                    '/usr/local/etc/mime.types',
                    '/opt/local/etc/mime.types',
                    '$HOME/.mime.types',
                  ):
      filepath = envsub(mtpath)
      if os.path.exists(filepath):
        mimetypes.init((filepath,))
    mtpath = os.environ.get('MIME_TYPES')
    if mtpath:
      mimetypes.init((mtpath,))
    MAGIC = magic.Magic(mime=True)

  if not argv:
    error("missing command")
    badopts = True
  else:
    command = argv.pop(0)
    with Pfx(command):
      try:
        if command == 's3':
          xit = cmd_s3(argv)
        else:
          warning("unrecognised command")
          xit = 2
      except GetoptError as e:
        warning(str(e))
        badopts = True
        xit = 2

  if badopts:
    print(USAGE % (cmd,), file=stderr)
    xit = 2

  return xit

def cmd_s3(argv):
  xit = 0
  s3 = boto3.resource('s3')
  if not argv:
    for bucket in s3.buckets.all():
      print(bucket.name)
    return 0
  bucket_name = argv.pop(0)
  bucket_pool = BucketPool(bucket_name)
  if not argv:
    with BucketPool.instance() as B:
      print("s3.%s.meta = %s", bucket_name, B.meta)
  else:
    s3op = argv.pop(0)
    with Pfx(s3op):
      if s3op == 'sync-up':
        doit = True
        do_delete = False
        badopts = False
        try:
          opts, argv = getopt(argv, 'Dn')
        except GetoptError as e:
          error("bad option: %s", e)
          badopts = True
          opts = ()
        else:
          for opt, val in opts:
            with Pfx(opt):
              if opt == '-D':
                do_delete = True
              elif opt == '-n':
                doit = False
              else:
                error("unimplemented option")
                badopts = True
          if not argv:
            error("missing source directory")
            badopts = True
          else:
            srcdir = argv.pop(0)
            if argv:
              dstdir = argv.pop(0)
            else:
              dstdir = ''
            if argv:
              error("extra arguments after srcdir: %r" % (argv,))
              badopts = True
        if not badopts:
          if not s3syncup_dir(bucket_pool, srcdir, dstdir, doit=doit, do_delete=do_delete, default_ctype='text/html'):
            xit = 1
      else:
        error("unrecognised s3 op")
        badopts = True
      if badopts:
        raise GetoptError("bad arguments")
  return xit

def action_summary(same_ctype, same_mtime, same_size, same_content):
  return ( ( 'C' if not same_ctype else '-' )
         + ( 'M' if not same_mtime else '-' )
         + ( 's' if not same_size else '-' )
         + ( 'd' if not same_content else '-' )
         )

def s3syncup_dir(bucket_pool, srcdir, dstdir, doit=False, do_delete=False, default_ctype=None):
  ''' Sync local directory tree to S3 directory tree.
  '''
  global UPD
  ok = True
  L = Later(4, name="s3syncup(%r, %r, %r)")
  Q = IterableQueue()
  def dispatch():
    for LF in s3syncup_dir_async(L, bucket_pool, srcdir, dstdir, doit=doit, do_delete=do_delete, default_ctype=default_ctype):
      Q.put(LF)
    Q.close()
  with L:
    Thread(target=dispatch).start()
    for LF in Q:
      change, ctype, srcpath, dstpath, e, error_msg = LF()
      if e:
        UPD.without(error, error_msg)
        ok = False
      else:
        line = "%s %-25s %s" % (change, ctype, dstpath)
        if change == '>----':
          UPD.out(line)
        else:
          UPD.nl(line)
  L.wait()
  return ok

def s3syncup_dir_async(L, bucket_pool, srcdir, dstdir, doit=False, do_delete=False, default_ctype=None):
  ''' Sync local directory tree to S3 directory tree.
  '''
  lsep = os.path.sep
  rsep = '/'
  srcdir0 = srcdir
  srcdir = abspath(srcdir)
  if not os.path.isdir(srcdir):
    raise ValueError("srcdir not a directory: %r", srcdir)
  srcdir_slash = srcdir + lsep
  # compare existing local files with remote
  for dirpath, dirnames, filenames in os.walk(srcdir, topdown=True):
    # arrange lexical order of descent
    dirnames[:] = sorted(dirnames)
    with Pfx(dirpath):
      # compute the path relative to srcdir
      if dirpath == srcdir:
        subdirpath = ''
      elif dirpath.startswith(srcdir_slash):
        subdirpath = dirpath[len(srcdir_slash):]
      else:
        raise RuntimeError("os.walk(%r) gave surprising dirpath %r" % (srcdir, dirpath))
      if subdirpath:
        dstdirpath = rsep.join([dstdir] + subdirpath.split(lsep))
      else:
        dstdirpath = dstdir
      for filename in sorted(filenames):
        with Pfx(filename):
          # TODO: dispatch these in parallel
          srcpath = joinpath(dirpath, filename)
          if dstdirpath:
            dstpath = dstdirpath + rsep + filename
          else:
            dstpath = filename
          yield L.defer(s3syncup_file, bucket_pool, srcpath, dstpath, doit=True, default_ctype=default_ctype)
  if do_delete:
    # now process deletions
    with bucket_pool.instance() as B:
      if dstdir:
        dstdir_prefix = dstdir + rsep
      else:
        dstdir_prefix = ''
      with Pfx("S3.filter(Prefix=%r)", dstdir_prefix):
        dstdelpaths = []
        for s3obj in B.objects.filter(Prefix=dstdir_prefix):
          dstpath = s3obj.key
          with Pfx(dstpath):
            if not dstpath.startswith(dstdir_prefix):
              error("unexpected dstpath, not in subdir")
              continue
            dstrpath = dstpath[len(dstdir_prefix):]
            if dstrpath.startswith(rsep):
              error("unexpected dstpath, extra %r", rsep)
              continue
            srcpath = joinpath(srcdir, lsep.join(dstrpath.split(rsep)))
            if os.path.exists(srcpath):
              ##info("src exists, not deleting (src=%r)", srcpath)
              continue
            if dstrpath.endswith(rsep):
              # a folder
              print("d DEL", dstpath)
            else:
              try:
                ctype = s3obj.content_type
              except AttributeError as e:
                ##XP("no .content_type")
                ctype = None
              print("* DEL", dstpath, ctype)
            dstdelpaths.append(dstpath)
        if dstdelpaths:
          dstdelpaths = sorted(dstdelpaths, reverse=True)
          while dstdelpaths:
            delpaths = dstdelpaths[:S3_MAX_DELETE_OBJECTS]
            X("delpaths %r", delpaths)
            if doit:
              B.delete_objects(
                  Delete={
                    'Objects':
                      [ {'Key': dstpath} for dstpath in delpaths ]})
            dstdelpaths[:len(delpaths)] = []

@logexc
def s3syncup_file(bucket_pool, srcpath, dstpath, trust_size_mtime=False, doit=False, default_ctype=None):
  with Pfx('s3syncup_file'):
    S = os.stat(srcpath)
    if not stat.S_ISREG(S.st_mode):
      raise ValueError("not a regular file")
    ctype = mimetype(srcpath)
    if ctype is None:
      raise ValueError("cannot deduce content_type")
    with bucket_pool.instance() as B:
      s3obj = B.Object(dstpath)
      try:
        s3obj.load()
      except ClientError as e:
        if e.response['Error']['Code'] == '404':
          missing = True
        else:
          raise
      else:
        missing = False

      hashcode_a_local = None
      same_ctype = False
      same_content = False
      same_mtime = False
      same_size = False
      if missing:
        hashname = 'sha256'
        hashcode_a = None
      else:
        S3 = s3stat_object(s3obj)
        s3meta = s3obj.metadata
        hashcode_a = s3meta.get('sha256')
        if hashcode_a:
          hashname = 'sha256'
        else:
          hashcode_a = s3meta.get('md5')
          if hashcode_a:
            hashname = 'md5'
          else:
            hashname = 'sha256'
        try:
          s3ctype = s3obj.content_type
        except AttributeError:
          s3ctype = None
          same_ctype = False
        else:
          same_ctype = s3ctype == ctype
        same_mtime = S3.st_mtime == S.st_mtime
        same_size = S3.st_size == S.st_size
        if hashcode_a:
          hashcode_a_local = hash_fp(srcpath, hashname).hexdigest()
          same_content = hashcode_a == hashcode_a_local
        elif trust_size_mtime:
          same_content = same_mtime and same_size

      change = '>' + action_summary(same_ctype, same_mtime, same_size, same_content)
      if same_content:
        if same_ctype:
          ##XP("OK")
          pass
        else:
          # update content_type
          kw={ 'ACL': 'public-read',
               'ContentType': ctype,
               'CopySource': dstpath,
               'MetadataDirective': 'COPY',
             }
          if doit:
            with Pfx("copy_from(**%r)", kw):
              s3obj.copy_from(**kw)
      else:
        # upload new content
        if hashcode_a_local is None:
          hashcode_a_local = hash_fp(srcpath, hashname).hexdigest()
        metadata = { hashname: hashcode_a_local,
                     'st_mtime': str(S.st_mtime),
                   }
        kw={ 'ACL': 'public-read',
             'ContentType': ctype,
             'Body': open(srcpath, 'rb'),
             'Metadata': metadata,
           }
        if doit:
          with Pfx("put(**%r)", kw):
            s3obj.put(**kw)
    return change, ctype, srcpath, dstpath, None, None

S3Stat = namedtuple('S3Stat', 'st_mtime st_size')

def s3stat(B, path):
  s3obj = B.Object(path)
  return s3stat_object(s3obj)

def s3stat_object(s3obj):
  st_mtime = None
  st_mtime_a = s3obj.metadata.get('st_mtime')
  if st_mtime_a:
    try:
      st_mtime = float(st_mtime_a)
    except ValueError:
      pass
  if st_mtime is None:
    st_mtime=s3obj.last_modified.timestamp()
  return S3Stat(st_mtime=st_mtime, st_size=s3obj.content_length)

def hash_byteses(bss, hashname, h=None):
  ''' Compute or update the sha256 hashcode for an iterable of bytes objects.
  '''
  if h is None:
    h = hashlib.new(hashname)
  for bs in bss:
    h.update(bs)
  return h

def hash_fp(fp, hashname, h=None, rsize=16384):
  ''' Compute or update the sha256 hashcode for data read from a file.
  '''
  if isinstance(fp, str):
    filename = fp
    with open(filename, 'rb') as fp:
      return hash_fp(fp, hashname, h=h, rsize=rsize)
  return hash_byteses(chunks_of(fp, rsize=rsize), hashname, h=h)

def mimetype(filename):
  global MAGIC
  base = basename(filename)
  try:
    baseleft, basequery = base.split('?', 1)
  except ValueError:
    guesspart = base
  else:
    guesspart = baseleft
  try:
    ctype, cencoding  = mimetypes.guess_type(guesspart)
  except ValueError as e:
    warning("cannot guess MIME type from basename, trying MAGIC: %r: %s", guesspart, e)
    ctype = MAGIC.from_file(filename).decode()
  return ctype

class BucketPool(Pool):

  def __init__(self, bucket_name):
    Pool.__init__(self, lambda: boto3.session.Session().resource('s3').Bucket(bucket_name))

if __name__ == '__main__':
  import signal
  from cs.debug import thread_dump
  signal.signal(signal.SIGHUP, lambda sig, frame: thread_dump())
  sys.exit(main(sys.argv))

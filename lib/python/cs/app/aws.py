#!/usr/bin/python
#
# Access Amazon AWS services.
# - Cameron Simpson <cs@cskk.id.au> 2016
#

from __future__ import print_function

DISTINFO = {
    'description': "Amazon AWS S3 upload client",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': ['boto3', 'python-magic',
                ],
}

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
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
import boto3
from botocore.exceptions import ClientError
from urllib.parse import quote, unquote
from urllib.error import HTTPError
from cs.env import envsub
from cs.excutils import logexc
from cs.fileutils import read_from
from cs.later import Later
from cs.logutils import setup_logging, D, error, warning, info
from cs.x import X
from cs.pfx import XP
from cs.pfx import Pfx
from cs.queues import IterableQueue
from cs.resources import Pool
from cs.threads import locked_property
from cs.upd import Upd
from cs.urlutils import URL

USAGE = r'''Usage: %s [-L location (ignored, fixme)] command [args...]
  s3
    List buckets.
  s3 bucket_name sync-up [-DnU%%] localdir [bucket_subdir]
    Synchronise bucket contents from localdir.
    -D  Delete items in bucket not in localdir.
    -n  No action. Recite required changes.
    -U  No upload phase; delete only.
    -%%  Decode %%hh sequences in local filenames.
  s3 bucket_name scrape [-DnU] url
    Synchronise bucket contents with website.
    -D  Delete items in bucket not in localdir.
    -n  No action. Recite required changes.
    -U  No upload phase; delete only.'''

S3_MAX_DELETE_OBJECTS = 1000
LSEP = os.path.sep
RSEP = '/'

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
  loginfo = setup_logging(cmd, level=logging.WARNING, upd_mode=True)
  UPD = loginfo.upd

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
    if mtpath and os.path.exists(mtpath):
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
  ''' Work with S3 resources.
  '''
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
        do_upload = True
        unpercent = False
        badopts = False
        try:
          opts, argv = getopt(argv, 'DnU%')
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
              elif opt == '-U':
                do_upload = False
              elif opt == '-%':
                unpercent = True
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
          if os.path.isfile(srcdir):
            diff, ctype, srcpath, dstpath, e, error_msg = \
                s3syncup_file(bucket_pool, srcdir, dstdir,
                              doit=doit, default_ctype='text/html')
            if e:
              error(error_msg)
              xit = 1
            else:
              line = "%s %-25s %s" % (diff.summary(), ctype, dstpath)
              UPD.nl(line)
          else:
            if not s3syncup_dir(bucket_pool, srcdir, dstdir,
                                doit=doit, do_delete=do_delete,
                                do_upload=do_upload, unpercent=unpercent,
                                default_ctype='text/html'):
              xit = 1
      elif s3op == 'scrape':
        doit = True
        do_delete = False
        do_upload = True
        badopts = False
        try:
          opts, argv = getopt(argv, 'DnU')
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
              elif opt == '-U':
                do_upload = False
              else:
                error("unimplemented option")
                badopts = True
          if not argv:
            error("missing source URL")
            badopts = True
          else:
            srcurl = URL(argv.pop(0), None)
            if argv:
              error("extra arguments after srcdir: %r" % (argv,))
              badopts = True
        if not badopts:
          if not s3scrape(bucket_pool, srcurl,
                          doit=doit, do_delete=do_delete, do_upload=do_upload):
              xit = 1
      else:
        error("unrecognised s3 op")
        badopts = True
      if badopts:
        raise GetoptError("bad arguments")
  return xit

class Differences(NS):

  def __init__(self):
    self.hashcodes = {}

  def summary(self):
    return ( ( '-' if self.same_content else 'C' )
           + ( '-' if self.same_mimetype else 'M' )
           + ( '-' if self.same_size else 's' )
           + ( '-' if self.same_time else 't' )
           )

  def changes(self):
    ''' Return a dict containing updated field values.
    '''
    ch = {}
    if not self.same_content:
      ch['content'] = self.hashcodes
    if not self.same_mimetype:
      ch['mimetype'] = self.mimetype_new
    if not self.same_size:
      ch['size'] = self.size_new
    if not self.same_time:
      ch['time'] = self.time_new
    return ch

  def changed_fields(self):
    ''' Return a lexically sorted list of the changed fields.
    '''
    return sorted(self.changes().keys())

  @property
  def unchanged(self):
    return self.same_time and self.same_size and self.same_mimetype and self.same_content

  @property
  def same_content(self):
    old = getattr(self, 'sha256_old', None)
    if old is not None:
      new = self.hashcodes['sha256']
      return old is not None and old == new
    old = getattr(self, 'md5_old', None)
    if old is not None:
      new = self.hashcodes['md5']
      return old is not None and old == new
    return False

  @property
  def same_mimetype(self):
    old = getattr(self, 'mimetype_old', None)
    new = getattr(self, 'mimetype_new', None)
    if new is not None:
      return old is not None and old == new
    return False

  @property
  def same_size(self):
    old = getattr(self, 'size_old', None)
    new = getattr(self, 'size_new', None)
    if new is not None:
      return old is not None and old == new
    return False

  @property
  def same_time(self):
    old = getattr(self, 'time_old', None)
    new = getattr(self, 'time_new', None)
    if new is not None:
      return old is not None and old == new
    return False

def path2s3(path, unpercent):
  ''' Accept a local path and return an S3 path.
      If `unpercent`, call urllib.parse.unquote on each component.
  '''
  components = path.split(LSEP)
  if unpercent:
    components = [ unquote(c) for c in components ]
  return RSEP.join(components)

def s32path(s3path, unpercent):
  ''' Accept an S3 path and return a local path.
      If `unpercent`, call urllib.parse.quote on each component.
  '''
  components = s3path.split(RSEP)
  if unpercent:
    components = [ quote(c, safe="'/ :(),?&=+") for c in components ]
  return LSEP.join(components)

def s3syncup_dir(bucket_pool, srcdir, dstdir, doit=False, do_delete=False, do_upload=False, unpercent=False, default_ctype=None):
  ''' Sync local directory tree to S3 directory tree.
  '''
  global UPD
  ok = True
  L = Later(4, name="s3syncup(%r, %r, %r)" % (bucket_pool.bucket_name, srcdir, dstdir))
  with L:
    if do_upload:
      Q = IterableQueue()
      def dispatch():
        for LF in s3syncup_dir_async(L, bucket_pool, srcdir, dstdir, doit=doit, do_delete=do_delete, unpercent=unpercent, default_ctype=default_ctype):
          Q.put(LF)
        Q.close()
      Thread(target=dispatch).start()
      for LF in Q:
        diff, ctype, srcpath, dstpath, e, error_msg = LF()
        if e:
          error(error_msg)
          ok = False
        else:
          line = "%s %-25s %s" % (diff.summary(), ctype, dstpath)
          if diff.unchanged:
            UPD.out(line)
          else:
            if diff.changed_fields() == ['time']:
              # be quiet about time changes
              UPD.out(line)
            else:
              UPD.nl(line)
              ##UPD.nl("  %r", diff.metadata)
    if do_delete:
      # now process deletions
      with bucket_pool.instance() as B:
        ##if dstdir:
        ##  dstdir_prefix = dstdir + RSEP
        ##else:
        ##  dstdir_prefix = ''
        dstdir_prefix = dstdir + RSEP
        with Pfx("S3.filter(Prefix=%r)", dstdir_prefix):
          dstdelpaths = []
          for s3obj in B.objects.filter(Prefix=dstdir_prefix):
            dstpath = s3obj.key
            with Pfx(dstpath):
              if not dstpath.startswith(dstdir_prefix):
                error("unexpected dstpath, not in subdir")
                continue
              dstrpath = dstpath[len(dstdir_prefix):]
              if dstrpath.startswith(RSEP):
                error("unexpected dstpath, extra %r", RSEP)
                continue
              srcpath = joinpath(srcdir, s32path(dstrpath, unpercent))
              if os.path.exists(srcpath):
                ##info("src exists, not deleting (src=%r)", srcpath)
                continue
              ## uncomment if new %hh omissions surface
              ##UPD.nl("MISSING local %r", srcpath)
              if dstrpath.endswith(RSEP):
                # a folder
                UPD.nl("d DEL %s", dstpath)
              else:
                UPD.nl("* DEL %s", dstpath)
              dstdelpaths.append(dstpath)
          if dstdelpaths:
            dstdelpaths = sorted(dstdelpaths, reverse=True)
            while dstdelpaths:
              delpaths = dstdelpaths[:S3_MAX_DELETE_OBJECTS]
              if doit:
                result = B.delete_objects(
                          Delete={
                            'Objects':
                              [ {'Key': dstpath} for dstpath in delpaths ]})
                errs = result.get('Errors')
                if errs:
                  ok = False
                  for err in errors:
                    error("delete: %s: %r", err['Message'], err['Key'])
              dstdelpaths[:len(delpaths)] = []
  L.wait()
  return ok

def s3syncup_dir_async(L, bucket_pool, srcdir, dstdir, doit=False, do_delete=False, unpercent=False, default_ctype=None):
  ''' Sync local directory tree to S3 directory tree.
  '''
  srcdir0 = srcdir
  srcdir = abspath(srcdir)
  if not os.path.isdir(srcdir):
    raise ValueError("srcdir not a directory: %r", srcdir)
  srcdir_slash = srcdir + LSEP
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
        s3subdirpath = path2s3(subdirpath, unpercent)
        if dstdir:
          dstdirpath = dstdir + RSEP + s3subdirpath
        else:
          dstdirpath = s3subdirpath
      else:
        dstdirpath = dstdir
      for filename in sorted(filenames):
        with Pfx(filename):
          # TODO: dispatch these in parallel
          srcpath = joinpath(dirpath, filename)
          s3filename = path2s3(filename, unpercent)
          if dstdirpath:
            dstpath = dstdirpath + RSEP + s3filename
          else:
            dstpath = s3filename
          yield L.defer(s3syncup_file, bucket_pool, srcpath, dstpath, doit=True, default_ctype=default_ctype)

@logexc
def s3syncup_file(bucket_pool, srcpath, dstpath, trust_size_mtime=False, doit=False, default_ctype=None):
  with Pfx('s3syncup_file'):
    diff = Differences()

    # fetch size and mtime of local file
    S = os.stat(srcpath)
    if not stat.S_ISREG(S.st_mode):
      raise ValueError("not a regular file")
    diff.size_new = S.st_size
    diff.time_new = S.st_mtime

    # compute MIME type for local file
    ctype = mimetype(srcpath)
    if ctype is None:
      if default_ctype is None:
        raise ValueError("cannot deduce content_type")
      ctype = default_ctype
    diff.mimetype_new = ctype

    with bucket_pool.instance() as B:
      # fetch S3 object attribute
      s3obj = B.Object(dstpath)
      try:
        s3obj.load()
      except UnicodeError as e:
        error("SKIP: cannot handle s3 path %r: %s", dstpath, e)
        return None, None, srcpath, dstpath, \
               e, ("SKIP: cannot handle s3 path %r: %s" % (dstpath, e))
      except ClientError as e:
        if e.response['Error']['Code'] == '404':
          diff.missing = True
          diff.s3metadata = None
        else:
          raise
      else:
        diff.missing = False
        s3m = diff.s3metadata = s3obj.metadata
        # import some s3cmd-attrs into our own scheme if present
        if 's3cmd-attrs' in s3m:
          for s3cmdattr in s3m['s3cmd-attrs'].split('/'):
            a, v = s3cmdattr.split(':', 1)
            if a == 'mtime' and 'st_mtime' not in s3m:
              s3m['st_mtime'] = v
            elif a == 'md5' and 'md5' not in s3m:
              s3m['md5'] = v
        diff.size_old = s3obj.content_length
        time_old_s = s3obj.metadata.get('st_mtime')
        if time_old_s is None:
          time_old = None
        else:
          try:
            time_old = float(time_old_s)
          except ValueError:
            time_old = None
        diff.time_old = time_old

      # look for content hash metadata
      sha256_local_a = None
      if diff.missing:
        hashname = 'sha256'
        hashcode_a = None
      else:
        s3meta = s3obj.metadata
        hashcode_a = s3meta.get('sha256')
        if hashcode_a:
          hashname = 'sha256'
          diff.sha256_old = hashcode_a
        else:
          hashcode_a = s3meta.get('md5')
          if hashcode_a:
            hashname = 'md5'
            diff.md5_old = hashcode_a
          else:
            hashname = 'sha256'
        # compute the local hashcode anyway
        # it will be the preferred SHA256 code if there was no S3-side hashcode
        diff.hashcodes[hashname] = hash_fp(srcpath, hashname).hexdigest()
        # fetch content-type
        try:
          s3ctype = s3obj.content_type
        except AttributeError:
          s3ctype = None
        else:
          diff.mimetype_old = s3ctype

      metadata = { 'st_mtime': str(S.st_mtime),
                 }
      if diff.same_content:
        for hn in 'sha256', 'md5':
          if hn in s3obj.metadata:
            metadata[hn] = s3obj.metadata[hn]
        if diff.same_mimetype and diff.same_time:
          pass
        else:
          # update content_type
          kw={ 'ACL': 'public-read',
               'ContentType': ctype,
               # NB: bucket name plus path
               'CopySource': B.name + RSEP + dstpath,
               'Key': dstpath,
               ##'MetadataDirective': 'COPY',
               'MetadataDirective': 'REPLACE',
               'Metadata': metadata,
             }
          if doit:
            with Pfx("copy_from(**%r)", kw):
              s3obj.copy_from(**kw)
      else:
        # upload new content
        # ensure we have our preferred hashcode
        if 'sha256' not in diff.hashcodes:
          diff.hashcodes['sha256'] = hash_fp(srcpath, 'sha256').hexdigest()
        for hashname, hashcode_a in diff.hashcodes.items():
          metadata[hashname] = hashcode_a
        kw={ 'ACL': 'public-read',
             'ContentType': ctype,
             'Body': open(srcpath, 'rb'),
             'Key': dstpath,
             'Metadata': metadata,
           }
        if doit:
          with Pfx("put(**%r)", kw):
            s3obj.put(**kw)
      diff.metadata = metadata
    return diff, ctype, srcpath, dstpath, None, None

def s3scrape(bucket_pool, srcurl, doit=False, do_delete=False, do_upload=False):
  ''' Sync website to S3 directory tree.
  '''
  global UPD
  ok = True
  L = Later(4, name="s3scrape(%r, %r)" % (bucket_pool.bucket_name, srcurl))
  with L:
    if do_upload:
      Q = IterableQueue()
      def dispatch():
        for LF in s3scrape_async(L, bucket_pool, srcurl, doit=doit, do_delete=do_delete):
          Q.put(LF)
        Q.close()
      Thread(target=dispatch).start()
      for LF in Q:
        diff, ctype, srcU, dstpath, e, error_msg = LF()
        with Pfx(srcU):
          if e:
            error(error_msg)
            ok = False
          else:
            line = "%s %-25s %s" % (diff.summary(), ctype, dstpath)
            if diff.unchanged:
              UPD.out(line)
              ##UPD.nl(line)
            else:
              if diff.changed_fields() == ['time']:
                # be quiet about time changes
                UPD.out(line)
              else:
                UPD.nl(line)
                ##UPD.nl("  %r", diff.metadata)
    if do_delete:
      # now process deletions
      with bucket_pool.instance() as B:
        ##if dstdir:
        ##  dstdir_prefix = dstdir + RSEP
        ##else:
        ##  dstdir_prefix = ''
        dstdir_prefix = RSEP
        with Pfx("S3.filter(Prefix=%r)", dstdir_prefix):
          dstdelpaths = []
          for s3obj in B.objects.filter(Prefix=dstdir_prefix):
            dstpath = s3obj.key
            with Pfx(dstpath):
              if not dstpath.startswith(dstdir_prefix):
                error("unexpected dstpath, not in subdir")
                continue
              dstrpath = dstpath[len(dstdir_prefix):]
              if dstrpath.startswith(RSEP):
                error("unexpected dstpath, extra %r", RSEP)
                continue
              raise RuntimeError("DELETION UNIMPLEMENTED")
              srcpath = joinpath(srcdir, s32path(dstrpath, unpercent))
              if os.path.exists(srcpath):
                ##info("src exists, not deleting (src=%r)", srcpath)
                continue
              ## uncomment if new %hh omissions surface
              ##UPD.nl("MISSING local %r", srcpath)
              if dstrpath.endswith(RSEP):
                # a folder
                UPD.nl("d DEL %s", dstpath)
              else:
                UPD.nl("* DEL %s", dstpath)
              dstdelpaths.append(dstpath)
          if dstdelpaths:
            dstdelpaths = sorted(dstdelpaths, reverse=True)
            while dstdelpaths:
              delpaths = dstdelpaths[:S3_MAX_DELETE_OBJECTS]
              if doit:
                result = B.delete_objects(
                          Delete={
                            'Objects':
                              [ {'Key': dstpath} for dstpath in delpaths ]})
                errs = result.get('Errors')
                if errs:
                  ok = False
                  for err in errors:
                    error("delete: %s: %r", err['Message'], err['Key'])
              dstdelpaths[:len(delpaths)] = []
  L.wait()
  return ok

def s3scrape_async(L, bucket_pool, srcurl, doit=False, do_delete=False, limit=None):
  ''' Sync website to S3 directory tree.
  '''
  if limit is None:
    limit = srcurl.default_limit()
  for U in srcurl.walk(limit=limit):
    ##s3scrape_single_url(bucket_pool, U, limit, doit=True)
    yield L.defer(s3scrape_single_url, bucket_pool, U, limit, doit=doit)
  if False: yield None

def s3scrape_single_url(bucket_pool, U, limit, doit, trust_size_mtime=True):
  with Pfx('s3scrape_single_url'):
    dstpath = U.path
    if U.params:
      dstpath += '&' + U.params
    if U.query:
      dstpath += '&' + U.query
    diff = Differences()
    if not U.exists():
      return diff, None, U, dstpath, HTTPError(U, 404, "does not exist", None, None), "missing URL"

    # fetch size and mtime of source URL
    diff.size_new = U.content_length
    diff.time_new = U.last_modified

    ctype = U.content_type
    diff.mimetype_new = ctype

    # now examine S3 object
    with bucket_pool.instance() as B:
      # fetch S3 object attribute
      s3obj = B.Object(dstpath)
      try:
        s3obj.load()
      except UnicodeError as e:
        error("SKIP: cannot handle s3 path %r: %s", dstpath, e)
        return None, None, srcpath, dstpath, \
               e, ("SKIP: cannot handle s3 path %r: %s" % (dstpath, e))
      except ClientError as e:
        if e.response['Error']['Code'] == '404':
          diff.missing = True
          diff.s3metadata = None
        else:
          raise
      else:
        diff.missing = False
        s3m = diff.s3metadata = s3obj.metadata
        # import some s3cmd-attrs into our own scheme if present
        if 's3cmd-attrs' in s3m:
          for s3cmdattr in s3m['s3cmd-attrs'].split('/'):
            a, v = s3cmdattr.split(':', 1)
            if a == 'mtime' and 'st_mtime' not in s3m:
              s3m['st_mtime'] = v
            elif a == 'md5' and 'md5' not in s3m:
              s3m['md5'] = v
        diff.size_old = s3obj.content_length
        time_old_s = s3obj.metadata.get('st_mtime')
        if time_old_s is None:
          time_old = None
        else:
          try:
            time_old = float(time_old_s)
          except ValueError:
            time_old = None
        diff.time_old = time_old

      if ( trust_size_mtime
       and diff.same_size
       and diff.same_time
       and diff.same_mimetype
         ):
        # leave it alone
        X("SAME SIZE/TIME/TYPE: %s", U)
        pass
      else:
        with NamedTemporaryFile(mode="wb") as T:
          T.write(U.content)
          T.flush()
          srcpath = T.name
          # look for content hash metadata
          sha256_local_a = None
          if diff.missing:
            hashname = 'sha256'
            hashcode_a = None
          else:
            s3meta = s3obj.metadata
            hashcode_a = s3meta.get('sha256')
            if hashcode_a:
              hashname = 'sha256'
              diff.sha256_old = hashcode_a
            else:
              hashcode_a = s3meta.get('md5')
              if hashcode_a:
                hashname = 'md5'
                diff.md5_old = hashcode_a
              else:
                hashname = 'sha256'
            # compute the local hashcode anyway
            # it will be the preferred SHA256 code if there was no S3-side hashcode
            diff.hashcodes[hashname] = hash_fp(srcpath, hashname).hexdigest()
            # fetch content-type
            try:
              s3ctype = s3obj.content_type
            except AttributeError:
              s3ctype = None
            else:
              diff.mimetype_old = s3ctype
          metadata = { 'st_mtime': str(diff.time_new),
                     }
          if diff.same_content:
            for hn in 'sha256', 'md5':
              if hn in s3obj.metadata:
                metadata[hn] = s3obj.metadata[hn]
            if diff.same_mimetype and diff.same_time:
              pass
            else:
              # update content_type
              kw={ 'ACL': 'public-read',
                   'ContentType': ctype,
                   # NB: bucket name plus path
                   'CopySource': B.name + RSEP + dstpath,
                   'Key': dstpath,
                   ##'MetadataDirective': 'COPY',
                   'MetadataDirective': 'REPLACE',
                   'Metadata': metadata,
                 }
              if doit:
                with Pfx("copy_from(**%r)", kw):
                  s3obj.copy_from(**kw)
          else:
            # upload new content
            # ensure we have our preferred hashcode
            if 'sha256' not in diff.hashcodes:
              diff.hashcodes['sha256'] = hash_fp(srcpath, 'sha256').hexdigest()
            for hashname, hashcode_a in diff.hashcodes.items():
              metadata[hashname] = hashcode_a
            kw={ 'ACL': 'public-read',
                 'ContentType': ctype,
                 'Body': open(srcpath, 'rb'),
                 'Key': dstpath,
                 'Metadata': metadata,
               }
            if doit:
              with Pfx("put(**%r)", kw):
                s3obj.put(**kw)
          diff.metadata = metadata
    return diff, ctype, U, dstpath, None, None

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
  return hash_byteses(read_from(fp, rsize=rsize), hashname, h=h)

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
    self.bucket_name = bucket_name

if __name__ == '__main__':
  import signal
  from cs.debug import thread_dump
  signal.signal(signal.SIGHUP, lambda sig, frame: thread_dump())
  sys.exit(main(sys.argv))

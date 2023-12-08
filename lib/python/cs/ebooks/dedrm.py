#!/usr/bin/env python3
#

''' Support for DeDRM/noDRM.

    This is an experimental module aimed at making the DeDRM/noDRM
    packages run outside Calibre's plugin environment.
'''

from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime
from getopt import GetoptError
import importlib
import json
import os
from os.path import (
    basename,
    dirname,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    normpath,
    realpath,
    splitext,
)
from shutil import copyfile
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
import time
from typing import Iterable, List, Optional

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc, Promotable
from cs.fileutils import atomic_filename
from cs.lex import r, stripped_dedent
from cs.logutils import warning
from cs.pfx import pfx, Pfx, pfx_call, pfx_method
from cs.sqltags import SQLTags
from cs.upd import print  # pylint: disable=redefined-builtin

DEDRM_PACKAGE_PATH_ENVVAR = 'DEDRM_PACKAGE_PATH'

def main(argv=None):
  ''' DeDRM command line mode.
  '''
  return DeDRMCommand(argv).run()

class DeDRMCommand(BaseCommand):
  ''' cs.dedrm command line implementation.
  '''

  GETOPT_SPEC = 'D:'
  USAGE_FORMAT = r'''Usage: {cmd} [-D dedrm_package_path] subcommand [args...]
    -D  Specify the filesystem path to the DeDRM/noDRM plugin top level.
        For example, if you had a checkout of git@github.com:noDRM/DeDRM_tools.git
        at /path/to/DeDRM_tools--noDRM you could supply:
        -D /path/to/DeDRM_tools--noDRM/DeDRM_plugin
        or place that value in the $DEDRM_PACKAGE_PATH environment variable.
  '''

  @dataclass
  class Options(BaseCommand.Options):
    ''' Command line option state.
    '''

    dedrm_package_path: Optional[str] = field(
        default_factory=lambda: os.environ.get(DEDRM_PACKAGE_PATH_ENVVAR)
    )

  def apply_opt(self, opt, val):
    badopt = False
    if opt == '-D':
      self.options.dedrm_package_path = val
    else:
      raise RuntimeError("unhandled option")
    if badopt:
      raise GetoptError("bad option value")

  @contextmanager
  def run_context(self):
    with super().run_context():
      options = self.options
      with Pfx("dedrm_package_path"):
        dedrm_package_path = options.dedrm_package_path
        if dedrm_package_path is None:
          if not os.environ.get(DEDRM_PACKAGE_PATH_ENVVAR):
            raise GetoptError(
                f'no ${DEDRM_PACKAGE_PATH_ENVVAR} and no -D option'
            )
        try:
          options.dedrm = pfx_call(DeDRMWrapper, dedrm_package_path)
        except ValueError as e:
          raise GetoptError("bad dedrm_package_path: %s" % (e,))
        options.dedrm_package_path = options.dedrm.dedrm_package_path
      yield

  def cmd_import(self, argv):
    ''' Usage: {cmd} module_name...
          Exercise the DeDRM python import mechanism for each module_name.
    '''
    if not argv:
      raise GetoptError("missing module_name")
    dedrm = self.options.dedrm
    xit = 0
    for module_name in argv:
      print("import", module_name, "...")
      with Pfx("import %r", module_name):
        try:
          module_name, name = module_name.split(':')
        except ValueError:
          name = None
        with dedrm.dedrm_imports():
          try:
            M = pfx_call(importlib.import_module, module_name)
          except ImportError as e:
            warning("import fails: %s", e)
            xit = 1
            continue
          print("import %r => %s" % (module_name, r(M)))
        with Pfx('.%s', name):
          if name is not None:
            try:
              value = getattr(M, name)
            except AttributeError as e:
              warning("not present: %s", e)
              xit = 1
              continue
            print("  .%r => %s" % (name, r(value)))
    return xit

  def cmd_kindlekeys(self, argv):
    ''' Usage: {cmd} [import]
          import    Read a JSON list of key dicts and update the cached keys.
    '''
    dedrm = self.options.dedrm
    if not argv:
      with redirect_stdout(sys.stderr):
        kks = dedrm.kindlekeys
      print(json.dumps(kks))
      return 0
    op = argv.pop(0)
    if op != 'import':
      raise GetoptError("expected 'import', got %r" % (op,))
    with Pfx(op):
      if argv:
        raise GetoptError("extra arguments: %r" % (argv,))
      new_kks = json.loads(sys.stdin.read())
      assert all(isinstance(kk, dict) for kk in new_kks)
      dedrm.update_kindlekeys_from_keys(new_kks)

  def cmd_remove(self, argv):
    ''' Usage: {cmd} filenames...
          Remove DRM from the specified filenames.
    '''
    dedrm = self.options.dedrm
    exists_ok = False
    output_dirpath = '.'
    # TODO: -f exists_ok -O output_dirpath
    if not argv:
      raise GetoptError("missing filenames")
    for filename in argv:
      with Pfx(filename):
        output_filename = normpath(
            joinpath(
                realpath(output_dirpath), 'decrypted-' + basename(filename)
            )
        )
        pfx_call(dedrm.remove, filename, output_filename, exists_ok=exists_ok)

class DeDRMWrapper(Promotable):
  ''' Class embodying the DeDRM/noDRM package actions.
  '''

  # The package name to use for the DeDRM/noDRM package.
  # We also use these symbols to convince the DRM package to run
  # without being installed as a calibre plugin inside calibre.
  DEDRM_PACKAGE_NAME = 'dedrm'
  DEDRM_PLUGIN_NAME = 'DeDRM'
  DEDRM_PLUGIN_VERSION = '7.2.1'

  @pfx_method
  @fmtdoc
  def __init__(
      self,
      dedrm_package_path: Optional[str] = None,
      sqltags: Optional[SQLTags] = None
  ):
    ''' Initialise the DeDRM package.

        Parameters:
        * `dedrm_package_path`: optional filesystem path to the
          DeDRM/noDRM package plugin subdirectory, default from
          ${DEDRM_PACKAGE_PATH_ENVVAR}
        * `sqltags`: optional `SQLTags` instance to store state
          under the `self.DEDRM_PACKAGE_NAME.` prefix;
          default from `SQLTags()`
    '''
    with Pfx("dedrm_package_path %r", dedrm_package_path):
      if dedrm_package_path is None:
        dedrm_package_path = os.environ.get(DEDRM_PACKAGE_PATH_ENVVAR)
        if dedrm_package_path is None:
          raise ValueError(
              f'no dedrm_package_path and no ${DEDRM_PACKAGE_PATH_ENVVAR}'
          )
        with Pfx('$%s=%r', DEDRM_PACKAGE_PATH_ENVVAR, dedrm_package_path):
          self.__init__(dedrm_package_path, sqltags=sqltags)
          return
      if not isdirpath(dedrm_package_path):
        raise ValueError("not a directory")
      if not isdirpath(joinpath(dedrm_package_path, 'standalone')):
        raise ValueError("no \"standalone\" subdirectory")
      self.dedrm_package_path = dedrm_package_path
      dedrm_DeDRM = self.import_name(self.DEDRM_PACKAGE_NAME, 'DeDRM')
      dedrm_DeDRMError = self.import_name(
          self.DEDRM_PACKAGE_NAME, 'DeDRMError'
      )

      class CSEBookDeDRM(DeDRMOverride, dedrm_DeDRM):
        ''' Our wrapper for the DeDRM/noDRM `DeDRM` class
            using overrides from `DeDRMOverride`.
        '''
        alfdir = dedrm_package_path

      self.dedrm = CSEBookDeDRM()
      self.DeDRMError = dedrm_DeDRMError
    with self.dedrm_imports():
      kindlekey = self.import_name('kindlekey')
      ##kindlekey = self.import_name('kindlekey', package=__package__)
      # monkey patch the kindlekey.kindlekeys function
      self.base_kindlekeys = kindlekey.kindlekeys
      kindlekey.kindlekeys = self.cached_kindlekeys
      # monkey patch the kindlekey.CryptUnprotectData class
      BaseCryptUnprotectData = kindlekey.CryptUnprotectData
      LibCrypto = getLibCrypto()

      class CryptUnprotectData(BaseCryptUnprotectData):

        def __init__(self, *args):
          self.crp = LibCrypto()
          super().__init__(*args)

      kindlekey.CryptUnprotectData = CryptUnprotectData
    if sqltags is None:
      sqltags = SQLTags()
    self.tags = sqltags.subdomain(f'{self.DEDRM_PACKAGE_NAME}')

  @contextmanager
  def dedrm_imports(self):
    ''' Context manager to run some code with `self.dedrm_package_path`
        prepended to `sys.path` for import purposes.

        This also crafts some stub modules to convince the plugin
        to run by providing some things normally provided by the
        Calibre plugin environment.
    '''

    def write_module(name, contents):
      ''' Write Python code `contents` to a top level module named `name`.
      '''
      module_path = joinpath(tmpdirpath, f'{name}.py')
      with pfx_call(open, module_path, 'w') as pyf:
        print(
            stripped_dedent(
                f'''
                #!/usr/bin/env python3
                #
                # Generated by {__file__} at {datetime.now()}.
                # - Cameron Simpson <cs@cskk.id.au>
                #
                '''
            ),
            file=pyf
        )
        print(stripped_dedent(contents), file=pyf)
      ##os.system(f'set -x; cat {module_path}')

    with TemporaryDirectory(prefix='dedrm_lib') as tmpdirpath:
      # present the dedrm package as DEDRM_PACKAGE_NAME ('dedrm')
      os.symlink(
          self.dedrm_package_path,
          joinpath(tmpdirpath, self.DEDRM_PACKAGE_NAME)
      )
      # fake up __version.py
      write_module(
          '__version',
          f'''
          PLUGIN_NAME = {self.DEDRM_PLUGIN_NAME!r}
          PLUGIN_VERSION = {self.DEDRM_PLUGIN_VERSION!r}
          PLUGIN_VERSION_TUPLE = {tuple(map(int, self.DEDRM_PLUGIN_VERSION.split('.')))!r}
        ''',
      )
      # fake up prefs.DeDRM_Prefs()
      write_module(
          'prefs',
          f'''
          import os
          import {self.DEDRM_PACKAGE_NAME}.prefs
          from {self.DEDRM_PACKAGE_NAME}.prefs import DeDRM_Prefs as BaseDeDRM_Prefs
          from {self.DEDRM_PACKAGE_NAME}.standalone.jsonconfig import JSONConfig as BaseJSONConfig
          from cs.ebooks.dedrm import DeDRM_PrefsOverride
          from cs.gimmicks import warning
          class DeDRM_Prefs(DeDRM_PrefsOverride, BaseDeDRM_Prefs):
            def __init__(self, json_path=None):
              if json_path is not None:
                warning(
                    "DeDRM_Prefs: ignoring json_path=%r, using %r", json_path,
                    os.devnull
                )
              BaseDeDRM_Prefs.__init__(self, json_path=os.devnull)
          class JSONConfig(BaseJSONConfig):
            def __init__(self, rel_path_to_cf_file):
              super().__init__(rel_path_to_cf_file)
              warning(
                  "JSONConfig: replacing file_path=%r with %r",
                  self.file_path,
                  os.devnull
              )
              self.file_path = os.devnull
          {self.DEDRM_PACKAGE_NAME}.prefs.JSONConfig = JSONConfig
          ''',
      )
      with stackattrs(
          sys,
          path=[tmpdirpath, joinpath(tmpdirpath, self.DEDRM_PACKAGE_NAME)] +
          sys.path):
        # pylint: disable=import-outside-toplevel
        import builtins
        with stackattrs(builtins, print=print):
          with redirect_stdout(sys.stderr):
            # pylint: disable=import-outside-toplevel
            import prefs  # imported for its side effect
            yield

  def import_name(self, module_name, name=None, *, package=None):
    ''' Shim for `importlib.import_module` to import from the DeDRM/noDRM package.

        Parameters:
        * `module_name`: the module name to import
        * `name`: optional name from `module_name`
        * `package`: `package` argument for `importlib.import_module`,
          required if `module_name` is a relative import

        If `name` is omitted, return the imported module.
        Otherwise return the value of `name` from the imported module.
    '''
    with self.dedrm_imports():
      M = pfx_call(importlib.import_module, module_name, package=package)
      if name is None:
        return M
      return pfx_call(getattr, M, name)

  @pfx_method
  def remove(
      self,
      srcpath,
      dstpath,
      *,
      booktype=None,
      exists_ok=False,
      obok_lib=None,
  ):
    ''' Remove the DRM from `srcpath`, writing the resulting file to `dstpath`.

        Parameters:
        * `exists_ok`: if true then `dstpath` may already exist; default `False`
    '''
    if booktype is None:
      # infer book type from file extension
      booktype = splitext(basename(srcpath))[1][1:].lower()
      if booktype == '':
        # Kobo kepub files inside the "kepub" directory
        if basename(dirname(srcpath)).lower() == 'kepub':
          booktype = 'kepub'
      if not booktype:
        raise ValueError("cannot infer book type")
    with atomic_filename(dstpath, exists_ok=exists_ok) as T:
      if booktype == 'kepub':
        from .kobo import import_obok, decrypt_obok
        obok = import_obok()
        if obok_lib is None:
          from .kobo import default_kobo_library  # pylint: disable=import-outside-toplevel
          obok_lib = obok.KoboLibrary(desktopkobodir=default_kobo_library())
          need_close_lib = True
        else:
          need_close_lib = False
        # pylint: disable=protected-access
        obok_book = obok.KoboBook(
            basename(srcpath), srcpath, srcpath, 'kepub', obok_lib.__cursor
        )
        decrypt_obok(obok_lib, obok_book, T.name, exists_ok=True)
        if need_close_lib:
          obok_lib.close()
      else:
        dedrm = self.dedrm
        # monkey patch temporary_file method to return tmpfilename
        with stackattrs(dedrm, temporary_file=lambda ext: T):
          with self.dedrm_imports():
            dedrm.starttime = time.time()
            if booktype in ['prc', 'mobi', 'pobi', 'azw', 'azw1', 'azw3',
                            'azw4', 'tpz', 'kfx-zip']:
              # Kindle/Mobipocket
              decrypted_ebook = dedrm.KindleMobiDecrypt(srcpath)
            elif booktype == 'pdb':
              # eReader
              decrypted_ebook = dedrm.eReaderDecrypt(srcpath)
            elif booktype == 'pdf':
              # Adobe PDF (hopefully) or LCP PDF
              decrypted_ebook = dedrm.PDFDecrypt(srcpath)
            elif booktype == 'epub':
              # Adobe Adept, PassHash (B&N) or LCP ePub
              decrypted_ebook = dedrm.ePubDecrypt(srcpath)
            else:
              raise ValueError(
                  "cannot decrypt %r, unhandled book type %r" %
                  (srcpath, booktype)
              )
            if decrypted_ebook == srcpath:
              pfx_call(copyfile, srcpath, T.name)

  @contextmanager
  def removed(self, srcpath):
    ''' Context manager to produce a deDRMed copy of `srcpath`,
        yielding the temporary file containing the copy.
    '''
    srcext = splitext(basename(srcpath))[1]
    with NamedTemporaryFile(
        prefix=f'.{self.__class__.__name__}-',
        suffix=srcext,
    ) as T:
      self.remove(srcpath, T.name, exists_ok=True)
      yield T.name

  @property
  def kindlekeys(self) -> List[dict]:
    ''' The cached `kindlekeys()`, a list of dicts.
        Obtained via `self.cached_kindlekeys()`.
    '''
    return self.cached_kindlekeys()

  def cached_kindlekeys(self) -> List[dict]:
    ''' Return the cached `kindlekeys()`, a list of dicts.

        If this is empty, a call is made to `self.update_kindlekeys()`
        to load keys from the current environment.
    '''
    kk_tags = self.tags['kindlekeys']
    kks = kk_tags.get('cache')
    if not kks:
      kks = self.update_kindlekeys()
    return kks

  def update_kindlekeys(self, filepaths: Optional[List[str]] = None):
    ''' Update the cached kindle keys from the current environment
        by calling `dedrm.kindlekey.kindlekeys(filepaths)`.
    '''
    if filepaths is None:
      filepaths = []
    new_kks = self.base_kindlekeys(filepaths)
    return self.update_kindlekeys_from_keys(new_kks)

  def update_kindlekeys_from_keys(self, new_kks: Iterable[dict]):
    ''' Update the cached kindle keys with the key `dict`s
        from the iterable `kindlekeys`.
    '''
    kk_tags = self.tags['kindlekeys']
    kks = kk_tags.get('cache') or []
    for keys in new_kks:
      if keys not in kks:
        kks.append(keys)
    print("update_kindlekeys =>", kks)
    kk_tags['cache'] = kks
    return kks

  @classmethod
  def promote(cls, obj) -> "DeDRMWrapper":
    ''' Promote an object to a `DeDRMWrapper`.

        If `obj` is `None` or a `str` return `DeDRMWrapper(dedrm_package_path=obj)`.
    '''
    if isinstance(obj, cls):
      return obj
    if obj is None or isinstance(obj, str):
      return cls(dedrm_package_path=obj)
    raise TypeError("%s.promote: cannot promote %s", cls.__name__, r(obj))

class DeDRMOverride:
  ''' A collection of override methods from the DeDRM/noDRM `DeDRM` class
      which we use to make it work outside the calibre plugin stuff.
  '''

class DeDRM_PrefsOverride:
  ''' Override methods for the DeDRM/noDRM `prefs.DeDRM_Prefs` class.
  '''

def getLibCrypto():
  ''' The OSX `LibCrypto` implementation as an experiment,
        copied from `Other_Tools/DRM_Key_Scripts/Kindle_for_Mac_and_PC/kindlekey.pyw`.
    '''

  class DrmException(Exception):
    ''' Really DeDRM/noDRM needs this in `__init__`, but instead
        there are distinct classes all over the code, a disaster.
        This class is bug-for-bug compatible.
    '''
    pass

  # interface to needed routines in openssl's libcrypto
  def _load_crypto_libcrypto():
    from ctypes import (
        CDLL, byref, POINTER, c_void_p, c_char_p, c_int, c_long, Structure,
        c_ulong, create_string_buffer, addressof, string_at, cast
    )
    from ctypes.util import find_library

    libcrypto = find_library('crypto')
    if libcrypto is None:
      raise DrmException(u"libcrypto not found")
    libcrypto = CDLL(libcrypto)

    # From OpenSSL's crypto aes header
    #
    # AES_ENCRYPT     1
    # AES_DECRYPT     0
    # AES_MAXNR 14 (in bytes)
    # AES_BLOCK_SIZE 16 (in bytes)
    #
    # struct aes_key_st {
    #    unsigned long rd_key[4 *(AES_MAXNR + 1)];
    #    int rounds;
    # };
    # typedef struct aes_key_st AES_KEY;
    #
    # int AES_set_decrypt_key(const unsigned char *userKey, const int bits, AES_KEY *key);
    #
    # note:  the ivec string, and output buffer are both mutable
    # void AES_cbc_encrypt(const unsigned char *in, unsigned char *out,
    #     const unsigned long length, const AES_KEY *key, unsigned char *ivec, const int enc);

    AES_MAXNR = 14
    c_char_pp = POINTER(c_char_p)
    c_int_p = POINTER(c_int)

    class AES_KEY(Structure):
      _fields_ = [
          ('rd_key', c_long * (4 * (AES_MAXNR + 1))), ('rounds', c_int)
      ]

    AES_KEY_p = POINTER(AES_KEY)

    def F(restype, name, argtypes):
      func = getattr(libcrypto, name)
      func.restype = restype
      func.argtypes = argtypes
      return func

    AES_cbc_encrypt = F(
        None, 'AES_cbc_encrypt',
        [c_char_p, c_char_p, c_ulong, AES_KEY_p, c_char_p, c_int]
    )

    AES_set_decrypt_key = F(
        c_int, 'AES_set_decrypt_key', [c_char_p, c_int, AES_KEY_p]
    )

    # From OpenSSL's Crypto evp/p5_crpt2.c
    #
    # int PKCS5_PBKDF2_HMAC_SHA1(const char *pass, int passlen,
    #                        const unsigned char *salt, int saltlen, int iter,
    #                        int keylen, unsigned char *out);

    PKCS5_PBKDF2_HMAC_SHA1 = F(
        c_int, 'PKCS5_PBKDF2_HMAC_SHA1',
        [c_char_p, c_ulong, c_char_p, c_ulong, c_ulong, c_ulong, c_char_p]
    )

    class LibCrypto(object):

      def __init__(self):
        self._blocksize = 0
        self._keyctx = None
        self._iv = 0

      def set_decrypt_key(self, userkey, iv):
        self._blocksize = len(userkey)
        if (self._blocksize != 16) and (self._blocksize
                                        != 24) and (self._blocksize != 32):
          raise DrmException(u"AES improper key used")
          return
        keyctx = self._keyctx = AES_KEY()
        self._iv = iv
        self._userkey = userkey
        rv = AES_set_decrypt_key(userkey, len(userkey) * 8, keyctx)
        if rv < 0:
          raise DrmException(u"Failed to initialize AES key")

      def decrypt(self, data):
        out = create_string_buffer(len(data))
        mutable_iv = create_string_buffer(self._iv, len(self._iv))
        keyctx = self._keyctx
        rv = AES_cbc_encrypt(data, out, len(data), keyctx, mutable_iv, 0)
        if rv == 0:
          raise DrmException(u"AES decryption failed")
        return out.raw

      def keyivgen(self, passwd, salt, iter, keylen):
        saltlen = len(salt)
        passlen = len(passwd)
        out = create_string_buffer(keylen)
        rv = PKCS5_PBKDF2_HMAC_SHA1(
            passwd, passlen, salt, saltlen, iter, keylen, out
        )
        return out.raw

    return LibCrypto

  def _load_crypto():
    LibCrypto = None
    try:
      LibCrypto = _load_crypto_libcrypto()
    except (ImportError, DrmException):
      pass
    return LibCrypto

  LibCrypto = _load_crypto()

  return LibCrypto

if __name__ == '__main__':
  sys.exit(main(sys.argv))

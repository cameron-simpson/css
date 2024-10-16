#!/usr/bin/env python3
#

''' Support for DeDRM/noDRM.

    This is an experimental module aimed at making the DeDRM/noDRM
    packages run outside Calibre's plugin environment.
'''

from contextlib import contextmanager, redirect_stdout
from datetime import datetime
from getopt import GetoptError
import importlib
import json
import os
from os.path import (
    abspath,
    basename,
    dirname,
    exists as existspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    normpath,
    realpath,
    splitext,
)
from pprint import pprint
from shutil import copyfile
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
import time
from typing import Iterable, List, Optional, Union
from zipfile import ZipFile

from typeguard import typechecked

from cs.cmdutils import vprint
from cs.context import contextif, stackattrs
from cs.deco import fmtdoc
from cs.fileutils import atomic_filename
from cs.fs import FSPathBasedSingleton, needdir, shortpath, validate_rpath
from cs.fstags import FSTags, uses_fstags
from cs.hashindex import file_checksum
from cs.lex import r, stripped_dedent
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags
from cs.upd import print  # pylint: disable=redefined-builtin

from .common import EBooksCommonBaseCommand

DEDRM_PACKAGE_PATH_ENVVAR = 'DEDRM_PACKAGE_PATH'

def main(argv=None):
  ''' DeDRM command line mode.
  '''
  return DeDRMCommand(argv).run()

class DeDRMCommand(EBooksCommonBaseCommand):
  ''' cs.dedrm command line implementation.
  '''

  USAGE_FORMAT = r'''Usage: {cmd} [options...] subcommand [args...]
    Operations using the DeDRM/NoDRM package.
    Options:
      -D  Specify the filesystem path to the DeDRM/noDRM plugin.
          This can be a checkout of the git@github.com:noDRM/DeDRM_tools.git
          repository or the path to the {DeDRMWrapper.DEDRM_PLUGIN_ZIPFILE_NAME} file
          as would be installed in a Calibre plugin directory.
          The default comes from the ${DEDRM_PACKAGE_PATH_ENVVAR} environment variable
          or the plugin zip file in the local Calibre plugins directory.
  '''

  @contextmanager
  def run_context(self):
    with super().run_context():
      with contextif(self.options.dedrm):
        yield

  def cmd_decrypt(self, argv):
    ''' Usage: {cmd} [--inplace] filenames...
          Remove DRM from the specified filenames.
          Write the decrypted contents of path/to/book.ext
          to the file book-decrypted.ext.
          Options:
            --inplace   Replace the original with the decrypted version.
    '''
    dedrm = self.options.dedrm
    options = self.options
    options.update(output_dirpath='.', inplace=False)
    options.popopts(
        argv,
        O_='output_dirpath',
        inplace=bool,
    )
    if not argv:
      raise GetoptError("missing filenames")
    for filename in argv:
      with Pfx(filename):
        if options.inplace:
          output_filename = filename
          exists_ok = True
        else:
          base, base_ext = splitext(basename(filename))
          output_filename = normpath(
              joinpath(
                  realpath(options.output_dirpath),
                  f'{base}-decrypted{base_ext}'
              )
          )
          exists_ok = False
        if not pfx_call(dedrm.decrypt, filename, output_filename,
                        exists_ok=exists_ok):
          # not encrypted, copy to source file
          if not options.inplace:
            if existspath(output_filename):
              warning("decrypted filename already exists: %r", output_filename)
            else:
              copyfile(filename, output_filename)

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

  def cmd_info(self, argv):
    ''' Usage: {cmd}
          List dedrm infomation.
    '''
    super().cmd_info(argv)
    # TODO: recite the actual DeDRM version

  def cmd_kindlekeys(self, argv):
    ''' Usage: {cmd} [base|import|json|print]
          Dump, print or import the Kindle DRM keys.
          Modes:
            base [-json] [filepaths...]
                      List the kindle keys derived from the current system.
            import    Read a JSON list of key dicts and update the cached keys.
            json      Write the cached list of keys as JSON.
            print     Readable listing of the cached keys.
          The default mode is 'json'.
          Examples:
            Import the base keys from this system into the local collection:
              python3 -m cs.ebooks.dedrm kindlekeys base \
              | python3 -m cs.ebooks dedrm kindlekeys import
            Import the keys from another host into the local collection:
              ssh otherhost python3 -m cs.ebooks.dedrm kindlekeys json \
              | python3 -m cs.ebooks dedrm kindlekeys import
    '''
    dedrm = self.options.dedrm
    if not argv:
      argv = ['json']
    op = argv.pop(0)
    with Pfx(op):
      if op == 'base':
        json_mode = not sys.stdout.isatty()
        if argv and argv[0] == '-json':
          json_mode = True
          argv.pop(0)
        # fetch the base system kindle keys
        with redirect_stdout(sys.stderr):
          new_kks = dedrm.base_kindlekeys(argv)
        if json_mode:
          print(json.dumps(new_kks, indent=2))
        else:
          for i, kk in enumerate(new_kks):
            print(f'{i}:')
            for k, v in sorted(kk.items()):
              print(" ", k, v)
      elif op == 'import':
        if argv:
          raise GetoptError("extra arguments: %r" % (argv,))
        if sys.stdin.isatty():
          print("Reading JSON keys from standard input.")
        new_kks = json.loads(sys.stdin.read())
        assert all(isinstance(kk, dict) for kk in new_kks)
        dedrm.update_kindlekeys_from_keys(new_kks)
      elif op == 'json':
        if argv:
          raise GetoptError("extra arguments: %r" % (argv,))
        with redirect_stdout(sys.stderr):
          kks = dedrm.kindlekeys
        print(json.dumps(kks, indent=2))
      elif op == 'print':
        if argv:
          raise GetoptError("extra arguments: %r" % (argv,))
        with redirect_stdout(sys.stderr):
          kks = dedrm.kindlekeys
        for kk in kks:
          pprint(kk)
      else:
        raise GetoptError("expected 'import' or 'print', got %r" % (op,))

@fmtdoc
class DeDRMWrapper(FSPathBasedSingleton, MultiOpenMixin):
  ''' Class embodying the DeDRM/noDRM package actions.

      This accepts the path to a checkout of the
      git@github.com:noDRM/DeDRM_tools.git source repositiory or
      the path to a DeDRM plugins zipfile as would be installed in
      a Calibre plugin directory.

      If not specified, the environment variable
      `${DEDRM_PACKAGE_PATH_ENVVAR}` is consulted.
      If absent, this looks for the plugin zipfile in the local
      Calibre install.
  '''

  # The package name to use for the DeDRM/noDRM package.
  # We also use these symbols to convince the DRM package to run
  # without being installed as a calibre plugin inside calibre.
  DEDRM_PACKAGE_NAME = 'dedrm'
  DEDRM_PLUGIN_NAME = 'DeDRM'
  DEDRM_PLUGIN_VERSION = '7.2.1'

  # used to locate the plugin zip fil inside a CalibreTree
  DEDRM_PLUGIN_ZIPFILE_NAME = 'DeDRM_plugin.zip'

  @classmethod
  @fmtdoc
  def FSPATH_DEFAULT(cls):
    ''' Called for the default `DeDRMWrapper` filesystem path
        if unspecified and no `${DEDRM_PACKAGE_PATH_ENVVAR}` environment
        variable.
    '''
    return cls.get_package_path()

  @pfx_method
  @fmtdoc
  @typechecked
  def __init__(
      self,
      dedrm_package_path: str,
      sqltags: Optional[SQLTags] = None,
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
    if hasattr(self, 'fspath'):
      # we're already set up
      return
    if not existspath(dedrm_package_path):
      raise ValueError(
          f'dedrm_package_path does not exist: {dedrm_package_path!r}'
      )
    self.fspath = dedrm_package_path
    self.sqltags = sqltags or SQLTags()

  @classmethod
  @fmtdoc
  def get_package_path(
      cls,
      dedrm_package_path: str = None,
      calibre: Optional[Union[str, "CalibreTree"]] = None,
  ) -> str:
    ''' Return the filesystem path of the DeDRM/noDRM package to use.
        If the supplied `dedrm_package_path` is `None`,
        obtain the path from ${DEDRM_PACKAGE_PATH_ENVVAR}.
    '''
    if dedrm_package_path is None:
      dedrm_package_path = os.environ.get(DEDRM_PACKAGE_PATH_ENVVAR)
      if dedrm_package_path is None:
        from .calibre import CalibreTree
        calibre = CalibreTree.promote(calibre)
        dedrm_package_path = joinpath(
            calibre.plugins_dirpath, cls.DEDRM_PLUGIN_ZIPFILE_NAME
        )
        if not isfilepath(dedrm_package_path):
          raise ValueError(
              'no dedrm_package_path'
              f' and no ${DEDRM_PACKAGE_PATH_ENVVAR}'
              f' and no {shortpath(dedrm_package_path)!r}'
          )
      vprint(f'${DEDRM_PACKAGE_PATH_ENVVAR} -> {dedrm_package_path!r}')
    else:
      dedrm_package_path = abspath(dedrm_package_path)
    if not existspath(dedrm_package_path):
      raise ValueError(
          f'dedrm_package_path:{dedrm_package_path!r} does not exist'
      )
    return dedrm_package_path

  @contextmanager
  def startup_shutdown(self):
    # prepare the shim modules
    with TemporaryDirectory(prefix='dedrm_shim_lib--') as shimlib_dirpath:
      self.prepare_dedrm_shims(shimlib_dirpath)
      with stackattrs(self, shimlib_dirpath=shimlib_dirpath):
        dedrm_DeDRM = self.import_name(self.DEDRM_PACKAGE_NAME, 'DeDRM')
        dedrm_DeDRMError = self.import_name(
            self.DEDRM_PACKAGE_NAME, 'DeDRMError'
        )

        class CSEBookDeDRM(DeDRMOverride, dedrm_DeDRM):
          ''' Our wrapper for the DeDRM/noDRM `DeDRM` class
              using overrides from `DeDRMOverride`.
          '''
          alfdir = self.fspath

        with stackattrs(
            self,
            dedrm=CSEBookDeDRM(),
            DeDRMError=dedrm_DeDRMError,
        ):
          with self.dedrm_imports():
            kindlekey = self.import_name('kindlekey')
            ##kindlekey = self.import_name('kindlekey', package=__package__)
            # monkey patch the kindlekey.kindlekeys function
            if not hasattr(self, 'base_kindlekeys'):
              # we only do this once!
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
            with self.sqltags:
              with stackattrs(
                  self,
                  tags=self.sqltags.subdomain(f'{self.DEDRM_PACKAGE_NAME}'),
              ):
                yield

  @pfx_method
  def prepare_dedrm_shims(self, libdirpath):
    ''' Create `__version` and `prefs` stub modules in `libdirpath`
        to support the `import_name` method.

        The stub modules to convince the plugin to run by providing
        some things normally provided by the Calibre plugin
        environment.
    '''

    def write_module(name, contents):
      ''' Write Python code `contents` to a top level module named `name`.
      '''
      module_path = joinpath(libdirpath, f'{name}.py')
      vprint("DeDRM: write module", module_path)
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
            file=pyf,
        )
        print(stripped_dedent(contents), file=pyf)
      ##os.system(f'set -x; cat {module_path}')

    # present the dedrm package as DEDRM_PACKAGE_NAME ('dedrm')
    dedrm_pkg_path = joinpath(libdirpath, self.DEDRM_PACKAGE_NAME)
    if isdirpath(self.fspath):
      # symlink to the source tree
      pfx_call(os.symlink, self.fspath, dedrm_pkg_path)
    else:
      # unpack the plugin zip file
      vprint("unpack", self.fspath)
      pfx_call(os.mkdir, dedrm_pkg_path)
      with Pfx("unzip %r", self.fspath):
        with ZipFile(self.fspath) as zipf:
          for member in zipf.namelist():
            if member.endswith('/'):
              vprint("skip", member)
              continue
            with Pfx(member):
              validate_rpath(member)
              path = joinpath(dedrm_pkg_path, member)
              pathdir = dirname(path)
              needdir(pathdir)
              pfx_call(zipf.extract, member, dedrm_pkg_path)
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
        from cs.gimmicks import debug
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
            debug(
                "JSONConfig: replacing file_path=%r with %r",
                self.file_path,
                os.devnull
            )
            self.file_path = os.devnull
        {self.DEDRM_PACKAGE_NAME}.prefs.JSONConfig = JSONConfig
        ''',
    )

  @contextmanager
  def dedrm_imports(self):
    ''' A context manager to run some code with `self.shimlib_dirpath`
        and {self.shimlib_dirpath}/{self.DEDRM_PACKAGE_NAME}
        prepended to `sys.path` for import purposes.

        This lets us import our generated stub modules and the DeDRM packages.
    '''
    with stackattrs(
        sys,
        path=[
            # shims first
            self.shimlib_dirpath,
            # then the dedrm package itself, since it directly imports
            # with absolute names like "kindlekeys"
            joinpath(self.shimlib_dirpath, self.DEDRM_PACKAGE_NAME),
        ] + sys.path + [
            # dedrm/standalone/__init__.py self inserts its directory
            # at the start of the path if it isn't already somewhere
            # in sys.path (this is bad)
            # https://github.com/noDRM/DeDRM_tools/issues/653
            joinpath(self.shimlib_dirpath, self.DEDRM_PACKAGE_NAME,
                     'standalone'),
        ],
    ):
      # the DeDRM code is amazingly noisy to stdout
      # so we intercept print and redirect stdout to stderr
      # pylint: disable=import-outside-toplevel
      import builtins
      with stackattrs(builtins, print=vprint):
        with redirect_stdout(sys.stderr):
          # pylint: disable=import-outside-toplevel
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
    with self:  # just in case the caller hasn't done this
      with self.dedrm_imports():
        M = pfx_call(importlib.import_module, module_name, package=package)
        if name is None:
          return M
        return pfx_call(getattr, M, name)

  @uses_fstags
  @pfx_method
  def decrypt(
      self,
      srcpath,
      dstpath,
      *,
      fstags: FSTags,
      booktype=None,
      exists_ok=False,
      obok_lib=None,
  ):
    ''' Remove the DRM from `srcpath`, writing the resulting file to `dstpath`.
        Return `True` if `srcpath` was decrypted into `dstpath`.
        Return `False` if `srcpath` was not encrypted; `dstpath` will not be created.

        A file may be decrypted in place by supplying the same path
        for `srcpath` and `dstpath` and `exists_ok=True`.

        Parameters:
        * `booktype`: specify the book type of `srcpath`
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

        # have T.close() just flush the file content to the filesystem
        with stackattrs(T, close=lambda: T.flush()):
          # monkey patch temporary_file method to return tmpfilename
          def dedrem_temporaryfile(fext):
            return T

          with stackattrs(dedrm, temporary_file=dedrem_temporaryfile):
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
                vprint("srcpath is already decrypted")
                pfx_call(os.remove, T.name)
                return False
              if file_checksum(srcpath) == file_checksum(decrypted_ebook):
                vprint("srcpath content is unchanged by decryption")
                pfx_call(os.remove, T.name)
                return False
    # copy tags from the srcpath to the dstpath
    fstags[dstpath].update(fstags[srcpath])
    return True

  @contextmanager
  def decrypted(self, srcpath, **decrypt_kw):
    ''' A context manager to produce a deDRMed copy of `srcpath`,
        yielding the temporary file containing the copy;
        if `srcpath` is not decrypted this context manager yields `None`.
        Keyword arguments are passed through to `DeDRMWrapper.decrypt`.
    '''
    srcext = splitext(basename(srcpath))[1]
    with NamedTemporaryFile(
        prefix=f'.{self.__class__.__name__}-',
        suffix=srcext,
    ) as T:
      yield (
          T.name if
          self.decrypt(srcpath, T.name, exists_ok=True, **decrypt_kw) else None
      )

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
  #
  # Note that this started crashing with:
  #   WARNING: /Users/cameron/tmp/venv--css-ebooks--apple.x86_64.darwin/bin/python3 is loading libcrypto in an unsafe way
  #   [1]    85172 abort      env-dev python3 -m cs.ebooks kindle
  # resolved by:
  #   CSS[~/hg/css-ebooks(hg:ebooks)]fleet2*> cd /usr/local/lib
  #   [/usr/local/lib]fleet2*> ln -s ~/var/homebrew/lib/libcrypto.* .
  #   + exec ln -s /Users/cameron/var/homebrew/lib/libcrypto.3.dylib /Users/cameron/var/homebrew/lib/libcrypto.a /Users/cameron/var/homebrew/lib/libcrypto.dylib .
  #
  def _load_crypto_libcrypto():
    from ctypes import (
        CDLL, POINTER, c_char_p, c_int, c_long, Structure, c_ulong,
        create_string_buffer
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

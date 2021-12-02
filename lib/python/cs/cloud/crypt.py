#!/usr/bin/env python3

''' An implementation of the encryption scheme outlined by BackBlaze here:

        https://github.com/Backblaze-B2-Samples/encryption

    The essential aspects of this scheme,
    which were not apparent to me until I had read the above page a few times,
    are:
    * there is a global public/private keypair
    * each file has its own symmetric encryption key
    * the uploaded content is encrypted using the per file key
    * the per file key is encrypted with the global private key and also uploaded
    This has the feature that a comprimised global keypair
    can be replaced by fetching all the per file keys,
    decrypting them against the comprimised key,
    encrypting it with the new key,
    and uploaded the reencrypted per file key.
    The (bulky) file itself does not need reprocessing.

    This implementation outsources the crypto to the openssl command.
'''

from mmap import mmap
import os
from os.path import join as joinpath
from subprocess import Popen, DEVNULL, PIPE
import sys
from tempfile import NamedTemporaryFile
from uuid import uuid4
from typeguard import typechecked
from cs.buffer import CornuCopyBuffer
from cs.fileutils import datafrom_fd
from cs.pfx import Pfx
from . import validate_subpath, Cloud, CloudArea

# used when creating RSA keypairs
DEFAULT_RSA_ALGORITHM = 'aes256'
DEFAULT_RSA_KEYSIZE = 2048

def openssl(
    openssl_args,
    **kw,
):
  ''' Construct a `subprocess.Popen` instance to run `openssl`.
      Note that this actually dispatches the external `openssl` command
      but does not `Popen.wait()` for it.

      Parameters:
      * `openssl_args`: the command line arguments
        to follow the `'openssl'` command itself;
        the first argument must be an `openssl` command name
      * `stdin`: defaults to `subprocess.DEVNULL` to avoid accidents;
        if an `int` or something with a `.read` attribute
        it is passed unchanged;
        if a `bytes` the bytes are delivered on standard input;
        if a `str` it is taken as a filename to attach to stdin;
        otherwise it is presumed to be some iterable of `bytes`
        and presented as a file descriptor driven by a `CornuCopyBuffer`.
      * `stdout`:
        if a `str` it is taken as a filename to attach as stdout;
        otherwise it is passed unchanged
      * `passphrase_option`: default `None`;
        otherwise a tuple of `(passphrase_opt,passphrase)`
        being the command line `'-passin'` or `'-passout'` option
        and a `str` containing a passphrase or password
        which will be supplied to `openssl` via a pipe
      * `pass_fds`: any file descriptors to be passed through
        to `subprocess.Popen`;
      All other keyword arguments are passed to `subprocess.Popen`.
  '''
  # if stdin is a str, presume it is a filename
  stdin = kw.pop('stdin', DEVNULL)
  if isinstance(stdin, str):
    with open(stdin, 'rb') as f:
      return openssl(openssl_args, stdin=f.fileno(), **kw)
  # if stdout is a str, presume it is a filename
  stdout = kw.pop('stdout', None)
  if isinstance(stdout, str):
    # presume a filename, open it for write
    with open(stdout, 'wb') as f:
      return openssl(openssl_args, stdin=stdin, stdout=f.fileno(), **kw)
  arg1, *argv = list(openssl_args)
  assert arg1 and not arg1.startswith('-')
  passphrase_option = kw.pop('passphrase_option', None)
  pass_fds = set(kw.pop('pass_fds', None) or ())
  close_my_fds = []
  if isinstance(stdin, int):
    # ints are file descriptors or the usual subprocess.Popen values
    # things with read() can be uses as files
    pass
  elif isinstance(stdin, (bytes, bytearray, memoryview, mmap)):
    # a bytes like object
    stdin = CornuCopyBuffer([stdin]).as_fd()
    close_my_fds.append(stdin)
  elif isinstance(stdin, CornuCopyBuffer):
    # a buffer
    stdin = stdin.as_fd()
    close_my_fds.append(stdin)
  elif hasattr(stdin, 'read'):
    # presume a file
    pass
  else:
    # presume some iterable of bytes
    stdin = CornuCopyBuffer(stdin).as_fd()
    close_my_fds.append(stdin)
  if passphrase_option is not None:
    # insert the passphrase command line option
    passphrase_opt, passphrase = passphrase_option
    assert passphrase_opt.startswith('-pass')
    assert '\n' not in passphrase
    # stash the passphrase in a pipe
    passphrase_fd = CornuCopyBuffer([(passphrase + '\n').encode()]).as_fd()
    pass_fds.add(passphrase_fd)
    close_my_fds.append(passphrase_fd)
    argv = [passphrase_opt, f"fd:{passphrase_fd}"] + argv
  argv = ['openssl', arg1] + argv
  ##warning('+ %r', argv)
  P = Popen(argv, stdin=stdin, stdout=stdout, pass_fds=pass_fds, **kw)
  for fd in close_my_fds:
    os.close(fd)
  return P

def run_openssl(openssl_args, stdout=None, **openssl_kwargs):
  ''' Construct a `subprocess.Popen` instance to run `openssl`
      via the `openssl()` function.
      Wait for the subprocess to complete.
      Return the output bytes if `stdout` is `bytes`,
      otherwise `None`.
      Raises `ValueError` if the exit code is not `0`.

      The `stdout` parameter accepts some special values
      in addition to those for `openssl()`:
      * if set to the type `bytes`
        then the command output is collected and returned as a `bytes` instance
      * if set to a callable
        then the command output is passed to the callable as received,
        as `bytes` instances,
        and a final call made with `None` to indicate end of data
      Other keyword parameters are passed unchanged to `openssl()`.
  '''
  special_stdout = None
  if stdout is bytes or callable(stdout):
    special_stdout = stdout
    stdout = PIPE
  P = openssl(openssl_args, stdout=stdout, **openssl_kwargs)
  result = None
  if special_stdout is not None:
    if special_stdout is bytes:
      result = P.stdout.read()
    elif callable(special_stdout):
      copy_output = special_stdout
      for bs in datafrom_fd(P.stdout.file.fileno()):
        copy_output(bs)
      copy_output(None)
  exitcode = P.wait()
  if exitcode != 0:
    raise ValueError("openssl failed: %r => %s" % (openssl_args, exitcode))
  return result

def create_key_pair(dirpath: str, passphrase: str):
  ''' Generate and save a public/private keypair into the directory `dirpath`
      as the files *uuid*`.private.pem` and *uuid*`.public.pem`
      where *uuid* is a newly generated UUID.

      Return `(uuid,private_path,public_path)`
      where `uuid` is a `uuid.UUID` instance.
  '''
  uuid = uuid4()
  uuid_s = str(uuid)
  private_path = joinpath(dirpath, uuid_s + '.private.pem')
  public_path = joinpath(dirpath, uuid_s + '.public.pem')
  run_openssl(
      [
          'genrsa', '-' + DEFAULT_RSA_ALGORITHM, '-out', private_path,
          str(DEFAULT_RSA_KEYSIZE)
      ],
      passphrase_option=('-passout', passphrase),
      stderr=DEVNULL,
  )
  run_openssl(
      ['rsa', '-in', private_path, '-pubout', '-out', public_path],
      passphrase_option=('-passin', passphrase),
      stderr=DEVNULL,
  )
  return uuid, private_path, public_path

@typechecked
def symencrypt(
    stdin,
    password,
    stdout=None,
    *,
    ciphername=None,
    **openssl_kwargs
) -> Popen:
  ''' Symmetricly encrypt `stdin` to `stdout`
      using the supplied `password` and the symmetic cipher `ciphername`.
      Return the `Popen` object of the encrypting `openssl` command.

      Parameters:
      * `stdin`: any value suitable for `openssl()`'s `stdin` parameter
      * `stdout`: any value suitable for `openssl()`'s `stdout` parameter
      * `ciphername`: a cipher name suitable for `openssl`'s `enc` command
      Other keyword arguments are passed to `openssl()`
      and its resulting `Popen` returned.
  '''
  if ciphername is None:
    ciphername = 'aes-256-cbc'
  return openssl(
      ['enc', '-' + ciphername, '-e', '-salt'],
      passphrase_option=('-pass', password),
      stdin=stdin,
      stdout=stdout,
      **openssl_kwargs
  )

@typechecked
def symdecrypt(
    stdin,
    password,
    stdout=None,
    *,
    ciphername=None,
    **openssl_kwargs
) -> Popen:
  ''' Symmetricly decrypt `stdin`
      using the supplied `password` and the symmetic cipher `ciphername`.
      Return the `Popen` object of the decrypting `openssl` command.

      Parameters:
      * `stdin`: any value suitable for `openssl()`'s `stdin` parameter
      * `stdout`: any value suitable for `openssl()`'s `stdout` parameter
      * `ciphername`: a cipher name suitable for `openssl`'s `enc` command
      Other keyword arguments are passed to `openssl()`
      and its resulting `Popen` returned.
  '''
  if ciphername is None:
    ciphername = 'aes-256-cbc'
  return openssl(
      ['enc', '-' + ciphername, '-d'],
      passphrase_option=('-pass', password),
      stdin=stdin,
      stdout=stdout,
      **openssl_kwargs
  )

def new_passtext(public_path=None):
  ''' Generate a new password text
      for use with an symmetric cipher.
      If `public_path` is not `None`,
      also generate the encryption of the password
      using the public key stored in the file `public_path`.
      Return `(per_file_passtext,per_file_passtext_enc)`.
  '''
  # generate per file random password text
  per_file_passtext = run_openssl(['rand', '-base64', '180'],
                                  stdout=bytes).decode().replace('\n', '')
  if public_path is None:
    per_file_passtext_enc = None
  else:
    # encrypt the password using the public key
    per_file_passtext_enc = encrypt_passtext(per_file_passtext, public_path)
  return per_file_passtext, per_file_passtext_enc

def encrypt_passtext(passtext: str, public_path: str) -> bytes:
  ''' Encrypt the `passtext` using the public key stored at `public_path`.
        Return the bytes comprising the encryption.
    '''
  passtext_enc = run_openssl(
      ['rsautl', '-encrypt', '-pubin', '-inkey', public_path],
      stdin=passtext.encode(),
      stdout=bytes,
  )
  return passtext_enc

def decrypt_password(per_file_passtext_enc, private_path, passphrase):
  ''' Decrypt the encrypted per file password `per_file_passtext_enc`
      using the private key stored in `private_path` and the `passphrase`.
      Return the decrypted password.
  '''
  per_file_passtext = run_openssl(
      ['rsautl', '-decrypt', '-inkey', private_path],
      passphrase_option=('-passin', passphrase),
      stdin=per_file_passtext_enc,
      stdout=bytes
  ).decode()
  return per_file_passtext

def recrypt_passtext(
    cloud, bucket_name, basepath, *, old_key_name, old_private_path,
    old_passphrase, new_key_name, new_public_path
):
  ''' Add another encryption of a passtext:
      Fetch an uploaded encrypted passtext and decrypt with the private key
      stored at `old_private_path` and the passphrase `old_passphrase`.
      Encrypt the passtext with the public key named `new_key_name`
      stored at `new_public_path` and upload.

      Parameters:
      * `cloud`: the `Cloud` instance to fetch and store the passtext
      * `bucket_name`: the bucket within the cloud
      * `basepath`: the basis for the paths within the bucket
      * `old_key_name`: the name of the existing key
      * `old_private_path`: the path to the private key for decryption
      * `old_passphrase`: the passphase to use with `old_private_path`
      * `new_key_name`: the name of the new key
      * `new_public_path`: the path to the public key for encryption

      This supports providing a new encrypted passtext for an already
      uploaded file.
  '''
  _, old_key_subpath = upload_paths(basepath, public_key_name=old_key_name)
  # fetch and decrypt the existing passtext
  passtext = download_passtext(
      cloud,
      bucket_name,
      old_key_subpath,
      private_path=old_private_path,
      passphrase=old_passphrase
  )
  # encrypt with our key and reupload
  passtext_enc = encrypt_passtext(passtext, new_public_path)
  _, new_key_subpath = upload_paths(basepath, public_key_name=new_key_name)
  cloud.upload_bytes(
      passtext_enc,
      bucket_name=bucket_name,
      path=new_key_subpath,
  )

def pubencrypt_popen(stdin, public_path, stdout=PIPE):
  ''' Encrypt the `stdin` to `stdout`
      using the public key from `public_path`,
      return `(per_file_passtext_enc,Popen)`.

      Parameters:
      * `stdin`: any value suitable for `openssl()`'s `stdin` parameter
      * `public_path`: the name of a file containing a public key
      * `stdout`: any value suitable for `openssl()`'s `stdout` parameter

      The encryption is as per the scheme from:

          https://github.com/Backblaze-B2-Samples/encryption

      A per file password is generated
      and used to symmetrically encrypt the file contents.
      The per file password is encrypted using `public_path`.

      The returned `(per_file_passtext_enc,Popen)`
      comprise the encrypted password (a `bytes` instance)
      and the `subprocess.Popen` instance for the `openssl` command
      running the symmetric cipher.
      The encrypted file data are available
      as the filelike object `Popen.stdout`.
  '''
  # generate per file random password text
  per_file_passtext, per_file_passtext_enc = new_passtext(public_path)
  # dispatch an openssl command to encrypt the contents of filename
  # using the per file password
  P = symencrypt(stdin, per_file_passtext, stdout)
  return per_file_passtext_enc, P

def pubdecrypt_popen(
    stdin,
    per_file_passtext_enc: bytes,
    private_path: str,
    passphrase: str,
    stdout=PIPE
):
  ''' Decrypt `stdin` to `stdout`
      with the per file encrypted password `per_file_passtext_enc`,
      the private key from the file `private_path`
      and its associated `passphrase`.
      Return the `Popen` object of the decrypting `openssl` command.

      Parameters:
      * `stdin`: any value suitable for `openssl()`'s `stdin` parameter
      * `public_path`: the name of a file containing a public key
      * `stdout`: any value suitable for `openssl()`'s `stdout` parameter

      The encryption is as per the scheme from:

          https://github.com/Backblaze-B2-Samples/encryption

      The returned `(per_file_passtext_enc,Popen)`
      comprise the encrypted password (a `bytes` instance)
      and the `subprocess.Popen` instance for the `openssl` command
      running the symmetric cipher.
      The encrypted file data are available
      as the filelike object `Popen.stdout`.
  '''
  # decrypt the encrypted per file password
  per_file_passtext = decrypt_password(
      per_file_passtext_enc, private_path, passphrase
  )
  # dispatch an openssl command to decrypt the contents of the source
  # using the per file password
  return symdecrypt(stdin, per_file_passtext, stdout)

def upload_paths(basepath: str, public_key_name=None):
  ''' Return the paths for the encrypted data and per-file private
      key components derived from `basepath`.
  '''
  assert public_key_name is None or '/' not in public_key_name
  data_subpath = basepath + '.data.enc'
  key_subpath = basepath + (
      f'.key-{public_key_name}.enc' if public_key_name else '.key.enc'
  )
  return data_subpath, key_subpath

# pylint: disable=too-many-locals
@typechecked
def upload(
    stdin,
    cloud: Cloud,
    bucket_name: str,
    basepath: str,
    *,
    public_path: str,
    public_key_name=None,
    upload_progress=None,
    overwrite: bool = False,
):
  ''' Upload `stdin` to `cloud` in bucket `bucket_name` at path `basepath`
      using the public key from the file named `public_path`.
      Return `(upload_result,data_subpath,key_subpath)`
      being the upload result and the created cloud paths
      (computed from `basepath`).

      Parameters:
      * `stdin`: any value suitable for `openssl()`'s `stdin` parameter
      * `cloud`: the `Cloud` instance to store the data
      * `bucket_name`: the bucket within the cloud
      * `basepath`: the basis for the paths within the bucket
      * `public_path`: the name of a file containing a public key
      * `public_key_name`: an optional name for the public key
        used to encrypt the per file key
      * `upload_progress`: optional `cs.progress.Progress` instance
        to which to report upload data
      * `overwrite`: optional flag, default `False`;
        if true, do the upload even if the content file and key file exist

      This stores the symmetrically encrypted `stdin`
      at the bucket path `basepath+'.data.enc'`
      and the per file key at the bucket path `basepath+'.key.enc'`
      (or `basepath+'.key-`*public_key_name*`.enc'
      if `public_key_name` was specified).
      The upload result is that for the `'.data.enc'` upload.
  '''
  validate_subpath(basepath)
  data_subpath, key_subpath = upload_paths(
      basepath, public_key_name=public_key_name
  )
  if not overwrite:
    # if already uploaded then return quickly
    file_info = cloud.stat(bucket_name=bucket_name, path=data_subpath)
    if file_info and cloud.stat(bucket_name=bucket_name, path=key_subpath):
      # already exists, skip the upload
      return file_info, data_subpath, key_subpath
  # Encrypt directly into an upload file.
  # See if the cloud has a preferred location for upload files.
  cloud_tmpdir = cloud.tmpdir_for(bucket_name=bucket_name, path=data_subpath)
  # Create the scratch file in the preferred location (if specified).
  with NamedTemporaryFile(dir=cloud_tmpdir, suffix="enc-for-upload.dat") as T:
    with Pfx("open(%r,'wb')", T.name):
      with open(T.name, 'wb') as f:
        per_file_passtext_enc, P = pubencrypt_popen(
            stdin, public_path, stdout=f
        )
      retcode = P.wait()
      if retcode != 0:
        raise ValueError(
            "openssl %r returns exit code %s" % (
                P.args,
                retcode,
            )
        )
    # Upload directly from the file,
    # passing as_is=True so that the FSCloud implementation
    # knows it may try a hard link.
    upload_result = cloud.upload_filename(
        T.name,
        bucket_name=bucket_name,
        path=data_subpath,
        upload_progress=upload_progress,
        as_is=True,
    )
    retcode = P.wait()
    if retcode != 0:
      raise ValueError("openssl %r returns exit code %s" % (
          P.args,
          retcode,
      ))
  cloud.upload_bytes(
      per_file_passtext_enc,
      bucket_name=bucket_name,
      path=key_subpath,
      upload_progress=upload_progress,
  )
  return upload_result, data_subpath, key_subpath

def download_passtext(
    cloud,
    bucket_name,
    passtext_path,
    *,
    private_path,
    passphrase,
    download_progress=None
):
  ''' Download the encrypted passtext for use with another encrypted file,
      usually the per-file passtext for an encrypted upload.
      Decrypt the download with the private key located at `private_path`
      and `passphrase`.
      Return the decrypted passtext.
  '''
  bfr, _ = cloud.download_buffer(
      bucket_name=bucket_name,
      path=passtext_path,
      download_progress=download_progress
  )
  per_file_passtext_enc = b''.join(bfr)
  bfr.close()
  per_file_passtext = decrypt_password(
      per_file_passtext_enc, private_path, passphrase
  )
  return per_file_passtext

@typechecked
def download(
    cloud,
    bucket_name,
    basepath,
    *,
    private_path,
    passphrase: str,
    public_key_name=None,
    download_progress=None,
    stdout=PIPE,
):
  ''' Download from `cloud` in bucket `bucket_name` at path `basepath`
      using the private key from the file named `private_path`
      and the `passphrase`.
      Return the `Popen` instance.

      Parameters:
      * `cloud`: the `Cloud` instance to store the data
      * `bucket_name`: the bucket within the cloud
      * `basepath`: the basis for the paths within the bucket
      * `private_path`: the name of a file containing a private key
        corresponding to the public key used during the upload
      * `passphrase`: the passphrase for use with the private key
      * `public_key_name`: an optional name for the public key
        used to encrypt the per file key
        during the upload

      This fetches the encrypted data
      from the bucket path `basepath+'.data.enc'`
      and the per file key from the bucket path `basepath+'.key.enc'`
      (or `basepath+'.key-`*public_key_name*`.enc'
      if `public_key_name` was specified).
  '''
  validate_subpath(basepath)
  assert public_key_name is None or '/' not in public_key_name
  data_subpath, key_subpath = upload_paths(
      basepath, public_key_name=public_key_name
  )
  per_file_passtext = download_passtext(
      cloud,
      bucket_name,
      key_subpath,
      private_path=private_path,
      passphrase=passphrase,
      download_progress=download_progress
  )
  bfr, _ = cloud.download_buffer(
      bucket_name=bucket_name,
      path=data_subpath,
      download_progress=download_progress
  )
  return symdecrypt(bfr, per_file_passtext, stdout=stdout)

# pylint: disable=unused-argument,too-many-locals
def main(argv):
  ''' Main command line: test stuff.
  '''
  # pylint: disable=import-outside-toplevel
  from cs.logutils import setup_logging
  from cs.upd import print  # pylint: disable=redefined-builtin
  setup_logging(argv[0])
  cloud_area = CloudArea.from_cloudpath(os.environ['CS_CLOUD_AREA'])
  CAF = cloud_area[__file__.lstrip('/')]
  print("upload %r => %s" % (__file__, CAF))
  passphrase = 'ss'  # passphrase = input("Passphrase: ")
  uuid, private_path, public_path = create_key_pair('.', passphrase)
  public_key_name = str(uuid)
  print(uuid)
  print(private_path)
  print(public_path)
  upload_result, data_subpath, key_subpath = upload(
      __file__,
      CAF.cloud,
      CAF.bucket_name,
      CAF.bucket_path,
      public_path=public_path,
      public_key_name=public_key_name,
  )
  print("upload result = %r" % (upload_result,))
  P = download(
      CAF.cloud,
      CAF.bucket_name,
      CAF.bucket_path,
      private_path=private_path,
      passphrase=passphrase,
      public_key_name=public_key_name,
  )
  print(P.stdout.read().decode())
  return
  # pylint: disable=unreachable
  #################################
  per_file_passtext_enc, P = pubencrypt_popen(__file__, public_path)
  encrypted_bytes = P.stdout.read()
  print("openssl exit code =", P.wait())
  print("per_file_passtext_enc=", repr(per_file_passtext_enc))
  per_file_passtext = decrypt_password(
      per_file_passtext_enc, private_path, passphrase
  )
  print("decrypted passtext =>", repr(per_file_passtext))
  encrypted_size = len(encrypted_bytes)
  print(encrypted_size, "bytes of encrypted data")
  P = pubdecrypt_popen(
      encrypted_bytes, per_file_passtext_enc, private_path, passphrase
  )
  decrypted_bytes = P.stdout.read()
  print("openssl exit code =", P.wait())
  print(repr(decrypted_bytes))
  print(decrypted_bytes.decode())

if __name__ == '__main__':
  sys.exit(main(sys.argv))

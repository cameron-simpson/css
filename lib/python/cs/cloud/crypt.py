#!/usr/bin/env python3

''' An implementation of the encryption scheme outlined by BackBlaze here:

        https://github.com/Backblaze-B2-Samples/encryption

    The essential aspects of this scheme,
    which were not apparent to me until I had read the above page a few time,
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

import os
from os.path import join as joinpath
from subprocess import Popen, DEVNULL
import sys
from uuid import uuid4

# used when creating RSA keypairs
DEFAULT_RSA__ALGORITHM = 'aes256'
DEFAULT_RSA_KEYSIZE = 2048

def openssl(
    openssl_args,
    **kw,
):
  ''' Construct a `subprocess.Popen` instance to run `openssl` and return it.
      Note that this actually dispatches the external `openssl` command.

      Parameters:
      * `openssl_args`: the command line arguments
        to follow the `'openssl'` command itself;
        the first argument must be an openssl command name
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
  if isinstance(stdin, int) or hasattr(stdin, 'read'):
    # ints are file descriptors or the usual subprocess.Popen values
    # things with read() can be uses as files
    pass
  elif isinstance(stdin, (bytes, bytearray, memoryview)):
    # a bytes like object
    stdin = CornuCopyBuffer([stdin]).as_fd()
    close_my_fds.append(stdin)
  elif isinstance(stdin, CornuCopyBuffer):
    # a buffer
    stdin = stdin.as_fd()
    close_my_fds.append(stdin)
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
  print('+', repr(argv), file=sys.stderr)
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

def create_key_pair(dirpath, passphrase):
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
          'genrsa', '-' + DEFAULT_RSA__ALGORITHM, '-out', private_path,
          str(DEFAULT_RSA_KEYSIZE)
      ],
      passphrase_option=('-passout', passphrase),
  )
  run_openssl(
      ['rsa', '-in', private_path, '-pubout', '-out', public_path],
      passphrase_option=('-passin', passphrase),
  )
  return uuid, private_path, public_path

def symencrypt(
    stdin, password, stdout=None, *, ciphername=None, **openssl_kwargs
):
  ''' Symmetricly encrypt `stdin` to `stdout`
      using the supplied `password` and the symmetic cipher `ciphername`.

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

# pylint: disable=unused-argument
def main(argv):
  ''' Main command line: test stuff.
  '''
  passphrase = input("Passphrase: ")
  uuid, private_path, public_path = create_key_pair('.', passphrase)
  print(uuid)
  print(private_path)
  print(public_path)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

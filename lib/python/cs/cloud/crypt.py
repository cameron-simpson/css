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

OPENSSL_ALGORITHM = 'aes256'
OPENSSL_KEYSIZE = 2048

def run_openssl_filter(
    openssl_args,
    stdin=DEVNULL,
    passphrase_option=None,
    pass_fds=(),
    **popen_kwargs
):
  ''' Run openssl as a filter.

      Note that this presumes that the inputs and outputs are separately supplied,
      for example from files,
      and this function only calls `Popen.wait()`.
      In particular, the `stdin` argument defaults to `subprocess.DEVNULL`.
  '''
  arg1, *argv = list(openssl_args)
  assert arg1 and not arg1.startswith('-')
  if passphrase_option is not None:
    passphrase_opt, passphrase = passphrase_option
    assert passphrase_opt.startswith('-pass')
    assert '\n' not in passphrase
    # stash the passphrase in a pipe
    rfd, wfd = os.pipe()
    os.write(wfd, (passphrase + '\n').encode())
    os.close(wfd)
    pass_fds = set(pass_fds or ())
    pass_fds.add(rfd)
    argv = [passphrase_opt, f"fd:{rfd}"] + argv
  argv = ['openssl', arg1] + argv
  print('+', repr(argv), file=sys.stderr)
  P = Popen(argv, stdin=stdin, pass_fds=pass_fds, **popen_kwargs)
  if passphrase is not None:
    os.system(f"lsof -p {rfd}")
    os.close(rfd)
  exitcode = P.wait()
  if exitcode != 0:
    raise ValueError("openssl failed: %r => %s" % (argv, exitcode))

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
  run_openssl_filter(
      [
          'genrsa', '-' + OPENSSL_ALGORITHM, '-out', private_path,
          str(OPENSSL_KEYSIZE)
      ],
      passphrase_option=('-passout', passphrase),
  )
  run_openssl_filter(
      ['rsa', '-in', private_path, '-pubout', '-out', public_path],
      passphrase_option=('-passin', passphrase),
  )
  return uuid, private_path, public_path

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

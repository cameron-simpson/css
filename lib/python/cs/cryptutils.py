#!/usr/bin/python
#
# Trite convenience routines to do with cryptography.
#

import crypt
import random

UNIX_SALT_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/.'

def unixcrypt(password, salt=None):
  if salt is None:
    salt = random.randint(0,4095)
    salt = ( UNIX_SALT_CHARS[ salt % 64 ]
           + UNIX_SALT_CHARS[ (salt // 64) % 64 ]
           )
  return crypt.crypt(password, salt)

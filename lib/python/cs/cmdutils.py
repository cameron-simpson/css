#!/usr/bin/python
#
# Convenience functions for working with external commands.
#   - Cameron Simpson <cs@zip.com.au> 03sep2015
#

from __future__ import print_function, absolute_import
import os
import sys
import io
import subprocess

def run(argv, trace=False):
  ''' Run a command. Optionally trace invocation. Return result of subprocess.call.
      `argv`: the command argument list
      `trace`: Default False. If True, recite invocation to stderr.
        Otherwise presume a stream to which to recite the invocation.

  '''
  if trace:
    tracefp = sys.stderr if trace is True else trace
    pargv = ['+'] + argv
    print(*pargv, file=tracefp)
  return subprocess.call(argv)

def pipefrom(argv, trace=False, binary=False, keep_stdin=False, **kw):
  ''' Pipe text from a command. Optionally trace invocation. Return the Popen object with .stdout decoded as text.
      `argv`: the command argument list
      `binary`: if true (default false) return the binary stdout instead of a text wrapper
      `trace`: Default False. If True, recite invocation to stderr.
        Otherwise presume a stream to which to recite the invocation.
      The command's stdin is attached to the null device.
      Other keyword arguments are passed to io.TextIOWrapper.
  '''
  if trace:
    tracefp = sys.stderr if trace is True else trace
    pargv = ['+'] + list(argv) + ['|']
    print(*pargv, file=tracefp)
  popen_kw = {}
  if not keep_stdin:
    sp_devnull = getattr(subprocess, 'DEVNULL', None)
    if sp_devnull is None:
      devnull = open(os.devnull, 'wb')
    else:
      devnull = sp_devnull
    popen_kw['stdin'] = devnull
  P = subprocess.Popen(argv, stdout=subprocess.PIPE, **popen_kw)
  if binary:
    if kw:
      raise ValueError("binary mode: extra keyword arguments not supported: %r", kw)
  else:
    P.stdout = io.TextIOWrapper(P.stdout, **kw)
  if not keep_stdin and sp_devnull is None:
    devnull.close()
  return P

def pipeto(argv, trace=False, **kw):
  ''' Pipe text to a command. Optionally trace invocation. Return the Popen object with .stdin encoded as text.
      `argv`: the command argument list
      `trace`: Default False. If True, recite invocation to stderr.
        Otherwise presume a stream to which to recite the invocation.
      Other keyword arguments are passed to io.TextIOWrapper.
  '''
  if trace:
    tracefp = sys.stderr if trace is True else trace
    pargv = ['+', '|'] + argv
    print(*pargv, file=tracefp)
  P = subprocess.Popen(argv, stdin=subprocess.PIPE)
  P.stdin = io.TextIOWrapper(P.stdin, **kw)
  return P

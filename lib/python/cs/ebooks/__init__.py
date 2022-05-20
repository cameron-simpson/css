#!/usr/bin/env python3

''' Utilities and command line for working with EBooks.
    Basic support for talking to Apple Books, Calibre, Kindle, Mobi.

    These form the basis of my personal Kindle and Calibre workflow.
'''

from builtins import print as builtin_print
import shlex
from subprocess import run as subprocess_run

from cs.logutils import warning
from cs.pfx import pfx_call
from cs.psutils import print_argv
from cs.upd import Upd, print

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.app.oxs.plist',
        'cs.cmdutils',
        'cs.context',
        'cs.deco',
        'cs.fileutils',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.obj',
        'cs.pfx',
        'cs.resources',
        'cs.sqlalchemy_utils',
        'cs.tagset',
        'cs.threads',
        'cs.units',
        'mobi',
    ],
}

# TODO: merge into cs.psutils
def run(argv, doit=True, quiet=False, **subp_options):
  ''' Run a command via `subprocess.run`.
      Return the `CompletedProcess` result or `None` if `doit` is false.

      Parameters:
      * `argv`: the command line to run
      * `doit`: optional flag, default `True`;
        if false do not run the command and return `None`
      * `quiet`: default `False`; if true, do not print the command or its output
      * `subp_options`: optional mapping of keyword arguments
        to pass to `subprocess.run`
  '''
  if not doit:
    if not quiet:
      with Upd().above():
        print_argv(*argv, fold=True)
    return None
  with Upd().above():
    quiet or print_argv(*argv)
    cp = pfx_call(subprocess_run, argv, **subp_options)
    if cp.stdout and not quiet:
      builtin_print(" ", cp.stdout.rstrip().replace("\n", "\n  "))
    if cp.stderr:
      builtin_print(" stderr:")
      builtin_print(" ", cp.stderr.rstrip().replace("\n", "\n  "))
  if cp.returncode != 0:
    warning(
        "run fails, exit code %s from %s",
        cp.returncode,
        shlex.join(cp.args),
    )
  return cp

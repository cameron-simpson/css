#!/usr/bin/env python3

''' My collection of things for working with Django.
'''

import sys

from django.core.management.base import (
    BaseCommand as DjangoBaseCommand,
    CommandError as DjangoCommandError,
)

from cs.cmdutils import BaseCommand as CSBaseCommand

__version__ = '20241111'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.cmdutils',
        'django',
    ],
}

class BaseCommand(CSBaseCommand, DjangoBaseCommand):
  ''' A drop in class for `django.core.management.base.BaseCommand`
      which subclasses `cs.cmdutils.BaseCommand`.

      This lets me write management commands more easily, particularly
      if there are subcommands.
  '''

  @classmethod
  def run_from_argv(cls, argv):
    ''' Intercept `django.core.management.base.BaseCommand.run_from_argv`.
        Construct an instance of `cs.djutils.DjangoBaseCommand` and run it.
    '''
    _, djcmdname, *argv = argv
    command = cls(argv, cmd=djcmdname)
    return command.run()

  @classmethod
  def handle(cls, *, argv, **options):
    ''' The Django `BaseComand.handle` method.
        This creates another instance for `argv` and runs it.
    '''
    if cls.has_subcommands():
      subcmd = options.pop('subcmd', None)
      if subcmd is not None:
        argv.insert(0, subcmd)
    argv.insert(0, sys.argv[0])
    command = cls(argv, **options)
    xit = command.run()
    if xit:
      raise DjangoCommandError(xit)

  def add_arguments(self, parser):
    ''' Add the `Options.COMMON_OPT_SPECS` to the `argparse` parser.
        This is basicly to support the Django `call_command` function.
    '''
    if self.has_subcommands():
      parser.add_argument('subcmd', nargs='?')
    options = self.options
    _, _, getopt_spec_map = options.getopt_spec_map({})
    for opt, opt_spec in getopt_spec_map.items():
      # known django argument conflicts
      # TODO: can I inspect the parser to detect these?
      if opt in ('-v',):
        continue
      opt_spec.add_argument(parser, options=options)
    parser.add_argument('argv', nargs='*')

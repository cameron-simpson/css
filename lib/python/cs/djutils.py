#!/usr/bin/env python3

''' My collection of things for working with Django.
'''

from inspect import isclass
import sys
from typing import List

from django.core.management.base import (
    BaseCommand as DjangoBaseCommand,
    CommandError as DjangoCommandError,
)
from typeguard import typechecked

from cs.cmdutils import BaseCommand as CSBaseCommand
from cs.lex import cutprefix, stripped_dedent

__version__ = '20241119'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.cmdutils',
        'cs.lex',
        'django',
        'typeguard',
    ],
}

class DjangoSpecificSubCommand(CSBaseCommand.SubCommandClass):
  ''' A subclass of `cs.cmdutils.SubCOmmand` with additional support
      for Django's `BaseCommand`.
  '''

  @property
  def is_pure_django_command(self):
    ''' Whether this subcommand is a pure Django `BaseCommand`. '''
    method = self.method
    return (
        isclass(method) and issubclass(method, DjangoBaseCommand)
        and not issubclass(method, CSBaseCommand)
    )

  @typechecked
  def __call__(self, argv: List[str]):
    ''' Run this `SubCommand` with `argv`.
        This calls Django's `BaseCommand.run_from_argv` for pure Django commands.
    '''
    if not self.is_pure_django_command:
      return super().__call__(argv)
    method = self.method
    instance = method()
    return instance.run_from_argv([method.__module__, self.cmd] + argv)

  def usage_text(self, *, cmd=None, **kw):
    ''' Return the usage text for this subcommand.
    '''
    if not self.is_pure_django_command:
      return super().usage_text(cmd=cmd, **kw)
    method = self.method
    help_text = stripped_dedent(method.help, sub_indent='  ')
    instance = method()
    parser = instance.create_parser("", self.cmd)
    usage = parser.usage or help_text
    usage = cutprefix(cutprefix(usage, 'usage:'), 'Usage:').lstrip()
    return usage

class BaseCommand(CSBaseCommand, DjangoBaseCommand):
  ''' A drop in class for `django.core.management.base.BaseCommand`
      which subclasses `cs.cmdutils.BaseCommand`.

      This lets me write management commands more easily, particularly
      if there are subcommands.

      This is a drop in in the sense that you still make a management command
      in nearly the same way:

          from cs.djutils import BaseCommand

          class Command(BaseCommand):

      and `manage.py` will find it and run it as normal.
      But from that point on the style is as for `cs.cmdutils.BaseCommand`:
      - no `aegparse` setup
      - direct support for subcommands as methods
      - succinct option parsing, if you want command line options

      A simple command looks like this:

          class Command(BaseCommand):

              def main(self, argv):
                  ... do stuff based on the CLI args `argv` ...

      A command with subcommands looks like this:

          class Command(BaseCommand):

              def cmd_this(self, argv):
                  ... do the "this" subcommand ...

              def cmd_that(self, argv):
                  ... do the "that" subcommand ...

      If want some kind of app/client specific "overcommand" composed
      from other management commands you can import them and make
      them subcommands of the overcommand:

          from .other_command import Command as OtherCommand

          class Command(BaseCommand):

              # provide it as the "other" subcommand
              cmd_other = OtherCommand

      Option parsing is inline in the command. `self` comes
      presupplied with a `.options` attribute which is an instance
      of `cs.cmdutils.BaseCommandOptions` (or some subclass).

      Parsing options is simple:

          class Command(BaseCommand):

              def cmd_this(self, argv):
                  options = self.options
                  # parsing options:
                  #
                  # boolean -x option, makes options.x
                  #
                  # --thing-limit n option taking an int
                  # makes options.thing_limit
                  # help text is "Thing limit."
                  #
                  # a --mode foo option taking a string
                  # makes options.mode
                  # help text is "The run mode."
                  options.popopts(
                      argv,
                      x=None,
                      thing_limit_=int,
                      mode_='The run mode.',
                  )
                  ... now consult options.x or whatever
                  ... argv is now the remaining arguments after the options
  '''

  # use our Django specific subclass of CSBaseCommand.SubCommandClass
  SubCommandClass = DjangoSpecificSubCommand

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

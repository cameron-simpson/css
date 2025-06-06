#!/usr/bin/env python3

''' My collection of things for working with Django.

    Presently this provides:
    * `BaseCommand`: a drop in replacement for `django.core.management.base.BaseCommand`
      which uses a `cs.cmdutils.BaseCommand` style of implementation
    * `model_batches_qs`: a generator yielding `QuerySet`s for batches of a `Model`
'''

from dataclasses import dataclass, field
from inspect import isclass
import os
import sys
from typing import Iterable, List, Mapping

from django.conf import settings
from django.core.management.base import (
    BaseCommand as DjangoBaseCommand,
    CommandError as DjangoCommandError,
)
from django.db.models import Model
from django.db.models.query import QuerySet
from django.utils.functional import empty as djf_empty

from typeguard import typechecked

from cs.cmdutils import BaseCommand as CSBaseCommand
from cs.gimmicks import warning
from cs.lex import cutprefix, stripped_dedent

__version__ = '20250606'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.cmdutils',
        'cs.gimmicks',
        'cs.lex',
        'django',
        'typeguard',
    ],
}
if (settings._wrapped is djf_empty
    and not os.environ.get('DJANGO_SETTINGS_MODULE')):
  warning("%s: calling settings.configure()", __name__)
  settings.configure()

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
    return instance.run_from_argv([method.__module__, self.cmd, *argv])

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
      - no `argparse` setup
      - direct support for subcommands as methods
      - succinct option parsing, if you want additional command line options
      - usage text in the subcommand method docstring

      A simple command looks like this:

          class Command(BaseCommand):

              def main(self, argv):
                  """ Usage: {cmd} .......
                        Do the main thing.
                  """
                  ... do stuff based on the CLI args `argv` ...

      A command with subcommands looks like this:

          class Command(BaseCommand):

              def cmd_this(self, argv):
                  """ Usage: {cmd} ......
                        Do this.
                  """
                  ... do the "this" subcommand ...

              def cmd_that(self, argv):
                  """ Usage: {cmd} ......
                        Do that.
                  """
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

      Parsing options is light weight and automatically updates the usage text.
      This example adds command line switches to the default switches:
      - `-x`: a Boolean, setting `self.options.x`
      - `--thing-limit` *n*: an `int`, setting `self.options.thing_limit=`*n*
      - `--mode` *blah*: a string, setting `self.options.mode=`*blah*

      Code sketch:

          from cs.cmdutils import popopts

          class Command(BaseCommand):

              @popopts(
                  x=None,
                  thing_limit_=int,
                  mode_='The run mode.',
              )
              def cmd_this(self, argv):
                  """ Usage: {cmd}
                        Do this thing.
                  """
                  options = self.options
                  ... now consult options.x or whatever
                  ... argv is now the remaining arguments after the options
  '''

  # use our Django specific subclass of CSBaseCommand.SubCommandClass
  SubCommandClass = DjangoSpecificSubCommand

  @dataclass
  class Options(CSBaseCommand.Options):
    settings: type(settings) = field(
        default_factory=lambda: dict(
            (k, getattr(settings, k, None)) for k in sorted(dir(settings)) if
            (k and not k.startswith('_') and k not in ('SECRET_KEY',))
        )
    )

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

def model_batches_qs(
    model: Model,
    field_name='pk',
    *,
    after=None,
    chunk_size=1024,
    desc=False,
    exclude=None,
    filter=None,  # noqa: A002
    only=None,
) -> Iterable[QuerySet]:
  ''' A generator yielding `QuerySet`s which produce nonoverlapping
      batches of `Model` instances.

      Efficient behaviour requires the field to be indexed.
      Correct behaviour requires the field values to be unique.

      See `model_instances` for an iterable of instances wrapper
      of this function, where you have no need to further amend the
      `QuerySet`s or to be aware of the batches.

      Parameters:
      * `model`: the `Model` to query
      * `field_name`: default `'pk'`, the name of the field on which
        to order the batches
      * `after`: an optional field value - iteration commences
        immediately after this value
      * `chunk_size`: the maximum size of each chunk
      * `desc`: default `False`; if true then order the batches in
        descending order instead of ascending order
      * `exclude`: optional mapping of Django query terms to exclude by
      * `filter`: optional mapping of Django query terms to filter by
      * `only`: optional sequence of field names for a Django query `.only()`

      Example iteration of a `Model` would look like:

          from itertools import chain
          from cs.djutils import model_batches_qs
          for instance in chain.from_iterable(model_batches_qs(MyModel)):
              ... work with instance ...

      By returning `QuerySet`s it is possible to further alter each query:

          from cs.djutils import model_batches_qs
          for batch_qs in model_batches_qs(MyModel):
              for result in batch_qs.filter(
                  some_field__gt=10
              ).select_related(.......):
                  ... work with each result in the batch ...

      or:

          from itertools import chain
          from cs.djutils import model_batches_qs
          for result in chain.from_iterable(
              batch_qs.filter(
                  some_field__gt=10
              ).select_related(.......)
              for batch_qs in model_batches_qs(MyModel)
          ):
                  ... work with each result ...
  '''
  if chunk_size <= 0:
    raise ValueError(f'{chunk_size=} must be > 0')
  ordering = f'-{field_name}' if desc else field_name
  after_condition = f'{field_name}__lt' if desc else f'{field_name}__gt'
  mgr = model.objects
  # initial batch
  qs0 = mgr.all()
  if exclude:
    qs0 = qs0.exclude(**exclude)
  if exclude is not None:
    if isinstance(exclude, Mapping):
      qs0 = qs0.exclude(**exclude)
    else:
      qs0 = qs0.exclude(exclude)
  if filter is not None:
    if isinstance(filter, Mapping):
      qs0 = qs0.filter(**filter)
    else:
      qs0 = qs0.filter(filter)
  if only is not None:
    qs0 = qs0.only(*only)
  while True:
    qs = qs0
    if after is not None:
      qs = qs.filter(**{after_condition: after})
    qs = qs.order_by(ordering)[:chunk_size]
    key_list = list(qs.only(field_name).values_list(field_name, flat=True))
    if not key_list:
      break
    yield qs
    after = key_list[-1]

def model_instances(
    model: Model,
    field_name='pk',
    prefetch_related=None,
    select_related=None,
    **mbqs_kw,
) -> Iterable[Model]:
  ''' A generator yielding Model instances.
      This is a wrapper for `model_batches_qs` and accepts the same arguments.
      If you need to extend the `QuerySet`s beyond what the
      `model_batches_qs` parameters support it may be better to use
      that and extend each returned `QuerySet`.

      Additional parameters beyond those for `model_batches_qs`:
      * `prefetch_related`: an optional list of fields to apply to
        each query with `.prefetch_related()`
      * `select_related`: an optional list of fields to apply to
        each query with `.select_related()`

      Efficient behaviour requires the field to be indexed.
      Correct behaviour requires the field values to be unique.
  '''
  for batch_qs in model_batches_qs(model, field_name=field_name, **mbqs_kw):
    if prefetch_related is not None:
      batch_qs = batch_qs.prefetch_related(*select_related)
    if select_related is not None:
      batch_qs = batch_qs.select_related(*select_related)
    yield from batch_qs

#!/usr/bin/env python3
#
# Command line stuff. - Cameron Simpson <cs@cskk.id.au> 03sep2015
#
# pylint: disable=too-many-lines

''' Convenience functions for working with the cmd stdlib module,
    the BaseCommand class for constructing command line programmes,
    and other command line related stuff.

    This module provides the following main items:
    - `BaseCommand`: a base class for creating command line programmes
      with easier setup and usage than libraries like `optparse` or `argparse`
    - `@popopts`: a decorator which works with `BaseCommand` subcommand
      methods to parse their command line options
    - `@docmd`: a decorator for command methods of a `cmd.Cmd` class
      providing better quality of service

    Editorial: why not arparse?
    I find the whole argparse `add_argument` thing very cumbersome
    and hard to use and remember.
    Also, when incorrectly invoked an argparse command line prints
    the help/usage messgae and aborts the whole programme with
    `SystemExit`.
'''

from cmd import Cmd
from code import interact
from collections import ChainMap
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
try:
  from functools import cache  # 3.9 onward
except ImportError:
  from functools import lru_cache
  cache = lru_cache(maxsize=None)
try:
  from functools import cached_property  # 3.8 onward
except ImportError:
  cached_property = lambda func: property(cache(func))

from getopt import getopt, GetoptError
from inspect import isclass
import os
from os.path import basename
from pprint import pformat
import re
# this enables readline support in the docmd stuff
try:
  import readline  # pylint: disable=unused-import
except ImportError:
  pass
import shlex
from signal import SIGHUP, SIGINT, SIGQUIT, SIGTERM
import sys
from textwrap import dedent
from typing import Any, Callable, List, Mapping, Optional, Tuple, Union

from typeguard import typechecked

from cs.context import stackattrs
from cs.deco import decorator, OBSOLETE, uses_cmd_options, uses_quiet
from cs.lex import (
    cutprefix,
    cutsuffix,
    indent,
    is_identifier,
    printt,
    r,
    stripped_dedent,
    tabulate,
)
from cs.logutils import setup_logging, warning, error, exception
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.py.doc import obj_docstring
from cs.resources import RunState, uses_runstate
from cs.result import CancellationError
from cs.threads import HasThreadState, ThreadState
from cs.typingutils import subtype
from cs.upd import Upd, uses_upd, print  # pylint: disable=redefined-builtin

__version__ = '20250531'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.deco>=decorator__attrs',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.doc',
        'cs.resources',
        'cs.result',
        'cs.threads',
        'cs.typingutils',
        'cs.upd',
        'typeguard',
    ],
}

def docmd(dofunc):
  ''' Decorator for `cmd.Cmd` subclass methods
      to supply some basic quality of service.

      This decorator:
      - wraps the function call in a `cs.pfx.Pfx` for context
      - intercepts `getopt.GetoptError`s, issues a `warning`
        and runs `self.do_help` with the method name,
        then returns `None`
      - intercepts other `Exception`s,
        issues an `exception` log message
        and returns `None`

      The intended use is to decorate `cmd.Cmd` `do_`* methods:

          from cmd import Cmd
          from cs.cmdutils import docmd
          ...
          class MyCmd(Cmd):
              @docmd
              def do_something(...):
                  ... do something ...
  '''
  funcname = dofunc.__name__

  def docmd_wrapper(self, *a, **kw):
    ''' Run a `Cmd` "do" method with some context and handling.
    '''
    if not funcname.startswith('do_'):
      raise ValueError(f"function does not start with 'do_': {funcname}")
    argv0 = funcname[3:]
    with Pfx(argv0):
      try:
        return dofunc(self, *a, **kw)
      except GetoptError as e:
        warning("%s", e)
        self.do_help(argv0)
        return None
      except Exception as e:  # pylint: disable=broad-except
        exception("%s", e)
        return None

  docmd_wrapper.__name__ = f'@docmd({funcname})'
  docmd_wrapper.__doc__ = dofunc.__doc__
  return docmd_wrapper

@dataclass
class OptionSpec:
  ''' A class to support parsing an option value.
  '''

  UNVALIDATED_MESSAGE_DEFAULT = "invalid value"

  # the option name, eg 'n' for -n or 'dry-run' for --dry-run
  opt_name: str
  # whether an argument is expected
  # the argument usage name or None
  # eg "username"
  arg_name: Optional[str] = None
  # the name of the options field/attribute
  field_name: Optional[str] = None
  # the initial value of the option
  field_default: Optional[Any] = None
  # the help text
  help_text: Optional[str] = None
  # optional callable to convert the argument to the value
  parse: Optional[Callable[[str], Any]] = None
  # optional callable to validate the value
  validate: Optional[Callable[[Any], bool]] = None
  # optional message for invalid values
  unvalidated_message: str = UNVALIDATED_MESSAGE_DEFAULT

  def __post_init__(self):
    ''' Infer `field_name` and `help_text` if unspecified.
    '''
    if self.field_name is None:
      self.field_name = self.opt_name.replace('-', '_')
    if self.help_text is None:
      self.help_text = self.help_text_from_field_name(self.field_name)

  def parse_value(self, value):
    ''' Parse `value` according to the spec.
        Raises `GetoptError` for invalid values.
    '''
    if self.parse is None:
      return value
    with Pfx("%s %r", self.help_text, value):
      try:
        value = pfx_call(self.parse, value)
        if self.validate is not None:
          try:
            if not pfx_call(self.validate, value):
              raise GetoptError(self.unvalidated_message)
          except ValueError as e:
            raise GetoptError(
                f'{self.unvalidated_message}: {e.__class__.__name__}:{e}'
            ) from e
      except ValueError as e:
        raise GetoptError(str(e)) from e  # pylint: disable=raise-missing-from
    return value

  @classmethod
  @pfx_method
  def from_opt_kw(cls, opt_k: str, specs: Union[str, List, Tuple, None]):
    ''' Factory to produce an `OptionSpec` from a `(key,specs)` 2-tuple
        as from the `items()` from a `popopts()` call.

        The `specs` is normally a list or tuple, but a bare string
        will be promoted to a 1-element list containing the string.

        The elements of `specs` are considered in order for:
        - an identifier specifying the `arg_name`,
          optionally prepended with a dash to indicate an inverted option
        - a help text about the option
        - a callable to parse the option string value to obtain the actual value
        - a callable to validate the option value
        - a message for use when validation fails
    '''
    # produce needs_arg and cleaned up opt_name from the opt_k
    needs_arg = False
    # leading underscore for numeric options like -1
    if opt_k.startswith('_'):
      opt_k = opt_k[1:]
      if is_identifier(opt_k):
        warning("unnecessary leading underscore on valid identifier option")
    # trailing underscore indicates that the option expected an argument
    if opt_k.endswith('_'):
      needs_arg = True
      opt_k = opt_k[:-1]
    opt_name = opt_k.replace('_', '-')
    field_name = opt_k.replace('-', '_')
    field_default = None
    help_text = None
    parse = None
    validate = None
    unvalidated_message = None
    # apply the provided specifications
    if specs is None:
      specs = [field_name]
    elif isinstance(specs, str):
      # bare field name or help text
      specs = [specs]
    elif isinstance(specs, (list, tuple)):
      specs = list(specs)
    elif callable(specs):
      # bare conversion function (or a type eg int)
      specs = [specs]
    else:
      raise TypeError(
          f'expected str or list or tuple for specs, got {r(specs)}'
      )
    spec0 = specs.pop(0) if specs else None
    # first: optional field_name
    # field_name, an identifier
    if isinstance(spec0, str) and is_identifier(spec0):
      field_name = spec0
      spec0 = specs.pop(0) if specs else None
    # -field_name, a dash followed by an identifier
    elif (isinstance(spec0, str) and spec0.startswith('-')
          and is_identifier(spec0[1:])):
      field_name = spec0[1:]
      if needs_arg:
        raise ValueError(
            f'field name {field_name!r} expects an argument'
            ': inverted options only make sense for Boolean options'
        )
      field_default = True
      spec0 = specs.pop(0) if specs else None
    # optional help text
    if isinstance(spec0, str):
      help_text = spec0
      spec0 = specs.pop(0) if specs else None
    # optional parse callable
    if callable(spec0):
      parse = spec0
      spec0 = specs.pop(0) if specs else None
    # optional validate callable
    if callable(spec0):
      validate = spec0
      spec0 = specs.pop(0) if specs else None
    # optional unvalidated_message
    if isinstance(spec0, str):
      if not parse and not validate:
        raise ValueError(
            f'unexpected unvalidated_message {spec0!r} when there is no parse or validate callable'
        )
      unvalidated_message = spec0
      spec0 = specs.pop(0) if specs else None
    if spec0 is not None:
      raise ValueError(f'unhandled specifications: {[spec0]+specs!r}')
    if not needs_arg:
      # sanity check Boolean option
      if field_default is None:
        field_default = False
      elif not isinstance(field_default, bool):
        raise ValueError(
            f'non-Booolean specified for the field default: {r(field_default)}'
        )
    if field_default is None and not needs_arg:
      field_default = False
    self = cls(
        opt_name=opt_name,
        arg_name=(field_name.replace('_', '-') if needs_arg else None),
        field_name=field_name,
        field_default=field_default,
        help_text=help_text,
        parse=parse,
        validate=validate,
        unvalidated_message=unvalidated_message,
    )
    return self

  @property
  def needs_arg(self):
    ''' Whether we expect an argument: we have a `self.arg_name`.
    '''
    return bool(self.arg_name)

  @property
  def getopt_opt(self):
    ''' The `opt` we expect from `opt,val=getopt(argv,...)`.
    '''
    return (
        f'-{self.opt_name}'
        if len(self.opt_name) == 1 else f'--{self.opt_name}'
    )

  @property
  def getopt_short(self):
    ''' The option specification for a getopt short option.
        Return `''` if `self.opt_name` is longer than 1 character.
    '''
    if len(self.opt_name) > 1:
      return ''
    opt_char = self.opt_name[0]
    return f'{opt_char}:' if self.needs_arg else opt_char

  @property
  def getopt_long(self):
    ''' The option specification for a getopt long option.
        Return `None` if `self.opt_name` is only 1 character.
    '''
    if len(self.opt_name) < 2:
      return None
    return f'{self.opt_name}=' if self.needs_arg else f'{self.opt_name}'

  def option_terse(self):
    ''' Return the `"-x"` or `"--name"` option string (with the arg name if expected).
    '''
    return f'{self.getopt_opt} {self.arg_name}' if self.needs_arg else self.getopt_opt

  @staticmethod
  def help_text_from_field_name(field_name):
    ''' Compute an inferred `help_text` from an option `field_name`.
    '''
    help_text = field_name.replace('_', ' ')
    return help_text[0].upper() + help_text[1:] + '.'

  def option_usage(self):
    ''' A 2 line usage entry for this option.

        Example:

            -j jobs
              Job limit.
    '''
    line1 = self.option_terse()
    # TODO: allow multiline help_text, indent it here
    return f'{line1}\n  {self.help_text}'

  def add_argument(self, parser, options=None):
    ''' Add this option to an `argparser`-style option parser.
        The optional `options` parameter may be used to supply an
        `Options` instance to provide a default value.
    '''
    parser.add_argument(
        self.getopt_opt,
        action=('store' if self.arg_name else 'store_true'),
        dest=self.field_name,
        help=self.help_text,
        default=(
            (None if self.arg_name else False)
            if options is None else getattr(options, self.field_name, None)
        ),
    )

def split_usage(doc: Union[str, None],
                usage_marker="Usage:") -> Tuple[str, str, str]:
  ''' Extract a `"Usage:"`paragraph from a docstring
      and return a 3-tuple of `(preusage,usage,postusage)`.

      If the usage paragraph is not present `''` is returned as
      the middle comonpent, otherwise it is the unindented usage
      without the leading `"Usage:"`.
  '''
  if not doc:
    # no doc, return unchanged
    return '', '', ''
  try:
    pre_usage, usage_onward = doc.split(usage_marker, 1)
  except ValueError:
    # no Usage: paragraph
    # use the first paragraph
    pre_usage = ''
    usage_format, *post_usage_paras = doc.split("\n\n")
    usage_format = f'Usage: {{cmd}} subcommand [options...]\n{usage_format}'
    post_usage = "\n\n".join(post_usage_paras)
  else:
    try:
      usage_format, post_usage = usage_onward.split("\n\n", 1)
    except ValueError:
      usage_format, post_usage = usage_onward.rstrip(), ''
  usage_format = stripped_dedent(usage_format)
  # indent the second and following lines
  try:
    top_line, post_lines = usage_format.split("\n", 1)
  except ValueError:
    # single line usage only
    pass
  else:
    usage_format = f'{top_line}\n{indent(post_lines)}'
  return pre_usage, usage_format, post_usage

@dataclass
class SubCommand:
  ''' An implementation for a subcommand.
  '''

  # the BaseCommand instance with which we're associated
  command: "BaseCommand"
  # a method or a subclass of BaseCommand
  method: Callable
  # the notional name of the command/subcommand
  cmd: str = None
  # optional additional usage keyword mapping
  usage_mapping: Mapping[str, Any] = field(default_factory=dict)

  @property
  def instance(self):
    ''' An instance of the class for `self.method`.
    '''
    return self.method(...) if isclass(self.method) else self.method.__self__

  def get_cmd(self) -> str:
    ''' Return the `cmd` string for this `SubCommand`,
        derived from the subcommand's method name or class name
        if `self.cmd` is not set.
    '''
    if self.cmd is None:
      method = self.method
      if isclass(method):
        return cutsuffix(method.__name__, 'Command').lower()
      return cutprefix(method.__name__, self.command.SUBCOMMAND_METHOD_PREFIX)
    return self.cmd

  @typechecked
  def __call__(self, argv: List[str]):
    ''' Run the subcommand.

        Parameters:
        * `argv`: the command line arguments after the subcommand name
    '''
    method = self.method
    if isclass(method):
      # plumb self.command.options through to the subcommand
      updates = self.command.options.as_dict()
      updates.update(cmd=self.get_cmd())
      return pfx_call(method, argv, **updates).run()
    return method(argv)

  @cached_property
  def usage_default(self):
    ''' The fallback usage line if nothing specified:
        `'{cmd} [options...]'` or `'{cmd} subcommand [options...]'`.
    '''
    if isclass(self.method):
      has_subcommands_test = getattr(
          self.instance, 'has_subcommands', lambda: False
      )
    else:
      has_subcommands_test = getattr(
          self.method, 'has_subcommands', lambda: False
      )
    return (
        '{cmd} subcommand [options...]'
        if has_subcommands_test() else '{cmd} [options...]'
    )

  @cached_property
  def usage_commonopts_format(self):
    ''' The `Common options:` format string paragraph
        or `None` if there are no common options.
    '''
    return self.command.Options.usage_options_format(
        "Common options:", **self.command.options.COMMON_OPT_SPECS
    )

  @cached_property
  def usage_format(self) -> str:
    ''' The usage format string for this subcommand.
        *Note*: no leading "Usage:" prefix.

        This first tries the legacy `self.method.USAGE_FORMAT`,
        falling back to deriving it from `obj_docstring(self.method)`.

        When deriving from the docstring we look for a paragraph
        commencing with the string `Usage:` and otherwise fall back
        to its first parapgraph.
    '''
    method = self.method
    try:
      # the old way
      usage_format = method.USAGE_FORMAT
    except AttributeError:
      # the preferred way
      # derive from the docstring or from self.usage_default
      doc = obj_docstring(method)
      pre_usage, usage_format, post_usage = split_usage(doc)
      if not usage_format:
        # No "Usage:" paragraph - use default usage line and first paragraph.
        usage_format = self.usage_default
        paragraph1 = stripped_dedent(pre_usage.split('\n\n', 1)[0])
        if paragraph1:
          usage_format += "\n" + indent(paragraph1)
    else:
      # The existing USAGE_FORMAT based usages have the word "Usage:"
      # at the front but this is supplied at print time now.
      usage_format = indent(
          stripped_dedent(cutprefix(usage_format.lstrip(), 'Usage:'))
      ).lstrip()
    return usage_format

  @cached_property
  def usage_format_parts(
      self
  ) -> Tuple[str, Union[str, None], Union[str, None]]:
    ''' The usage description format string brokoen into:
        - the usage line (or lines if slosh extended)
        - the first line of the description, or `None`
        - the trailing lines of the description, or `None`
    '''
    lines = self.usage_format.split('\n')
    usage_lines = [lines.pop(0)]
    while usage_lines[-1].endswith('\\'):
      usage_lines.append(lines.pop(0))
    return (
        "\n".join(usage_lines),
        lines.pop(0).lstrip() if lines and lines[0].endswith('.') else None,
        dedent("\n".join(lines)) if lines else None,
    )

  @cached_property
  def usage_format_usage(self) -> str:
    ''' The usage line(s) part of the format string.
    '''
    return self.usage_format_parts[0]

  @cached_property
  def usage_format_desc1(self) -> str:
    ''' The leading usage line part of the format string.
    '''
    return self.usage_format_parts[1]

  def get_usage_format(self, show_common=False) -> str:
    ''' Return the usage format string for this subcommand.
        *Note*: no leading "Usage:" prefix.
        If `show_common` is true, include the `Common options:` paragraph.
    '''
    usage_format = self.usage_format
    if show_common:
      copts_format = self.usage_commonopts_format
      if copts_format:
        usage_format += "\n" + indent(copts_format)
    return usage_format

  def get_usage_keywords(
      self,
      *,
      cmd: Optional[str] = None,
      usage_mapping: Optional[Mapping] = None,
  ) -> str:
    ''' Return a mapping to be used when formatting the usage format string.

        This is an elaborate `ChainMap` of:
        - the optional `cmd` or `self.get_cmd()`
        - the optional `usage_mapping` parameter
        - `self.usage_mapping`
        - `self.method.USAGE_KEYWORDS` if present
        - the attributes of `self.command`
        - the attributes of `type(self.command)`
        - the attributes of the module for `type(self.command)`
    '''
    # TODO maybe this should return the ChainMap used in usage_text
    # elaborate search path for symbols in the usage format string
    # TODO: should this _be_ get_usage_keywords? pretty verbose
    format_cmd = cmd or self.get_cmd().replace('_', '-')
    if self.command.options.COMMON_OPT_SPECS:
      format_cmd += ' [common-options...]'
    return ChainMap(
        # normalised cmd name
        {'cmd': format_cmd},
        # supplied usage_mapping, if any
        usage_mapping or {},
        # direct .usage_mapping attribute
        self.usage_mapping or {},
        # usage mapping via the method
        dict(getattr(self.method, 'USAGE_KEYWORDS', {})),
        # the names in the command
        self.command.__dict__,
        # ... and its class
        self.command.__class__.__dict__,
        # ... and its module's top level
        sys.modules[self.command.__module__].__dict__,
    )

  def get_subcommands(self):
    ''' Return `self.method`'s mapping of subcommand name to `SubCommand`.
    '''
    method = self.method
    if isclass(method):
      # special constructor using Ellipsis to get a placeholder instance
      method = method(...)
    try:
      get_subcommands = method.subcommands
    except AttributeError:
      return {}
    return get_subcommands()

  def has_subcommands(self):
    ''' Whether this `SubCommand`'s `.method` has subcommands.
    '''
    try:
      has_subcommands = self.method.has_subcommands
    except AttributeError:
      # just inspect whatever subcommands the method has
      return bool(self.get_subcommands())
    # probaby a class - use its has_subcommands() test
    return has_subcommands()

  def get_subcmds(self):
    ''' Return the names of `self.method`'s subcommands in lexical order.
    '''
    return sorted(self.get_subcommands().keys())

  def subusage_table(self, subcmds: List[str], *, recurse=False, short=False):
    ''' Return rows for use with `cs.lex.tabulate`
        for the short subusage listing.
    '''
    rows = []
    subcommands = self.get_subcommands()
    for subcmd in subcmds:
      subcommand = subcommands[subcmd]
      rows.append(
          [
              subcmd.replace('_', '-'),
              (
                  # TODO: full desc if not short
                  subcommand.usage_format_desc1
                  or f'{subcmd.title()} subcommand.'
              )
          ]
      )
      if recurse and subcommand.has_subcommands():
        rows.extend(
            [indent(subc), indent(subd)]
            for subc, subd in subcommand.subusage_table(
                sorted(subcommand.get_subcommands().keys()),
                recurse=recurse,
                short=short,
            )
        )
    return rows

  def short_subusages(self, subcmds: List[str], *, recurse=False, short=False):
    ''' Return a list of tabulated one line subcommand summaries.
    '''
    table = self.subusage_table(subcmds, recurse=recurse, short=short)
    return list(tabulate(*table))

  @typechecked
  def usage_text(
      self,
      *,
      cmd=None,
      short: bool,
      recurse: bool = False,
      show_common: bool = False,
      show_subcmds: Optional[Union[bool, str, List[str]]] = None,
      usage_mapping: Optional[Mapping] = None,
      seen_subcommands: Optional[Mapping] = None,
  ) -> str:
    ''' Return the filled out usage text for this subcommand.
    '''
    if show_subcmds is None:
      show_subcmds = True
    if seen_subcommands is None:
      seen_subcommands = {}
    subcommands = self.get_subcommands()
    if show_subcmds:
      # compute those already seen and those new
      common_subcmds = subcommands.keys() & seen_subcommands.keys()
      additional_subcommands = subcommands.keys() - common_subcmds
      # turn show_subcmds into the list of subcommand names to show in the usage
      if isinstance(show_subcmds, bool):
        # all the subcommands winnowed by the sub_seen_subcommands
        assert show_subcmds is True
        show_subcmds = sorted(additional_subcommands)
      elif isinstance(show_subcmds, str):
        # show a single subcommand
        show_subcmds = [show_subcmds]
      # the seen_subcommands for our subcommands
      sub_seen_subcommands = dict(seen_subcommands)
      sub_seen_subcommands.update(subcommands)
    # normalise the subcommand names to match the subcommands mapping
    show_subcmds = [subcmd.replace('-', '_') for subcmd in show_subcmds]
    if short:
      usage_line, desc1, _ = self.usage_format_parts
      usage_format = usage_line
      if desc1:
        usage_format += '\n' + indent(desc1)
    else:
      usage_format = self.usage_format
    if show_common:
      copts_format = self.usage_commonopts_format
      if copts_format:
        usage_format += "\n" + indent(copts_format)
    # the elaborate search path for symbols in the usage format string
    mapping = self.get_usage_keywords(cmd=cmd, usage_mapping=usage_mapping)
    with Pfx("format %r using %r", usage_format, mapping):
      usage = usage_format.format_map(mapping)
    subusage_listing = []
    if self.has_subcommands():
      if short:
        # the terse one subcommand-per-line listing
        subusages = self.short_subusages(
            show_subcmds, recurse=recurse, short=short
        )
      else:
        # the longer descriptions
        subusages = []
        for subcmd in show_subcmds:
          try:
            subcommand = subcommands[subcmd]
          except KeyError:
            warning("unknown subcommand %r", subcmd)
          else:
            # recursive long listing
            subusages.append(
                subcommand.usage_text(
                    short=short,
                    recurse=recurse,
                    seen_subcommands=sub_seen_subcommands,
                )
            )
      if common_subcmds:
        common_subcmds_line = f'Common subcommands: {", ".join(sorted(common_subcmds))}.'
      if subusages:
        subcmds_header = (
            'Subcommands'
            if show_subcmds is None or len(show_subcmds) > 1 else 'Subcommand'
        )
        subusage_listing.append(f'{subcmds_header}:')
        if common_subcmds:
          subusage_listing.append(indent(common_subcmds_line))
        subusage_listing.extend(map(indent, subusages))
      elif common_subcmds:
        subusage_listing.append(common_subcmds_line)
    if subusage_listing:
      subusage = "\n".join(subusage_listing)
      usage = f'{usage}\n{indent(subusage)}'
    return usage

# exposed outside the class for @fmtdoc
SSH_EXE_DEFAULT = 'ssh'
SSH_EXE_ENVVAR = 'SSH_EXE'

# gimmicked name to support @fmtdoc on BaseCommandOptions.popopts
_COMMON_OPT_SPECS = dict(
    dry_run=('dry_run', 'Dry run, aka no action.'),
    e_=(
        'ssh_exe',
        ''' An ssh-like command to use for remote command execution.
            The string is a shell-like command string parsable by shlex.split.''',
    ),
    n=('dry_run', 'No action, aka dry run.'),
    q='quiet',
    v='verbose',
)

@dataclass
class BaseCommandOptions(HasThreadState):
  ''' A base class for the `BaseCommand` `options` object.

      This is the default class for the `self.options` object
      available during `BaseCommand.run()`,
      and available as the `BaseCommand.Options` attribute.

      Any keyword arguments are applied as field updates to the instance.

      It comes prefilled with:
      * `.dry_run=False`
      * `.force=False`
      * `.quiet=False`
      * `.ssh_exe='ssh'`
      * `.verbose=False`
      and a `.doit` property which is the inverse of `.dry_run`.

      It is recommended that if `BaseCommand` subclasses use a
      different type for their `Options` that it should be a
      subclass of `BaseCommandOptions`.
      Since `BaseCommandOptions` is a data class, this typically looks like:

          @dataclass
          class Options(BaseCommand.Options):
              ... optional extra fields etc ...
  '''

  INFO_SKIP_NAMES = ('runstate', 'runstate_signals')
  DEFAULT_SIGNALS = SIGHUP, SIGINT, SIGQUIT, SIGTERM
  COMMON_OPT_SPECS = _COMMON_OPT_SPECS

  # the cmd prefix while a command runs
  cmd: Optional[str] = None
  # dry run, no action
  dry_run: bool = False
  force: bool = False
  quiet: bool = False
  runstate: Optional[RunState] = None
  runstate_signals: Tuple[int] = DEFAULT_SIGNALS
  ssh_exe: str = field(
      default_factory=lambda: (
          os.environ.get(SSH_EXE_ENVVAR, os.environ.get('RSYNC_RSH', '')) or
          SSH_EXE_DEFAULT
      )
  )
  verbose: bool = False

  opt_spec_class = OptionSpec

  perthread_state = ThreadState()

  def as_dict(self):
    ''' Return the options as a `dict`.
        This contains all the public attributes of `self`.
    '''
    return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

  def fields_as_dict(self):
    ''' Return the options' fields as a `dict`.
        This contains all the field values of `self`.
    '''
    return {f.name: getattr(self, f.name) for f in fields(self)}

  def copy(self, **updates):
    ''' Return a new instance of `BaseCommandOptions` (well, `type(self)`)
        which is a shallow copy of the public attributes from `self.__dict__`.

        Any keyword arguments are applied as attribute updates to the copy.
    '''
    # instantiate copied with the fields
    copied = pfx_call(type(self), **self.fields_as_dict())
    # infill any attributes which were not fields
    for k, v in self.as_dict().items():
      setattr(copied, k, v)
    # apply the supplied updates
    for k, v in updates.items():
      setattr(copied, k, v)
    return copied

  def update(self, **updates):
    ''' Modify the options in place with the mapping `updates`.
        It would be more normal to call the options in a `with` statement
        as shown for `__call__`.
    '''
    for k, v in updates.items():
      setattr(self, k, v)

  # TODO: remove this - the overt make-a-copy-and-with-the-copy is clearer
  @contextmanager
  def __call__(self, **updates):
    ''' Calling the options object returns a context manager whose
        value is a shallow copy of the options with any `updates` applied.

        Example showing the semantics:

            >>> from cs.cmdutils import BaseCommandOptions
            >>> @dataclass
            ... class DemoOptions(BaseCommandOptions):
            ...   x: int = 0
            ...
            >>> options = DemoOptions(x=1)
            >>> assert options.x == 1
            >>> assert not options.verbose
            >>> with options(verbose=True) as subopts:
            ...     assert options is not subopts
            ...     assert options.x == 1
            ...     assert not options.verbose
            ...     assert subopts.x == 1
            ...     assert subopts.verbose
            ...
            >>> assert options.x == 1
            >>> assert not options.verbose

    '''
    suboptions = self.copy(**updates)
    yield suboptions

  @property
  def doit(self):
    ''' I usually use a `doit` flag, the inverse of `dry_run`.
    '''
    return not self.dry_run

  @doit.setter
  def doit(self, new_doit):
    ''' Set `dry_run` to the inverse of `new_doit`.
    '''
    self.dry_run = not new_doit

  @classmethod
  def getopt_spec_map(cls, opt_specs_kw: Mapping, common_opt_specs=None):
    ''' Return a 3-tuple of (shortopts,longopts,getopt_spec_map)` being:
       - `shortopts`: the `getopt()` short options specification string
       - `longopts`: the `getopts()` long option specification list
       - `getopt_spec_map`: a mapping of `opt`->`OptionSpec`
         where `opt` is as from `opt,val` from `getopt()`
         and `opt_spec` is the associated `OptionSpec` instance
    '''
    if common_opt_specs is None:
      common_opt_specs = cls.COMMON_OPT_SPECS
    opt_spec_cls = cls.opt_spec_class
    shortopts = ''
    longopts = []
    getopt_spec_map = {}
    # gather up the option specifications and make getopt arguments
    for opt_k, opt_specs in ChainMap(
        opt_specs_kw,
        common_opt_specs,
    ).items():
      with Pfx("opt_spec[%r]=%r", opt_k, opt_specs):
        opt_spec = opt_spec_cls.from_opt_kw(opt_k, opt_specs)
        if opt_spec.getopt_opt in getopt_spec_map:
          # keep the earlier definition
          continue
        getopt_spec_map[opt_spec.getopt_opt] = opt_spec
        # update the arguments for getopt()
        shortopts += opt_spec.getopt_short
        getopt_long = opt_spec.getopt_long
        if getopt_long is not None:
          longopts.append(getopt_long)
    return shortopts, longopts, getopt_spec_map

  def popopts(
      self,
      argv,
      **opt_specs_kw,
  ):
    ''' Parse option switches from `argv`, a list of command line strings
        with leading option switches and apply them to `self`.
        Modify `argv` in place.

        Example use in a `BaseCommand` `cmd_foo` method:

            def cmd_foo(self, argv):
                options = self.options
                options.popopts(
                    c_='config',
                    l='long',
                    x='trace',
                )
                if self.options.dry_run:
                    print("dry run!")

        The expected options are specified by the keyword parameters
        in `opt_specs`.
        Each keyword name has the following semantics:
        * options not starting with a letter may be preceeded by an underscore
          to allow use in the parameter list, for example `_1='once'`
          for a `-1` option setting the `once` option name
        * a single letter name specifies a short option
          and a multiletter name specifies a long option
        * options requiring an argument have a trailing underscore
        * options not requiring an argument normally imply a value
          of `True`; if their synonym commences with a dash they will
          imply a value of `False`, for example `n='dry_run',y='-dry_run'`.

        The `BaseCommand` class provides a `popopts` method
        which is a shim for this method applied to its `.options`.
        So common use in a command method usually looks like this:

            class SomeCommand(BaseCommand):

                def cmd_foo(self, argv):
                    # accept a -j or --jobs options
                    self.popopts(argv, jobs=1, j='jobs')
                    print("jobs =", self.options.jobs)

        The `self.options` object is preprovided as an instance of
        the `self.Options` class, which is `BaseCommandOptions` by
        default. There is presupplies support for some basic options
        like `-v` for "verbose" and so forth, and a subcommand
        need not describe these in a call to `self.options.popopts()`.

        Example:

            >>> import os.path
            >>> from typing import Optional
            >>> @dataclass
            ... class DemoOptions(BaseCommandOptions):
            ...   all: bool = False
            ...   jobs: int = 1
            ...   number: int = 0
            ...   once: bool = False
            ...   path: Optional[str] = None
            ...   trace_exec: bool = False
            ...
            >>> options = DemoOptions()
            >>> argv = ['-1', '-v', '-y', '-j4', '--path=/foo', 'bah', '-x']
            >>> opt_dict = options.popopts(
            ...   argv,
            ...   _1='once',
            ...   a='all',
            ...   j_=('jobs',int),
            ...   x='-trace_exec',
            ...   y='-dry_run',
            ...   dry_run=None,
            ...   path_=(str, os.path.isabs, 'not an absolute path'),
            ...   verbose=None,
            ... )
            >>> opt_dict
            {'once': True, 'verbose': True, 'dry_run': False, 'jobs': 4, 'path': '/foo'}
            >>> options # doctest: +ELLIPSIS
            DemoOptions(cmd=None, dry_run=False, force=False, quiet=False, runstate_signals=(...), verbose=True, all=False, jobs=4, number=0, once=True, path='/foo', trace_exec=False)
    '''
    shortopts, longopts, getopt_spec_map = self.getopt_spec_map(opt_specs_kw)
    # infill default False/None for new fields
    for opt_spec in getopt_spec_map.values():
      field_name = opt_spec.field_name
      if not hasattr(self, field_name):
        setattr(self, field_name, opt_spec.field_default)
    opts, argv[:] = getopt(argv, shortopts, longopts)
    for opt, val in opts:
      with Pfx(opt):
        opt_spec = getopt_spec_map[opt]
        if opt_spec.needs_arg:
          with Pfx("%r", val):
            value = opt_spec.parse_value(val)
        else:
          value = not opt_spec.field_default
        setattr(self, opt_spec.field_name, value)

  @classmethod
  def usage_options_format(
      cls,
      headline="Options:",
      *,
      _common_opt_specs=None,
      **opt_specs_kw,
  ):
    ''' Return an options paragraph describing `opt_specs_kw`.
        or `''` if `opt_specs_kw` is empty.
    '''
    if not opt_specs_kw:
      return ''
    _, _, getopt_spec_map = cls.getopt_spec_map(
        opt_specs_kw, _common_opt_specs
    )
    return headline + "\n" + indent(
        "\n".join(
            tabulate(
                *(
                    (
                        opt_spec.option_terse(),
                        stripped_dedent(opt_spec.help_text)
                    ) for _, opt_spec in sorted(
                        getopt_spec_map.items(),
                        key=lambda kv: kv[0].lstrip('-').lower()
                    )
                ),
            )
        )
    )

@decorator
def popopts(cmd_method, **opt_specs_kw):
  ''' A decorator to parse command line options from a `cmd_`*method*'s `argv`
      and update `self.options`. This also updates the method's usage message.

      Example:

          @popopts(x=('trace', 'Trace execution.'))
          def cmd_do_something(self, argv):
              """ Usage: {cmd} [-x] blah blah
                    Do some thing to blah.
              """

      This arranges for cmd_do_something to call `self.options.popopts(argv)`,
      and updates the usage message with an "Options:" paragraph.

      This avoids needing to manually enumerate the options in the
      docstring usage and avoids explicitly calling `self.options.popopts`
      inside the method.
  '''

  def _popopts_cmd_method_wrapper(self, argv, *method_a, **method_kw):
    self.options.popopts(argv, **opt_specs_kw)
    return cmd_method(self, argv, *method_a, **method_kw)

  wrapper_attrs = {}
  if opt_specs_kw:
    # patch the cmd_method usage text
    pre_usage, usage_format, post_usage = split_usage(cmd_method.__doc__ or '')
    if usage_format:
      usage_format = (
          f'Usage: {usage_format}\n' + indent(
              BaseCommandOptions.usage_options_format(
                  "Options:",
                  _common_opt_specs={},
                  **opt_specs_kw,
              )
          )
      )
      wrapper_attrs.update(
          __doc__=f'{pre_usage}\n\n{usage_format}\n\n{post_usage}'.strip(),
      )

  return _popopts_cmd_method_wrapper, wrapper_attrs

class BaseCommand:
  ''' A base class for handling nestable command lines.

      This class provides the basic parse and dispatch mechanisms
      for command lines.
      To implement a command line one instantiates a subclass of `BaseCommand`:

          class MyCommand(BaseCommand):
              """ My command to do something. """

      and provides either a `main` method if the command has no subcommands
      or a suite of `cmd_`*subcommand* methods, one per subcommand.

      Running a command is done by:

          MyCommand(argv).run()

      Modules which implement a command line mode generally look like this:

          ... imports etc ...
          def main(argv=None, **run_kw):
              """ The command line mode.
              """
              return MyCommand(argv).run(**run_kw)
          ... other code ...
          class MyCommand(BaseCommand):
          ... other code ...
          if __name__ == '__main__':
              sys.exit(main(sys.argv))

      Instances have a `self.options` attribute on which optional
      modes are set, avoiding conflict with the attributes of `self`.

      The `self.options` object is an instance of the class' `Options` class.
      The default comes from `BaseCommand.Options` (aka `BaseCommandOptions`)
      but classes with additional command line options will usually
      provide their own subclass:

          class MyCommand(BaseCommand):

              @dataclass
              class Options(BaseCommand.Options):
                  extra_mode : str = None
                  some_flag : bool = False

                  # extend the common options for the new fields
                  COMMON_OPT_SPECS = dict(
                      **BaseCommand.Options.COMMON_OPT_SPECS,
                      mode_=('extra_mode', 'The extra mode to do something.'),
                      flag='some_flag',
                  )

      This adds an additional `--mode` *mode* and a `--flag` command line option
      which affects the fields of `self.options` and updates the
      automaticly generated usage messages accordingly.
      See the documentation for `BaseCommandOptions.popopts` for
      explaination of the `COMMON_OPT_SPECS` values.

      Subclasses with no subcommands
      generally just implement a `main(argv)` method.

      Subclasses with subcommands
      should implement a `cmd_`*subcommand*`(argv)` instance method
      for each subcommand.
      If a subcommand is itself implemented using `BaseCommand`
      then it can be a simple attribute:

          cmd_subthing = SubThingCommand

      Returning to methods, if there is a paragraph in the method docstring
      commencing with `Usage:` then that paragraph is incorporated
      into the main usage message automatically.

      Example:

          @popopts(l='long_mode')
          def cmd_ls(self, argv):
              """ Usage: {cmd} [-l] [paths...]
                    Emit a listing for the named paths.

                  Further docstring non-usage information here.
              """
              ... do the "ls" subcommand ...
              ... with use of self.options.long_mode as needed ...

      The subclass is customised by overriding the following methods:
      * `run_context()`:
        a context manager to provide setup or teardown actions
        to occur before and after the command implementation respectively,
        such as to open and close a database.
      * `cmd_`*subcmd*`(argv)`:
        if the command line options are followed by an argument
        whose value is *subcmd*,
        then the method `cmd_`*subcmd*`(subcmd_argv)`
        will be called where `subcmd_argv` contains the command line arguments
        following *subcmd*.
      * `main(argv)`:
        if there are no `cmd_`*subcmd* methods then method `main(argv)`
        will be called where `argv` contains the command line arguments.
  '''

  SUBCOMMAND_METHOD_PREFIX = 'cmd_'
  SUBCOMMAND_ARGV_DEFAULT = None
  SubCommandClass = SubCommand

  GETOPT_SPEC = ''
  Options = BaseCommandOptions

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  def __init__(
      self, argv=None, *, cmd=None, options=None, **kw_options
  ) -> str:
    ''' Initialise the command line.
        Return the subcommand name, or `None` if there is only `main`.
        Raises `GetoptError` for unrecognised options.

        Parameters:
        * `argv`:
          optional command line arguments
          including the main command name if `cmd` is not specified.
          The default is `sys.argv`.
          The contents of `argv` are copied,
          permitting desctructive parsing of `argv`.
        * `cmd`:
          optional keyword specifying the command name for context;
          if this is not specified it is taken from `argv.pop(0)`.
        * `options`:
          an optional keyword providing object for command state and context.
          If not specified a new `self.Options` instance
          is allocated for use as `options`.
          The default `Options` class is `BaseCommandOptions`,
          a dataclass with some prefilled attributes and properties
          to aid use later.
        Other keyword arguments are applied to `self.options`
        as attributes.

        The `cmd` and `argv` parameters have some fiddly semantics for convenience.
        There are 3 basic ways to initialise:
        * `BaseCommand()`: `argv` comes from `sys.argv`
          and the value for `cmd` is derived from `argv[0]`
        * `BaseCommand(argv)`: `argv` is the complete command line
          including the command name and the value for `cmd` is
          derived from `argv[0]`
        * `BaseCommand(argv, cmd=foo)`: `argv` is the command
          arguments _after_ the command name and `cmd` is set to
          `foo`

        The command line arguments are parsed according to
        the optional `GETOPT_SPEC` class attribute (default `''`).
        If `getopt_spec` is not empty
        then `apply_opts(opts)` is called
        to apply the supplied options to the state
        where `opts` is the return from `getopt.getopt(argv,getopt_spec)`.

        After the option parse,
        if the first command line argument *foo*
        has a corresponding method `cmd_`*foo*
        then that argument is removed from the start of `argv`
        and `self.cmd_`*foo*`(argv,options,cmd=`*foo*`)` is called
        and its value returned.
        Otherwise `self.main(argv,options)` is called
        and its value returned.

        If the command implementation requires some setup or teardown
        then this may be provided by the `run_context`
        context manager method,
        called with `cmd=`*subcmd* for subcommands
        and with `cmd=None` for `main`.
    '''
    if argv is None:
      # using sys.argv
      argv = list(sys.argv)
    elif argv is ...:
      # dummy mode for BaseCOmmand instances made to access this
      # but not run a command
      pass
    else:
      # argv provided
      argv = list(argv)
    if cmd is None:
      if argv is ... or not argv:
        cmd = cutsuffix(self.__class__.__name__, 'Command').lower()
      else:
        cmd = basename(argv.pop(0))
        if cmd.endswith('.py'):
          # "python -m foo" sets argv[0] to "..../foo.py"
          # fall back to the class name
          cmd = (
              cutsuffix(self.__class__.__name__, 'Command').lower()
              or self.__class__.__module__
          )
    options = self.__class__.Options(cmd=cmd)
    # override the default options
    for option, value in kw_options.items():
      setattr(options, option, value)
    self.cmd = cmd
    self._argv = argv
    self.options = options

  def _prerun_setup(self):
    argv = self._argv
    options = self.options
    subcmds = self.subcommands()
    has_subcmds = self.has_subcommands()
    log_level = getattr(options, 'log_level', None)
    loginfo = setup_logging(cmd=self.cmd, level=log_level)
    # post: argv is list of arguments after the command name
    self.loginfo = loginfo
    self._run = lambda argv: 2
    # we catch GetoptError from this suite...
    subcmd = None  # default: no subcmd specific usage available
    try:
      getopt_spec = getattr(self, 'GETOPT_SPEC', '')
      # catch bare -help or --help or -h (if no 'h' in the getopt_spec)
      if (len(argv) == 1
          and (argv[0] in ('-help', '--help') or
               ('h' not in getopt_spec and argv[0] in ('-h',)))):
        argv = self._argv = ['help', '-l']
        has_subcmds = True  # fake this mode in order to run cmd_help
      else:
        # ordinary mode: leading options then preargv
        if has_subcmds:
          # do the leading argument parse
          if not getopt_spec:
            # the modern way with popopts
            # use the options.COMMON_OPT_SPECS
            options.popopts(argv)
          else:
            # legacy GETOPT_SPEC mode
            opts, argv = getopt(argv, getopt_spec, '')
            self.apply_opts(opts)
        # We do this regardless so that subclasses can do some presubcommand parsing
        # _after_ any command line options.
        argv = self.apply_preargv(argv)
      # now prepare self._run, a callable
      if not has_subcmds:
        # no subcommands, just use the main() method
        try:
          main = self.main
        except AttributeError:
          # pylint: disable=raise-missing-from
          raise GetoptError("no main method and no subcommand methods")
        self._argv = argv
        self._run = self.SubCommandClass(self, main)
      else:
        # expect a subcommand on the command line
        subcmd = argv[0] if argv else None
        if subcmd is not None and re.match(r'^[a-z][-_\w]*$', subcmd):
          # looks like a subcommand name, take it
          argv.pop(0)
        else:
          # not a command name, is there a default command?
          default_argv = self.SUBCOMMAND_ARGV_DEFAULT
          if not default_argv:
            # no, emit the short help
            warning(
                "missing subcommand, expected one of: %s",
                ', '.join(sorted(subcmds.keys()))
            )
            argv = ['help', '-s']
          else:
            # prefix the argv with the default argv
            if isinstance(default_argv, str):
              default_argv = [default_argv]
            argv = [*default_argv, *argv]
          subcmd = argv.pop(0)
        try:
          subcommand = self.subcommand(subcmd)
        except KeyError:
          # pylint: disable=raise-missing-from
          bad_subcmd = subcmd
          subcmd = None
          raise GetoptError(
              f'unrecognised subcommand {bad_subcmd!r}, expected one of:'
              f' {", ".join(sorted(subcmds.keys()))}'
          )

        def _run(argv):
          with Pfx(subcmd):
            return subcommand(argv)

        self._argv = argv
        self._run = _run
    except GetoptError as e:
      if self.getopt_error_handler(
          options.cmd,
          self.options,
          e,
          self.usage_text(short=True, show_subcmds=subcmd),
      ):
        return subcmd
      raise
    else:
      return subcmd

  @classmethod
  def method_cmdname(cls, method_name: str):
    ''' The `cmd` value from a method name.
    '''
    return cutprefix(method_name, cls.SUBCOMMAND_METHOD_PREFIX)

  @cache
  def subcommands(self):
    ''' Return a mapping of subcommand names to subcommand specifications
        for class attributes which commence with `cls.SUBCOMMAND_METHOD_PREFIX`
        by default `'cmd_'`.
    '''
    cls = type(self)
    prefix = cls.SUBCOMMAND_METHOD_PREFIX
    usage_mapping = getattr(cls, 'USAGE_KEYWORDS', {})
    mapping = {}
    for method_name in dir(cls):
      if method_name.startswith(prefix):
        subcmd = self.method_cmdname(method_name)
        method = getattr(self, method_name)
        subusage_mapping = dict(usage_mapping)
        method_keywords = getattr(method, 'USAGE_KEYWORDS', {})
        subusage_mapping.update(method_keywords)
        subusage_mapping.update(cmd=subcmd)
        mapping[subcmd] = self.SubCommandClass(
            self,
            method,
            cmd=subcmd,
            usage_mapping=subusage_mapping,
        )
    return mapping

  @classmethod
  def has_subcommands(cls):
    ''' Test whether the class defines additional subcommands.
    '''
    prefix = cls.SUBCOMMAND_METHOD_PREFIX
    for method_name in dir(cls):
      if not method_name.startswith(prefix):
        continue
      if getattr(cls, method_name) is getattr(BaseCommand, method_name, None):
        continue
      return True
    return False

  @cache
  def subcommand(self, subcmd: str):
    ''' Return the `SubCommand` associated with `subcmd`.
    '''
    subcmd_ = subcmd.replace('-', '_').replace('.', '_')
    subcommands = self.subcommands()
    return subcommands[subcmd_]

  def usage_text(
      self,
      **subcommand_kw,
  ):
    ''' Compute the "Usage:" message for this class.
        Parameters are as for `SubCommand.usage_text`.
    '''
    return self.SubCommandClass(
        self, method=type(self)
    ).usage_text(**subcommand_kw)

  def subcommand_usage_text(
      self, subcmd, usage_format_mapping=None, short=False
  ):
    ''' Return the usage text for a subcommand.
    '''
    method = self.subcommands()[subcmd].method
    subusage = None
    # support (method, get_suboptions)
    try:
      classy = issubclass(method, BaseCommand)
    except TypeError:
      classy = False
    if classy:
      # first paragraph of the class usage text
      doc = method([]).usage_text(cmd=subcmd)
      subusage_format, *_ = cutprefix(doc, 'Usage:').lstrip().split("\n\n", 1)
    else:
      # extract the usage from the object docstring
      doc = obj_docstring(method)
      if doc:
        if 'Usage:' in doc:
          # extract the Usage: paragraph
          pre_usage, post_usage = doc.split('Usage:', 1)
          pre_usage = pre_usage.strip()
          post_usage_format, *_ = post_usage.split('\n\n', 1)
          subusage_format = stripped_dedent(post_usage_format)
        else:
          # extract the first paragraph
          subusage_format, *_ = doc.split('\n\n', 1)
      else:
        # default usage text - include the docstring below a header
        paragraph1 = doc.split("\n\n", 1)[0]
        subusage_format = f'{{cmd}} ...\n  {paragraph1}'
    if subusage_format:
      if short:
        subusage_format, *_ = subusage_format.split('\n', 1)
      mapping = dict(sys.modules[method.__module__].__dict__)
      if usage_format_mapping:
        mapping.update(usage_format_mapping)
      mapping.update(cmd=subcmd)
      subusage = subusage_format.format_map(mapping)
    return subusage.replace('\n', '\n  ')

  @classmethod
  def extract_usage(cls, cmd=None):
    ''' Extract the `Usage:` paragraph from `cls__doc__` if present.
        Return a 2-tuple of `(doc_without_usage,usage_text)`
        being the remaining docstring and a full usage message.

        *Note*: this actually sets `cls.USAGE_FORMAT` if that does
        not already exist.
    '''
    if cmd is None:
      # infer a cmd from the class name
      cmd = cutsuffix(cls.__name__, 'Command').lower()
    instance = cls([cmd])
    pre_usage, usage_format, post_usage = split_usage(obj_docstring(cls))
    if usage_format and not hasattr(cls, 'USAGE_FORMAT'):
      cls.USAGE_FORMAT = usage_format
    usage_text = instance.usage_text(recurse=True, short=False)
    return pre_usage + post_usage, usage_text

  @pfx_method
  # pylint: disable=no-self-use
  def apply_opt(self, opt, val):
    ''' Handle an individual global command line option.

        This default implementation raises a `NotImplementedError`.
        It only fires if `getopt` actually gathered arguments
        and would imply that a `GETOPT_SPEC` was supplied
        without an `apply_opt` or `apply_opts` method to implement the options.
    '''
    raise NotImplementedError(f'unhandled option {opt!r}')

  def apply_opts(self, opts):
    ''' Apply command line options.

        Subclasses can override this
        but it is usually easier to override `apply_opt(opt,val)`.
    '''
    badopts = False
    for opt, val in opts:
      with Pfx(opt if val is None else f'{opt} {val!r}'):
        try:
          self.apply_opt(opt, val)
        except GetoptError as e:
          warning("%s", e)
          badopts = True
    if badopts:
      raise GetoptError("bad options")

  # pylint: disable=no-self-use
  def apply_preargv(self, argv):
    ''' Do any preparsing of `argv` before the subcommand/main-args.
        Return the remaining arguments.

        This default implementation does nothing.
    '''
    return argv

  @classmethod
  @typechecked
  def poparg(
      cls,
      argv: List[str],
      *specs,
      unpop_on_error: bool = False,
      opt_spec_class=None
  ):
    ''' Pop the leading argument off `argv` and parse it.
        Return the parsed argument.
        Raises `getopt.GetoptError` on a missing or invalid argument.

        This is expected to be used inside a `main` or `cmd_*`
        command handler method or inside `apply_preargv`.

        You can just use:

            value = argv.pop(0)

        but this method provides conversion and valuation
        and a richer failure mode.

        Parameters:
        * `argv`: the argument list, which is modified in place with `argv.pop(0)`
        * the argument list `argv` may be followed by some help text
          and/or an argument parser function.
        * `validate`: an optional function to validate the parsed value;
          this should return a true value if valid,
          or return a false value or raise a `ValueError` if invalid
        * `unvalidated_message`: an optional message after `validate`
          for values failing the validation
        * `unpop_on_error`: optional keyword parameter, default `False`;
          if true then push the argument back onto the front of `argv`
          if it fails to parse; `GetoptError` is still raised

        Typical use inside a `main` or `cmd_*` method might look like:

            self.options.word = self.poparg(argv, int, "a count value")
            self.options.word = self.poparg(
                argv, int, "a count value",
               lambda count: count > 0, "count should be positive")

        Because it raises `GetoptError` on a bad argument
        the normal usage message failure mode follows automatically.

        Demonstration:

            >>> argv = ['word', '3', 'nine', '4']
            >>> BaseCommand.poparg(argv, "word to process")
            'word'
            >>> BaseCommand.poparg(argv, int, "count value")
            3
            >>> BaseCommand.poparg(argv, float, "length")
            Traceback (most recent call last):
              ...
            getopt.GetoptError: length 'nine': float('nine'): could not convert string to float: 'nine'
            >>> BaseCommand.poparg(argv, float, "width", lambda width: width > 5)
            Traceback (most recent call last):
              ...
            getopt.GetoptError: width '4': invalid value
            >>> BaseCommand.poparg(argv, float, "length")
            Traceback (most recent call last):
              ...
            getopt.GetoptError: length: missing argument
            >>> argv = ['-5', 'zz']
            >>> BaseCommand.poparg(argv, float, "size", lambda size: size > 0, "size should be >0")
            Traceback (most recent call last):
              ...
            getopt.GetoptError: size '-5': size should be >0
            >>> argv  # -5 was still consumed
            ['zz']
            >>> BaseCommand.poparg(argv, float, "size2", unpop_on_error=True)
            Traceback (most recent call last):
              ...
            getopt.GetoptError: size2 'zz': float('zz'): could not convert string to float: 'zz'
            >>> argv  # zz was pushed back
            ['zz']
    '''
    if opt_spec_class is None:
      opt_spec_class = OptionSpec
    opt_spec = opt_spec_class.from_opt_kw('_', specs)
    with Pfx(opt_spec.help_text):
      if not argv:
        raise GetoptError("missing argument")
      arg0 = argv.pop(0)
    try:
      return opt_spec.parse_value(arg0)
    except GetoptError:
      if unpop_on_error:
        argv.insert(0, arg0)
      raise

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  def run(self, **kw_options):
    ''' Run a command.
        Returns the exit status of the command.
        May raise `GetoptError` from subcommands.

        Any keyword arguments are used to override `self.options` attributes
        for the duration of the run,
        for example to presupply a shared `Upd` from an outer context.

        If the first command line argument *foo*
        has a corresponding method `cmd_`*foo*
        then that argument is removed from the start of `argv`
        and `self.cmd_`*foo*`(cmd=`*foo*`)` is called
        and its value returned.
        Otherwise `self.main(argv)` is called
        and its value returned.

        If the command implementation requires some setup or teardown
        then this may be provided by the `run_context()`
        context manager method.
    '''
    subcmd = self._prerun_setup()
    options = self.options
    try:
      try:
        with self.run_context(**kw_options) as xit:
          if xit is not None:
            error("exit status from run_context: %s", xit)
            return xit
          return self._run(self._argv)
      except CancellationError:
        error("cancelled")
        return 1
    except GetoptError as e:
      if self.getopt_error_handler(
          self.cmd,
          options,
          e,
          self.usage_text(cmd=self.cmd, show_subcmds=subcmd, short=False),
      ):
        return 2
      raise

  def cmdloop(self, intro=None):
    ''' Use `cmd.Cmd` to run a command loop which calls the `cmd_`* methods.
    '''
    if not sys.stdin.isatty():
      raise GetoptError("input is not a tty")
    # TODO: get intro from usage/help
    cmdobj = BaseCommandCmd(self)
    cmdobj.prompt = f'{self.cmd}> '
    cmdobj.cmdloop(intro)

  # pylint: disable=unused-argument
  @staticmethod
  def getopt_error_handler(cmd, options, e, usage, subcmd=None):  # pylint: disable=unused-argument
    ''' The `getopt_error_handler` method
        is used to control the handling of `GetoptError`s raised
        during the command line parse
        or during the `main` or `cmd_`*subcmd*` calls.

        This default handler issues a warning containing the exception text,
        prints the usage message to standard error,
        and returns `True` to indicate that the error has been handled.

        The handler is called with these parameters:
        * `cmd`: the command name
        * `options`: the `options` object
        * `e`: the `GetoptError` exception
        * `usage`: the command usage or `None` if this was not provided
        * `subcmd`: optional subcommand name;
          if not `None`, is the name of the subcommand which caused the error

        It returns a true value if the exception is considered handled,
        in which case the main `run` method returns 2.
        It returns a false value if the exception is considered unhandled,
        in which case the main `run` method reraises the `GetoptError`.

        To let the exceptions out unhandled
        this can be overridden with a method which just returns `False`.

        Otherwise,
        the handler may perform any suitable action
        and return `True` to contain the exception
        or `False` to cause the exception to be reraised.
    '''
    warning("%s", e)
    if usage:
      print("Usage:", usage, file=sys.stderr)
    return True

  @uses_runstate
  def handle_signal(self, sig, frame, *, runstate: RunState):
    ''' The default signal handler, which cancels the default `RunState`.
    '''
    runstate.cancel()

  @contextmanager
  @uses_runstate
  @uses_upd
  def run_context(self, *, runstate: RunState, upd: Upd, **options_kw):
    ''' The context manager which surrounds `main` or `cmd_`*subcmd*.
        Normally this will have a bare `yield`, but you can yield
        a not `None` value to exit before the command, for example
        if the run setup fails.

        This default does several things, and subclasses should
        override it like this:

            @contextmanager
            def run_context(self):
              with super().run_context():
                try:
                  ... subclass context setup ...
                  yield
                finally:
                  ... any unconditional cleanup ...

        This base implementation does the following things:
        - provides a `self.options` which is a copy of the original
          `self.options` so that it may freely be modified during the
          command
        - provides a prevailing `RunState` as `self.options.runstate`
          if one is not already present
        - provides a `cs.upd.Upd` context for status lines
        - catches `self.options.runstate_signals` and handles them
          with `self.handle_signal`
    '''
    # prefer the runstate from the options if specified
    runstate = self.options.runstate or runstate
    # redundant try/finally to remind subclassers of correct structure
    try:
      run_options = self.options.copy(runstate=runstate, **options_kw)
      with run_options:  # make the default ThreadState
        with stackattrs(
            self,
            options=run_options,
        ):
          with upd:
            with runstate:
              with runstate.catch_signal(
                  run_options.runstate_signals,
                  call_previous=False,
                  handle_signal=self.handle_signal,
              ):
                yield

    finally:
      pass

  @popopts(
      l=('long', 'Long listing.'),
      r=('recurse', 'Recurse into subcommands.'),
      s=('short', 'Short listing.'),
  )
  def cmd_help(self, argv):
    ''' Usage: {cmd} [-l] [-s] [subcommand-names...]
          Print help for subcommands.
          This outputs the full help for the named subcommands,
          or the short help for all subcommands if no names are specified.
    '''
    options = self.options
    if not options.short and not options.long:
      options.short = not argv
    recurse = options.recurse
    short = options.short
    all_subcmds = self.subcommands()
    subcmds = argv or sorted(all_subcmds)
    unknown = False
    show_subcmds = []
    for subcmd in subcmds:
      if subcmd in subcmds:
        show_subcmds.append(subcmd)
      else:
        warning("unknown subcommand %r", subcmd)
        unknown = True
    if unknown:
      warning("I know: %s", ', '.join(sorted(all_subcmds)))
    if short:
      print("Longer help with the -l option.")
    if not recurse:
      print("Recursive help with the -r option.")
    print(
        "Usage:",
        self.usage_text(
            short=short,
            recurse=recurse,
            show_common=not short,
            show_subcmds=show_subcmds or None
        )
    )

  def cmd_info(self, argv, *, field_names=None, skip_names=None):
    ''' Usage: {cmd} [field-names...]
          Recite general information.
          Explicit field names may be provided to override the default listing.

        This default method recites the values from `self.options`,
        excluding those enumerated by `self.options.INFO_SKIP_NAMES`.

        This base method provides two optional parameters to allow
        subclasses to tune its behaviour:
        - `field_namees`: an explicit list of options attributes to print
        - `skip_names`: a list of option attributes to not print,
          default from `self.options.INFO_SKIP_NAMES`
    '''
    if skip_names is None:
      skip_names = getattr(
          self.options, 'INFO_SKIP_NAMES', ('runstate', 'runstate_signals')
      )
    self.options.popopts(argv)
    xit = 0
    options = self.options
    if argv:
      field_names = []
      for field_name in argv:
        if not hasattr(options, field_name):
          warning("no options.%s attribute", field_name)
          xit = 1
        else:
          field_names.append(field_name)
    if not field_names:
      field_names = sorted(
          field_name for field_name in options.as_dict().keys()
          if field_name not in skip_names
      )
    printt(
        *(
            (f'{field_name}:', getattr(options, field_name))
            for field_name in field_names
        )
    )
    return xit

  def repl(self, *argv, banner=None, local=None):
    ''' Run an interactive Python prompt with some predefined local names.
        Aka REPL (Read Evaluate Print Loop).

        Parameters:
        * `argv`: any notional command line arguments
        * `banner`: optional banner string
        * `local`: optional local names mapping

        The default `local` mapping is a `dict` containing:
        * `argv`: from `argv`
        * `options`: from `self.options`
        * `self`: from `self`
        * the attributes of `options`
        * the attributes of `self`
    '''
    options = self.options
    if local is None:
      pub_mapping = lambda d: {
          k: v
          for k, v in d.items()
          if k and not k.startswith('_')
      }
      local = pub_mapping(self.__dict__)
      del local['options']
      local.update(
          {
              f'options.{k}': v
              for k, v in sorted(pub_mapping(options.__dict__).items())
          }
      )
      local.update(argv=argv, cmd=self.cmd, self=self)
    if banner is None:
      vars_banner = indent(
          "\n".join(
              tabulate(
                  *(
                      [k, pformat(v, compact=True)]
                      for k, v in sorted(local.items())
                      if k and not k.startswith('_')
                  )
              )
          )
      )
      banner = f'{self.cmd}\n\n{vars_banner}\n'
    try:
      # pylint: disable=import-outside-toplevel
      from bpython import embed
    except ImportError:
      return interact(
          banner=banner,
          local=local,
      )
    return embed(
        banner=banner,
        locals_=local,
    )

  @popopts(banner_=None)
  def cmd_repl(self, argv):
    ''' Usage: {cmd}
          Run a REPL (Read Evaluate Print Loop), an interactive Python prompt.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    options = self.options
    banner = options.banner
    del options.banner
    return self.repl(*argv, banner=banner)

  @uses_upd
  def cmd_shell(self, argv, *, upd: Upd):
    ''' Usage: {cmd}
          Run a command prompt via cmd.Cmd using this command's subcommands.
    '''
    if argv:
      raise GetoptError("extra arguments")
    with upd.without():
      self.cmdloop()

  @OBSOLETE("self.options.popopts")
  def popopts(self, argv, options, **opt_specs):
    ''' A convenience shim which returns `self.options.popopts(argv,**opt_specs)`.
    '''
    if options is not self.options:
      warning(
          "obsolete use of %s.popopts\n    with options %s\n    is not self.options %s",
          self.__class__.__name__, r(options), r(self.options)
      )
    return options.popopts(argv, **opt_specs)

BaseCommandSubType = subtype(BaseCommand)

class BaseCommandCmd(Cmd):
  ''' A `cmd.Cmd` subclass used to provide interactive use of a
      command's subcommands.

      The `BaseCommand.cmdloop()` class method instantiates an
      instance of this and calls its `.cmdloop()` method
      i.e. `cmd.Cmd.cmdloop`.
  '''

  @typechecked
  def __init__(self, command: BaseCommandSubType):
    super().__init__()
    self.__command = command

  def get_names(self):
    ''' Return a list of the subcommand names.
    '''
    cmdcls = type(self.__command)
    names = []
    for method_name in dir(cmdcls):
      if method_name.startswith(cmdcls.SUBCOMMAND_METHOD_PREFIX):
        subcmd = cutprefix(method_name, cmdcls.SUBCOMMAND_METHOD_PREFIX)
        names.append('do_' + subcmd)
        ##names.append('help_' + subcmd)
    return names

  def __getattr__(self, attr):
    command = self.__command
    cmdcls = type(command)
    subcmd = cutprefix(attr, 'do_')
    if subcmd is not attr:
      method_name = cmdcls.SUBCOMMAND_METHOD_PREFIX + subcmd
      try:
        method = getattr(command, method_name)
      except AttributeError:
        pass
      else:

        def do_subcmd(arg: str):
          argv = shlex.split(arg)
          method(argv)

        do_subcmd.__name__ = attr
        do_subcmd.__doc__ = command.subcommand_usage_text(subcmd)
        return do_subcmd
      if subcmd in ('EOF', 'exit', 'quit'):
        return lambda _: True
    raise AttributeError(f'{self.__class__.__name__}.{attr}')

@uses_cmd_options(quiet=False, verbose=False)
def qvprint(*print_a, quiet, verbose, **print_kw):
  ''' Call `print()` if `options.verbose` and not `options.quiet`.
  '''
  if verbose and not quiet:
    print(*print_a, **print_kw)

@uses_quiet
def qprint(*print_a, quiet: bool, **qvprint_kw):
  ''' Call `print()` if `not options.quiet`.
      This is a compatibility shim for `qvprint()` with `verbose=not
      quiet` and `quiet=False`.
  '''
  qvprint_kw.update(
      quiet=False,
      verbose=not quiet,
  )
  return qvprint(*print_a, **qvprint_kw)

def vprint(*print_a, **qvprint_kw):
  ''' Call `print()` if `options.verbose`.
      This is a compatibility shim for `qvprint()` with `quiet=False`.
  '''
  qvprint_kw.update(quiet=False)
  return qvprint(*print_a, **qvprint_kw)

if __name__ == '__main__':

  class DemoCommand(BaseCommand):
    ''' A deomnstration CLI.
    '''

    @popopts
    def cmd_demo(self, argv):
      ''' Usage: {cmd} [args...]
            Demonstration subcommand.
      '''
      print("This is a demo.")
      print("argv =", argv)

  sys.exit(DemoCommand(sys.argv).run())

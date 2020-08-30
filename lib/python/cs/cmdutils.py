#!/usr/bin/python
#
# Command line stuff. - Cameron Simpson <cs@cskk.id.au> 03sep2015
#

''' Convenience functions for working with the Cmd module
    and other command line related stuff.
'''

from __future__ import print_function, absolute_import
from contextlib import contextmanager
from getopt import getopt, GetoptError
from os.path import basename
import sys
from types import SimpleNamespace as NS
from cs.context import nullcontext, stackattrs
from cs.deco import cachedmethod
from cs.lex import cutprefix, stripped_dedent
from cs.logutils import setup_logging, warning, exception
from cs.pfx import Pfx, XP
from cs.py.doc import obj_docstring
from cs.resources import RunState

__version__ = '20200615-post'

DISTINFO = {
    'description':
    "convenience functions for working with the Cmd module, a BaseCommand class for constructing command lines and other command line related stuff",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context', 'cs.deco', 'cs.lex', 'cs.logutils', 'cs.pfx',
        'cs.py.doc', 'cs.resources'
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

  def wrapped(self, *a, **kw):
    ''' Run a `Cmd` "do" method with some context and handling.
    '''
    if not funcname.startswith('do_'):
      raise ValueError("function does not start with 'do_': %s" % (funcname,))
    argv0 = funcname[3:]
    with Pfx(argv0):
      try:
        return dofunc(self, *a, **kw)
      except GetoptError as e:
        warning("%s", e)
        self.do_help(argv0)
        return None
      except Exception as e:
        exception("%s", e)
        return None

  wrapped.__name__ = '@docmd(%s)' % (funcname,)
  wrapped.__doc__ = dofunc.__doc__
  return wrapped

class BaseCommand:
  ''' A base class for handling nestable command lines.

      This class provides the basic parse and dispatch mechanisms
      for command lines.
      To implement a command line
      one instantiates a subclass of BaseCommand:

          class MyCommand(BaseCommand):
            GETOPT_SPEC = 'ab:c'
            USAGE_FORMAT = r"""Usage: {cmd} [-a] [-b bvalue] [-c] [--] arguments...
              -a    Do it all.
              -b    But using bvalue.
              -c    The 'c' option!
            """
          ...
          the_cmd = MyCommand()

      Running a command is done by:

          the_cmd.run(argv)

      The subclass is customised by overriding the following methods:
      * `apply_defaults(options)`:
        prepare the initial state of `options`
        before any command line options are applied
      * `apply_opts(options,opts)`:
        apply the `opts` to `options`.
        `opts` is an `(option,value)` sequence
        as returned by `getopot.getopt`.
      * `cmd_`*subcmd*`(argv,options)`:
        if the command line options are followed by an argument
        whose value is *subcmd*,
        then the method `cmd_`*subcmd*`(argv,options)`
        will be called where `argv` contains the command line arguments
        after *subcmd*.
      * `main(argv,options)`:
        if there are no command line aguments after the options
        or the first argument does not have a corresponding
        `cmd_`*subcmd* method
        then method `main(argv,options)`
        will be called where `argv` contains the command line arguments.
      * `run_context(argv,options,cmd)`:
        a context manager to provide setup or teardown actions
        to occur before and after the command implementation respectively.
        If the implementation is a `cmd_`*subcmd* method
        then this is called with `cmd=`*subcmd*;
        if the implementation is `main`
        then this is called with `cmd=None`.

      To aid recursive use
      it is intended that all the per command state
      is contained in the `options` object
      and therefore that in typical use
      all of `apply_opts`, `cmd_`*subcmd*, `main` and `run_context`
      should be static methods making no reference to `self`.

      Editorial: why not arparse?
      Primarily because when incorrectly invoked
      an argparse command line prints the help/usage messgae
      and aborts the whole programme with `SystemExit`.
  '''

  SUBCOMMAND_METHOD_PREFIX = 'cmd_'

  @classmethod
  def subcommands(cls):
    ''' Return a mapping of subcommand names to class attributes
        for attributes which commence with `cls.SUBCOMMAND_METHOD_PREFIX`
        by default `'cmd_'`.
    '''
    prefix = cls.SUBCOMMAND_METHOD_PREFIX
    return {
        cutprefix(attr, prefix): getattr(cls, attr)
        for attr in dir(cls)
        if attr.startswith(prefix)
    }

  @classmethod
  @cachedmethod
  def usage_text(cls, *, cmd=None, format_mapping=None):
    ''' Compute the "Usage: message for this class
        from the top level `USAGE_FORMAT`
        and the `'Usage:'`-containing docstrings
        from its `cmd_*` methods.

        This is a cached method because it tries to update the
        method docstrings after formatting, which is bad if it
        happens more than once.
    '''
    if cmd is None:
      cmd = cls.__name__
    if format_mapping is None:
      format_mapping = {}
    if cmd is not None or 'cmd' not in format_mapping:
      format_mapping['cmd'] = cls.__name__ if cmd is None else cmd
    usage_format_mapping = dict(getattr(cls, 'USAGE_KEYWORDS', {}))
    usage_format_mapping.update(format_mapping)
    usage_format = getattr(cls, 'USAGE_FORMAT', None)
    subcmds = cls.subcommands()
    has_subcmds = subcmds and list(subcmds) != ['help']
    if usage_format is None:
      usage_format = (
          r'Usage: {cmd} subcommand [...]'
          if has_subcmds else 'Usage: {cmd} [...]'
      )
    usage_message = usage_format.format_map(usage_format_mapping)
    if has_subcmds:
      subusages = []
      for attr, method in sorted(subcmds.items()):
        with Pfx(attr):
          subusage = cls.subcommand_usage_text(attr)
          if subusage:
            subusages.append(subusage.replace('\n', '\n  '))
      if subusages:
        usage_message = '\n'.join(
            [usage_message, '  Subcommands:'] + [
                '    ' + subusage.replace('\n', '\n    ')
                for subusage in subusages
            ]
        )
    return usage_message

  @classmethod
  def subcommand_usage_text(cls, subcmd, fulldoc=False):
    ''' Return the usage text for a subcommand.

        Parameters:
        * `subcmd`: the subcommand name
        * `fulldoc`: if true (default `False`)
          return the full docstring with the Usage section expanded
          otherwise just return the Usage section.
    '''
    method = cls.subcommands()[subcmd]
    subusage = None
    try:
      classy = issubclass(method, BaseCommand)
    except TypeError:
      classy = False
    if classy:
      subusage = method.usage_text(cmd=subcmd)
    else:
      doc = obj_docstring(method)
      if doc and 'Usage:' in doc:
        pre_usage, post_usage = doc.split('Usage:', 1)
        pre_usage = pre_usage.strip()
        post_usage_parts = post_usage.split('\n\n', 1)
        post_usage_format = post_usage_parts.pop(0)
        subusage_format = stripped_dedent(post_usage_format)
        if subusage_format:
          mapping = dict(sys.modules[method.__module__].__dict__)
          mapping.update(cmd=subcmd)
          subusage = subusage_format.format_map(mapping)
          if fulldoc:
            parts = [pre_usage, subusage] if pre_usage else [subusage]
            parts.extend(post_usage_parts)
            subusage = '\n\n'.join(parts)
    return subusage if subusage else None

  @classmethod
  def add_usage_to_docstring(cls):
    ''' Append `cls.usage_text()` to `cls.__doc__`.
    '''
    usage_message = cls.usage_text()
    cls.__doc__ += (
        '\n\nCommand line usage:\n\n    ' +
        usage_message.replace('\n', '\n    ')
    )

  def apply_defaults(self, options):
    ''' Stub `apply_defaults` method.

        Subclasses can override this to set up the initial state of `options`.
    '''

  def run(self, argv=None, options=None, cmd=None):
    ''' Run a command from `argv`.
        Returns the exit status of the command.
        Raises `GetoptError` for unrecognised options.

        Parameters:
        * `argv`:
          optional command line arguments
          including the main command name if `cmd` is not specified.
          The default is `sys.argv`.
          The contents of `argv` are copied,
          permitting desctructive parsing of `argv`.
        * `options`:
          a object for command state and context.
          If not specified a new `SimpleNamespace`
          is allocated for use as `options`,
          and prefilled with `.cmd` set to `cmd`
          and other values as set by `.apply_default(options)`
          if such a method is provided.
        * `cmd`:
          optional command name for context;
          if this is not specified it is taken from `argv.pop(0)`.

        The command line arguments are parsed according to `getopt_spec`.
        If `getopt_spec` is not empty
        then `apply_opts(opts,options)` is called
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
    if options is None:
      options = NS()
    if argv is None:
      argv = list(sys.argv)
      if cmd is not None:
        # we consume the first argument anyway
        argv.pop(0)
    else:
      argv = list(argv)
    if cmd is None:
      cmd = basename(argv.pop(0))
    options.cmd = cmd
    log_level = getattr(options, 'log_level', None)
    loginfo = setup_logging(cmd, level=log_level)
    # post: argv is list of arguments after the command name
    usage = self.usage_text(cmd=cmd)
    options.usage = usage
    options.loginfo = loginfo
    self.apply_defaults(options)
    # we catch GetoptError from this suite...
    try:
      getopt_spec = getattr(self, 'GETOPT_SPEC', '')
      # we do this regardless in order to honour '--'
      opts, argv = getopt(argv, getopt_spec, '')
      if getopt_spec:
        self.apply_opts(opts, options)

      subcmds = self.subcommands()
      if subcmds and list(subcmds) != ['help']:
        # expect a subcommand on the command line
        if not argv:
          raise GetoptError(
              "missing subcommand, expected one of: %s" %
              (', '.join(sorted(subcmds.keys())),)
          )
        subcmd = argv.pop(0)
        try:
          main = getattr(self, self.SUBCOMMAND_METHOD_PREFIX + subcmd)
        except AttributeError:
          raise GetoptError(
              "%s: unrecognised subcommand, expected one of: %s" % (
                  subcmd,
                  ', '.join(sorted(subcmds.keys())),
              )
          )
        subcmd_context = Pfx(subcmd)
        try:
          main_is_class = issubclass(main, BaseCommand)
        except TypeError:
          main_is_class = False
        if main_is_class:
          cls = main
          main = lambda argv, options: cls().run(
              argv, options=options, cmd=subcmd
          )
      else:
        subcmd = cmd
        try:
          main = self.main
        except AttributeError:
          raise GetoptError("no main method and no subcommand methods")
        subcmd_context = nullcontext()
      upd_context = options.loginfo.upd
      if upd_context is None:
        upd_context = nullcontext()
      with RunState(cmd) as runstate:
        with upd_context:
          with stackattrs(options, cmd=subcmd, runstate=runstate,
                          upd=options.loginfo.upd):
            with self.run_context(argv, options):
              with subcmd_context:
                return main(argv, options)
    except GetoptError as e:
      handler = getattr(self, 'getopt_error_handler')
      if handler and handler(cmd, options, e, usage):
        return 2
      raise

  @staticmethod
  def getopt_error_handler(cmd, options, e, usage):
    ''' The `getopt_error_handler` method
        is used to control the handling of `GetoptError`s raised
        during the command line parse
        or during the `main` or `cmd_`*subcmd*` calls.

        The handler is called with these parameters:
        * `cmd`: the command name
        * `options`: the `options` object
        * `e`: the `GetoptError` exception
        * `usage`: the command usage or `None` if this was not provided

        It returns a true value if the exception is considered handled,
        in which case the main `run` method returns 2.
        It returns a false value if the exception is considered unhandled,
        in which case the main `run` method reraises the `GetoptError`.

        This default handler prints an error message to standard error,
        prints the usage message (if specified) to standard error,
        and returns `True` to indicate that the error has been handled.

        To let the exceptions out unhandled
        this can be overridden with a method which just returns `False`
        or even by setting the `getopt_error_handler` attribute to `None`.

        Otherwise,
        the handler may perform any suitable action
        and return `True` to contain the exception
        or `False` to cause the exception to be reraised.
    '''
    print("%s: %s" % (cmd, e), file=sys.stderr)
    if usage:
      print(usage.rstrip(), file=sys.stderr)
    return True

  @staticmethod
  @contextmanager
  def run_context(argv, options):
    ''' Stub context manager which surrounds `main` or `cmd_`*subcmd*.
    '''
    # redundant try/finally to remind subclassers of correct structure
    try:
      yield
    finally:
      pass

  @classmethod
  def cmd_help(cls, argv, options):
    ''' Usage: {cmd} [subcommand-names...]
          Print the help for the named subcommands,
          or for all subcommands if no names are specified.
    '''
    subcmds = cls.subcommands()
    if argv:
      fulldoc = True
    else:
      fulldoc = False
      argv = sorted(subcmds)
    xit = 0
    print("help:")
    for subcmd in argv:
      with Pfx(subcmd):
        if subcmd not in subcmds:
          warning("unknown subcommand")
          xit = 1
          continue
        subusage = cls.subcommand_usage_text(subcmd, fulldoc=fulldoc)
        if not subusage:
          warning("no help")
          xit = 1
          continue
        print(' ', subusage.replace('\n', '\n    '))
    return xit

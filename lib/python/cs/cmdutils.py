#!/usr/bin/python
#
# Convenience functions for working with the Cmd module
# and other command line related stuff.
# - Cameron Simpson <cs@cskk.id.au> 03sep2015
#

from __future__ import print_function, absolute_import
from contextlib import contextmanager
from getopt import getopt, GetoptError
from logging import warning, exception
from cs.mappings import StackableValues
from cs.pfx import Pfx
from cs.resources import RunState

DISTINFO = {
    'description':
    "convenience functions for working with the Cmd module and other command line related stuff",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.mappings', 'cs.pfx', 'cs.resources'],
}

def docmd(dofunc):
  ''' Decorator for Cmd subclass methods
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

        @docmd
        def do_something(...):
          ... do something ...
  '''

  def wrapped(self, *a, **kw):
    funcname = dofunc.__name__
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
        `opts` is an option value mapping
        as returned by `getopot.getopt`.
      * `cmd_`*subcmd*`(argv,options)`:
        if the command line options are followed by an argument
        whose value is *subcmd*,
        then method `cmd_`*subcmd*`(argv,options)`
        will be called where `argv` contains the command line arguments
        after *subcmd*.
      * `main(argv,options)`:
        if there are no command line aguments after the options
        or the first argument does not have a corresponding
        `cmd_`*subcmd* method
        then method `main(argv,options)`
        will be called where `argv` contains the command line arguments.
      * `run_context(argv,options,cmd=None)`:
        a context manager to provide setup or teardown actions
        to occur before and after the command implementation respectively.
        If the implementation is a `cmd_`*subcmd* method
        then this is called with `cmd=`*subcmd`;
        if the implementation is `main`
        then this is called with `cmd=None`.

      To aid recursive use
      it is intended that all the per command state
      is contained in the `options` object
      and therefore that in typical use
      all of `apply_opts`, `cmd_`*subcmd*`, `main` and `run_context`
      should be static methods making no reference to `self`.

      Editorial: why not arparse?
      Primarily because when incorrectly invoked
      an argparse command line prints the help/usage messgae
      and aborts the whole programme with `SystemExit`.
  '''

  def __init__(self, getopt_spec=None):
    ''' Initialise the BaseCommand.

        Parameters:
        * `getopt_spec`: optional `getopt.getopt` compatible
          option specifier.
          The default comes from the class' `.GETOPT_SPEC` attribute.
    '''
    if getopt_spec is None:
      getopt_spec = self.GETOPT_SPEC
    self.getopt_spec = getopt_spec

  def apply_defaults(self, options):
    ''' Stub apply_defaults method.

        Subclasses can override this to set up the initial state of `options`.
    '''

  def run(self, argv, options=None, cmd=None):
    ''' Run a command from `argv`.
        Returns the exit status of the command.
        Raises `GetoptError` for unrecognised options.

        Parameters:
        * `argv`:
          the command line arguments
          including the main command name if `cmd` is not specified.
        * `options`:
          a object for command state and context.
          If not specified a new `cs.mappings.StackableValues`
          is allocated for use as `options`,
          and prefilled with `.cmd` set to `cmd`
          and other values as set by `.apply_default(options)`
          if such a method is provided.
        * `cmd`:
          optional command name for context;
          if this is not specified it is taken as `argv[0]`
          which is then popped from the list.

        The command line arguments are parsed according to `getopt_spec`.
        If `getopt_spec` is not empty
        then `apply_opts(opts,options)` is called
        to apply the supplied options to the state.

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
    if cmd is None:
      cmd = argv.pop(0)
    if options is None:
      options = StackableValues(cmd=cmd)
      self.apply_defaults(options)
    with Pfx(cmd):
      opts, argv = getopt(argv, self.getopt_spec)
      if self.getopt_spec:
        self.apply_opts(opts, options)
      runstate = options.runstate = RunState(cmd)
      # expose the runstate for use by global caller who only has "self" :-(
      self.runstate = runstate
      with runstate:
        if argv:
          # see if the first arg is a subcommand name
          # by check for a cmd_{subcommand} method
          subcmd_attr = 'cmd_' + argv[0]
          subcmd_method = getattr(self, subcmd_attr, None)
          if subcmd_method is not None:
            subcmd = argv.pop(0)
            with Pfx(subcmd):
              with self.run_context(argv, options, cmd=subcmd):
                return subcmd_method(argv, options, cmd=subcmd)
        try:
          main = self.main
        except AttributeError:
          raise GetoptError(
              "%s: missing subcommand and no main method" %
              (type(self).__name__,)
          )
        else:
          with self.run_context(argv, options, cmd=None):
            return main(argv, options, cmd=None)

  @staticmethod
  @contextmanager
  def run_context(argv, options, cmd):
    ''' Stub context manager which surronds `main` or `cmd_`*subcmd*.
    '''
    yield

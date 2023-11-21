#!/usr/bin/env python3

''' Docker related utilities.
'''

import csv
from dataclasses import dataclass, field
from getopt import GetoptError
from io import StringIO
import os
from os.path import (
    abspath,
    basename,
    isabs as isabspath,
    isdir as isdirpath,
    join as joinpath,
    normpath,
)
from subprocess import CompletedProcess
import sys
from tempfile import TemporaryDirectory
from typing import List, Optional

from typeguard import typechecked

from cs.cmdutils import BaseCommand, BaseCommandOptions
from cs.context import stackattrs
from cs.fs import validate_rpath
from cs.pfx import Pfx
from cs.psutils import print_argv, run

def main(argv=None, **run_kw):
  ''' Invoke the `DockerUtilCommand` with `argv`.
  '''
  return DockerUtilCommand(argv).run(**run_kw)

DOCKER_COMMAND_ENVVAR = 'DK_COMMAND'
DOCKER_COMMAND_DEFAULT = 'docker'
default_docker_command = lambda: os.environ.get(
    DOCKER_COMMAND_ENVVAR, DOCKER_COMMAND_DEFAULT
)

DOCKER_COMPOSE_COMMAND_ENVVAR = 'DK_COMPOSE_COMMAND'
DOCKER_COMPOSE_COMMAND_DEFAULT = [default_docker_command(), 'compose']
default_docker_compose_command = lambda: os.environ.get(
    DOCKER_COMPOSE_COMMAND_ENVVAR, DOCKER_COMPOSE_COMMAND_DEFAULT
)

DOCKER_COMPOSE_CONFIG_ENVVAR = 'DK_COMPOSE_YML'
DOCKER_COMPOSE_CONFIG_DEFAULT = 'docker-compose.yml'
default_docker_compose_config = lambda: os.environ.get(
    DOCKER_COMPOSE_CONFIG_ENVVAR, DOCKER_COMPOSE_CONFIG_DEFAULT
)

@dataclass
class DockerUtilCommandOptions(BaseCommandOptions):
  # the default container for "docker exec"
  docker_command: str = field(default_factory=default_docker_command)
  docker_compose_command: str = field(
      default_factory=default_docker_compose_command
  )
  docker_compose_config: str = field(
      default_factory=default_docker_compose_config
  )
  target_container: str = None

class DockerUtilCommand(BaseCommand):
  ''' A command line mode for working with Docker et al.
  '''

  GETOPT_SPEC = 'f:nqvx'

  USAGE_FORMAT = r'''Usage: {cmd} [options...] [@container] subcommand...
    -f docker-compose.yml
      Specify {DOCKER_COMPOSE_COMMAND_DEFAULT} YAML file.
      Default: {DOCKER_COMPOSE_CONFIG_DEFAULT!r}, overridden by ${DOCKER_COMPOSE_CONFIG_ENVVAR}
    @container  Specify a target container.
  '''

  USAGE_KEYWORDS = dict(
      DOCKER_COMPOSE_COMMAND_DEFAULT=DOCKER_COMPOSE_COMMAND_DEFAULT,
      DOCKER_COMPOSE_CONFIG_DEFAULT=DOCKER_COMPOSE_CONFIG_DEFAULT,
      DOCKER_COMPOSE_CONFIG_ENVVAR=DOCKER_COMPOSE_CONFIG_ENVVAR,
  )

  Options = DockerUtilCommandOptions

  def apply_preargv(self, argv):
    ''' Consume a leading @container_name if present.
    '''
    if argv and argv[0].startswith('@'):
      target_ = argv.pop(0)
      self.options.target_container = target_[1:]
    return argv

  def docker(self, *dk_argv) -> CompletedProcess:
    ''' Invoke `docker`.
    '''
    options = self.options
    return docker(
        *dk_argv,
        exe=options.docker_command,
        doit=options.doit,
        quiet=options.quiet,
    )

  def docker_compose(self, *dc_argv) -> CompletedProcess:
    ''' Invoke `docker-compose`.
    '''
    options = self.options
    return docker_compose(
        *dc_argv,
        exe=options.docker_compose_command,
        config=options.docker_compose_config,
        doit=options.doit,
        quiet=options.quiet,
    )

  def cmd_ps(self, argv):
    ''' Usage: {cmd}
          Show the running docker containers.
    '''
    options = self.options
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    return self.docker_compose('ps').returncode

  def cmd_run(self, argv):
    ''' Usage: {cmd} [options] image [command] [arg...]
          Invoke command in an instance of image.
          A read only directory for input data will be present as /input.
          A read/write directory for output data will be present at /output.
          The command's working directory will be /output.
          -i inputpath
              Mount inputpath as /input/basename(inputpath)
          -I inputpath dockerpath
              Mount inputpath as /input/dockerpath
          --root
              Do not switch to the current effective uid:gid inside
              the container.
          -U  Update the local copy of image before running.
          Other options are passed to "docker run".
    '''
    options = self.options
    DR = DockerRun()
    DR.popopts(argv)
    if not argv:
      raise GetoptError("missing image")
    DR.image = argv.pop(0)
    with TemporaryDirectory(dir='.') as T:
      with stackattrs(DR, outputpath=T):
        DR.run(*argv, exe=options.docker_command)

def docker(
    *dk_argv,
    exe=None,
    config=None,
    doit=True,
    quiet=True
) -> CompletedProcess:
  ''' Invoke `docker` with `dk_argv`.
  '''
  if exe is None:
    exe = default_docker_command()
  return run([exe, *dk_argv], doit=doit, quiet=quiet)

def docker_compose(
    *dc_argv,
    exe=None,
    config=None,
    doit=True,
    quiet=True
) -> CompletedProcess:
  ''' Invoke `docker-compose` with `dc_argv`.
  '''
  if exe is None:
    exe = default_docker_compose_command()
  if isinstance(exe, str):
    exe = exe,
  if config is None:
    config = default_docker_compose_config()
  return run([*exe, '-f', config, *dc_argv], doit=doit, quiet=quiet)

def mount_escape(*args) -> str:
  ''' Escape the strings in `args` for us in the `docker run --mount` option.

      Apparently the arguments to `docker run --mount` are in fact
      a CSV data line.
      (Of course you need to find this allusion in the bug tracker,
      heaven forfend that the docs actually detail this kind of
      thing.)

      Rather that try to enumerate what needs escaping, here we use
      the `csv` module to escape using the default "excel" dialect.
  '''
  buf = StringIO()
  csvw = csv.writer(buf)
  csvw.writerow(args)
  return buf.getvalue().rstrip('\n')

@dataclass
class DockerRun:
  INPUTDIR_DEFAULT = '/input'
  OUTPUTDIR_DEFAULT = '/output'
  image: str = None
  options: List[str] = field(default_factory=list)
  input_root: str = INPUTDIR_DEFAULT
  input_map: dict = field(default_factory=dict)
  output_root: str = OUTPUTDIR_DEFAULT
  outputpath: str = None
  as_root: bool = False
  pull_mode: str = 'missing'

  @typechecked
  def popopts(self, argv: List[str]) -> None:
    ''' Pop options from the list `argv`.

        The command's working directory will be /output.
        -i inputpath
            Mount inputpath as /input/basename(inputpath)
        -I inputpath dockerpath
            Mount inputpath as /input/dockerpath
        --root
            Do not switch to the current effective uid:gid inside
            the container.
        -U  Update the local copy of image before running.
        Other options are passed to "docker run".
    '''
    while argv:
      arg0 = argv.pop(0)
      if arg0 == '--':
        break
      if not arg0.startswith('-'):
        argv.insert(0, arg0)
        break
      with Pfx(arg0):
        assert arg0.startswith('-')
        arg0_ = arg0[1:]
        if arg0 == '-i':
          inputpath = argv.pop(0)
          inputmount = basename(inputpath)
          self.add_input(inputmount, inputpath)
        elif arg0 == '-I':
          inputpath = argv.pop(0)
          inputmount = argv.pop(0)
          self.add_input(inputmount, inputpath)
        elif arg0 == '--root':
          self.as_root = True
        elif arg0 == '-U':
          self.options.append('--pull-mode')
          self.options.append('always')
        elif arg0_ in (
            'd',
            'detach',
            'help',
            'init',
            'i',
            'interactive',
            'no-healthcheck',
            'oom-kill-disable',
            'privileged',
            'P',
            'publish-all',
            'q',
            'quiet',
            'read-only',
            'rm',
            'sig-proxy',
            't',
            'tty',
        ):
          self.options.append(arg0)
        else:
          arg1 = argv.pop(0)
          self.options.append(arg0)
          self.options.append(arg1)

  def add_input(self, inputmount: str, inputpath: str):
    ''' Add an input mount mapping. '''
    with Pfx("inputpath:%r", inputpath):
      if not inputpath:
        raise ValueError('may not be empty')
    with Pfx("inputmount:%r", inputmount):
      validate_rpath(inputmount)
      if '/' in inputmount:
        raise ValueError('may not contain multiple components')
      if inputmount in self.input_map:
        raise ValueError(f'already present in input_map:{self.input_map!r}')
    self.input_map[inputmount] = abspath(inputpath)

  def run(self, *argv, doit=None, quiet=None, docker_exe=None):
    ''' Run a command via `docker run`.
        Return the `CompletedProcess` result or `None` if `doit` is false.
    '''
    if doit is None:
      doit = True
    if quiet is None:
      quiet = True
    argv = list(argv)  # work with a mutable copy
    if docker_exe is None:
      docker_exe = default_docker_command()
    if self.image is None:
      raise ValueError("self.image is still None")
    with Pfx("input_root:%r", self.input_root):
      if not isabspath(self.input_root):
        raise ValueError('not an absolute path')
      if self.input_root != normpath(self.input_root):
        raise ValueError('not normalised')
    with Pfx("output_root:%r", self.output_root):
      if not isabspath(self.output_root):
        raise ValueError('not an absolute path')
      if self.output_root != normpath(self.output_root):
        raise ValueError('not normalised')
    with Pfx("outputpath:%r", self.outputpath):
      # output mount point
      if not self.outputpath:
        raise ValueError("no outputpath")
      if not isdirpath(self.outputpath):
        raise ValueError('not a directory')
    docker_argv = [
        docker_exe,
        'run',
        '--rm',
        '-w',
        self.output_root,
        *self.options,
    ]
    # input readonly mounts
    for inputmount, inputpath in self.input_map.items():
      mnt = joinpath(self.input_root, inputmount)
      with Pfx("%r->%r", mnt, inputpath):
        docker_argv.extend(
            [
                '--mount',
                mount_escape(
                    'type=bind',
                    'readonly',
                    f'source={inputpath}',
                    f'destination={mnt}',
                ),
            ]
        )
    docker_argv.extend(
        [
            '--mount',
            mount_escape(
                'type=bind',
                f'source={abspath(self.outputpath)}',
                f'destination={self.output_root}',
            ),
        ]
    )
    if self.as_root:
      entrypoint = argv.pop(0)
    else:
      entrypoint = '/usr/bin/s6-setuidgid'
      uid = os.geteuid()
      gid = os.getegid()
      argv.insert(0, f'{uid}:{gid}')
    docker_argv.extend(['--entrypoint', entrypoint])
    docker_argv.append('--')
    docker_argv.append(self.image)
    docker_argv.extend(argv)
    return run(docker_argv, doit=doit, quiet=quiet)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

#!/usr/bin/env python3

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
import os.path
from os.path import expanduser
from getopt import GetoptError
from pprint import pformat
import sys
from typing import Iterable

from bs4 import NavigableString
from typeguard import typechecked

from cs.cmdutils import BaseCommand, popopts
from cs.context import stackattrs
from cs.later import Later
from cs.lex import (
    cutprefix,
    cutsuffix,
    get_dotted_identifier,
    is_identifier,
    printt,
    skipwhite,
)
import cs.logutils
from cs.logutils import debug, error, warning
import cs.pfx
from cs.pfx import Pfx, pfx_call
from cs.queues import ListQueue
from cs.urlutils import URL

from . import (
    DEFAULT_JOBS, DEFAULT_FLAGS_CONJUNCTION, DEFAULT_MITM_LISTEN_HOST,
    DEFAULT_MITM_LISTEN_PORT
)
from .parse import get_action_args, get_delim_regexp, import_name
from .pilfer import Pilfer
from .pipelines import PipeLineSpec
from .sitemap import FlowState

def main(argv=None):
  ''' Pilfer command line function.
  '''
  return PilferCommand(argv).run()

@typechecked
def urls(url, stdin=None, cmd=None) -> Iterable[str]:
  ''' Generator to yield input URLs.
  '''
  if stdin is None:
    stdin = sys.stdin
  if cmd is None:
    cmd = cs.pfx.cmd
  if url != '-':
    # literal URL supplied, deliver to pipeline
    yield url
  else:
    # read URLs from stdin
    try:
      do_prompt = stdin.isatty()
    except AttributeError:
      do_prompt = False
    if do_prompt:
      # interactively prompt for URLs, deliver to pipeline
      prompt = cmd + ".url> "
      while True:
        try:
          url = input(prompt)
        except EOFError:
          break
        else:
          yield url
    else:
      # read URLs from non-interactive stdin, deliver to pipeline
      lineno = 0
      for line in stdin:
        lineno += 1
        with Pfx("stdin:%d", lineno):
          if not line.endswith('\n'):
            raise ValueError("unexpected EOF - missing newline")
          url = line.strip()
          if not line or line.startswith('#'):
            debug("SKIP: %s", url)
            continue
          yield url

class PilferCommand(BaseCommand):

  @dataclass
  class Options(BaseCommand.Options):
    configpath: str = field(
        default_factory=lambda: os.environ.get('PILFERRC') or
        expanduser('~/.pilferrc')
    )
    jobs: int = DEFAULT_JOBS
    flagnames: str = tuple(DEFAULT_FLAGS_CONJUNCTION.replace(',', ' ').split())
    db_url: str = None

    @property
    def configpaths(self):
      ''' A list of the config filesystem paths.
      '''
      return [fspath for fspath in self.configpath.split(':') if fspath]

    COMMON_OPT_SPECS = dict(
        **BaseCommand.Options.COMMON_OPT_SPECS,
        c_=('configpath', 'Colon separated list of config paths.'),
        j_=(
            'jobs',
            ''' How many jobs (actions: URL fetches, minor computations)
                to run at a time. Default: {DEFAULT_JOBS}
            ''',
            int,
        ),
        F_=(
            'flagnames',
            'Flags which must be true for operation to continue.',
            lambda s: s.replace(',', ' ').split(),
        ),
        load_cookies='Load the browser cookie state into the Pilfer seesion.',
        u='unbuffered',
        x=('trace', 'Trace action execution.'),
    )

  @contextmanager
  def run_context(self):
    ''' Apply the `options.runstate` to the main `Pilfer`.
    '''
    options = self.options
    badopts = False
    # sanity check the flagnames
    for raw_flagname in options.flagnames:
      with Pfx(raw_flagname):
        flagname = cutprefix(raw_flagname, '!')
        if not is_identifier(flagname):
          error('invalid flag specifier')
          badopts = True
    if badopts:
      raise GetoptError(f'invalid flags: {options.flagnames!r}')
    with super().run_context():
      later = Later(self.options.jobs)
      with later:
        pilfer = Pilfer(
            later=later,
            rcpaths=options.configpaths,
            sqltags_db_url=options.db_url,
        )
        with pilfer:
          with stackattrs(
              self.options,
              later=later,
              pilfer=pilfer,
              sqltags=pilfer.sqltags,
              db_url=pilfer.sqltags.db_url,
          ):
            if self.options.load_cookies:
              pilfer.load_browser_cookies(pilfer.session.cookies)
            yield

  @staticmethod
  def get_argv_pipespec(argv, argv_offset=0):
    ''' Parse a pipeline specification from the argument list `argv`.
        Return `(PipeLineSpec,new_argv_offset)`.

        A pipeline specification is specified by a leading argument of the
        form *pipe_name*`:{`, followed by arguments defining functions for the
        pipeline, and a terminating argument of the form `}`.

        Note: this syntax works well with traditional Bourne shells.
        Zsh users can use 'setopt IGNORE_CLOSE_BRACES' to get
        sensible behaviour. Bash users may be out of luck.
    '''
    start_arg = argv[argv_offset]
    pipe_name = cutsuffix(start_arg, ':{')
    if pipe_name is start_arg or not pipe_name:
      raise ValueError('expected "pipe_name:{", got: %r' % (start_arg,))
    with Pfx(start_arg):
      argv_offset += 1
      spec_offset = argv_offset
      while argv[argv_offset] != '}':
        argv_offset += 1
      spec = PipeLineSpec(pipe_name, argv[spec_offset:argv_offset])
      argv_offset += 1
      return spec, argv_offset

  @popopts(
      md=('show_md', 'Show metadata.'),
      modified=('Only update the cache if the content is modified.')
  )
  def cmd_cache(self, argv):
    ''' Usage: {cmd} URLs...
          Cache the GET content of URLs.
    '''
    if not argv:
      raise GetoptError('missing URs')
    options = self.options
    mode = 'modified' if options.modified else 'missing'
    show_md = options.show_md
    P = options.pilfer
    for url in argv:
      with Pfx("cache %s", url):
        md_map = P.cache_url(url, mode=mode)
        if show_md:
          print(url)
          for cache_key, md in sorted(md_map.items()):
            print(" ", cache_key)
            printt(
                *[
                    [f'    {mdk}', pformat(mdv)]
                    for mdk, mdv in sorted(md.items())
                ]
            )

  @popopts(md=('show_md', 'Show metadata.'))
  def cmd_cacheq(self, argv):
    ''' Usage: {cmd} [cache-keys...]
          Query the URLs associated with the cache keys.
    '''
    options = self.options
    show_md = options.show_md
    cache = options.pilfer.content_cache
    with cache:
      cache_keys = argv or sorted(cache.keys())
      if show_md:
        for cache_key in cache_keys:
          print(cache_key)
          md = cache.get(cache_key, {})
          printt(
              *([f'  {mdk}', pformat(mdv)] for mdk, mdv in sorted(md.items()))
          )
      else:
        printt(
            *(
                [cache_key, cache.get(cache_key, {}).get('url')]
                for cache_key in cache_keys
            )
        )

  @popopts(soup='Dump the page soup, normally omitted.')
  def cmd_dump(self, argv):
    ''' Usage: {cmd} URL
          Fetch URL and dump information from it.
    '''
    if not argv:
      raise GetoptError("missing URL")
    url = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments after URL: {argv!r}')
    print(url)
    state = FlowState(url=url)
    state.GET()
    printt(
        (f'{state.request.method} response headers:',),
        *[
            (f'  {key}', value) for key, value in sorted(
                state.response.headers.items(), key=lambda kv: kv[0].lower()
            )
        ],
    )
    soup = state.soup
    if soup is None:
      print("no soup for content_type", state.content_type)
    else:
      table = []
      title = soup.head.title
      if title:
        table.append(["Title:", title.string])
      meta = state.meta
      if meta.tags:
        table.extend(
            (
                ['Tags:'],
                *(
                    [f'  {tag_name}', tag_value]
                    for tag_name, tag_value in sorted(meta.tags.items())
                ),
            )
        )
      if meta.properties:
        table.extend(
            (
                ['Properties:'],
                *(
                    [f'  {tag_name}', tag_value]
                    for tag_name, tag_value in sorted(meta.properties.items())
                ),
            )
        )
      links_by_rel = state.links
      if links_by_rel:
        table.extend(
            (
                ["Links:"], *(
                    [
                        f'  {rel}',
                        "\n".join(
                            sorted(
                                f'{link.attrs["href"]} {link.attrs.get("type","")}'
                                for link in links
                                if link.attrs.get('href')
                            )
                        ),
                    ]
                    for rel, links in sorted(links_by_rel.items())
                )
            )
        )
      printt(*table)
    if soup is not None and self.options.soup:
      print("Content:", state.content_type)
      table = []
      q = ListQueue([('', soup.head), ('', soup.body)])
      for indent, tag in q:
        subindent = indent + '  '
        # TODO: looks like commants are also NavigableStrings, intercept here
        if isinstance(tag, NavigableString):
          text = str(tag).strip()
          if text:
            table.append(('', text))
          continue
        if tag.name == 'script':
          continue
        # sorted copy of the attributes
        attrs = dict(sorted(tag.attrs.items()))
        label = tag.name
        # pop off the id attribute if present, include in the label
        try:
          id_attr = attrs.pop('id')
        except KeyError:
          pass
        else:
          label += f' #{id_attr}'
        children = list(tag.children)
        if not attrs and len(children) == 1 and isinstance(children[0],
                                                           NavigableString):
          desc = str(children[0].strip())
        else:
          desc = "\n".join(
              f'{attr}={value!r}' for attr, value in attrs.items()
          ) if attrs else ''
          for index, subtag in enumerate(children):
            q.insert(index, (subindent, subtag))
        table.append((
            f'{indent}{label}',
            desc,
        ))
      printt(*table)

  @popopts
  def cmd_from(self, argv):
    ''' Usage: {cmd} source [pipeline-defns...]
          Scrape information from source.
          Source may be a URL or "-" to read URLs from standard input.
    '''
    options = self.options
    P = options.pilfer
    if not argv:
      raise GetoptError("missing URL")
    url = argv.pop(0)
    # Load any named pipeline definitions on the command line.
    argv_offset = 0
    while argv and argv[argv_offset].endswith(':{'):
      spec, argv_offset = self.get_argv_pipespec(argv, argv_offset)
      P.pipe_specs[spec.name] = spec
    # prepare the main pipeline specification from the remaining argv
    if not argv:
      raise GetoptError("missing main pipeline")
    pipespec = PipeLineSpec(name="CLI", stage_specs=argv)
    # prepare an input containing URLs
    if url == '-':
      urls = (line.rstrip('\n') for line in sys.stdin)
    else:
      urls = [url]

    # sanity check the pipeline spec
    try:
      pipeline = pipespec.make_stage_funcs(P=P)
    except ValueError as e:
      raise GetoptError(
          f'invalid pipeline spec {pipespec.stage_specs}: {e}'
      ) from e

    async def print_from(item_Ps):
      ''' Consume `(result,Pilfer)` 2-tuples from the pipeline and print the results.
      '''
      async for result, _ in item_Ps:
        print(result)

    async def run():
      # consume everything from the main pipeline
      await print_from(pipespec.run_pipeline(urls))
      # close and join any running diversions
      async for diversion_name in P.close_diversions():
        print("CLOSED DIVERSION", diversion_name)

    asyncio.run(run())

  @popopts
  def cmd_grok(self, argv):
    ''' Usage: {cmd} URL
          Call every matching @on method for sitemaps matching URL.
    '''
    if not argv:
      raise GetoptError("missing URL")
    url = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments after URL: {argv!r}')
    options = self.options
    P = options.pilfer
    print(url)
    table = []
    for method, match_tags, grokked in P.grok(url):
      table.append(
          (
              f'  {method.__qualname__}',
              "\n".join(map(str, sorted(match_tags)))
          )
      )
      if grokked is not None:
        for k, v in grokked.items():
          table.append((f'    {k}', v))
    printt(*table)

  @popopts
  def cmd_mitm(self, argv):
    ''' Usage: {cmd} [@[address]:port] action[:params...][@hook,...]...
          Run a mitmproxy for traffic filtering.
          @[address]:port   Specify the listen address and port.
                            Default: {DEFAULT_MITM_LISTEN_HOST}:{DEFAULT_MITM_LISTEN_PORT}
          Actions take the form of an action name, optionally
          followed by :params to specify parameters to prepend to
          calls, optionally followed by @hook,... to specify which
          mitmproy hooks should call the action.
          The function for an action is called with 2 positional
          parameters, the hook name and the mitmproxy Flow instance.
          If additional parameters are specified they are prepaended
          to the positional and keyword arguments to the function.
          If hook names are not specified they are obtained from
          the action function's .default_hooks attribute.
          Predefined actions:
            cache   Cache URL content according to the pilfer sitemaps.
            dump    Dump request information according to the pilfer sitemaps.
            print   Print the request URL.
          The action name can actually take 2 forms:
          - a identifier, which must be a predefined action name
          - a dotted name followed by a colon and another dotted subname,
            which specifies a callable object from an importable module
          Examples:
            cache   The predefined "cache" action on its default hooks.
            dump@requestheaders
                    The predefined "dump" action on the "requestheaders" hook.
            my.module:handler:3,x=4@requestheaders
                    Call handler(3,hook_name,flow,x=4) from module my.module
                    on the "requestheaders" hook.
          It is generally better to use named parameters in actions because
          it is easier to give them default values in the function.
    '''
    from .mitm import (MITMAddon, run_proxy)
    listen_host = DEFAULT_MITM_LISTEN_HOST
    listen_port = DEFAULT_MITM_LISTEN_PORT
    # leading optional @[host:]port
    if argv and argv[0].startswith('@'):
      ip_port = argv.pop(0)[1:]
      with Pfx("@[address]:port %r", ip_port):
        try:
          host, port = ip_port.rsplit(':', 1)
          if host:
            listen_host = host
          listen_port = int(port)
        except ValueError as e:
          raise GetoptError(f'invalid [address]:port: {e}') from e
    if not argv:
      raise GetoptError('missing actions')
    bad_actions = False
    mitm_addon = MITMAddon()
    for action in argv:
      with Pfx("action %r", action):
        hook_names = None
        args = []
        kwargs = {}
        url_regexps = []
        name, offset = get_dotted_identifier(action)
        if not name:
          warning("no action name")
          bad_actions = True
          continue
        if '.' not in name:
          # a built in name
          mitm_action = name
        else:
          # a callable imported from a module
          if not action.startswith(':', offset):
            warning('missing module ":subname" after module %r', name)
            bad_actions = True
            continue
          offset += 1
          subname, offset = get_dotted_identifier(action, offset)
          if not subname:
            warning('missing module "subname" after module "%s:"', name)
            bad_actions = True
            continue
          try:
            mitm_action = pfx_call(import_name, action[:offset])
          except ImportError as e:
            warning("cannot import %r: %s", action[:offset], e._)
            bad_actions = True
            continue
        # gather @hooks and :params suffixes
        while offset < len(action):
          print("action", action, offset)
          with Pfx("offset %d", offset):
            # :params
            if action.startswith(':', offset):
              offset += 1
              a, kw, offset = get_action_args(action, offset, '@')
              args.extend(a)
              kwargs.update(kw)
            # @hook,...
            elif action.startswith('@', offset):
              offset += 1
              # TODO: gather commas separated identifiers
              end_hooks = action.find(':', offset)
              if end_hooks == -1:
                hook_names = action[offset:].split(',')
                offset = len(action)
              else:
                hook_names = action[offset:end_hooks].split(',')
                offset = end_hooks
            # ~ /url-regexp/
            elif action.startswith('~', offset):
              offset = skipwhite(action, offset + 1)
              regexp, offset = get_delim_regexp(action, offset)
              url_regexps.append(regexp)
            else:
              warning("unparsed text: %r", action[offset:])
              bad_actions = True
              break
        try:
          pfx_call(
              mitm_addon.add_action,
              hook_names,
              mitm_action,
              args,
              kwargs,
              url_regexps=url_regexps,
          )
        except ValueError as e:
          warning("invalid action spec: %s", e)
          bad_actions = True
    if bad_actions:
      raise GetoptError("invalid action specifications")
    asyncio.run(run_proxy(listen_host, listen_port, addon=mitm_addon))

  def cmd_sitemap(self, argv):
    ''' Usage: {cmd} [sitemap|domain [URL...]]
          List or query the site maps from the config.
          With no arguments, list the sitemaps.
          With a sitemap name (eg "docs"), list the sitemap.
          WIth additional URLs, print the key for each URL.
    '''
    options = self.options
    P = options.pilfer
    xit = 0
    if not argv:
      # list site maps
      printt(
          *[
              [pattern, str(sitemap)]
              for pattern, sitemap in self.options.pilfer.sitemaps
          ]
      )
      return 0
    # use a particular sitemap
    map_name = argv.pop(0)
    print("map_name", map_name)
    for domain_glob, sitemap in P.sitemaps:
      if map_name == sitemap.name:
        break
    else:
      warning("no sitemap named %r", map_name)
      return 1
    if not argv:
      # no URLs: recite the site map patterns
      for pattern in sitemap.URL_KEY_PATTERNS:
        (domain_glob, path_re_s), format_s = pattern
        printt(
            ('Domain:', '*' if domain_glob is None else domain_glob),
            ('  Path RE:', path_re_s),
            ('  Format:', format_s),
        )
      return 0
    # match URLs against the sitemap
    table = []
    for url in argv:
      with Pfx(url):
        U = URL(url)
        table.extend((
            ("URL:", url),
            ("  key:", sitemap.url_key(url)),
        ))
    printt(*table)

sys.exit(main(sys.argv))

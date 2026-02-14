#!/usr/bin/env python3

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import os
from os.path import expanduser, join as joinpath
from getopt import GetoptError
from pprint import pformat, pprint
import re
import sys
from typing import Iterable

from bs4 import NavigableString
from lxml.etree import tostring as xml_tostring
from typeguard import typechecked

from cs.cmdutils import BaseCommand, popopts
from cs.context import stackattrs
from cs.fileutils import atomic_filename
from cs.later import Later
from cs.lex import (
    cutprefix,
    cutsuffix,
    get_identifier,
    is_identifier,
    printt,
    s,
    skipwhite,
)
import cs.logutils
from cs.logutils import debug, error, warning
import cs.pfx
from cs.pfx import Pfx, pfx_call
from cs.queues import ListQueue
from cs.sqltags import SQLTagSet
from cs.tagset import TagSet
from cs.urlutils import URL

from . import (
    DEFAULT_JOBS,
    DEFAULT_FLAGS_CONJUNCTION,
    DEFAULT_MITM_LISTEN_HOST,
    DEFAULT_MITM_LISTEN_PORT,
)
from .parse import get_delim_regexp
from .pilfer import Pilfer
from .pipelines import PipeLineSpec
from .rss import RSSChannelMixin
from .sitemap import FlowState, SiteEntity, SiteMap

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
    dl_output_format: str = '{basename}'

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
        load_cookies='Load the browser cookie state into the Pilfer session.',
        no_check_certificates='Do not verify SSL certificates.',
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
            verify=not options.no_check_certificates,
        )
        with pilfer:
          pilfer.sitemaps  # loads SiteMaps from the pilferrc files as side effect
          with stackattrs(
              self.options,
              later=later,
              pilfer=pilfer,
              sqltags=pilfer.sqltags,
              # TODO: this feels clumsy
              db_url=pilfer.sqltags and pilfer.sqltags.db_url,
          ):
            if self.options.load_cookies:
              pilfer.load_browser_cookies(pilfer.session.cookies)
            yield

  # TODO: accept a URL and look it up?
  def popentity(self, argv: list[str], sitemap=None) -> SiteEntity:
    ''' Return a `SiteEntity` bound to the entity-name at `argv[0]`.
    '''
    if not argv:
      raise GetoptError('missing site-entity')
    entity_spec = argv.pop(0)
    if '://' in entity_spec:
      # match a URL to an entity
      ent = self.options.pilfer.url_entity(entity_spec)
      if ent is None:
        raise GetoptError(
            f'{entity_spec=} does not match a known SiteEntity subclass'
        )
    else:
      try:
        ent = SiteMap.by_db_key(entity_spec)
      except KeyError as e:
        raise GetoptError(f'unrecognised {entity_spec=}: {e}') from e
    return ent

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

  @popopts
  def cmd_dbshell(self, argv):
    ''' Usage: {cmd}
          Open a dbshell on the Pilfer SQLTags.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv=}')
    self.options.pilfer.dbshell()

  @popopts(
      o=(
          'dl_output_format', 'Output format string for the download filename.'
      )
  )
  def cmd_dl(self, argv):
    ''' Usage: {cmd} URLs...
          Download the specified URLs. "-" may be used to read URLs from stdin.
    '''
    if not argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    options = self.options
    dl_output_format = options.dl_output_format
    runstate = options.runstate

    def dl(url_s):
      ''' The common logic for a download of a single URL.
      '''
      nonlocal dl_output_format, xit
      try:
        FlowState.download_url(url_s, dl_output_format, format_filename=True)
      except FileExistsError as e:
        warning("output path already exists: %s", e)
        xit = 1
      except OSError as e:
        warning("error saving: %s", e)
        xit = 1

    xit = 0
    for url_s in argv:
      runstate.raiseif()
      with Pfx("%r", url_s):
        if url_s == '-':
          for lineno, url_line in enumerate(sys.stdin, 1):
            url_s = url_line.rstrip('\n')
            with Pfx("stdin:%d: %r", lineno, url_s):
              runstate.raiseif()
              dl(url_s)
        else:
          if '://' in url_s:
            dl(url_s)
          else:
            try:
              ent = SiteMap.by_db_key(url_s)
            except KeyError as e:
              warning("cannot get entity %r: %s", url_s, e)
              xit = 1
              continue
            ent.printt()
            save_filename = ent.format_as(dl_output_format)
            print("  ->", save_filename)
            ent.download(save_filename)
    return xit

  @popopts(
      content=(
          'dump_content',
          ''' Dump the page content, normally omitted.
              Currently supports HTML (text/html) and JSON (application/json).
          ''',
      ),
      no_redirects='Do not follow redirects.',
  )
  def cmd_dump(self, argv):
    ''' Usage: {cmd} [METHOD] url [header:value...] [param=value...]
          Fetch url and dump information from it.
    '''
    options = self.options
    P = options.pilfer
    # optional leading METHOD
    if argv and argv[0].isupper():
      method = argv.pop(0)
    else:
      method = 'GET'
    # url
    if not argv:
      raise GetoptError("missing URL")
    url = argv.pop(0)
    # optional header:value
    rqhdrs = {}
    while argv:
      name, offset = get_identifier(argv[0], extra='_-')
      if not name or not name.startswith(':'):
        # not a header
        break
      rqhdrs[name] = name[offset + 1:]
      argv.pop(0)
    # optional param=value
    params = {}
    while argv:
      name, offset = get_identifier(argv[0], extra='_-')
      if not name or not name.startswith('='):
        # not a parameter
        break
      params[name] = name[offset + 1:]
      argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments after URL/headers/params: {argv!r}')
    print(
        method,
        url,
        *(f'{name}:{value}' for name, value in rqhdrs.items()),
        *(f'{param}={value}' for param, value in params.items()),
    )
    rsp = P.request(
        url,
        method=method,
        headers=rqhdrs,
        params=(None if not params or method == 'POST' else params),
        data=(None if not params or method != 'POST' else params),
        allow_redirects=not options.no_redirects,
    )
    if rsp.status_code != 200:
      warning("%s %s -> status_code %r", method, url, rsp.status_code)
    flowstate = FlowState(
        url=url,
        request=rsp.request,
        response=rsp,
    )
    printt(
        [
            f'{flowstate.request.method} response {flowstate.response.status_code} headers:'
        ],
        *(
            [f'  {key}', value] for key, value in sorted(
                flowstate.response.headers.items(),
                key=lambda kv: kv[0].lower()
            )
        ),
    )
    soup = flowstate.soup
    if soup is None:
      print("no soup for content_type", flowstate.content_type)
    else:
      table = []
      title = soup.head.title
      if title:
        table.append(["Title:", title.string])
      meta = flowstate.meta
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
      links_by_rel = flowstate.links
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
    table = []
    for i, (method, match_tags,
            grokked) in enumerate(self.options.pilfer.grok(flowstate)):
      if i == 0:
        table.append(['Grokked:'])
      table.append(
          (
              f'  {method.__qualname__}',
              "\n".join(map(str, sorted(match_tags)))
          )
      )
      if grokked is not None:
        for k, v in grokked.items():
          table.append([f'    {k}', dict(v) if isinstance(v, TagSet) else v])
    printt(*table)
    if self.options.dump_content:
      print("Content:", flowstate.content_type)
      if flowstate.content_type in ('text/html',):
        soup = flowstate.soup
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
      elif flowstate.content_type in ('application/json',):
        jdata = flowstate.json
        if isinstance(jdata, dict):
          jkeys = list(jdata.keys())
          if len(jkeys) == 0:
            pprint(jdata)
          elif len(jkeys) > 1:
            printt(
                ['{'],
                *sorted(jdata.items()),
                ['}'],
            )
          else:
            jkey, = jkeys
            printt(
                [f'{{ {pformat(jkey)}'],
                *(
                    [f'    {pformat(k)}', v]
                    for k, v in sorted(jdata[jkey].items())
                ),
                ['}'],
            )
        else:
          pprint(jdata)
      else:
        warning("No content dump for %s.", flowstate.content_type)

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
          Call every matching @on grok_* method for sitemaps matching URL.
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
      if grokked is None:
        table.append(('    grokked =>', 'None'))
      else:
        if isinstance(grokked, SQLTagSet):
          table.append(
              ('    grokked =>', f'{grokked.sqltags}[{grokked.name}]')
          )
        else:
          table.append(('    grokked =>', s(grokked)))
        for k, v in grokked.items():
          table.append((f'      {k}', v))
    printt(*table)

  @popopts
  def pop_mitm_action(self, argv):
    ''' Pop a mitm action specification, return `(action,hook_names,criteria)`.
    '''
    from .mitm import MITMHookAction
    if not argv:
      raise GetoptError("missing mitm action")
    action_s = argv.pop(0)
    criteria = []
    with Pfx("%r", action_s):
      try:
        action, offset = MITMHookAction.parse(action_s)
        with Pfx(offset):
          # @hook,...
          if action_s.startswith('@', offset):
            offset += 1
            # TODO: gather commas separated identifiers
            end_hooks = action_s.find('~', offset)
            if end_hooks == -1:
              hook_names = action_s[offset:].split(',')
              offset = len(action_s)
            else:
              hook_names = action_s[offset:end_hooks].split(',')
              offset = end_hooks
          else:
            hook_names = []
        while offset < len(action_s):
          with Pfx(offset):
            # ~ /url-regexp/
            if action_s.startswith('~', offset):
              offset = skipwhite(action_s, offset + 1)
              regexp_s, offset = get_delim_regexp(action_s, offset)
              regexp = pfx_call(re.compile, regexp_s)
              criteria.append(lambda url_s: regexp.match(url_s))
            else:
              raise ValueError('unparsed text: {action_s[offset:]!r}')
      except ValueError as e:
        raise GetoptError("bad action: %s", e) from e
    return action, hook_names, criteria

  @popopts(
      proxy_=''' Upstream proxy to use, default from $https_proxy.
                 Supply an empty string to force no proxy.
             '''
  )
  def cmd_mitm(self, argv):
    ''' Usage: {cmd} [@[address]:port] action[(params...)][@hook,...][~/url-regexp/...]...
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
          - `cache`: the predefined "cache" action on its default hooks.
          - `dump@requestheaders`: the predefined "dump" action on
            the "requestheaders" hook.
          - `my.module:handler(3,x=4)@requestheaders`: call
            `handler(3,hook_name,flow,x=4)` from module `my.module`
            on the "requestheaders" hook.
          It is generally better to use named parameters in actions because
          it is easier to give them default values in the function.
    '''
    from .mitm import MITMAddon, run_proxy
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
    # make a MITMAddon and attach the CLI hooks
    mitm_addon = MITMAddon(logging_handlers=list(logging.getLogger().handlers))
    while argv:
      try:
        action, hook_names, criteria = self.pop_mitm_action(argv)
      except GetoptError as e:
        warning(str(e))
        bad_actions = True
      pfx_call(mitm_addon.add_action, action, hook_names, criteria or None)
    if bad_actions:
      raise GetoptError("invalid action specifications")
    asyncio.run(
        run_proxy(
            listen_host,
            listen_port,
            addon=mitm_addon,
            upstream_proxy_url=self.options.proxy,
        )
    )

  def cmd_patch(self, argv):
    ''' Usage: {cmd} URL
          Patch the soup for URL and recite the result to the output.
    '''
    if not argv:
      raise GetoptError("missing URL")
    url = argv.pop(0)
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    flowstate = FlowState(url=url)
    # Patch the soup of a URL by calling all SiteMap.patch_soup_* methods.
    for method, match_tags, result in self.options.pilfer.run_matches(
        flowstate, 'soup', 'patch_soup_*'):
      print(method, match_tags)
    print(flowstate.soup)

  @popopts(f='Force: refresh the entity even if it is not stale.')
  def cmd_refresh(self, argv):
    ''' Usage: {cmd} entity...
          Refresh the specified entities by fetching and grokking their site pages.
    '''
    if not argv:
      raise GetoptError("missing entities")
    while argv:
      ent = self.popentity(argv)
      ent.printt()
      ent.refresh(force=self.options.force)
      ent.printt()

  @popopts(
      d_=('dirpath', 'Output directory for output_fspath, default ".".'),
      f=('force', 'Force overwrite of the RSS file if it already exists.'),
      o_=('output_fspath', 'Output the RSS to the file output_fspath.'),
      refresh='Refresh the required web pages even if not stale.',
      xmlv='Provide a leading xml version tag.',
  )
  def cmd_rss(self, argv):
    ''' Usage: {cmd} entity|URL
          Generate an RSS feed for the specified entity or URL.
    '''
    pilfer = self.options.pilfer
    if not argv:
      raise GetoptError('missing entity or URL')
    entity = self.popentity(argv)
    if argv:
      raise GetoptError(f'extra arguments after entity/URL: {argv!r}')
    if not isinstance(entity, RSSChannelMixin):
      raise GetoptError(
          f'entity {entity} is not an instance of RSSChannelMixin'
      )
    output_fspath = joinpath(
        self.options.dirpath or ".", self.options.output_fspath
        or f'{entity.sitemap.URL_DOMAIN}--{entity.name}.rss'
    )
    rss = entity.rss(refresh=self.options.refresh)
    with atomic_filename(output_fspath, mode='w',
                         exists_ok=self.options.force) as T:
      if self.options.xmlv:
        print('<?xml version="1.0" encoding="UTF-8"?>', file=T)
      print(
          xml_tostring(rss, encoding='unicode', pretty_print=True),
          end='',
          file=T
      )
    print(output_fspath)
    print("Reparse", output_fspath)
    from rss_parser import RSSParser
    with open(output_fspath) as rssf:
      parsed = RSSParser.parse(rssf.read())
    print("Language", parsed.channel.language)
    print("RSS", parsed.version)
    # Iteratively print feed items
    for item in parsed.channel.items:
      print(item.title)
      print(item.description[:50])

  @popopts(p=('makedirs', 'Make required intermeditate directories.'))
  def cmd_save(self, argv):
    ''' Usage: {cmd} URL savepath
          Save URL to the file savepath.
    '''
    try:
      url_s, savepath = argv
    except ValueError as e:
      raise GetoptError(f'expected URL and savepath: {e}')
    url = URL(url_s)
    options = self.options
    rsp = options.pilfer.save(url, savepath, makedirs=options.makedirs)
    if options.verbose:
      printt(
          [f'GET {url.short} => {rsp.status_code}'],
          *([f'  {hdr}', value] for hdr, value in rsp.headers.items()),
      )

  def cmd_sitemap(self, argv):
    ''' Usage: {cmd} [sitemap|domain {{sitecmd [args...] | [URL...]}}]
          List or query the site maps from the config.
          With no arguments, list the sitemaps.
          With a sitemap name (eg "docs") and no further arguments, list the sitemap.
          If a word follows the sitemap name, treat it as a subcommand of the sitemap.
          Otherwise assume all remaining arguments are URLs and print the the key for
          each URL.
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
    # a subcommand?
    argv0_ = argv[0].replace('-', '_')
    if is_identifier(argv0_):
      sitecmd = argv0_
      argv.pop(0)
      with Pfx(sitecmd):
        try:
          cmdmethod = getattr(sitemap, f'cmd_{sitecmd}')
        except AttributeError:
          cmds = sorted(
              name.removeprefix('cmd_')
              for name in dir(sitemap)
              if name.startswith('cmd_')
          )
          raise GetoptError(
              f'unknown sitemap command, expected one of {", ".join(cmds)}'
          )
        with stackattrs(sitemap, options=self.options):
          return cmdmethod(argv)
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

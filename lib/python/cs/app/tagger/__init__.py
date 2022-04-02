#!/usr/bin/env python3

''' Tagger class and `tagger` command line tool for filing files by tags.
'''

from collections import defaultdict
import os
from os.path import (
    abspath,
    basename,
    dirname,
    exists as existspath,
    expanduser,
    isabs as isabspath,
    isdir as isdirpath,
    join as joinpath,
    realpath,
    samefile,
)
from threading import RLock

from typeguard import typechecked

from cs.deco import cachedmethod, fmtdoc
from cs.fs import FSPathBasedSingleton
from cs.fstags import FSTags
from cs.lex import FormatAsError, r, get_dotted_identifier
from cs.logutils import warning
from cs.onttags import Ont, ONTTAGS_PATH_DEFAULT, ONTTAGS_PATH_ENVVAR
from cs.pfx import Pfx, pfx, pfx_call
from cs.queues import ListQueue
from cs.seq import unrepeated
from cs.tagset import Tag, TagSet, RegexpTagRule
from cs.threads import locked

# the subtags containing Tagger releated values
TAGGER_TAG_PREFIX_DEFAULT = 'tagger'

class Tagger(FSPathBasedSingleton):
  ''' The core logic of a tagger.
  '''

  TAG_PREFIX = TAGGER_TAG_PREFIX_DEFAULT

  def __init__(self, dirpath: str, fstags=None, ont=None):
    ''' Initialise the `Tagger`.

        Parameters:
        * `fstags`: optional `FSTags` instance
    '''
    if hasattr(self, 'fspath'):
      return
    if fstags is None:
      fstags = FSTags()
    if ont is None:
      ont = os.environ.get(ONTTAGS_PATH_ENVVAR
                           ) or expanduser(ONTTAGS_PATH_DEFAULT)
    if isinstance(ont, str):
      ont = Ont(ont)
    super().__init__(abspath(dirpath))
    self.fstags = fstags
    self.ont = ont
    self._file_by_mappings = {}
    # mapping of (dirpath,tag_name)=>tag_value=>set(subdirpaths)
    # used by per_tag_auto_file_map
    self._per_tag_auto_file_mappings = defaultdict(lambda: defaultdict(set))
    self._lock = RLock()

  def tagger_for(self, dirpath):
    ''' Factory to return a `Tagger` for a directory
        using the same `FSTags` and ontology as `self`.
    '''
    return type(self)(dirpath, fstags=self.fstags, ont=self.ont)

  @property
  @cachedmethod
  def conf(self):
    ''' The direct configuration `Tag`s.
    '''
    return self.fstags[self.fspath].subtags(self.TAG_PREFIX)

  @property
  def conf_all(self):
    ''' The configuration `Tag`s as inherited.
    '''
    return self.fstags[self.fspath].all_tags.subtags(self.TAG_PREFIX)

  @property
  def auto_name_formats(self) -> List[str]:
    ''' The sequence of `auto_name` formats.
    '''
    return self.conf_all.get('auto_name', [])

  @classmethod
  @pfx
  def auto_name(cls, srcpath, dstdirpath):
    ''' Generate a pathname computed from `srcpath`, `dstdirpath` and `tags`.

        The format strings used to generate the pathname
        come from the `auto_name` configuration tag of `dstdirpath`.
        `{self.TAG_PREFIX}.auto_name`, usually `"tagger.auto_name"`.

        If no formats match, return `basename(srcpath)`.
    '''
    tagger = cls(dstdirpath)
    fstags = tagger.fstags
    formats = tagger.auto_name_formats
    if isinstance(formats, str):
      formats = [formats]
    if formats:
      # the tags to use in the format string are the inherited Tags of
      # dstdirpath overlaid by the inherited Tags of srcpath
      fmttags = fstags[dstdirpath].merged_tags()
      fmttags.update(fstags[srcpath].merged_tags())
      for fmt in formats:
        with Pfx(repr(fmt)):
          try:
            formatted = pfx_call(fmttags.format_as, fmt, strict=True)
            if formatted.endswith('/'):
              formatted += basename(srcpath)
            return formatted
          except FormatAsError:
            ##warning("%s", e)
            ##print("auto_name(%r): %r: %s", srcpath, fmt, e)
            continue
    return basename(srcpath)

  def ont_values(self, tag_name):
    ''' Return a list of alternative values for `tag_name`
        derived from the ontology `self.ont`.
    '''
    return list(self.ont.type_values(tag_name))

  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
  @pfx
  @fmtdoc
  def file_by_tags(
      self, path: str, prune_inherited=False, no_link=False, do_remove=False
  ):
    ''' Examine a file's tags.
        Where those tags imply a location, link the file to that location.
        Return the list of links made.

        Parameters:
        * `path`: the source path to file
        * `prune_inherited`: optional, default `False`:
          prune the inherited tags from the direct tags on the target
        * `no_link`: optional, default `False`;
          do not actually make the hard link, just report the target
        * `do_remove`: optional, default `False`;
          remove source files if successfully linked

        Note: if `path` is already linked to an implied location
        that location is also included in the returned list.

        The filing process is as follows:
        - for each target directory, initially `dirname(path)`,
          look for a filing map on tag `file_by_mapping`
        - for each directory in that mapping which matches a tag from `path`,
          queue it as an additional target directory
        - if there were no matching directories, file `path` at the current
          target directory under the filename
          returned by `{TAGGER_TAG_PREFIX_DEFAULT}.auto_name`
    '''
    if do_remove and no_link:
      raise ValueError("do_remove and no_link may not both be true")
    fstags = self.fstags
    # start the queue with the resolved `path`
    tagged = fstags[path]
    srcpath = tagged.fspath
    tags = tagged.all_tags
    # a queue of reference directories
    q = ListQueue((dirname(srcpath),))
    linked_to = []
    seen = set()
    for refdirpath in unrepeated(q, signature=abspath, seen=seen):
      with Pfx(refdirpath):
        # places to redirect this file
        mapping = self.file_by_mapping(refdirpath)
        interesting_tag_names = {tag.name for tag in mapping.keys()}
        # locate specific filing locations in the refdirpath
        refile_to = set()
        for tag_name in sorted(interesting_tag_names):
          with Pfx("tag_name %r", tag_name):
            if tag_name not in tags:
              continue
            bare_tag = Tag(tag_name, tags[tag_name])
            try:
              target_dirs = mapping.get(bare_tag, ())
            except TypeError as e:
              warning("  %s not mapped (%s), skipping", bare_tag, e)
              continue
            if not target_dirs:
              continue
            # collect other filing locations
            refile_to.update(target_dirs)
        # queue further locations if they are new
        if refile_to:
          new_refile_to = set(map(abspath, refile_to)) - seen
          if new_refile_to:
            q.extend(new_refile_to)
            continue
        # file locally (no new locations)
        dstbase = self.auto_name(srcpath, refdirpath, tags)
        with Pfx("%s => %s", refdirpath, dstbase):
          dstpath = dstbase if isabspath(dstbase
                                         ) else joinpath(refdirpath, dstbase)
          if existspath(dstpath):
            if not samefile(srcpath, dstpath):
              warning("already exists, skipping")
            continue
          if no_link:
            linked_to.append(dstpath)
          else:
            linkto_dirpath = dirname(dstpath)
            if not isdirpath(linkto_dirpath):
              pfx_call(os.mkdir, linkto_dirpath)
            try:
              pfx_call(os.link, srcpath, dstpath)
            except OSError as e:
              warning("cannot link to %r: %s", dstpath, e)
            else:
              linked_to.append(dstpath)
              fstags[dstpath].update(tags)
              if prune_inherited:
                fstags[dstpath].prune_inherited()
    if linked_to and do_remove:
      S = pfx_call(os.stat, srcpath)
      if S.st_nlink < 2:
        warning(
            "not removing %r, insufficient hard links (%s)", srcpath,
            S.st_nlink
        )
      else:
        pfx_call(os.remove, srcpath)
    return linked_to

  @locked
  @pfx
  @fmtdoc
  def per_tag_auto_file_map(self, dirpath: str, tag_names):
    ''' Walk the file tree at `dirpath`
        looking for directories whose direct tags contain tags
        whose name is in `tag_names`.
        Return a mapping of `Tag->[dirpaths...]`
        mapping specific tag values to the directory paths where they occur.

        Parameters:
        * `dirpath`: the path to the directory to walk
        * `tag_names`: an iterable of `Tag` names of interest

        The intent here is to derive filing locations
        from the tree layout.

        We automatically skip subdirectories whose names commence with `'.'`.
        We also skip subdirectories tagged with `{TAGGER_TAG_PREFIX_DEFAULT}.skip`.
    '''
    fstags = self.fstags
    tagged = fstags[dirpath]
    dirpath = tagged.fspath  # canonical absolute path
    all_tag_names = set(tag_names)
    assert all(isinstance(tag_name, str) for tag_name in all_tag_names)
    # collect all the per-tag_name mappings which exist for dirpath
    # note tha mappings which do not exist
    mappings = {}
    missing_tag_names = set()
    for tag_name in all_tag_names:
      per_tag_cache_key = dirpath, tag_name
      if per_tag_cache_key in self._per_tag_auto_file_mappings:
        mappings[per_tag_cache_key] = self._per_tag_auto_file_mappings[
            per_tag_cache_key]
      else:
        missing_tag_names.add(tag_name)
    if missing_tag_names:
      # walk the tree to find the missing tags
      subdirpaths_by_tag = defaultdict(list)
      for path, dirnames, _ in os.walk(realpath(dirpath)):
        with Pfx("os.walk @ %r", path):
          # order the descent
          dirnames[:] = sorted(
              dname for dname in dirnames
              if dname and not dname.startswith('.')
          )
          tagged_subdir = fstags[path]
          if self.has_conf_tag(tagged_subdir, 'skip'):
            # tagger.skip => prune this directory tree from the mapping
            dirnames[:] = []
          else:
            # look for the tags of interest
            for tag_name in missing_tag_names:
              try:
                tag_value = tagged_subdir[tag_name]
              except KeyError:
                pass
              else:
                bare_tag = Tag(tag_name, tag_value)
                subdirpaths_by_tag[bare_tag].append(tagged_subdir.filepath)
      # make sure each cache exists and gather them up
      for tag_name in missing_tag_names:
        per_tag_cache_key = dirpath, tag_name
        assert per_tag_cache_key not in mappings
        assert per_tag_cache_key not in self._per_tag_auto_file_mappings
        mappings[per_tag_cache_key] = self._per_tag_auto_file_mappings[
            per_tag_cache_key]
      # fill in the caches
      for bare_tag, subdirpaths in subdirpaths_by_tag.items():
        tag_name = bare_tag.name
        per_tag_cache_key = dirpath, tag_name
        # get the value=>[subdirpaths] mapping
        by_tag_name = self._per_tag_auto_file_mappings[per_tag_cache_key]
        by_tag_name[bare_tag.value].update(subdirpaths)
    # collate mapping
    mapping = {}
    for per_tag_cache_key, by_tag_value in mappings.items():
      _, tag_name = per_tag_cache_key
      for tag_value, subpaths in by_tag_value.items():
        mapping[Tag(tag_name, tag_value)] = subpaths
    return mapping

  @locked
  @pfx
  @fmtdoc
  def file_by_mapping(self, srcdirpath):
    ''' Examine the `{TAGGER_TAG_PREFIX_DEFAULT}.file_by` tag for `srcdirpath`.
        Return a mapping of specific tag values to filing locations
        derived via `per_tag_auto_file_map`.

        The file location specification in the tag may be a list or a string
        (for convenient single locations).

        For example, I might tag my downloads directory with:

            {TAGGER_TAG_PREFIX_DEFAULT}.file_by={{"abn":"~/them/me"}}

        indicating that files with an `abn` tag may be filed in the `~/them/me` directory.
        That directory is then walked looking for the tag `abn`,
        and wherever some tag `abn=`*value*` is found on a subdirectory
        a mapping entry for `abn=`*value*=>*subdirectory* is added.

        This results in a direct mapping of specific tag values to filing locations,
        such as:

            {{ Tag('abn','***********') => ['/path/to/them/me/abn-**-***-***-***'] }}

        because the target subdirectory has been tagged with `abn="***********"`.
    '''
    assert isdirpath(srcdirpath)
    assert not srcdirpath.startswith('~')
    assert '~' not in srcdirpath
    fstags = self.fstags
    tagged = fstags[srcdirpath]
    key = tagged.fspath
    try:
      mapping = self._file_by_mappings[key]
    except KeyError:
      mapping = defaultdict(set)
      file_by = self.conf_tag(fstags[srcdirpath].all_tags, 'file_by', {})
      # group the tags by file_by target path
      grouped = defaultdict(set)
      for tag_name, file_to in file_by.items():
        if isinstance(file_to, str):
          file_to = (file_to,)
        for file_to_path in file_to:
          if not isabspath(file_to_path):
            if file_to_path.startswith('~'):
              file_to_path = expanduser(file_to_path)
              assert isabspath(file_to_path)
            else:
              file_to_path = joinpath(srcdirpath, file_to_path)
          file_to_path = realpath(file_to_path)
          grouped[file_to_path].add(tag_name)
      # walk each path for its tag_names of interest
      for file_to_path, tag_names in sorted(grouped.items()):
        with Pfx("%r:%r", file_to_path, tag_names):
          # accrue destination paths by tag values
          for bare_key, dstpaths in self.per_tag_auto_file_map(
              file_to_path, tag_names).items():
            mapping[bare_key].update(dstpaths)
      self._file_by_mappings[key] = mapping
    return mapping

  def suggested_tags(self, path):
    ''' Return a mapping of `tag_name=>set(tag_values)`
        representing suggested tags
        obtained from the autofiling configurations.
    '''
    tagged = self.fstags[path]
    srcdirpath = dirname(tagged.fspath)
    suggestions = defaultdict(set)
    for bare_tag, _ in self.file_by_mapping(srcdirpath).items():
      if bare_tag not in tagged:
        suggestions[bare_tag.name].add(bare_tag.value)
    for refpath in self.file_by_tags(path, no_link=True) or [path]:
      dirpath = dirname(refpath)
      mapping = self.file_by_mapping(dirpath)
      for bare_tag, _ in mapping.items():
        if bare_tag not in tagged:
          suggestions[bare_tag.name].add(bare_tag.value)
    return suggestions

  def inference_rules(self, prefix, rule_spec):
    ''' Generator yielding inference functions from `rule_spec`.

        Each yielded function accepts a path
        and returns an iterable of `Tag`s or other values.
        Because some functions are implemented as lambdas it is reasonable
        to return an iterable conatining `None` values
        to be discarded by the consumer of the rule.
    '''
    with Pfx(r(rule_spec)):
      if isinstance(rule_spec, str):
        if rule_spec.startswith('/'):
          rule = RegexpTagRule(rule_spec[1:])
          yield lambda path, rule=rule: rule.infer_tags(basename(path)
                                                        ).as_tags()
        else:
          tag_name, _ = get_dotted_identifier(rule_spec)
          if tag_name and tag_name == rule_spec:
            # return the value of tag_name or None, as a 1-tuple
            yield lambda path: (self.fstags[path].get(tag_name),)
          else:
            warning("skipping unrecognised pattern")
      elif isinstance(rule_spec, (list, tuple)):
        for subspec in rule_spec:
          yield from self.inference_rules(prefix, subspec)
      else:
        warning("skipping unhandled type")

  @pfx
  @fmtdoc
  def inference_mapping(self, dirpath):
    r'''Scan `path`'s `{TAGGER_TAG_PREFIX_DEFAULT}.filename_inference` tag,
        a mapping of prefix=>rule specifications.
        Return a mapping of prefix=>inference_function
        where `inference_function(pathname)` returns a list of inferred `Tag`s.

        The prefix is a tag prefix. Example:

            {TAGGER_TAG_PREFIX_DEFAULT}.filename_inference={{
                'cs.tv_episode':
                    '/^(?P<series_title_lc>([^-]|-[^-])+)--s0*(?P<season_n>\d+)e0*(?P<episode_n>\d+)--(?P<episode_title_lc>([^-]|-[^-])+)--',
            }}

        which would try to match a filename against my habitual naming convention,
        and if so return a `TagSet` with tags named `series_title_lc`,
        `season_n`, `episode_n`, `episode_title_lc`.
    '''
    inference_spec = self.conf_tag(self.fstags[dirpath], 'inference', {})
    mapping = defaultdict(list)
    with Pfx("inference=%r", inference_spec):
      for prefix, rule_spec in inference_spec.items():
        with Pfx(prefix):
          mapping[prefix].extend(self.inference_rules(prefix, rule_spec))
    return mapping

  @pfx
  @fmtdoc
  def infer(self, path, apply=False):
    ''' Compare the `{TAGGER_TAG_PREFIX_DEFAULT}.filename_inference` rules to `path`,
        producing a mapping of prefix=>[Tag] for each rule which infers tags.
        Return the mapping.

        If `apply` is true,
        also apply all the inferred tags to `path`
        with each `Tag` *name*=*value* applied as *prefix*.*name*=*value*.
    '''
    tagged = self.fstags[path]
    srcpath = tagged.fspath
    srcdirpath = dirname(srcpath)
    inference_mapping = self.inference_mapping(srcdirpath)
    inferences = defaultdict(list)
    for prefix, infer_funcs in inference_mapping.items():
      with Pfx(prefix):
        assert isinstance(prefix, str)
        for infer_func in infer_funcs:
          try:
            values = list(infer_func(path))
          except Exception as e:  # pylint: disable=broad-except
            warning("skip rule %s: %s", infer_func, e)
            continue
          bare_values = []
          for value in values:
            if isinstance(value, Tag):
              tag = value
              tag_name = prefix + '.' + tag.name if prefix else tag.name
              inferences[tag_name] = tag.value
            else:
              bare_values.append(value)
          if bare_values:
            if len(bare_values) == 1:
              bare_values = bare_values[0]
            inferences[prefix] = bare_values
          break
    if apply:
      with Pfx("apply"):
        for tag_name, values in inferences.items():
          tagged[tag_name] = tag.value
    return inferences

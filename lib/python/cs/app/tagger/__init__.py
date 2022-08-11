#!/usr/bin/env python3

''' Tagger class and `tagger` command line tool for filing files by tags.
'''

from collections import defaultdict
import filecmp
from functools import partial
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
from typing import List

from cs.deco import cachedmethod, fmtdoc
from cs.fs import FSPathBasedSingleton, shortpath
from cs.fstags import FSTags
from cs.lex import FormatAsError, r, get_dotted_identifier
from cs.logutils import warning
from cs.onttags import Ont, ONTTAGS_PATH_DEFAULT, ONTTAGS_PATH_ENVVAR
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.queues import ListQueue
from cs.seq import unrepeated
from cs.tagset import Tag, TagSet, RegexpTagRule
from cs.threads import locked

__version__ = None

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts': ['tagger = cs.app.tagger.__main__:main'],
        'gui_scripts': ['tagger-gui = cs.app.tagger.gui_tk:main'],
    },
    'install_requires': [
        'cs.deco',
        'cs.fs',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.onttags',
        'cs.pfx',
        'cs.queues',
        'cs.seq',
        'cs.tagset',
        'cs.threads',
    ],
}

pfx_link = partial(pfx_call, os.link)
pfx_mkdir = partial(pfx_call, os.mkdir)
pfx_remove = partial(pfx_call, os.remove)
pfx_rename = partial(pfx_call, os.rename)
pfx_stat = partial(pfx_call, os.stat)

# the subtags containing Tagger releated values
TAGGER_TAG_PREFIX_DEFAULT = 'tagger'

class Tagger(FSPathBasedSingleton):
  ''' The core logic of a tagger.
  '''

  TAG_PREFIX = TAGGER_TAG_PREFIX_DEFAULT

  def __init__(self, dirpath: str, fstags=None, ont=None):
    ''' Initialise the `Tagger`.

        Parameters:
        * `fstags`: optional `FSTags` instance;
          an instance will be created if not supplied
        * `ont`: optional `cs.onttags.Ont`;
          an instance will be created if not supplied
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

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.fspath)

  def tagger_for(self, dirpath):
    ''' Factory to return a `Tagger` for a directory
        using the same `FSTags` and ontology as `self`.
    '''
    return type(self)(dirpath, fstags=self.fstags, ont=self.ont)

  @property
  def tagged(self):
    ''' The `TaggedPath` associated with this directory.
    '''
    return self.fstags[self.fspath]

  @property
  @cachedmethod
  def conf(self):
    ''' The direct configuration `Tag`s.
    '''
    conf_tags = self.tagged.subtags(self.TAG_PREFIX)
    conf_tags.setdefault('autoname', [])
    conf_tags.setdefault('file_by', {})
    conf_tags.setdefault('autotag', {}).setdefault('basename', [])
    return conf_tags

  @property
  def conf_all(self):
    ''' The configuration `Tag`s as inherited.
    '''
    return self.tagged.all_tags.subtags(self.TAG_PREFIX)

  def autoname_formats(self) -> List[str]:
    ''' The sequence of `autoname` formats.
    '''
    return self.conf_all.get('autoname', [])

  @pfx
  def autoname(self, srcpath):
    ''' Generate a file basename computed from `srcpath`.

        The format strings used to generate the pathname
        come from the `autoname` configuration tag of this `Tagger`
        `{self.TAG_PREFIX}.autoname`, usually `"tagger.autoname"`.

        If no formats match, return `basename(srcpath)`.
    '''
    formats = self.autoname_formats()
    if isinstance(formats, str):
      formats = [formats]
    if formats:
      # the tags to use in the format string are the inherited Tags of
      # dstdirpath overlaid by the inherited Tags of srcpath
      fmttags = self.tagged.merged_tags()
      fmttags.update(self.fstags[srcpath].merged_tags())
      for fmt in formats:
        with Pfx(repr(fmt)):
          try:
            formatted = pfx_call(fmttags.format_as, fmt, strict=True)
            if formatted.endswith('/'):
              formatted += basename(srcpath)
            return formatted
          except FormatAsError:
            ##warning("%s", e)
            ##print("autoname(%r): %r: %s", srcpath, fmt, e)
            continue
    return basename(srcpath)

  def ont_values(self, tag_name):
    ''' Return a list of alternative values for `tag_name`
        derived from the ontology `self.ont`.
    '''
    return list(self.ont.type_values(tag_name))

  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
  @fmtdoc
  def file_by_tags(
      self,
      origpath: str,
      prune_inherited=False,
      no_link=False,
      do_remove=False,
  ):
    ''' Examine a file's tags.
        Where those tags imply a location, link the file to that location.
        Return the list of links made.

        Parameters:
        * `origpath`: the source path to file
        * `prune_inherited`: optional, default `False`:
          prune the inherited tags from the direct tags on the target
        * `no_link`: optional, default `False`;
          do not actually make the hard link, just report the target
        * `do_remove`: optional, default `False`;
          remove source files if successfully linked

        Note: if `origpath` is already linked to an implied location
        that location is also included in the returned list.

        The filing process is as follows:
        - for each target directory, initially `dirname(origpath)`,
          look for a filing map on tag `file_by_mapping`
        - for each directory in that mapping which matches a tag from `origpath`,
          queue it as an additional target directory
        - if there were no matching directories, file `origpath` at the current
          target directory under the filename
          returned by `{TAGGER_TAG_PREFIX_DEFAULT}.autoname`
    '''
    if do_remove and no_link:
      raise ValueError("do_remove and no_link may not both be true")
    # start the queue with origpath
    # a queue of reference directories
    q = ListQueue((origpath,))
    linked_to = []
    seen = set()
    for srcpath in unrepeated(q, signature=realpath, seen=seen):
      with Pfx(shortpath(srcpath)):
        tagged = self.fstags[srcpath]
        tagger = self.tagger_for(dirname(srcpath))
        # infer tags before filing
        tagger.infer_tags(tagged.fspath, mode='infill')
        tags = tagged.merged_tags()
        fstags = tagger.fstags
        conf = tagger.conf
        # examine the file_by mapping for entries which match tags on origpath
        refile_to_dirs = set()
        for k, paths in conf.file_by.items():
          try:
            tag = Tag.from_str(k)
          except ValueError as e:
            warning("skipping bad Tag spec: %s", e)
            continue
          if (tag.name if tag.value is None else tag) not in tags:
            # this file_by entry does not match the tags
            continue
          # this file_by entry matches a Tag on origpath
          refile_paths = [
              (ep if isabspath(ep) else joinpath(tagger.fspath, ep))
              for ep in (expanduser(p) for p in paths)
          ]
          # follow the subdir tag map for further refiling
          tag_value = tags[tag.name]
          for refile_to_dir in refile_paths:
            refile_tagger = self.tagger_for(refile_to_dir)
            tagmap = refile_tagger.subdir_tag_map()
            try:
              subdirs = tagmap[tag.name][tag_value]
            except KeyError:
              # no mapping for this tag, file in refile_to_dir
              refile_to_dirs.add(refile_to_dir)
            else:
              if subdirs:
                # link the file into each
                refile_to_dirs.update(subdirs)
              else:
                # no subdirs, file in refile_to_dir
                refile_to_dirs.add(refile_to_dir)
        if refile_to_dirs:
          # winnow directories already processed
          refile_to_dirs = set(map(realpath, refile_to_dirs)) - seen
          if refile_to_dirs:
            # refile to other places
            srcbase = basename(srcpath)
            for subdir in refile_to_dirs:
              qpath = joinpath(subdir, srcbase)
              q.prepend((qpath,))
              fstags[qpath].update(tagged)
              if prune_inherited:
                fstags[qpath].prune_inherited()
            continue
        # not refiling elsewhere, file here
        # file locally (no new locations)
        dstbase = tagger.autoname(srcpath)
        dstpath = dstbase if isabspath(dstbase
                                       ) else joinpath(tagger.fspath, dstbase)
        dstdirpath = dirname(dstpath)
        link_count_fudge = 0
        with Pfx("=> %s", shortpath(dstpath)):
          if existspath(dstpath):
            if samefile(origpath, dstpath):
              warning("same file, already \"linked\"")
              linked_to.append(dstpath)
            elif filecmp.cmp(origpath, dstpath, shallow=False):
              warning("exists with same content")
              linked_to.append(dstpath)
              link_count_fudge += 1
            else:
              warning("already exists with different content, skipping")
              continue
          else:
            if no_link:
              linked_to.append(dstpath)
            else:
              if not isdirpath(dstdirpath):
                pfx_mkdir(dstdirpath)
              try:
                pfx_link(origpath, dstpath)
              except OSError as e:
                warning("cannot link to %r: %s", dstpath, e)
                continue
          linked_to.append(dstpath)
          fstags[dstpath].update(tagged)
          if prune_inherited:
            fstags[dstpath].prune_inherited()
    if linked_to and do_remove:
      S = pfx_stat(origpath)
      if S.st_nlink + link_count_fudge < 2:
        warning(
            "not removing %r, insufficient hard links (%s)", origpath,
            S.st_nlink
        )
      else:
        pfx_remove(origpath)
    return linked_to

  @locked
  @pfx
  @fmtdoc
  def per_tag_auto_file_map(self, tag_names):
    ''' Walk the file tree at `self.fspath`
        looking for directories whose direct tags contain tags
        whose name is in `tag_names`.
        Return a mapping of `Tag->[dirpaths...]`
        mapping specific tag values to the directory paths where they occur.

        Parameters:
        * `tag_names`: an iterable of `Tag` names of interest

        The intent here is to derive filing locations
        from the tree layout.

        We automatically skip subdirectories whose names commence with `'.'`.
        We also skip subdirectories tagged with `{TAGGER_TAG_PREFIX_DEFAULT}.skip`.
    '''
    fstags = self.fstags
    tagged = fstags[self.fspath]
    dirpath = tagged.fspath  # canonical absolute path
    all_tag_names = set(tag_names)
    assert all(isinstance(tag_name, str) for tag_name in all_tag_names)
    # collect all the per-tag_name mappings which exist for dirpath
    # note the mappings which do not exist
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
          tagged_subdir = fstags[path]
          if 'skip' in self.tagger_for(tagged_subdir.fspath).conf:
            # tagger.skip => prune this directory tree from the mapping
            dirnames[:] = []
            continue
          # order the descent
          dirnames[:] = sorted(
              dname for dname in dirnames
              if dname and not dname.startswith('.')
          )
          # look for the tags of interest
          for tag_name in missing_tag_names:
            try:
              tag_value = tagged_subdir[tag_name]
            except KeyError:
              pass
            else:
              bare_tag = Tag(tag_name, tag_value)
              subdirpaths_by_tag[bare_tag].append(tagged_subdir.fspath)
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

  @cachedmethod
  def subdir_tag_map(self):
    ''' Return a mapping of `tag_name`->`tag_value`->[dirpath,...]
        covering the entire subdirectory tree.

        Returns an empty mapping if `self.fspath` does not exist
        or is not a directory.
    '''
    # scan the whole directory before recursing
    tag_map = defaultdict(lambda: defaultdict(set))
    if isdirpath(self.fspath):
      for entry in [entry for entry in os.scandir(self.fspath)
                    if entry.is_dir() and not entry.name.startswith('.')]:
        subtagger = self.tagger_for(entry.path)
        # make entries for each tag on the immediate subdir
        for tag in subtagger.tagged.as_tags():
          if isinstance(tag.value, (int, str)):
            tag_map[tag.name][tag.value].add(entry.path)
        # infill with all the entries from the subdir's own tag map
        for tag_name, submap in subtagger.subdir_tag_map().items():
          for tag_value, paths in submap.items():
            tag_map[tag_name][tag_value].update(paths)
    return tag_map

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
    assert not srcdirpath.startswith('~')
    assert '~' not in srcdirpath
    fstags = self.fstags
    tagged = fstags[srcdirpath]
    key = tagged.fspath
    try:
      mapping = self._file_by_mappings[key]
    except KeyError:
      mapping = defaultdict(set)
      file_by = self.conf_all.get('file_by', {})
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
          subtagger = self.tagger_for(file_to_path)
          for bare_key, dstpaths in subtagger.per_tag_auto_file_map(tag_names
                                                                    ).items():
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

  @staticmethod
  def inference_rule(rule_spec):
    ''' Return an inference rule from `rule_spec`.

        Supported syntaxes:
        - [tag_prefix`:`]`/`regexp a regular expression
    '''
    if isinstance(rule_spec, str):
      tag_prefix, offset = get_dotted_identifier(rule_spec)
      if offset > 0 and rule_spec.startswith(':', offset):
        offset += 1
      else:
        tag_prefix = None
      if rule_spec.startswith('/', offset):
        rule = RegexpTagRule(rule_spec[offset + 1:], tag_prefix=tag_prefix)
      else:
        raise ValueError("skipping unrecognised pattern")
    else:
      raise ValueError("skipping unhandled type")
    return rule

  @pfx
  @fmtdoc
  def infer_tags(self, path, mode='infer'):
    ''' Compare the `{TAGGER_TAG_PREFIX_DEFAULT}.filename_inference` rules to `path`,
        producing a mapping of prefix=>[Tag] for each rule which infers tags.
        Return the mapping.

        If `apply` is true,
        also apply all the inferred tags to `path`
        with each `Tag` *name*=*value* applied as *prefix*.*name*=*value*.
    '''
    tagged = self.fstags[path]
    srcpath = tagged.fspath
    srcbase = basename(srcpath)
    inferred_tags = TagSet()
    basename_rule_specs = self.conf['autotag']['basename']
    for rule_spec in basename_rule_specs:
      try:
        rule = self.inference_rule(rule_spec)
      except ValueError as e:
        warning("skipping invalid rule: %s", e)
        continue
      for tag in rule.infer_tags(srcbase):
        inferred_tags.add(tag)
    if mode == 'infer':
      pass
    elif mode == 'infill':
      for tag in inferred_tags.as_tags():
        if tag.name not in tagged:
          tagged.add(tag)
    elif mode == 'overwrite':
      for tag in inferred_tags.as_tags():
        tagged.add(tag)
    else:
      raise RuntimeError("unhandled mode %r" % (mode,))
    return inferred_tags

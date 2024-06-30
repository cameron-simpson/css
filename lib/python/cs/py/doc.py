#!/usr/bin/env python3

''' Create documentation from python modules and other objects.
'''

import abc
import importlib
from inspect import (
    getcomments, getmodule, isclass, isdatadescriptor, isfunction, ismethod,
    signature
)
from itertools import chain

from cs.lex import cutprefix, stripped_dedent
from cs.logutils import warning
from cs.pfx import Pfx
from cs.py.modules import module_attributes

__version__ = '20240630.1'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.cmdutils',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.modules',
    ],
}

def is_dunder(name):
  ''' Test whether a name is a dunder name (`__`*foo*`__`).
  '''
  return len(name) > 4 and name.startswith('__') and name.endswith('__')

def module_doc(
    module,
    *,
    sort_key=lambda item: item[0].lower(),
    filter_key=lambda key: key != 'DISTINFO' and not key.startswith('_'),
    method_names=None,
):
  ''' Fetch the docstrings from a module and assemble a MarkDown document.

      Parameters:
      * `module`: the module or module name to inspect
      * `sort_key`: optional key for sorting names in the documentation;
        default: `name`
      * filter_key`: optional test for a key used to select or reject keys
        to appear in the documentation
      * `method_names`: optional list of method names to document;
        the default is to document `__init__`, then CONSTANTS, the
        dunders, then other public names
  '''
  if isinstance(module, str):
    module_name = module
    with Pfx("import_module(%r)", module_name):
      module = importlib.import_module(module_name)
  full_docs = [obj_docstring(module)]
  ALL = getattr(module, '__all__', None)
  for Mname, obj in sorted(module_attributes(module), key=sort_key):
    with Pfx(Mname):
      if ALL and Mname not in ALL:
        continue
      if not filter_key(Mname):
        continue
      obj_module = getmodule(obj)
      if obj_module is not module:
        # name imported from another module
        continue
      obj_doc = obj_docstring(obj) if obj_module else ''
      if not callable(obj):
        if obj_doc:
          full_docs.append(f'\n\n## `{Mname} = {obj!r}`\n\n{obj_doc}')
        continue
      if not obj_doc:
        continue
      if isfunction(obj):
        sig = signature(obj)
        full_docs.append(f'\n\n## Function `{Mname}{sig}`\n\n{obj_doc}')
      elif isclass(obj):
        from cs.cmdutils import BaseCommand
        classname_etc = Mname
        mro_names = []
        mro_set = set(obj.__mro__)
        for superclass in obj.__mro__:
          if superclass not in mro_set:
            continue
          if (superclass is not object and superclass is not obj
              and superclass is not abc.ABC):
            supername = superclass.__name__
            supermod = getmodule(superclass)
            if supermod is not module:
              supername = supermod.__name__ + '.' + supername
            mro_names.append(supername)
            mro_set.difference_update(superclass.__mro__)
        if mro_names:
          classname_etc += '(' + ', '.join(mro_names) + ')'
          ##obj_doc = 'MRO: ' + ', '.join(mro_names) + '  \n' + obj_doc
        full_docs.append(f'\n\n## Class `{classname_etc}`\n\n{obj_doc}')
        if issubclass(obj, BaseCommand):
          instance = obj([obj.__name__])
          full_docs.append("\n\nUsage summary:\n\n    ")
          full_docs.append("\n    ".join(instance.usage_text().split("\n")))
        seen_names = set()
        direct_attrs = dict(obj.__dict__)
        # iterate over specified names or default names in order
        for attr_name in method_names or chain(
            # constructor and initialiser
            (
                '__init__',),
            # "constants"
            sorted(filter(lambda name: name and name[0].isupper(),
                          direct_attrs)),
            # dunder methods
            sorted(filter(is_dunder, direct_attrs)),
            # remaining attributes
            sorted(filter(lambda name: name and not name.startswith('_'),
                          direct_attrs)),
        ):
          # prevent repeats, as the automatic list is composed of
          # overlapping components
          if attr_name in seen_names:
            continue
          seen_names.add(attr_name)
          if not method_names:
            # prune some boring names
            if attr_name in ('__abstractmethods__', '__doc__',
                             '__getnewargs__', '__module__', '__new__',
                             '__repr__', '__weakref__'):
              continue
            # prune private names which are not dunder names
            if attr_name.startswith('_') and not is_dunder(attr_name):
              continue
          if attr_name not in direct_attrs:
            ##print("  skip, not in direct_attrs", direct_attrs)
            continue
          attr = getattr(obj, attr_name)
          attr_doc = obj_docstring(attr)
          if not attr_doc:
            continue
          # Class.name is a function, not a method
          if ismethod(attr) or isfunction(attr):
            method_sig = signature(attr)
            full_docs.append(
                f'\n\n*Method `{Mname}.{attr_name}{method_sig}`*:\n{attr_doc}'
            )
          elif isdatadescriptor(attr):
            full_docs.append(
                f'\n\n*Property `{Mname}.{attr_name}`*:\n{attr_doc}'
            )
          elif not callable(attr):
            pass
          elif isinstance(attr, property):
            full_docs.append(f'\n\n*`{Mname}.{attr_name}`*:\n{attr_doc}')
          else:
            full_docs.append(f'\n\n*`{Mname}.{attr_name}`*')
      else:
        warning("UNHANDLED %r, neither function nor class", Mname)
  return ''.join(full_docs)

def obj_docstring(obj):
  ''' Return a docstring for `obj` which has been passed through `stripped_dedent`.

      This function uses `obj.__doc__` if it is not `None`,
      otherwise `getcomments(obj)` if that is not `None`,
      otherwise `''`.
      The chosen string is passed through `stripped_dedent` before return.
  '''
  docstring = getattr(obj, '__doc__', None)
  if docstring is None:
    docstring = '\n'.join(
        map(
            lambda line: cutprefix(line, '# '), (getcomments(obj)
                                                 or '').rstrip().split('\n')
        )
    )
  return stripped_dedent(docstring)

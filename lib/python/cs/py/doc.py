#!/usr/bin/env python3

''' Create documentation from python modules and other objects.
'''

import abc
import importlib
from inspect import (
    getcomments, getmodule, isclass, isfunction, ismethod, signature
)
from itertools import chain
from cs.lex import cutprefix, stripped_dedent
from cs.logutils import warning
from cs.pfx import Pfx
from cs.py.modules import module_attributes

__version__ = '20200521-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.lex', 'cs.logutils', 'cs.pfx', 'cs.py.modules'],
}

def is_dunder(name):
  ''' Test whether a name is a dunder name (`__`*foo*`__`).
  '''
  return len(name) > 4 and name.startswith('__') and name.endswith('__')

def module_doc(
    module,
    *,
    sort_key=lambda item: item[0],
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
  '''
  if isinstance(module, str):
    module_name = module
    with Pfx("import_module(%r)", module_name):
      module = importlib.import_module(module_name)
  full_doc = obj_docstring(module)
  for Mname, obj in sorted(module_attributes(module), key=sort_key):
    with Pfx(Mname):
      if not filter_key(Mname):
        continue
      obj_module = getmodule(obj)
      if obj_module and obj_module is not module:
        # name imported from another module
        continue
      if not isclass(obj) and not isfunction(obj):
        continue
      obj_doc = obj_docstring(obj)
      if not obj_doc:
        continue
      if isfunction(obj):
        sig = signature(obj)
        full_doc += f'\n\n## Function `{Mname}{sig}`\n\n{obj_doc}'
      elif isclass(obj):
        classname_etc = Mname
        mro_names = []
        for superclass in obj.__mro__:
          if (superclass is not object and superclass is not obj
              and superclass is not abc.ABC):
            supername = superclass.__name__
            supermod = getmodule(superclass)
            if supermod is not module:
              supername = supermod.__name__ + '.' + supername
            mro_names.append(supername)
        if mro_names:
          classname_etc += '(' + ','.join(mro_names) + ')'
          ##obj_doc = 'MRO: ' + ', '.join(mro_names) + '  \n' + obj_doc
        direct_attrs = dict(obj.__dict__)
        for attr_name in method_names or chain(
            # constructor and initialiser
            ('__new__', '__init__'),
            # "constants"
            sorted(filter(lambda name: name and name[0].isupper(),
                          direct_attrs)),
            # dunder methods
            sorted(filter(is_dunder, direct_attrs)),
            # remaining attributes
            sorted(direct_attrs),
        ):
          if not method_names:
            # prune some boring names
            if attr_name in ('__doc__', '__module__'):
              continue
            # prune private names which are not dunder names
            if attr_name.startswith('_') and is_dunder(attr_name):
              continue
          if attr_name in direct_attrs:
            attr = direct_attrs.pop(attr_name)
            attr_doc = obj_docstring(attr)
            if not attr_doc:
              continue
            # Class.name is a function, not a (bound?) method
            if ismethod(attr) or isfunction(attr):
              method_sig = signature(attr)
              obj_doc += f'\n\n### Method `{Mname}.{attr_name}{method_sig}`\n\n{attr_doc}'
            else:
              obj_doc += f'\n\n### `{Mname}.{attr_name}`\n\n{attr_doc}'
        full_doc += f'\n\n## Class `{classname_etc}`\n\n{obj_doc}'
      else:
        warning("UNHANDLED %r, neither function nor class", Mname)
  return full_doc

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

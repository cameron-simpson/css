Convenience facilities related to Python functions.
===================================================

funcname(func)
--------------

Returns a string to name `func`. I use this instead of func.__name__ because several things do not have a .__name__. It tries to use .__name__, but falls back to ..__str__().

@derived_property
-----------------

Decorator for property functions which must be recomputed if another property is updated.

@derived_from(property_name)
----------------------------

Convenience wrapper of derived_property which names the parent property.

@returns_type(func, basetype)
-----------------------------

Basis for decorators to do type checking of function return values. Example::

  def returns_bool(func):
    ''' Decorator for functions which should return Booleans.
    '''
    return returns_type(func, bool)

  @returns_bool
  def f(x):
    return x == 1

This has been used for debugging functions called far from their definitions.

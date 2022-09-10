#!/usr/bin/env python3

''' Utility functions around state changes.
'''

from time import sleep

from icontract import require

from cs.obj import Sentinel

MISSING = Sentinel("MISSING")

def delta(old, new, keys=None):
  ''' Return a mapping representing differences between the mappings
      `old` and `new` for the specified `keys`.
      If `keys` is not specified, the union of the keys of `old`
      and `new` is used.

      The returned mapping has a key for each changed value.
      If the key does not exist in `new` the value is the `MISSING`
      sentinel object otherwise it is `new[key]`.
      Values are compared using `==`; if that raises `TypeError`
      the values are considered not equal.

      Example:

        >>> d1 = {1: 2, 3: 4, 5: 6}
        >>> d2 = {1: 2, 3: 44, 7: 8}
        >>> diff = delta(d1, d2)
        >>> diff  # doctest: +ELLIPSIS
        {3: 44, 5: <object object at ...>, 7: 8}
        >>> diff[5] is MISSING
        True
  '''
  if keys is None:
    keys = set(old.keys()) | set(new.keys())
  d = {}
  for k in keys:
    oldv = old.get(k, MISSING)
    newv = new.get(k, MISSING)
    if oldv is MISSING:
      if newv is MISSING:
        continue
    elif newv is not MISSING:
      try:
        if oldv == newv:
          continue
      except TypeError:
        pass
    d[k] = newv
  return d

@require(lambda get_state: callable(get_state))
@require(lambda interval: interval > 0.0)
def monitor(
    get_state,
    keys=None,
    *,
    ifunchanged=False,
    interval=0.3,
    runstate=None,
):
  ''' A generator yielding 3-tuples of `(old,new,delta(old,new,keys))`
      at poll intervals of `interval` seconds.

      Parameters:
      * `get_state`: a callable which polls the current state,
        returning a mapping
      * `keys`: an optional iterable of keys of interest;
        if omitted, all the old and new mapping keys are examined
      * `ifunchanged`: optional flag, default `False`;
        if true yield a tuple on every poll instead of only when a
        state change is seen
      * `interval`: an optional interpoll `time.sleep` period,
        default `0.3`s
      * `runstate`: an optional `RunState`, whose `cancelled`
        attribute will be polled for loop termination
  '''
  old = get_state()
  while runstate is None or not runstate.cancelled:
    sleep(interval)
    if runstate is None or not runstate.cancelled:
      new = get_state()
      d = delta(old, new, keys)
      if ifunchanged or d:
        yield old, new, d
      old = new

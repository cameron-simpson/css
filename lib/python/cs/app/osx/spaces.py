#!/usr/bin/env python3

''' Access to the display spaces.
'''

from .objc import apple, cg

CG = apple.CoreGraphics
HI = apple.HIServices

class Spaces:
  ''' The spaces for a particular display.
  '''

  @cg
  def __init__(self, display_index=0, *, cg_conn):
    ''' Initialise.

        Parameters:
        * `display_index`: optional display index, default `0`
        * `cg_conn`: optional CoreGraphics connection,
          default from `CoreGraphics._CGSDefaultConnection()`
    '''
    self.cg_conn = cg_conn
    self.display_index = display_index

  def forget(self):
    ''' Forget the current spaces information.
        This will cause it to be obtained anew.
    '''
    self.__dict__.pop('_spaces', None)

  @property
  def x(self):
    return "X"

  def __getattr__(self, attr):
    X("attr=%r", attr)
    if attr == '_spaces':
      obj = self._load_spaces()
    else:
      raise AttributeError(f'{self.__class__.__name__}.{attr}')
    self.__dict__[attr] = obj
    return obj

  def __len__(self):
    return len(self._spaces["Spaces"])

  def __getitem__(self, space_index):
    ''' Return the space at index `space_index`.
        Note that the index counts from `0`, while the desktop space
        number counts from `1`.
    '''
    space_list = self._spaces["Spaces"]
    if space_index < 0 or space_index >= len(space_list):
      raise IndexError(space_index)
    return self._spaces["Spaces"][space_index]

  def _load_spaces(self):
    display_spaces = CG.CGSCopyManagedDisplaySpaces(self.cg_conn)
    return display_spaces[self.display_index]

  @property
  def current_index(self):
    ''' The index of the current space, found by scanning the spaces
        for the current space UUID.
        Returns `None` if not found.
    '''
    uuid = self.current["uuid"]
    for i, space in enumerate(self._spaces["Spaces"]):
      if space["uuid"] == uuid:
        return i
    return None

  @property
  def current(self):
    return self._spaces["Current Space"]

  @property
  def current_uuid(self):
    return self.current["uuid"]

  @property
  def display_uuid(self):
    return self._spaces["Display Identifier"]

  @property
  def display_id(self):
    cfuuid = CFUUIDCreateFromString(None, self.display_uuid)
    return CG.CGSGetDisplayForUUID(cfuuid)

if __name__ == '__main__':
  spaces = Spaces()
  print(spaces.x)
  print(spaces.display_uuid)
  print(spaces._spaces)
  print(spaces.current_index)
  print(spaces.current)

#!/usr/bin/python
#

DISTINFO = {
    'description': "Mixin for .FOO uppercase attributes mapped to ['FOO'] access.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': [],
}

class WithUC_Attrs(object):
  ''' An mixin where access to obj.FOO accesses obj['FOO']
      if FOO matches ^[A-Z][_A-Z0-9]*$.
  '''

  def __uc_(self, s):
    ''' Test is the string `s` matches.
    '''
    if s.isalpha() and s.isupper():
      return True
    if len(s) < 1:
      return False
    if not s[0].isupper():
      return False
    for c in s[1:]:
      if c != '_' and not (c.isupper() or c.isdigit()):
        return False
    return True

  def __getattr__(self, attr):
    ''' Access to self.UCName returns self['UCName'].
    '''
    if self.__uc_(attr):
      return self[attr]
    return super(WithUC_Attrs, self).__getattr__(attr)

  def __setattr__(self, attr, value):
    ''' Setting self.UCName sets self['UCName'].
    '''
    if self.__uc_(attr):
      self[attr] = value
      return
    return super(WithUC_Attrs, self).__setattr__(attr, value)

class UCdict(WithUC_Attrs, dict):
  ''' Subclass of dict with .X ==> ['X'] support.
  '''
  pass

if __name__ == '__main__':
  d = UCdict({1:2, 'X': 3})
  print(repr(d))
  print(d[1])
  print(d['X'])
  print(d.X)
  d.G = 4
  print(repr(d))

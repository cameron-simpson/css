import os

def is_iphone():
  ''' Test if we're on an iPhone.
  '''
  return os.uname()[4].startswith('iPhone')

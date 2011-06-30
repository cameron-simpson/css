#############################################################################
# Functions to handle general filenames on the basis that they are a sequence
# of (by default) Latin-1 bytes, and recode them as a Normal Form D UTF-8
# sequence of bytes.
# Such names will still be valid on a UNIX filesystem, and be accepted by
# a MacOSX HFS filesystem, which rejects attempts to make filenames with
# invalid NFD UTF8 byte sequence names.
#

def nfd(name, srcencoding='iso8859-1'):
  ''' Convert a name from another encoding (default ISO8859-1, Latin-1)
      into MacOSX HFS friendly UTF-8 Normal Form D.
  '''
  uf = unicode(name, srcencoding)          # get Unicode version
  nfduf = unicodedata.normalize('NFD', uf) # transform to Normal Form D
  utf8f = nfduf.encode('utf8')            # transcribe to UTF-8
  return utf8f

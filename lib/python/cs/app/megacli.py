#!/usr/bin/python
#
# Convenience routines to access MegaRAID adapters via the megacli
# command line tool.
#       - Cameron Simpson <cs@zip.com.au> 29jan2013
#

import sys
from contextlib import contextmanager
from subprocess import Popen, PIPE
from cs.logutils import setup_logging, error, warning, D
from cs.misc import O, O_attrs

# location of MegaCLI executable
MEGACLI = '/opt/MegaRAID/MegaCli/MegaCli64'

def main(argv):
  argv = list(argv)
  cmd = argv.pop(0)
  setup_logging(cmd)
  usage="Usage: %s adaptor#" % (cmd,)

  badopts = False

  if not argv:
    warning("missing adaptor#")
    badopts = True
  else:
    adaptor = int(argv.pop(0))

  if argv:
    warning("extra arguments: %s", " ".join(argv))
    badopts = True

  if badopts:
    error(usage)
    return 2

  D("MM")
  M = megacli_info()
  D("MM2")
  print "M.attrs =", list(O_attrs(M))
  for An in M.adapters:
    print "Adapter", An
    A = M.adapters[An]
    for Vn in A.virtual_drives:
      print "  Virtual Drive", Vn
      V = A.virtual_drives[Vn]
      print "    %d drives, size = %s%s, raid = %s" % (V.n_drives, V.size, V.size_units, V.raid_level)
    print "  %d drives:" % (len(A.drives),)
    for DRV in A.drives:
      ##print "    attrs =", list(O_attrs(DRV))
      print "    [%d:%d]: slot %d, %s" % (DRV.enclosure_device_id, DRV.device_id, DRV.slot_number, DRV.firmware_state)

def megacli_info():
  ''' Read various megacli query command outputs and construct a
      data structure with the adpater information.
  '''
  I = O(adapters={})
  A = None
  V = None
  with megacli('-LDInfo', '-LAll', '-aAll') as M:
    mlineno = 0
    for line in M:
      mlineno += 1
      if not line.endswith('\n'):
        raise ValueError("%d: missing newline" % (mlineno,))
      line = line.rstrip()
      if not line:
        continue
      if line.startswith('Adapter ') and line.endswith(' -- Virtual Drive Information:'):
        An = int(line.split()[1])
        A = O(number=An, virtual_drives={})
        I.adapters[An] = A
        D("A = %s", A)
        o = A
        continue
      if line == 'Name                :':
        o.name = ''
        continue
      if ': ' in line:
        heading, info = line.split(': ', 1)
        heading = heading.rstrip()
        info = info.lstrip()
        if heading == 'Virtual Drive':
          Vn, Tn = info.split(' (Target Id: ', 1)
          Vn = int(Vn)
          Tn = int(Tn[:-1])
          V = O(number=Vn, target_id=Tn)
          D("V = %s", V)
          A.virtual_drives[Vn] = V
          o = V
          continue
        attr = heading.lower().replace(' ', '_')
        if attr == 'raid_level':
          raid_level = O()
          for field in line[22:].split(', '):
            k, v = field.split('-', 1)
            setattr(raid_level, k.lower().replace(' ', '_'), int(v))
          info = raid_level
        elif attr in ('size', 'mirror_data', 'strip_size'):
          size, size_units = info.split()
          setattr(o, attr+'_units', size_units)
          info = float(size)
        elif heading == 'Number Of Drives':
          attr = 'n_drives'
          info = int(info)
          o.drives = []
        elif attr == 'span_depth':
          info = int(info)
        elif attr in ('default_cache_policy', 'current_cache_policy'):
          info = info.split(', ')
        elif attr == 'is_vd_cached':
          attr = 'vd_cached'
          info = True if info == 'Yes' else False
        setattr(o, attr, info)
        continue
      raise ValueError("%d: unparsed line: [%s]" % (mlineno, line))
  A = None
  V = None
  with megacli('-PDlist', '-aAll') as M:
    mlineno = 0
    for line in M:
      mlineno += 1
      if not line.endswith('\n'):
        raise ValueError("%d: missing newline" % (mlineno,))
      line = line.rstrip()
      if not line:
        continue
      if line.startswith('Adapter #'):
        An = int(line[9:])
        A = I.adapters[An]
        A.drives = []
        continue
      if line.startswith('Enclosure Device ID: '):
        enc_dev_id = int(line[21:])
        DRV = O(adapter=A, enclosure_device_id=enc_dev_id)
        o = DRV
        A.drives.append(DRV)
        continue
      if line == 'WWN:':
        o.wwn = ''
        continue
      if line.startswith('Port-') and line.endswith(' :'):
        P = O(drive=DRV, number=int(line[5:-2]))
        o = P
        continue
      if line.startswith('Drive has flagged a S.M.A.R.T alert : '):
        flagged = line[38:]
        DRV.smart_alert_flagged = False if flagged == 'No' else True
        o = DRV
        continue
      if ': ' in line:
        heading, info = line.split(': ', 1)
        heading = heading.rstrip()
        info = info.lstrip()
        attr = heading.lower().replace(' ', '_')
        try:
          n = int(info)
        except ValueError:
          pass
        else:
          info = n
        setattr(o, attr, info)
        continue
      if line.startswith('Drive Temperature :'):
        o.drive_temperature = line[19:].strip()
        continue
      raise ValueError("%d: unparsed line: %s" % (mlineno, line))
  return I

@contextmanager
def megacli(*args):
  ''' Open a pipe from the megacli command, returning a subprocess.Popen object.
      Yield the stdout attribute.
  '''
  P = Popen(['set-x', MEGACLI] + list(args), stdout=PIPE, close_fds=True)
  yield P.stdout
  P.wait()

if __name__ == '__main__':
  sys.exit(main(sys.argv))

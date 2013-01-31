#!/usr/bin/python
#
# Convenience routines to access MegaRAID adapters via the megacli
# command line tool.
#       - Cameron Simpson <cs@zip.com.au> 29jan2013
#

import re
import sys
from contextlib import contextmanager
from subprocess import Popen, PIPE
from cs.logutils import setup_logging, error, warning, D, Pfx
from cs.misc import O, O_attrs, O_merge

# location of MegaCLI executable
MEGACLI = '/opt/MegaRAID/MegaCli/MegaCli64'

mode_CFGDSPLY = 0       # -CfgDsply mode
mode_PDLIST = 1         # -PDlist mode

re_SPEED = re.compile('^(\d+(\.\d+)?)\s*(\S+)$')

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

  M = megacli_info()

  ##print repr(M)

  for An in M.adapters:
    A = M.adapters[An]
    print "Adapter", An, A.product_name, "serial#", A.serial_no
    for Vn, V in A.virtual_drives.items():
      print "  Virtual Drive", Vn
      print "    %d drives, size = %s%s, raid = %s" % (len(V.physical_disks), V.size, V.size_units, V.raid_level)
      for DRVn, DRV in V.physical_disks.items():
        print "      physical drive", DRV.enc_devid
    print "  %d drives:" % (len(A.physical_disks),)
    for DRV in A.physical_disks.values():
      ##print "    attrs =", list(O_attrs(DRV))
      print "    [%s]: slot %d, VD %s, DG %s: %s %s %s, %s" % (DRV.enc_devid, DRV.slot_number,
                                                       getattr(DRV, 'virtual_drive', O(number=None)).number,
                                                       getattr(DRV, 'disk_group', O(number=None)).number,
                                                       DRV.fru, DRV.raw_size, DRV.raw_size_units,
                                                       DRV.firmware_state)

def megacli_info():
  ''' Read various megacli query command outputs and construct a
      data structure with the adpater information.
  '''

##  with Pfx("LDinfo"):
##    A = None
##    V = None
##    with megacli('-LDInfo', '-LAll', '-aAll') as M:
##      mlineno = 0
##      for line in M:
##        mlineno += 1
##        if not line.endswith('\n'):
##          raise ValueError("%d: missing newline" % (mlineno,))
##        line = line.rstrip()
##        if not line:
##          continue
##        if line.startswith('Adapter ') and line.endswith(' -- Virtual Drive Information:'):
##          An = int(line.split()[1])
##          A = Adapter(number=An, virtual_drives={}, physical_disks={})
##          M.adapters[An] = A
##          o = A
##          continue
##        if line == 'Name                :':
##          o.name = ''
##          continue
##        if ': ' in line:
##          heading, info = line.split(': ', 1)
##          heading = heading.rstrip()
##          info = info.lstrip()
##          if heading == 'Virtual Drive':
##            Vn, Tn = info.split(' (Target Id: ', 1)
##            Vn = int(Vn)
##            Tn = int(Tn[:-1])
##            V = Virtual_Drive(adapter=A, number=Vn, target_id=Tn, physical_disks={})
##            D("created virtual disk %d id=%s", Vn, id(V))
##            A.virtual_drives[Vn] = V
##            o = V
##            continue
##          attr = heading.lower().replace(' ', '_')
##          if attr == 'raid_level':
##            raid_level = O()
##            for field in line[22:].split(', '):
##              k, v = field.split('-', 1)
##              setattr(raid_level, k.lower().replace(' ', '_'), int(v))
##            info = raid_level
##          elif attr in ('size', 'mirror_data', 'strip_size'):
##            size, size_units = info.split()
##            setattr(o, attr+'_units', size_units)
##            info = float(size)
##          elif heading == 'Number Of Drives':
##            attr = 'n_drives'
##            info = int(info)
##            o.drives = []
##          elif attr == 'span_depth':
##            info = int(info)
##          elif attr in ('default_cache_policy', 'current_cache_policy'):
##            info = info.split(', ')
##          elif attr == 'is_vd_cached':
##            attr = 'vd_cached'
##            info = True if info == 'Yes' else False
##          setattr(o, attr, info)
##          continue
##        raise ValueError("%d: unparsed line: [%s]" % (mlineno, line))
##
##  with Pfx("PDlist"):
##    A = None
##    V = None
##    with megacli('-PDlist', '-aAll') as M:
##      mlineno = 0
##      for line in M:
##        mlineno += 1
##        if not line.endswith('\n'):
##          raise ValueError("%d: missing newline" % (mlineno,))
##        line = line.rstrip()
##        if not line:
##          continue
##        if line.startswith('Adapter #'):
##          An = int(line[9:])
##          A = M.adapters[An]
##          A.drives = []
##          continue
##        if line.startswith('Enclosure Device ID: '):
##          if DRV is not None:
##            A.physical_disks[DRV.enc_devid] = DRV
##          enc_dev_id = int(line[21:])
##          DRV = Physical_Disk(adapter=A, enclosure_device_id=enc_dev_id)
##          o = DRV
##          A.drives.append(DRV)
##          continue
##        if line == 'WWN:':
##          o.wwn = ''
##          continue
##        if line.startswith('Port-') and line.endswith(' :'):
##          P = Disk_Port(drive=DRV, number=int(line[5:-2]))
##          o = P
##          continue
##        if line.startswith('Drive has flagged a S.M.A.R.T alert : '):
##          flagged = line[38:]
##          DRV.smart_alert_flagged = False if flagged == 'No' else True
##          o = DRV
##          continue
##        if ': ' in line:
##          heading, info = line.split(': ', 1)
##          heading = heading.rstrip()
##          info = info.lstrip()
##          attr = heading.lower().replace(' ', '_')
##          try:
##            n = int(info)
##          except ValueError:
##            pass
##          else:
##            info = n
##          setattr(o, attr, info)
##          continue
##        if line.startswith('Drive Temperature :'):
##          o.drive_temperature = line[19:].strip()
##          continue
##        raise ValueError("%d: unparsed line: %s" % (mlineno, line))
##    if DRV is not None:
##      A.physical_disks[DRV.enc_devid] = DRV

  Mconfigured = O(adapters={})
  megacli_parse(['-CfgDsply', '-aAll'], Mconfigured, mode_CFGDSPLY)
  # record physical drives by enclosure/slot
  for A in Mconfigured.adapters.values():
    for V in A.virtual_drives.values():
      for DRV in V.physical_disks.values():
        A.physical_disks[DRV.enc_devid] = DRV

  with Pfx("merge CfgDsply/PDlist"):
    Mphysical = O(adapters={})
    megacli_parse(['-PDlist', '-aAll'], Mphysical, mode_PDLIST)
    for A in Mphysical.adapters.values():
      disks = Mconfigured.adapters[A.number].physical_disks
      for enc_devid, DRV in A.physical_disks.items():
        with Pfx(enc_devid):
          if enc_devid in disks:
            D("%s: merge PDlist DRV with Mconfigured DRV", enc_devid)
            O_merge(disks[enc_devid], **DRV.__dict__)
          else:
            D("%s: add new DRV to Mconfigured", enc_devid)
            disks[enc_devid] = DRV
      D("Mphysical merged")

  return Mconfigured

def megacli_parse(megacli_args, M, mode):
  ''' Generic parser for megacli output.
  '''
  o = 9
  with Pfx(" ".join(megacli_args)):
    A = None
    V = None
    SP = None
    DG = None
    DRV = None
    o = None
    with megacli(*megacli_args) as mlines:
      mlineno = 0
      for line in mlines:
        mlineno += 1
        with Pfx("%d", mlineno):
          if not line.endswith('\n'):
            raise ValueError("%d: missing newline" % (mlineno,))
          line = line.rstrip()
          if not line:
            continue
          if line.startswith('======='):
            continue
          if ( line == 'Virtual Drive Information:'
            or line == 'Physical Disk Information:'
             ):
            o = None
            continue
          if line.startswith('Adapter #'):
            An = int(line[9:])
            A = Adapter(number=An, physical_disks={})
            M.adapters[An] = A
            o = A
            continue
          if ': ' in line:
            heading, info = line.split(': ', 1)
            heading = heading.rstrip()
            info = info.lstrip()
            attr = heading.lower().replace(' ', '_').replace('.','').replace("'",'').replace('/','_')
            try:
              n = int(info)
            except ValueError:
              pass
            else:
              info = n
            if mode == mode_CFGDSPLY:
              if heading == 'Adapter':
                An = info
                ##D("new adapter %d", An)
                A = M.adapters[info] = Adapter(number=An, disk_groups={}, physical_disks={}, virtual_drives={})
                o = A
                continue
              if heading == 'DISK GROUP':
                DGn = info
                ##D("new disk_group %d", DGn)
                A.disk_groups[DGn] = Disk_Group(adapter=A, number=DGn, spans={})
                DG = A.disk_groups[DGn]
                o = DG
                continue
              if heading == 'SPAN':
                SPn = info
                ##D("new span %d", SPn)
                DG.spans[SPn] = Span(disk_group=DG, number=SPn, arms={})
                SP = DG.spans[SPn]
                o = SP
                continue
              if heading == 'Virtual Drive':
                Vn, Tn = info.split(' (Target Id: ', 1)
                Vn = int(Vn)
                Tn = int(Tn[:-1])
                ##D("new virtual drive %d (target id %d)", Vn, Tn)
                V = A.virtual_drives[Vn] = Virtual_Drive(adapter=A, number=Vn, physical_disks={})
                o = V
                continue
              if heading == 'Physical Disk':
                DRVn = info
                ##D("new physical disk %d", DRVn)
                DRV = Physical_Disk(virtual_drive=V, number=DRVn, adapter=A)
                V.physical_disks[DRVn] = DRV
                DRV.virtual_drive = V
                o = DRV
                continue
            if mode == mode_PDLIST:
              if heading == 'Enclosure Device ID':
                # new physical drive commences
                if DRV is not None:
                  enc_devid = DRV.enc_devid
                  with Pfx("merge previous DRV [%s]", enc_devid):
                    if enc_devid in A.physical_disks:
                      O_merge(A.physical_disks[enc_devid], **DRV.__dict__)
                    else:
                      A.physical_disks[enc_devid] = DRV
                DRV = Physical_Disk()
                o = DRV
            if attr in ('size', 'mirror_data', 'strip_size'):
              size, size_units = info.split()
              setattr(o, attr+'_units', size_units)
              info = float(size)
            elif attr in ('raw_size', 'non_coerced_size', 'coerced_size'):
              size, size_units, sectors = info.split(None, 2)
              setattr(o, attr+'_units', size_units)
              if sectors.startswith('[0x') and sectors.endswith(' Sectors]'):
                setattr(o, attr+'_sectors', int(sectors[3:-9], 16))
              else:
                warning("invalid sectors: %s", sectors)
              info = float(size)
            elif attr.endswith('_speed'):
              m = re_SPEED.match(info)
              if m:
                speed, speed_units = m.group(1), m.group(3)
                setattr(o, attr+'_units', speed_units)
                info = float(speed)
              else:
                warning("failed to match re_SPEED against: %s", info)
            elif attr in ('default_cache_policy', 'current_cache_policy'):
              info = info.split(', ')
            elif attr == 'drives_postion':
              DPOS = O()
              for posinfo in info.split(', '):
                dposk, dposv = posinfo.split(': ')
                setattr(DPOS, dposk.lower(), int(dposv))
              attr = 'drive_position'
              info = DPOS
            elif info == 'Yes':
              info = True
            elif info == 'No':
              info = False
            ##D("%s.%s = %s", o.__class__.__name__, attr, info)
            setattr(o, attr, info)
            continue
          warning("unparsed line: %s", line)

        # catch trailing drive
        if mode == mode_PDLIST:
          if DRV is not None:
            enc_devid = DRV.enc_devid
            D("PDLIST: note physical drive %s", enc_devid)
            with Pfx("final merge previous DRV [%s]", enc_devid):
              if enc_devid in A.physical_disks:
                O_merge(A.physical_disks[enc_devid], **DRV.__dict__)
              else:
                A.physical_disks[enc_devid] = DRV

@contextmanager
def megacli(*args):
  ''' Open a pipe from the megacli command, returning a subprocess.Popen object.
      Yield the stdout attribute.
  '''
  P = Popen(['set-x', MEGACLI] + list(args), stdout=PIPE, close_fds=True)
  yield P.stdout
  P.wait()

class Adapter(O):
  pass
class Virtual_Drive(O):
  def __init__(self, **kw):
    O.__init__(self, **kw)
    self._O_omit.append('adapter')
class Disk_Group(O):
  def __init__(self, **kw):
    O.__init__(self, **kw)
    self._O_omit.append('adapter')
class Span(O):
  pass
class Physical_Disk(O):
  @property
  def enc_devid(self):
    return "%d:%d" % (self.enclosure_device_id, self.device_id)
  @property
  def fru(self):
    return self.ibm_fru_cru
class Disk_Port(O):
  pass

if __name__ == '__main__':
  sys.exit(main(sys.argv))

#!/usr/bin/python
#
# Convenience routines to access MegaRAID adapters via the megacli
# command line tool.
# - Cameron Simpson <cs@cskk.id.au> 29jan2013
#

r'''
Command line tool to inspect and manipulate LSI MegaRAID adapters,
such as used in IBM ServeRAID systems and Dell PowerEdge RAID Controller (PERC).

This is the obsolete Python 2 version of the `cs.app.megacli` module
preserved should someone need to run it on a Python 2 only system.
'''

DISTINFO = {
    'description': "(Python 2 version) command line tool to inspect and manipulate LSI MegaRAID adapters, such as used in IBM ServerRAID systems and Dell PowerEdge RAID Controller (PERC)",
    'keywords': ["python2"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        ],
}

import os
import re
import sys
from subprocess import call, Popen, PIPE

USAGE = '''Usage:
    %s locate enc_slot [{start|stop}]
    %s new_raid raid_level enc:devid...
    %s offline enc_slot
    %s report
    %s save save_file
    %s status'''

# default location of MegaCLI executable
MEGACLI = '/opt/MegaRAID/MegaCli/MegaCli64'

mode_CFGDSPLY = 0       # -CfgDsply mode
mode_PDLIST = 1         # -PDlist mode

re_SPEED = re.compile('^(\d+(\.\d+)?)\s*(\S+)$')

def main(argv=None, debug=None):
  global D, cmd, cmd_old
  if debug is None:
    debug = os.environ.get('DEBUG')
  if not debug:
    def D(msg, *a): pass
  if argv is None:
    argv = sys.argv
  argv = list(argv)
  cmd = argv.pop(0)
  cmd_old = []
  usage = USAGE % (cmd, cmd, cmd, cmd, cmd, cmd)

  badopts = False

  # default adapter number
  adapter = 0

  if not argv:
    warning("missing command")
    badopts = True
  else:
    command = argv.pop(0)

  if not badopts:
    xit = 0

    M = MegaRAID()

    if command == "report":
      for An in M.adapters:
        A = M.adapters[An]
        print "Adapter", An, A.product_name, "serial#", A.serial_no
        for Vn, V in A.virtual_drives.items():
          print "  Virtual Drive", Vn
          print "    %d drives, size = %s%s, raid = %s" % (len(V.physical_disks), V.size, V.size_units, V.raid_level)
          for DRVn, DRV in V.physical_disks.items():
            print "      physical drive", DRV.id, "[%s]" % (DRV.enc_slot,)
        print "  %d drives:" % (len(A.physical_disks),)
        for DRV in A.physical_disks.values():
          print "    %s [%s]: VD %s, DG %s: %s %s %s, %s" % (DRV.id, DRV.enc_slot,
                                                             getattr(DRV, 'virtual_drive', O(number=None)).number,
                                                             getattr(DRV, 'disk_group', O(number=None)).number,
                                                             DRV.fru, DRV.raw_size, DRV.raw_size_units,
                                                             DRV.firmware_state
                                                            ),
          try:
            count = DRV.media_error_count
          except AttributeError:
            pass
          else:
            if count:
              print ", media errors %s" % count,
          try:
            count = DRV.other_error_count
          except AttributeError:
            pass
          else:
            if count:
              print ", other errors %s" % count,
          print
    elif command == "save":
      save_file, = argv
      if save_raid(save_file) != 0:
        xit = 1
    elif command == "locate":
      enc_slot = argv.pop(0)
      if argv:
        do_start = argv.pop(0)
        if do_start == "start":
          do_start = true
        elif do_start == "stop":
          do_start = False
        else:
          warning("locate: bad start/stop setting: %r", do_start)
          badopts = True
        if argv:
          warning("locate: extra arguments after start/stop: %s", ' '.join(argv))
          badopts = True
      else:
        do_start = True
      if not badopts:
        M.locate(adapter, enc_slot, do_start)
    elif command == "offline":
      enc_slot = argv.pop(0)
      if argv:
        warning("locate: extra arguments after start/stop: %s", ' '.join(argv))
        badopts = True
      if not badopts:
        M.offline(adapter, enc_slot)
    elif command == "new_raid":
      if len(argv) < 2:
        warning("new_raid: missing raid_level or drives")
        badopts = True
      else:
        level = int(argv.pop(0))
        if M.new_raid(level, argv, adapter=adapter) != 0:
          xit = 1
    elif command == "status":
      status_all = []
      for An in M.adapters:
        adapter_errs = []
        A = M.adapters[An]
        for DRV in A.physical_disks.values():
          firmware_state = getattr(DRV, 'firmware_state', 'UNKNOWN')
          if firmware_state not in ( "Online, Spun Up", "Unconfigured(good), Spun Up"):
            adapter_errs.append("drive:%s[%s]/VD:%s/%s"
                                % (DRV.id, DRV.enc_slot,
                                   getattr(DRV, 'virtual_drive', O(number=None)).number,
                                   DRV.firmware_state))
        if adapter_errs:
          print "FAIL A%d %s" % (An, ",".join(adapter_errs))
        else:
          print "OK A%d" % (An,)
    else:
      error("unsupported command")
      xit = 1

  if badopts:
    print >>sys.stderr, usage
    return 2

  return xit

class O(object):

  def __init__(self, **kw):
    for attr, value in kw.items():
      setattr(self, attr, value)
    self._O_omit = []

class MegaRAID(O):

  def __init__(self, megacli=None):
    if megacli is None:
      megacli = os.environ.get('MEGACLI', MEGACLI)
    self.megacli = megacli

  @property
  def adapters(self):
    ''' Mapping of adapter numbers to Adapters.
    '''
    return self.info.adapters

  @property
  def info(self):
    ''' Read various megacli query command outputs and construct a
        data structure with the adpater information.
    '''
    cmd_append("megacli -CfgDsply -aAll")
    ##Mconfigured = self._parse(self.readcmd(['-CfgDsply', '-aAll']), mode_CFGDSPLY)
    Mconfigured = self._parse(open('CfgDsply.txt'), mode_CFGDSPLY)
    # record physical drives by id (NB: _not_ enclosure/slot)
    for A in Mconfigured.adapters.values():
      for V in A.virtual_drives.values():
        for VDRVn, DRV in V.physical_disks.items():
          if DRV.id in A.physical_disks:
            error("VD drive %d: %s already in A.physical_disks", VDRVn)
          else:
            A.physical_disks[DRV.id] = DRV
    cmd_pop()

    cmd_append("megacli -PDlist -aAll")
    ##Mphysical = self._parse(self.readmcd(['-PDlist', '-aAll']), mode_PDLIST)
    Mphysical = self._parse(open('PDList.txt'), mode_PDLIST)
    for A in Mphysical.adapters.values():
      disks = Mconfigured.adapters[A.number].physical_disks
      for DRVid, DRV in A.physical_disks.items():
        cmd_append(str(DRVid))
        if DRVid in disks:
          D("%s: merge PDlist DRV with Mconfigured DRV", DRVid)
          merge_attrs(disks[DRVid], **DRV.__dict__)
        else:
          D("%s: add new DRV to Mconfigured", DRVid)
          disks[DRVid] = DRV
        cmd_pop()
      D("Mphysical merged")
    cmd_pop()

    return Mconfigured

  def _preparse(self, fp, start=1):
    ''' Generator yielding (lineno, line, heading, info, attr).
        Skips blank lines etc.
    '''
    for mlineno, line in enumerate(fp, start=start):
      if not line.endswith('\n'):
        raise ValueError("%d: missing newline" % (mlineno,))
      line = line.rstrip()
      if not line:
        continue
      if line.startswith('======='):
        continue
      line = line.rstrip()
      heading = line
      info = None
      attr = None
      if ': ' in line:
        heading, info = line.split(': ', 1)
      elif ' :' in line:
        heading, info = line.split(' :', 1)
      elif line.endswith(':'):
        heading = line[:-1]
        info = ''
      else:
        heading = line
        info = ''
      heading = heading.rstrip()
      info = info.lstrip()
      attr = heading.lower().replace(' ', '_').replace('.','').replace("'",'').replace('/','_')
      try:
        n = int(info)
      except ValueError:
        pass
      else:
        info = n
      yield mlineno, line, heading, info, attr

  def _parse(self, fp, mode):
    ''' Generic parser for megacli output.
        Update 
    '''
    M = O(adapters={})
    o = None
    A = None
    V = None
    SP = None
    DG = None
    DRV = None
    o = None
    for mlineno, line, heading, info, attr  in self._preparse(fp):
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
          # new physical drive commences,
          # do housekeeping for previous drive and then proceed
          if DRV is not None:
            DRVid = DRV.id
            cmd_append("merge previous DRV %s", DRVid)
            if DRVid in A.physical_disks:
              merge_attrs(A.physical_disks[DRV.id], **DRV.__dict__)
            else:
              A.physical_disks[DRV.id] = DRV
            cmd_pop()
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
        elif info != "Unknown":
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
      if o is None:
        error("o is None, not setting .%s to %r", attr, info)
      else:
        setattr(o, attr, info)
      continue

      # catch trailing drive
      if mode == mode_PDLIST:
        if DRV is not None:
          DRVid = DRV.id
          D("PDLIST: note physical drive %s", DRVid)
          cmd_append("final merge previous DRV %s", DRVid)
          if DRVid in A.physical_disks:
            merge_attrs(A.physical_disks[DRV.id], **DRV.__dict__)
          else:
            A.physical_disks[DRV.id] = DRV
          cmd_pop()

    return M

  def locate(self, adapter, enc_slot, do_start=True):
    ''' Start or stop to location light on the specified drive.
    '''
    if do_start:
      start_opt = '-start'
    else:
      start_opt = '-stop'
    return self.docmd('-PdLocate',
                      start_opt,
                      '-physdrv[%s]' % (enc_slot,),
                      '-a%d' % (adapter,),
                     )

  def offline(self, adapter, enc_slot):
    ''' Take a drive offline (==> failed).
    '''
    return self.docmd('-PDOffline',
                      '-physdrv[%s]' % (enc_slot,),
                      '-a%d' % (adapter,),
                     )

  def new_raid(self, level, enc_slots, adapter=0):
    ''' Construct a new RAID device with specified RAID `level` on
        `adapter` using the devices specified by `enc_slots`.
    '''
    ## RAID 6 example: -CfgLdAdd -r6 [0:0,0:1,0:2,0:3,0:4,0:5,0:6] -a1
    ok = True
    cmd_append("#%d", adapter)
    if adapter not in self.adapters:
      error("unknown adapter")
      ok = False
    else:
      A = self.adapters[adapter]
      for enc_slot in enc_slots:
        cmd_append(str(enc_slot))
        DRV = A.DRV_by_enc_slot(enc_slot)
        if DRV is None:
          error("unknown disk")
          ok = False
        else:
          if DRV.firmware_state != 'Unconfigured(good), Spun Up':
            error("rejecting drive, firmware state not unconfigured good: %s", DRV.firmware_state)
            ok = False
          else:
            D("acceptable drive: %s", DRV.firmware_state)
        cmd_pop()
    cmd_pop()
    if not ok:
      return False
    return self.docmd('-CfgLdAdd', '-r%d' % (level,), "[" + ",".join(enc_slots) + "]", '-a%d' % (adapter,))

  def readcmd(self, *args):
    ''' Open a pipe from the megacli command and yield lines from its output.
    '''
    cmdargs = [self.megacli] + list(args)
    D("+ %r", cmdargs)
    P = Popen(cmdargs, stdout=PIPE, close_fds=True)
    for line in P.stdout:
      yield line
    P.wait()

  def docmd(self, *args):
    ''' Pretend to execute a megacli command as specified.
        This currently just echoes commands to stderr; I fear running
        the "new raid" stuff etc automatically.
        Return True if the exit code is 0, False otherwise.
    '''
    cmdargs = [self.megacli] + list(args)
    D("%r", cmdargs)
    ## return call(cmdargs) == 0
    return True

  def adapter_save(self, adapter, save_file):
    savepath = "%s-a%d" % (save_file, adapter)
    if os.path.exists(save_file):
      error("file exists: %s", savepath)
      return False
    return self.docmd('-CfgSave', '-f', save_file, '-a%d' % (adapter,))

class Adapter(O):
  def DRV_by_enc_slot(self, enc_slot):
    ''' Find first matching drive by enclosure id and slot number.
        Report errors on multiple matches - serious misconfiguration.
        Returns None if no match.
    '''
    DRV = None
    for aDRV in self.physical_disks.values():
      if aDRV.enc_slot == enc_slot:
        if DRV is None:
          DRV = aDRV
        else:
          error("Adapter #%d: DRV_by_enc_slot(%s): multiple enc_slot matches: %s vs %s",
                self.number, enc_slot, DRV.id, aDRV.id)
    return DRV

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

  def __init__(self, **kw):
    self.enclosure_device_id = None
    self.device_id = None
    self.slot_number = None
    self.firmware_state = "UNKNOWN"
    self.ibm_fru_cru = None
    self.raw_size = None
    self.raw_size_units = None
    O.__init__(self, **kw)

  @property
  def id(self):
    ''' Unique identifier for drive, regrettably not what the megacli
        wants to use.
    '''
    return "enc%s.devid%s" % (self.enclosure_device_id, self.device_id)

  @property
  def enc_slot(self):
    ''' Identifier used by megacli, regretably not unique if enclosure
        misconfigure/misinstalled.
    '''
    return "%s:%s" % (self.enclosure_device_id, self.slot_number)
  @property
  def fru(self):
    return self.ibm_fru_cru
class Disk_Port(O):
  pass

def cmd_append(msg, *a):
  global cmd, cmd_old
  if msg:
    msg = msg % a
  cmd_old.append(cmd)
  cmd += ": " + msg

def cmd_pop():
  global cmd, cmd_old
  cmd = cmd_old.pop()

def merge_attrs(o, **kw):
  for attr, value in kw.items():
    if not len(attr) or not attr[0].isalpha():
      D(".%s: ignoring, does not start with a letter", attr)
      continue
    try:
      ovalue = getattr(o, attr)
    except AttributeError:
      # new attribute
      setattr(o, attr, value)
    else:
      if ovalue != value:
        D("%s: %s: %r => %r", o, attr, ovalue, value)

def D(msg, *a):
  message(msg, sys.stderr, "debug", *a)

def warning(msg, *a):
  message(msg, sys.stderr, "warning", *a)

def error(msg, *a):
  message(msg, sys.stderr, "error", *a)

def message(msg, fp, prefix, *a):
  global cmd
  if a:
    msg = msg % a
  print >>fp, cmd+":", prefix+":", msg

if __name__ == '__main__':
  sys.exit(main(sys.argv, debug=None))

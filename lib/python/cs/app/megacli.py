#!/usr/bin/env python3
#
# Convenience routines to access MegaRAID adapters via the megacli
# command line tool.
# - Cameron Simpson <cs@cskk.id.au> 29jan2013
#

r'''
Command line tool to inspect and manipulate LSI MegaRAID adapters,
such as used in IBM ServeRAID systems and Dell PowerEdge RAID Controller (PERC).

Many IBM xSeries servers come with LSI Logic MegaRAID RAID controllers,
under the name IBM ServerRAID.
These controllers are also used by Dell as Dell PowerEdge RAID Controller (PERC).

These can be accessed during the machine boot process via the BIOS screens
using a conventional BIOS-like text interface or a ghastly and painful to use
GUI interface. However, either of these requires the machine OS to be down.

The RAID adapters can also be accessed while the machine OS is up.
For Linux, IBM offer a set of command line tools named MegaCLI_,
which are installed in `/opt/MegaRAID`.
Unfortunately, their MegaCLI executable is both fiddly to invoke
and, in its reporting mode, produces a barely human readable report
which is quite hostlie to machine parsing.
I would surmise that someone was told to dump the adapter data in text form,
and did so with an ad hoc report; it is pages long and arduous to inspect by eye.

The situation was sufficiently painful that I wrote this module
which runs a couple of the report modes and parses their output.

Report Mode
-----------

The "report" mode then dumps a short summary report of relevant information
which can be eyeballed immediately;
RAID configuration and issues are immediately apparent.
Here is an example output
(the "+" tracing lines are on stderr
and recite the underlying MegaCLI commands used):

    # mcli report
    + exec py26+ -m cs.app.megacli report
    + exec /opt/MegaRAID/MegaCli/MegaCli64 -CfgDsply -aAll
    + exec /opt/MegaRAID/MegaCli/MegaCli64 -PDlist -aAll
    Adapter 0 IBM ServeRAID-MR10i SAS/SATA Controller serial# Pnnnnnnnnn
      Virtual Drive 0
        2 drives, size = 278.464GB, raid = Primary-1, Secondary-0, RAID Level Qualifier-0
          physical drive enc252.devid8 [252:0]
          physical drive enc252.devid7 [252:1]
      4 drives:
        enc252.devid7 [252:1]: VD 0, DG None: 42D0628 279.396 GB, Online, Spun Up
        enc252.devid8 [252:0]: VD 0, DG None: 81Y9671 279.396 GB, Online, Spun Up
        enc252.devid2 [252:2]: VD None, DG None: 42D0628 279.396 GB, Unconfigured(good), Spun Up
        enc252.devid3 [252:3]: VD None, DG None: 42D0628 279.396 GB, Unconfigured(good), Spun Up

Status Mode
-----------

The "status" mode recites the RAID status in a series of terse one line summaries;
we use its output in our nagios monitoring.
Here is an example output (the "+" tracing lines are on stderr,
and recite the underlying MegaCLI commands used):

    # mcli status
    + exec py26+ -m cs.app.megacli status
    + exec /opt/MegaRAID/MegaCli/MegaCli64 -CfgDsply -aAll
    + exec /opt/MegaRAID/MegaCli/MegaCli64 -PDlist -aAll
    OK A0

Locate Mode
-----------

The "locate" mode prints a MegaCLI command line
which can be used to activate or deactivate the location LED on a specific drive.
Here is an example output:

    # mcli locate 252:4
    /opt/MegaRAID/MegaCli/MegaCli64 -PdLocate -start -physdrv[252:4] -a0

    # mcli locate 252:4 stop
    /opt/MegaRAID/MegaCli/MegaCli64 -PdLocate -stop -physdrv[252:4] -a0

New_RAID Mode
-------------
The "new_raid" mode prints a MegaCLI command line
which can be used to instruct the adapter to assemble a new RAID set.

MegaCLI class
-------------

The module provides a MegaCLI class which embodies the parsed information
from the MegaCLI reporting modes.
This can be imported and used for special needs.

.. _MegaCLI: http://www-947.ibm.com/support/entry/portal/docdisplay?lndocid=migr-5082327
'''

import os
import re
import sys
from subprocess import call, Popen, PIPE
from types import SimpleNamespace as NS

from cs.x import X
DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts': [
            'mcli = cs.app.megacli:main',
        ],
    },
}

cmd = __file__

USAGE = '''Usage:
    %s locate enc_slot [{start|stop}]
    %s new_raid raid_level enc:devid...
    %s offline enc_slot
    %s report
    %s save save_file
    %s status'''

# default location of MegaCLI executable
MEGACLI = '/opt/MegaRAID/MegaCli/MegaCli64'

mode_CFGDSPLY = 0  # -CfgDsply mode
mode_PDLIST = 1  # -PDlist mode

re_SPEED = re.compile('^(\d+(\.\d+)?)\s*(\S+)$')

def main(argv=None):
  global cmd_old
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
        print("Adapter", An, A.product_name, "serial#", A.serial_no)
        for Vn, V in A.virtual_drives.items():
          print("  Virtual Drive", Vn)
          print(
              "    %s: %d drives, size = %s%s, raid = %s" % (
                  V.state, len(V.physical_disks
                               ), V.size, V.size_units, V.raid_level
              )
          )
          for DRVn, DRV in V.physical_disks.items():
            print(
                "      physical drive %s[%s] %s" %
                (DRV.id, DRV.enc_slot, DRV.firmware_state)
            )
        print("  %d drives:" % (len(A.physical_disks),))
        for DRV in A.physical_disks.values():
          print(
              "    %s [%s]: VD %s, DG %s: %s %s %s, %s" % (
                  DRV.id, DRV.enc_slot,
                  getattr(DRV, 'virtual_drive', NS(number=None)).number,
                  getattr(DRV, 'disk_group', NS(number=None)).number, DRV.fru,
                  DRV.raw_size, DRV.raw_size_units, DRV.firmware_state
              ),
              end=''
          )
          try:
            count = DRV.media_error_count
          except AttributeError:
            pass
          else:
            if count:
              print(", media errors %s" % count, end='')
          try:
            count = DRV.other_error_count
          except AttributeError:
            pass
          else:
            if count:
              print(", other errors %s" % count, end='')
          print()
    elif command == "save":
      save_file, = argv
      if save_raid(save_file) != 0:
        xit = 1
    elif command == "locate":
      enc_slot = argv.pop(0)
      if argv:
        do_start = argv.pop(0)
        if do_start == "start":
          do_start = True
        elif do_start == "stop":
          do_start = False
        else:
          warning("locate: bad start/stop setting: %r", do_start)
          badopts = True
        if argv:
          warning(
              "locate: extra arguments after start/stop: %s", ' '.join(argv)
          )
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
      for An in M.adapters:
        adapter_errs = []
        A = M.adapters[An]
        for DRV in A.physical_disks.values():
          firmware_state = getattr(DRV, 'firmware_state', 'UNKNOWN')
          if firmware_state not in ("Online, Spun Up",
                                    "Unconfigured(good), Spun Up"):
            adapter_errs.append(
                "drive:%s[%s]/VD:%s/%s" % (
                    DRV.id, DRV.enc_slot,
                    getattr(DRV, 'virtual_drive',
                            NS(number=None)).number, DRV.firmware_state
                )
            )
        if adapter_errs:
          print("FAIL A%d %s" % (An, ",".join(adapter_errs)))
        else:
          print("OK A%d" % (An,))
    else:
      error("unsupported command")
      xit = 1

  if badopts:
    print(usage, file=sys.stderr)
    return 2

  return xit

class MegaRAID(NS):

  FW_STATES_AVAILABLE = ('Unconfigured(good), Spun Up', 'Online, Spun Up')

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
    Mconfigured = self._parse(
        self.readcmd('-CfgDsply', '-aAll'), mode_CFGDSPLY
    )
    ##Mconfigured = self._parse(open('CfgDsply.txt'), mode_CFGDSPLY)
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
    Mphysical = self._parse(self.readcmd('-PDlist', '-aAll'), mode_PDLIST)
    ##Mphysical = self._parse(open('PDList.txt'), mode_PDLIST)
    for A in Mphysical.adapters.values():
      disks = Mconfigured.adapters[A.number].physical_disks
      for DRVid, DRV in A.physical_disks.items():
        cmd_append(str(DRVid))
        if DRVid in disks:
          ##X("%s: merge PDlist DRV with Mconfigured DRV", DRVid)
          merge_attrs(disks[DRVid], **DRV.__dict__)
        else:
          ##X("%s: add new DRV to Mconfigured", DRVid)
          disks[DRVid] = DRV
        cmd_pop()
      X("Mphysical merged")
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
    cmd_append("megacli " + " ".join(megacli_args))
    M = NS(adapters={})
    A = None
    V = None
    SP = None
    DG = None
    DRV = None
    o = None
    for mlineno, line in enumerate(self.readcmd(*megacli_args), 1):
      if not line.endswith('\n'):
        raise ValueError("%d: missing newline" % (mlineno,))
      line = line.rstrip()
      if not line:
        continue
      if line.startswith('======='):
        continue
      if (line == 'Virtual Drive Information:'
          or line == 'Physical Disk Information:'):
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
      elif ' :' in line:
        heading, info = line.split(' :', 1)
      elif line.endswith(':'):
        heading = line[:-1]
        info = ''
      elif ':' in line:
        heading, info = line.split(':', 1)
      else:
        warning("unparsed line: %s", line)
        continue
      heading = heading.rstrip()
      info = info.lstrip()
      attr = heading.lower().replace(' ', '_').replace('.', '').replace(
          "'", ''
      ).replace('/', '_')
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
          A = M.adapters[info] = Adapter(
              number=An, disk_groups={}, physical_disks={}, virtual_drives={}
          )
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
          V = A.virtual_drives[Vn] = Virtual_Drive(
              adapter=A, number=Vn, physical_disks={}
          )
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
        setattr(o, attr + '_units', size_units)
        info = float(size)
      elif attr in ('raw_size', 'non_coerced_size', 'coerced_size'):
        size, size_units, sectors = info.split(None, 2)
        setattr(o, attr + '_units', size_units)
        if sectors.startswith('[0x') and sectors.endswith(' Sectors]'):
          setattr(o, attr + '_sectors', int(sectors[3:-9], 16))
        else:
          warning("invalid sectors: %s", sectors)
        info = float(size)
      elif attr.endswith('_speed'):
        m = re_SPEED.match(info)
        if m:
          speed, speed_units = m.group(1), m.group(3)
          setattr(o, attr + '_units', speed_units)
          info = float(speed)
        elif info != "Unknown":
          warning("failed to match re_SPEED against: %s", info)
      elif attr in ('default_cache_policy', 'current_cache_policy'):
        info = info.split(', ')
      elif attr == 'drives_postion':
        DPOS = NS()
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
          X("PDLIST: note physical drive %s", DRVid)
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
    return self.docmd(
        '-PdLocate',
        start_opt,
        '-physdrv[%s]' % (enc_slot,),
        '-a%d' % (adapter,),
    )

  def offline(self, adapter, enc_slot):
    ''' Take a drive offline (==> failed).
    '''
    return self.docmd(
        '-PDOffline',
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
          if DRV.firmware_state not in self.FW_STATES_AVAILABLE:
            error(
                "rejecting drive, firmware state (%s) not unconfigured good: %r",
                DRV.firmware_state,
                self.FW_STATES_AVAILABLE,
            )
            ok = False
          else:
            X("acceptable drive: %s", DRV.firmware_state)
        cmd_pop()
    cmd_pop()
    if not ok:
      return False
    return self.docmd(
        '-CfgLdAdd', '-r%d' % (level,), "[" + ",".join(enc_slots) + "]",
        '-a%d' % (adapter,)
    )

  def readcmd(self, *args):
    ''' Open a pipe from the megacli command and yield lines from its output.
    '''
    cmdargs = [self.megacli] + list(args)
    X("+ %r", cmdargs)
    P = Popen(cmdargs, stdout=PIPE, close_fds=True, encoding='ascii')
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
    print("#", quotecmd(cmdargs))
    ## return call(cmdargs) == 0
    return True

  def adapter_save(self, adapter, save_file):
    savepath = "%s-a%d" % (save_file, adapter)
    if os.path.exists(save_file):
      error("file exists: %s", savepath)
      return False
    return self.docmd('-CfgSave', '-f', save_file, '-a%d' % (adapter,))

class Adapter(NS):

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
          error(
              "Adapter #%d: DRV_by_enc_slot(%s): multiple enc_slot matches: %s vs %s",
              self.number, enc_slot, DRV.id, aDRV.id
          )
    return DRV

class Virtual_Drive(NS):

  def __init__(self, **kw):
    NS.__init__(self, **kw)

class Disk_Group(NS):

  def __init__(self, **kw):
    NS.__init__(self, **kw)

class Span(NS):
  pass

class Physical_Disk(NS):

  def __init__(self, **kw):
    self.enclosure_device_id = None
    self.device_id = None
    self.slot_number = None
    self.firmware_state = "UNKNOWN"
    self.ibm_fru_cru = None
    self.raw_size = None
    self.raw_size_units = None
    NS.__init__(self, **kw)

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

class Disk_Port(NS):
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
      X(".%s: ignoring, does not start with a letter", attr)
      continue
    try:
      ovalue = getattr(o, attr)
    except AttributeError:
      # new attribute
      setattr(o, attr, value)
    else:
      if ovalue != value:
        X("%s: %s: %r => %r", o, attr, ovalue, value)

debug = os.environ.get('DEBUG')
if debug:

  def D(msg, *a):
    message(msg, sys.stderr, "debug", *a)
else:

  def D(msg, *a):
    pass

def warning(msg, *a):
  message(msg, sys.stderr, "warning", *a)

def error(msg, *a):
  message(msg, sys.stderr, "error", *a)

def message(msg, fp, prefix, *a):
  global cmd
  if a:
    msg = msg % a
  print(cmd + ":", prefix + ":", msg, file=fp)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

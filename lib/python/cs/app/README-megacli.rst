MegaCLI access kit.
===================

Many IBM xSeries servers come with LSI Logic MegaRAID RAID controllers, under the name IBM ServerRAID. These controllers are also used by Dell as Dell PowerEdge RAID Controller (PERC).

These can be accessed during the machine boot process via the BIOS screens using a conventional BIOS-like text interface or a ghastly and painful to use GUI interface. However, either of these requires the machine OS to be down.

The RAID adapters can also be accessed while the machine OS is up.
For Linux, IBM offer a set of command line tools named MegaCLI_, which are installed in /opt/MegaRAID.
Unfortunately, their MegaCLI executable is both fiddly to invoke and in its reporting mode, produces a barely human readable report which is quite hostlie to machine parsing.
I would surmise that someone was told to dump the adapter data in text form, and did so with an ad hoc report; it is pages long and arduous to inspect by eye.

The situation was sufficiently painful that I wrote this module which runs a couple of the report modes and parses their output. It is deliberately python 2.4 compatible so that it can run on RHEL 5 systems.

Report Mode
-----------

The "report" mode then dumps a short summary report of relevant information which can be eyeballed immediately; RAID configuration and issues are immediately apparent. Here is an example output (the "+" tracing lines are on stderr, and recite the underlying MegaCLI commands used)::

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

The "status" mode recites the RAID status in a series of terse one line summaries; we use its output in our nagios monitoring. Here is an example output (the "+" tracing lines are on stderr, and recite the underlying MegaCLI commands used)::

  # mcli status
  + exec py26+ -m cs.app.megacli status
  + exec /opt/MegaRAID/MegaCli/MegaCli64 -CfgDsply -aAll
  + exec /opt/MegaRAID/MegaCli/MegaCli64 -PDlist -aAll
  OK A0

Locate Mode
-----------

The "locate" mode prints a MegaCLI command line which can be used to activate or deactivate the location LED on a specific drive. Here is an example output::

  # mcli locate 252:4
  /opt/MegaRAID/MegaCli/MegaCli64 -PdLocate -start -physdrv[252:4] -a0

  # mcli locate 252:4 stop
  /opt/MegaRAID/MegaCli/MegaCli64 -PdLocate -stop -physdrv[252:4] -a0

New_RAID Mode
-------------
The "new_raid" mode prints a MegaCLI command line which can be used to instruct the adapter to assemble a new RAID set.

MegaCLI class
-------------

The module provides a MegaCLI class which embodies the parsed information from the MegaCLI reporting modes.
This can be imported and used for special needs.

.. _MegaCLI: http://www-947.ibm.com/support/entry/portal/docdisplay?lndocid=migr-5082327

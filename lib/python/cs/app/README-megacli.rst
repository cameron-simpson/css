MegaCLI access kit.
===================

Many IBM xSeries servers come with LSI Logic MegaRAID RAID controllers, under the name IBM ServerRAID.

These can be accessed during the machine boot process via the BIOS screens using a conventional BIOS-like text interface or a ghastly and painful to use GUI interface. However, either of these requires the machine OS to be down.

The RAID adapters can also be accessed while the machine OS is up. For Linux, IBM offer a set of command line tools named MegaCLI_, which are installed in /opt/MegaRAID. Unfortunately, their MegaCLI executable is both fiddly to invoke and in its reporting mode, produces a barely human readable report which is quite hostlie to machine parsing. I would surmise that someone was told to dump the adapter data in text form, and did so with an ad hoc report; it is pages long and arduous to inspect by eye.

The situation was sufficiently painful that I wrote this module which runs a couple of the report modes and parses their output.

The primary "report" mode then dumps a short summary report of relevant information which can be eyeballed immediately; RAID configuration and issues are immediately apparent.

The secondary "status" mode recites the RAID status in a series of terse one line summaries; we use its output in our nagios monitoring.

The other current mode is "new_raid", which will print a MegaCLI command line which will instruct the adapter to assemble a new RAID set.

:: _MegaCLI: http://www-947.ibm.com/support/entry/portal/docdisplay?lndocid=migr-5082327

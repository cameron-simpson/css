This is my personal kit,
containing thousands of scripts and hundreds of Python modules
and assorted other code.

Some relevant links:
* Documentation:
  [some overview documentation](http://www.cskk.ezoshosting.com/cs/css/),
  [some manual entries](http://www.cskk.ezoshosting.com/cs/man/)
* Bitbucket.org source repository:
  [overview](https://bitbucket.org/cameron_simpson/css/),
  [commit log](https://bitbucket.org/cameron_simpson/css/commits/all)
* PyPI.org:
  [several modules](https://pypi.org/user/cameron.simpson/)
  are published here for reuse.

An autoindex of the scripts is in the file 1INDEX.txt.

Installation:
* Unpack the tarball: http://www.cskk.ezoshosting.com/cs/css/css.tar.gz
  into a suitable directory such as `/opt/css`, which I will assume for
  the sake of example below.
* Configure your environment to use the scripts:
  `. /opt/css/env.sh` 
  That line should be in your .profile or other environment setup
  script.  It's pretty simple; it adds the css components to the
  END of your PATHs (so that no css script name preempts anything you
  already have).

The configuration script mostly does the following:

    PATH=$PATH:/opt/css/bin
    MANPATH=$MANPATH:/opt/css/man
    PYTHONPATH=$PYTHONPATH:/opt/css/lib/python
    CLASSPATH=$CLASSPATH:/opt/css/lib/java/au.com.zip.cs.jar
    PERL5LIB=$PERL5LIB:/opt/css/lib/perl
    export PATH MANPATH PYTHONPATH CLASSPATH PERL5LIB

Several of the scripts expect the following shell environment variables:
* `$HOST`: your machine's short name.
  Defaults to $HOSTNAME without the trailing components.
* `$HOSTNAME`: your machine's fully qualified domain name.
  Defaults to `hostname`.
* `$ARCH`: your architecture, in the form vendor.cpu.os,
  for example `sun.sparc.solaris` or `sgi.mips.irix`.
* `$SYSTEMID`: a name for your machines' administrative domain.
  For example, I use `home` for my home LAN.
* `$USER`: your login name.
* `$SITENAME`: your email domain (eg cskk.id.au for me).
* `$EMAIL`: your email address
  (just _who@where_, no "<>"s);
  normally `$USER@$SITENAME`.
There are some others, but those cover the common stuff.

*Licence*:
You're free to use, modify and redistribute these scripts provided that:
* you leave my code marked as mine and your modifications (if any)
  marked as yours; it's enough to prefix your code with a terse
  comment like: 
  `# change text - reason text - yourname <youremail> - date`
* you make recipients aware that the scripts can be obtained for free from my
  own web page

*Warranty*:
None whatsoever!

These scripts work for me, but any of them may have bugs or be
arbitrarily dependent on my own login environment.
While I try to make most of them usable by others
(and am happy to hear suggestions or bug reports),
they're for my use and may well not meet your needs.

But feel free to hack them to meet your needs.

- Cameron Simpson <cs@cskk.id.au>
http://www.cskk.ezoshosting.com/cs/

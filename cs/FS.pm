#!/usr/bin/perl
#
# "Smart" IO. Gnows about compress, pgp, gzip, etc.
#	- Cameron Simpson <cs@zip.com.au> 29jun96
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::FS;

@cs::FS::_Exts=qw(Z gz pgp);
%cs::FS::_Decode=( Z	=> 'zcat',
	   gz	=> 'gzcat',
	   pgp	=> 'pgp -fd',
	 );
%cs::FS::_Encode=( Z	=> 'compress',
	   gz	=> 'gzip',
	   pgp	=> 'pgp -fe "$USERID"',
	 );

sub _realPath
	{ my($path)=shift;
	  my($dir)=Pathname::dirname($path);
	  my($base)=Pathname::basename($path);
	  my(@entries)=Pathname::dirents($dir);

	  @entries=grep(substr($_,$[,length $path),@entries);
	}

sub from
	{ my($path)=shift;
1;

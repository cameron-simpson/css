#!/usr/bin/perl
#
# Maintain a list of queued items.
#	- Cameron Simpson <cs@zip.com.au> 26dec99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Net::TCP;
## use cs::HTTP;
use cs::Sink;
use cs::RFC822;

package cs::Tk::FetchURL;

sub new
{ my($class,$parent)=@_;
}

1;

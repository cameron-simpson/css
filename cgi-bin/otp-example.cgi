#!/usr/local/bin/perl -w
#
# Require an OTP challenge and if successful set up a cookie
# to be used by the calling CGI.
#	- Cameron Simpson <cs@cskk.id.au> 17dec96
#

use CGI;
use cs::OTP::CGI;

$Q = new CGI;

# bails (via exit) if auth not completed
# were $retstatus set in the new() call we'd get a return;
# then, if $auth were a ref then things are cool, otherwise
# the auth failed or is incomplete
$auth=new cs::OTP::CGI $Q, 'OTPid', '/u/cameron/OTP_Data';

print $Q->header();

print "Got it [", cs::Hier::h2a($auth,0), "\n";

exit 0;

#!/usr/locall/bin/perl5

use HTTPD;
use HTTPD::Proxy;
use Net::TCP;

package main;

$port=(@ARGV ? shift(@ARGV) : 2002);

HTTPD::serve(HTTPD::Proxy,$port,1);

print STDERR "FINISHED\n";
exit 0;

package myHTTPD;

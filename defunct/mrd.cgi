#!/usr/bin/perl

BEGIN { push(@INC,'/u/cameron/etc/pl/cs'); }

use cs::Upd;
use cs::Misc;
use cs::Hier;
use cs::HTML;
use cs::CGI;

$Q=new cs::CGI;

# check authorisation first up
if ($Q->remote_host() ne 'sid.research.canon.com.au')
	{ fail('host unauthorised',
		"You may not use this script from ".$Q->remote_host().".");
	}

print $Q->header();

print $Q->start_html();

($path=$ENV{PATH_INFO}) =~ s:^/+::;
$path =~ m:/*$:;
$suffix=$&;
$path=$`;

($path,$sfx,@p)=normPath($Q->path_info());


print "<PRE>\n";

print "PATH=$path [sfx=$sfx,p=[@p]]\n\n";
for (sort keys %ENV)
	{ print "$_=$ENV{$_}\n";
	}
print "</PRE>\n";

print $Q->end_html(), "\n";

exit 0;

sub fail
	{ my($short)=shift;

	  print $Q->header('text/html',
			"404 $short");
	  print $Q->start_html(),
		join("<BR>\n",@_), "\n";
	  print $Q->end_html(), "\n";
	  exit 0;
	}

sub normPath
	{ my($path)=@_;
	  my($suffix,@path);
	  local($_);

	  $path =~ m:/*$:;
	
	  $suffix=$&;
	  $path=$`;

	  @path=();
	  for (grep(length,split(m:/+:,$path)))
		{ if ($_ eq '.')	{}
		  elsif ($_ eq '..')	{ pop(@path); }
		  else			{ push(@path,$_); }
		}

	  $path=join('/',@path).$suffix;

	  wantarray ? ($path,$suffix,@path) : $path;
	}

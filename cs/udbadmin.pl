#!/usr/bin/perl
#
# UDBadmin - administer the DAP User Database.
#	- Cameron Simpson <cs@zip.com.au> 27mar95
#

%Param =(	YPdir = '/usr/etc/yp/src',
	);

print STDERR "loading modules...\n";
require 'cs/upd.pl';
use DAP::UDB;
use DAP::Phone;
use Hier;

($cmd=$0) =~ s:.*/::;
$xit=0;

%commands=(	'load' => \&cmd_load
	  );

Prompt:
  while(defined($_=&prompt("$cmd> ")))
	{ next Prompt if ! length;

	  @argv=&parse($_);

	  next Prompt if ! @argv;

	  $command=shift @argv;

	  if (! defined $commands{$command})
		{ print STDERR "$cmd: unrecognised command: $command\n";
		  $xit=1;
		  next Prompt;
		}

	  &{$commands{$command}}(@argv);
	}
  continue
	{ &upd'out('');
	}

&upd'out('');

exit $xit;

sub prompt
	{ print @_; &flush(STDOUT);
	  local($_);

	  $_=<STDIN>;

	  return undef if ! defined;

	  chomp;
	  s/^\s+//;
	  s/\s+$//;

	  $_;
	}

sub cmd_load
	{ my(@argv)=@_;
	  my($what);

	  for $what (@argv)
		{ if ($what eq 'phonelist')	{ &loadphone; }
		  elsif ($what eq 'yppasswd')	{ &loadyppasswd; }
		  elsif ($what eq 'ypgroup')	{ &loadypgroup; }
		  else
		  { print STDERR "$cmd: don't know how to load \"$what\"\n";
		    $xit=1;
		  }
		}
	}

sub loadphone
	{ &upd'nl('loading phonelist usings keys from yppasswd');

	  for $login (sort &YP::keys('passwd'))
		{ &upd'out("$login from YP");
		  if (defined ($f=&DAP::UDB::finger($login)))
			{ if (defined ($p=&DAP::Phone::phone($login)))
				{ &upd'out("$login amended from phone");
				  for (Room,Phone,Programme)
					{ if (length $p->{$_})
						{ $f->{$_}=$p->{$_};
						}
					}

				  $f->{Projects}=$p->{Projects};

			  	  &upd'out("$login save");
			  	  &DAP::UDB::poke($f);
				}
			}
		}

	  &upd'out('');
	}

sub loadyppasswd
{ &upd'nl('loading phonelist usings keys from yppasswd');

  for $login (sort &YP::keys('passwd'))
  { &upd'out("$login from YP");
    my($pw);
    if (defined ($pw=&YP::map('passwd',$login)))
    { my($login,$crypt,$uid,$gid,$gecos,$home,$shell)
	    =split(/:/,$pw);
    }
  }

  &upd'out('');
}

print STDERR("YPpasswd(cameron)=[",
		&DAP::UDB::mkYPmap('passwd','cameron'),"]\n");

$finger=&DAP::UDB::finger('cameron');
print "finger($finger)=", &Hier::h2a($finger), "\n";
for $g ('cameron','gil','alan','damian')
	{ @g=&YP::groups($g);
	  print "groups($g) = [@g]\n";
	}
$finger->{Room}='B34';
&DAP::UDB::poke($finger);
exit 0;

$save=&Hier::h2a(
		{ A => 1,
		  B => [ 1,2,3 ],
		  C => "now is the time"
		});
print "hier=", $save, "\n";

($hier,$tail)=&Hier::a2h($save);

$save2=&Hier::h2a($hier);
print "unparsed($hier)=\"$tail\", hier2=$save2\n";

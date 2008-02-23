#!/usr/bin/perl
#
# Require an OTP challenge and if successful set up a cookie
# to be used by the calling CGI.
#	- Cameron Simpson <cs@zip.com.au> 17dec96
#

use strict qw(vars);

use cs::CGI;
use cs::OTP;
use cs::Hier;
use cs::Source;
use cs::Sink;
use cs::MD5;

package cs::OTP::CGI;

$cs::OTP::CGI::HelpPage='http://web/sysadmin/otp/';

sub new	# CGI -> auth
	{ my($class,$Q,$cid,$file,$force,$retstatus)=@_;
	  $cid='OTPid'				if ! defined $cid;
	  die "getOTPid(): \$file undefined"	if ! defined $file;
	  $force=0				if ! defined $force;
	  $retstatus=0				if ! defined $retstatus;

	  my($cval)=$Q->cookie($cid);

	  my($c);

	  if (! $force && defined $cval && defined ($c=_fetch($cval,$file)))
		{ return bless $c, $class;
		}

	  # force mode or no cookie or the identifier is expired or invalid

	  my($login)=$Q->param('login');
	  my($selfurl)=$Q->param('selfurl');

	  if (defined $selfurl)
		{ # print STDERR "supplied selfurl=[$selfurl]\n";
		}
	  else
	  { # print STDERR "selfurl not set as parameter, caching current one: ";
	  }

	  $selfurl=$Q->self_url() unless defined $selfurl && length $selfurl;

	  # print STDERR "selfurl now = [$selfurl]\n";

	  if (! defined $login)
		# prompt for login name
		{ _needhdr($Q);
		  print $Q->startform(GET,$Q->url()), "\n",
			'Login: ', $Q->textfield('login','',32,32), "\n",
			$Q->hidden('selfurl',$selfurl), "\n",
			$Q->hidden('cid',$cid), "\n",
			$Q->submit('submit','login'), "\n",
			$Q->endform(), "\n";

		  $retstatus ? return 0 : exit 0;
		}

	  my($otp)=new cs::OTP;

	  if (! defined $otp)
		{ _needhdr($Q);
		  print "Sorry, I can't connect to the authentication server.<BR>\n",
			"Possible error: $!<BR>\n",
			"Ask your system administrator to check on it,",
			"then try reloading this page when you think it may work again.<BR>\n";
		  $retstatus ? return 0 : exit 0;
		}

	  my($response)=$Q->param('response');

	 RESPOND:
	  while (1)
	    { if (! defined $response)
		{ my($chal)=scalar $otp->Get($login);

		  if (! defined $chal)
			{ _needhdr($Q);
			  print "Sorry, I couldn't get a correct response to \"GET $login\" from the authentication server.<BR>\n",
				"Possible error: $!<BR>\n",
				"Ask your system administrator to check on it,",
				"then try reloading this page when you think it may work again.<BR>\n";
			  $retstatus ? return 0 : exit 0;
			}

		  # challenge the user with the OTP info
		  print STDERR "chal=[$chal]\n";
		  _needhdr($Q);
		  print $Q->startform(GET,$Q->url()), "\n",
			"Login: <TT>$login</TT><P>\n", "\n",
			$Q->hidden('login',$login), "\n",
			$Q->hidden('selfurl',$selfurl), "\n",
			$Q->hidden('cid',$cid), "\n",
			"Challenge: <TT>$chal</TT><BR>\n";

		  if (length $cs::OTP::CGI::HelpPage)
			{ print "See the <A HREF=$cs::OTP::CGI::HelpPage>OTP Help Page</A> for more info.<BR>\n";
			}

		  print "\n",
			'Response: ', $Q->textfield('response','',64,64), "\n",
			$Q->submit('submit','submit response'), "\n",
			$Q->endform(), "\n";

		  $retstatus ? return 0 : exit 0;
		}

	      if (! $otp->Try($login,$response))
		{ _needhdr($Q);
		  print "Sorry, $login, your response \"$response\" failed.<BR>\n";
		  print "I'm going to have to ask again.\n<P>\n";
		  undef $response;
		  redo RESPOND;
		}

	      # a good response
	      # make a cookie, stash it,
	      # redirect them to the original page

	      my($now)=time;

	      $c={ LOGIN => $login,
		   KEY => cs::MD5::md5string("$$.$.$login.$now"),
		   EXPIRY => $now+3600,	# good for 1 hour
		 };

	      # set up header with the new cookie in it
	      _needhdr($Q,-cookie=>$Q->cookie(-name=>$cid,
					    -value=>$c->{KEY},
					    -expires=>'+24h'));

	      print "Thanks, $login, that looks good.<BR>\n";

	      if (! _save($c,$file))
		{ print "Unfortunately, I couldn't save this authority.<BR>\n",
			"Possible error: $!<BR>\n",
			"I'm going to have to ask again, although responding\n",
			"may not be productive until this is investigated.\n<P>\n";
		  undef $response;
		  redo RESPOND;
		}

	      last RESPOND;
	      print STDERR "NOTREACHED!\n";
	    }

	  print "Please <A HREF=$selfurl>continue</A>.<BR>\n";
#	  print $Q->startform(POST,$Q->url()), "\n",
#			"Login: <TT>$login</TT><P>\n", "\n",
#			$Q->hidden('login',$login), "\n",
#			$Q->hidden('selfurl',$selfurl), "\n",
#			$Q->hidden('cid',$cid), "\n",
#			"Challenge: <TT>$chal</TT><BR>\n";
#
#		  if (length $cs::OTP::CGI::HelpPage)
#			{ print "See the <A HREF=$cs::OTP::CGI::HelpPage>OTP Help Page</A> for more info.<BR>\n";
#			}
#
#		  print "\n",
#			'Response: ', $Q->textfield('response','',64,64), "\n",
#			$Q->submit('submit','submit response'), "\n",
#			$Q->endform(), "\n";

	  $retstatus ? return 0 : exit 0;
	}

$cs::OTP::CGI::_need_header=1;
sub _needhdr
	{ my($Q)=shift;
	  # carp "_needhdr(@_,-expires=>'now')";
	  # warn "here I am";
	  if ($cs::OTP::CGI::_need_header)
		{ print $Q->header(@_,-expires=>'now'),
			$Q->h1("OTP Authorisation Procedure"),
			"\n";
		  $cs::OTP::CGI::_need_header=0;
		  print STDERR "Issuing header:\n", $Q->header(@_), "\n";
		}
	  elsif (@_)
		{ warn "_needhdr(@_) with arguments when header already emitted";
		}
	}

sub _fetch
	{ my($id,$file)=@_;
	  die if ! defined $file;

	  my($c);
	  local($_);

	  my($s)=new cs::Source (PATH,$file);
	  my($now)=time;

	  # take the last valid since new records are appended, not replaced
	  while (defined ($_=$s->GetLine()) && length)
		{ $a=cs::Hier::a2h($_);
		  if (ref $a
		   && defined $a->{KEY}
		   && $a->{KEY} eq $id
		   && defined $a->{EXPIRY}
		   && $a->{EXPIRY} > $now)
			{ $c=$a;
			}
		}

	  $c;
	}

sub _save
	{ my($c,$file)=@_;
	  die if ! defined $file;

	  my($s)=new cs::Sink (APPEND,$file);

	  return undef if ! defined $s;

	  $s->Put(cs::Hier::h2a($c,0),"\n");

	  1;
	}

1;

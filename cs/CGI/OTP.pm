#!/usr/bin/perl
#
#	- Cameron Simpson <cs@zip.com.au> 
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

package cs::cs::CGI::OTP;

@cs::cs::CGI::OTP::ISA=qw();

sub ChkOTP	# ([cookie-id[,db]]) -> (ok,ok ? auth : @html)
{ my($CGI,$otpid,$db)=@_;
  $otpid='OTP' if ! defined $otpid;

  ## warn "ChkOTP(@_)";

  my($C)=$CGI->Cookies();
  my($Q)=$CGI->Query();
  my($oq)=(exists $Q->{ORIGINAL_QUERY}
		? $Q->{ORIGINAL_QUERY}
		: cs::Hier::h2a($Q,0));

  # start off a form for the challenge
  ::need(cs::HTML::Form);
  my($F)=new cs::HTML::Form($CGI->SelfURL(),POST);
  $F->Hidden(ORIGINAL_QUERY,$oq);

  my($otp);

 OTP:
  while (1)
  {
    if (! exists $C->{$otpid}
     || ($otp=$C->{$otpid}) !~ /-/)
	# no cookie
    { if (! exists $Q->{LOGIN})
      { $F->MarkUp("Login name: ");
	$F->TextField(LOGIN,"",16,16);

	return (0,[H1,"OTP Authentication Login Prompt"],"\n",
		  $F->Close(),"\n",
	       );
      }

      my($login)=$Q->{LOGIN};
      my($serv);

      if (! defined ($serv=new cs::OTP))
	    {
	      return (0,[H1,"Can't connect to OTP service"],"\n",
			"Possible error: $!.",[B],"\n",
			"Please reload this page to retry.",[BR],"\n",
		       );
	    }

      if (! exists $Q->{RESPONSE})
	    {
	      my($chal);

	      if (! defined ($chal=$serv->Get($login)))
		    {
		      return (0,[H1,"Error retrieving challenge for \"$login\""],"\n",
				"Possible error: $!.",[B],"\n",
			     );
		    }

	      $F->Hidden(LOGIN,$login);
	      $F->MarkUp("Challenge string:\n",
			 [BLOCKQUOTE,[TT,$chal]],"\n",
			 "Enter this and your passphrase into your OTP\n",
			 "calculator and enter the response below.",[BR],"\n",
			);
	      $F->TextField(RESPONSE,"",32,32);
	      return (0,[H1,"OTP Challenge for \"$login\""],"\n",
			$F->Close(),"\n",
		     );
	    }

      if (! $serv->Try($login,$Q->{RESPONSE}))
	    {
	      return (0,[H1,"Invalid response for \"$login\""],"\n",
			"The response\n",
			[BLOCKQUOTE,[TT,$Q->{RESPONSE}]],"\n",
			"was not accepted by the OTP service.",[BR],"\n",
			"Please back up to the challenge page,\n",
			"reload it (in case the challenge is now out of date),\n",
			"and recompute the response.\n",[P],"\n",
			"Possible problems include\n",
			[UL,
			 [LI,"The challenge is out of date.",[BR],"\n",
			      "This can happen if you were doing another OTP login at the same time\n",
			      "(eg telnetting in).\n",
			 ],"\n",
			 [LI,"You typed your pass-phrase incorrectly."],"\n",
			 [LI,"You have never set up your OTP pass-phrase on ",[TT,"elph"], "."],"\n",
			 [LI,"Your calculator is not computing an MD5 response, but is set to some other method."],"\n",
			],"\n",
		       );
	    }

      # a good response
      # make a cookie, stash it,

      my($now)=time;
      my($c);

      $c={ LOGIN => $login,
	   KEY => cs::MD5::md5string("$$.$.$login.$now"),
	   EXPIRY => $now+3600,	# good for 1 hour
	 };

      # attach to browser
      $CGI->SetCookie($otpid,"$login-$c->{KEY}");

      # attach to database
      $db=_db($db);
      $db->{$login}=cs::Hier::hdup($c);
      undef $db;
      cs::Persist::finish();

      $oq=cs::Hier::a2h($oq);
      return (0,[H1,"OTP Response for \"$login\" Accepted"],"\n",
		"Thanks. This is good for 1 hour.\n",
		"Please ",
		[A,{HREF => $CGI->SelfQuery($oq)},
		   "proceed"],".\n",
	     );
    }

    ($otp =~ /-/) || die "bogus otp [$otp]";
    my($login,$key)=($`,$');
    ## warn "cookie $otpid: login=[$login], key=[$key]";

    ## warn "pre  _db(): db=$db";
    $db=_db($db);
    ## warn "post _db(): db=$db";

    ## warn "login=[$login]";
    ## warn "db=".cs::Hier::h2a($db,0);

    if (! exists $db->{$login}
     || $db->{$login}->{KEY} ne $key
     || $db->{$login}->{EXPIRY} < time)
	# invalid or expired key
	{ $Q->{LOGIN}=$login;
	  delete $C->{$otpid};
	  redo OTP;
	}

    my($auth)=cs::Hier::hdup($db->{$login});
    undef $db;
    cs::Persist::finish();

    return (1,$auth);
  }
}

sub _db
{ my($db)=@_;

  ## warn "_db: db=[$db]";

  $db=(defined $db
	? ref $db
		? $db
		: cs::Persist::db($db,1)
	: cs::Persist::db($::DBotp,1)
      );

  $db;
}

1;

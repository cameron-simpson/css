use cs::CGI;
use cs::Hier;
use cs::Net::TCP;
use cs::HTTPD;
use cs::HTML;
use cs::Pathname;

my($serv)=new cs::HTTPD (2007,{ GET => \&do_get },{});
die "can't attach to 2007: $!" if ! defined $serv;

while (1)
	{ warn "serving ...";
	  $serv->Serve(0,\&doconn);
	}

exit 0;

sub do_get
	{
	  my($httpd,$conn,$uri,$H,$hvers,$state)=@_;

	  my($RH)=new cs::RFC822;

	  if ($uri =~ m|^/file(/+)|)
		{ my($path)="$1$'";
		  my($npath)=cs::Pathname::norm($path);

		  if ($npath ne $path && "$npath/" ne $path)
			{ $RH->Add("Location: /file$npath");
			warn "resolve $path => $npath";
			  $httpd->Respond($conn,
				$cs::HTTP::M_MOVED,
				"resolved . and ..",
				$RH);
			  return 1;
			}

		  my($statpath)=$path;
		  $statpath =~ s|(.+)/+$|$1|;
		  system("ls -ldL $statpath >&2");

		  if (! stat($statpath))
			{ $httpd->Respond($conn,
				$cs::HTTP::E_NOT_FOUND,
				"stat($path): $!");
			  return 1;
			}

		  my($isdir)=-d _;
		  if ($isdir && $uri !~ m|/$|)
			{ $RH->Add("Location: $uri/");
			warn "resolve $uri => $uri/";
			  $httpd->Respond($conn,
				$cs::HTTP::M_MOVED,
				"you need a slash for a dir",
				$RH);
			  return 1;
			}

		  if (! $isdir && $uri =~ m|/+$|)
			{ $RH->Add("Location: $`");
			warn "resolve $uri => $`";
			  $httpd->Respond($conn,
				$cs::HTTP::M_MOVED,
				"you need no slash for a nondir",
				$RH);
			  return 1;
			}

		  my($s);

		  if ($isdir)
			{
			  $RH->Add("Content-Type: text/html");
			  $httpd->Respond($conn,
				$cs::HTTP::R_OK,
				"directory listing follows",
				$RH);

			  $conn->Put("<TT>$path</TT>:<BR>\n");

			  my(@entries)=cs::Pathname::dirents($path);
			  my($epath);

			  for $e (sort @entries)
				{ $epath="$path$e";
				  if (! stat($epath) || ! -d _)
					{
					  $conn->Put("<TT><A HREF=\"/file$epath\">$e</A></TT><BR>\n");
					}
				  else
					{
					  $conn->Put("<TT><A HREF=\"/file$epath/\">$e/</A></TT><BR>\n");
					}
				}
			}
		  elsif (! defined ($s=new cs::Source (PATH,$path)))
			{
			  $httpd->Respond($conn,
				$cs::HTTP::E_FORBIDDEN,
				"can't open, possible error: $!");
			  return 1;
			}
		  else
			{ my($type);

			  if ($path =~ /\.html?$/i)	{ $type="text/html"; }
			  elsif ($path =~ /\.txt?$/i)	{ $type="text/plain"; }
			  elsif ($path =~ /\.gif$/i)	{ $type="image/gif"; }
			  elsif ($path =~ /\.jpe?g?$/i)	{ $type="image/jpeg"; }
			  else				{ $type="text/plain"; }

			  $RH->Add("Content-Type: $type");

			  $httpd->Respond($conn,
				$cs::HTTP::R_OK,
				"data follows",
				$RH);

			  local($_);

			  while (defined ($_=$s->Read()) && length)
				{ $conn->Put($_);
				}

			  undef $s;
			}
		}
	  else
	  {
	    $RH->Add("Content-Type: text/html");

	    $httpd->Respond($conn,$cs::HTTP::R_OK,"GET [$uri] Accepted",$RH);
	    $conn->Put("URI = $uri<BR>\n");
	  }

	  1;
	}

sub doconn
	{ my($conn)=@_;
	  my($rq);

	  if (! defined ($rq=$conn->GetLine()) || ! length $rq)
		{ warn "nothing from ".cs::Hier::h2a($conn,1);
		  return undef;
		}

	  warn "rq=[$rq]";
	  my($H)=new cs::RFC822 $conn;
	  warn "hdrs=".cs::Hier::h2a($H,1);

	  $conn->Put("Content-Type: text/plain\n\nHi there.\n");
	}

# my($Q)=new cs::CGI;
# 
# my(@html)=$Q->MkForm({ F1 => [ 'field 1', KEYWORDS, { A => 'type A',
# 						      B => 'type B',
# 						    }, 0 ],
# 		       F2 => [ 'field 2', TEXTFIELD ],
# 		     },
# 		     { F1 => A,
# 		       F2 => 'some text',
# 		     }
# 		    );
# 
# print "<PRE>\n";
# print "html=\n".cs::Hier::h2a(\@html,1)."\n";
# print "</PRE>\n";
# print cs::CGI::tok2a(@html);

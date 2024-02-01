#!/usr/bin/env python3

''' Utilities and command line for working with EBooks.
    Basic support for talking to Apple Books, Calibre, CBZ, Kindle, Kobo, Mobi, PDF.
    These form the basis of my personal Kindle/Kobo/Calibre workflow.

    The command `python -m cs.ebooks help -l` gives the basic usage information:

      help:
        apple subcommand [...]
            Subcommands:
              dbshell
                Start an interactive database shell.
              help [-l] [subcommand-names...]
                Print the full help for the named subcommands,
                or for all subcommands if no names are specified.
                -l  Long help even if no subcommand-names provided.
              ls
                List books in the library.
              md
                List metadata.
              shell
                Run a command prompt via cmd.Cmd using this command's subcommands.
        calibre [-C calibre_library] [-K kindle-library-path] subcommand [...]
            -C calibre_library
              Specify calibre library location.
            -K kindle_library
              Specify kindle library location.
            -O other_calibre_library
              Specify alternate calibre library location, the default library
              for pull etc. The default comes from $CALIBRE_LIBRARY_OTHER.
            Subcommands:
              add [-nqv] bookpaths...
                Add the specified ebook bookpaths to the library.
                --cbz Also make a CBZ.
                -n    No action: recite planned actions.
                -q    Quiet: only emit warnings.
                -v    Verbose: report all actions and decisions.
              convert [-fnqv] formatkey dbids...
                Convert books to the format `formatkey`.
                -f    Force: convert even if the format is already present.
                -n    No action: recite planned actions.
                -q    Quiet: only emit warnings.
                -v    Verbose: report all actions and decisions.
              dbshell
                Start an interactive database prompt.
              help [-l] [subcommand-names...]
                Print the full help for the named subcommands,
                or for all subcommands if no names are specified.
                -l  Long help even if no subcommand-names provided.
              info
                Report basic information.
              linkto [-1fnqv] [-d linkto-dir] [-F fmt,...] [-o link-format] [dbids...]
                Export books to linkto-dir by hard linking.
                -1              Link only the first format found.
                -d linkto-dir   Specify the target directory, default from $MEDIA_BOOKSPATH
                                or ~/media/books.
                -F fmt,...      Source formats, default: ['CBZ', 'EPUB']
                -f              Force. Replace existing links.
                -n              No action. Report planned actions.
                -o link-format  Link name format.
                -q              Quiet.
                -v              Verbose.
              ls [-l] [-o ls-format] [book_specs...]
                List the contents of the Calibre library.
                -l            Long mode, listing book details over several lines.
                -o ls_format  Output format for use in a single line book listing.
                -r            Reverse the listing order.
                -t            Order listing by timestamp.
              make_cbz book_specs...
                Add the CBZ format to the designated Calibre books.
              prefs
                List the library preferences.
              pull [-fnqv] [/path/to/other-library] [identifiers...]
                Import formats from another Calibre library.
                -f    Force. Overwrite existing formats with formats from other-library.
                -n    No action: recite planned actions.
                -q    Quiet. Only issue warnings and errors.
                -v    Verbose. Print more information.
                /path/to/other-library: optional path to another Calibre library tree
                identifier-name: the key on which to link matching books;
                  the default is mobi-asin
                  If the identifier '?' is specified the available
                  identifiers in use in other-library are listed.
                identifier-values: specific book identifiers to import
                  If no identifiers are provided, all books which have
                  the specified identifier will be pulled.
              shell
                Run an interactive Python prompt with some predefined names:
                calibre: the CalibreTree
                options: self.options
              tag [-n] [--] [-]tag[,tag...] book_specs...
        dedrm [-D dedrm_package_path] subcommand [args...]
              -D  Specify the filesystem path to the DeDRM/noDRM plugin top level.
                  For example, if you had a checkout of git@github.com:noDRM/DeDRM_tools.git
                  at /path/to/DeDRM_tools--noDRM you could supply:
                  -D /path/to/DeDRM_tools--noDRM/DeDRM_plugin
                  or place that value in the $DEDRM_PACKAGE_PATH environment variable.
            Subcommands:
              help [-l] [subcommand-names...]
                Print the full help for the named subcommands,
                or for all subcommands if no names are specified.
                -l  Long help even if no subcommand-names provided.
              import module_name...
                Exercise the DeDRM python import mechanism for each module_name.
              kindlekeys [import]
                import    Read a JSON list of key dicts and update the cached keys.
              remove filenames...
                Remove DRM from the specified filenames.
              shell
                Run a command prompt via cmd.Cmd using this command's subcommands.
        help [-l] [subcommand-names...]
          Print the full help for the named subcommands,
          or for all subcommands if no names are specified.
          -l  Long help even if no subcommand-names provided.
        kindle [-C calibre_library] [-K kindle-library-path] [subcommand [...]]
            -C calibre_library
              Specify calibre library location.
            -K kindle_library
              Specify kindle library location.
            Subcommands:
              app_path [content-path]
                Report or set the content path for the Kindle application.
              dbshell
                Start an interactive database prompt.
              export [-fnqv] [ASINs...]
                Export AZW files to Calibre library.
                -f    Force: replace the AZW3 format if already present.
                -n    No action, recite planned actions.
                -q    Quiet: report only warnings.
                -v    Verbose: report more information about actions and inaction.
                ASINs Optional ASIN identifiers to export.
                      The default is to export all books with no "calibre.dbid" fstag.
              help [-l] [subcommand-names...]
                Print the full help for the named subcommands,
                or for all subcommands if no names are specified.
                -l  Long help even if no subcommand-names provided.
              import_tags [-nqv] [ASINs...]
                Import Calibre book information into the fstags for a Kindle book.
                This will support doing searches based on stuff like
                titles which are, naturally, not presented in the Kindle
                metadata db.
              info
                Report basic information.
              ls [-l]
                List the contents of the library.
                -l  Long mode.
              shell
                Run a command prompt via cmd.Cmd using this command's subcommands.
        kobo subcommand [...]
            Subcommands:
              export [-fnqv] [volumeids...]
                Export Kobo books to Calibre library.
                -f    Force: replace the EPUB format if already present.
                -n    No action, recite planned actions.
                -q    Quiet: report only warnings.
                -v    Verbose: report more information about actions and inaction.
                volumeids
                      Optional Kobo volumeid identifiers to export.
                      The default is to export all books.
                      (TODO: just those with no "calibre.dbid" fstag.)
              help [-l] [subcommand-names...]
                Print the full help for the named subcommands,
                or for all subcommands if no names are specified.
                -l  Long help even if no subcommand-names provided.
              info
                Report basic information.
              ls [volumeids...]
                List the contents of the library.
                (TODO: -l  Long mode.)
                volumeids
                      Optional Kobo volumeid identifiers to list.
              shell
                Run a command prompt via cmd.Cmd using this command's subcommands.
        mobi subcommand [...]
            Subcommands:
              extract mobipath [outdir]
                Extract the contents of the MOBI file mobipath
                into the directory outdir, default based on the mobipath basename.
                Prints the outdir and the name of the top file.
              help [-l] [subcommand-names...]
                Print the full help for the named subcommands,
                or for all subcommands if no names are specified.
                -l  Long help even if no subcommand-names provided.
              make_cbz mobipath [cbzpath]
                Unpack a MOBI file and construct a CBZ file.
                Prints the path of the CBZ file to the output.
                The default cbzpath is mobibase.cbz where mobibase is the
                basename of mobipath with its extension removed.
              shell
                Run a command prompt via cmd.Cmd using this command's subcommands.
        pdf subcommand [...]
            Subcommands:
              extract_images pdf-files...
                Extract the images from the named page files.
              help [-l] [subcommand-names...]
                Print the full help for the named subcommands,
                or for all subcommands if no names are specified.
                -l  Long help even if no subcommand-names provided.
              make_cbz pdf-files...
                Extract the images from the named page files.
              scan pdf-files...
                Scan the PDF-data in pdf-files and report.
              shell
                Run a command prompt via cmd.Cmd using this command's subcommands.
              xi pdf-files...
                Extract the images from the named page files.
        shell
          Run a command prompt via cmd.Cmd using this command's subcommands.

'''

__version__ = '20240201.4'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.app.osx.defaults',
        'cs.app.osx.plist',
        'cs.binary',
        'cs.buffer',
        'cs.cmdutils',
        'cs.context',
        'cs.debug',
        'cs.deco',
        'cs.fileutils',
        'cs.fs',
        'cs.fstags',
        'cs.gimmicks',
        'cs.lex',
        'cs.logutils',
        'cs.numeric',
        'cs.obj',
        'cs.pfx',
        'cs.progress',
        'cs.psutils',
        'cs.queues',
        'cs.resources',
        'cs.sqlalchemy_utils',
        'cs.sqltags',
        'cs.tagset',
        'cs.threads',
        'cs.units',
        'cs.upd',
        'cs.x',
        'icontract',
        'mobi',
        'pillow',
        'pycryptodomex',
        'sqlalchemy',
        'typeguard',
    ],
    'entry_points': {
        'console_scripts': {
            'ebooks': 'cs.ebooks.__main__:main'
        },
    },
}

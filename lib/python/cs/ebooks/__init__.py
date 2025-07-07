#!/usr/bin/env python3

''' Utilities and command line for working with EBooks.
    Basic support for talking to Apple Books, Calibre, CBZ, Kindle, Kobo, Mobi, PDF.
    These form the basis of my personal Kindle/Kobo/Calibre workflow.

    The command `python -m cs.ebooks help -r` gives the basic usage information:

        Longer help with the -l option.
        Usage: ebooks [common-options...] subcommand [options...]
          Ebooks utility command.
          Subcommands:
            apple    Command line access to Apple Books.
              dbshell  Start an interactive database shell.
              ls       List books in the library.
              md       List metadata.
            calibre  Operate on a Calibre library.
              add       Add the specified ebook bookpaths to the library.
              convert   Convert books to the format `formatkey`.
              dbshell   Start an interactive database prompt.
              decrypt   Remove DRM from the specified books.
              linkto    Export books to linkto-dir by hard linking.
              ls        List the contents of the Calibre library.
              make-cbz  Add the CBZ format to the designated Calibre books.
              prefs     List the library preferences.
              pull      Import formats from another Calibre library.
              tag       Modify the tags of the specified books.
            dedrm    Operations using the DeDRM/NoDRM package.
              decrypt     Remove DRM from the specified filenames.
              import      Exercise the DeDRM python import mechanism for each module_name.
              kindlekeys  Dump, print or import the Kindle DRM keys.
            help     Print help for subcommands.
            info     Info subcommand.
            kindle   Operate on a Kindle library.
              app-path     Report or set the content path for the Kindle application.
              dbshell      Start an interactive database prompt.
              export       Export AZW files to Calibre library.
              import-tags  Import Calibre book information into the fstags for a Kindle book.
              keys         Shortcut to "dedrm kindlekeys".
              ls           List the contents of the library.
            kobo     Command line for interacting with a Kobo Desktop filesystem tree.
              export  Export Kobo books to Calibre library.
              ls      List the contents of the library.
            mobi     Command line implementation for `mobi2cbz`.
              extract   Extract the contents of the MOBI file mobipath.
              make-cbz  Unpack a MOBI file and construct a CBZ file.
              toc       List the contents of the MOBI file mobipath.
            pdf      Command line tool for doing things with PDF files.
              extract-images  Extract the images from the named page files.
              make-cbz        Extract the images from the named page files.
              mmap            Decode a PDF document using mmap_pdf.
              scan            Scan the PDF-data in pdf-files and report.
              xi              Extract the images from the named page files.
            shell    Run a command prompt via cmd.Cmd using this command's subcommands.

'''

__version__ = '20241007-post'

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

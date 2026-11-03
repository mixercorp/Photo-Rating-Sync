import sys

if len(sys.argv) > 1:
    from ratingsync.cli import main
else:
    from ratingsync.gui import main

main()

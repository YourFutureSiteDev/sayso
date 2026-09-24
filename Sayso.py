"""Entry script for the packaged app.

PyInstaller runs its entry file as a top-level script, not as part of a
package, so pointing it straight at `sayso/__main__.py` breaks every
`from .config import ...` in the tree. This imports the package properly
first, which is all the build needs.
"""

from sayso.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

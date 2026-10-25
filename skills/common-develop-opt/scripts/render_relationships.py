"""Public facade for document_core relationship rendering."""
from document_core.render_relationships import *
from document_core.render_relationships import main, parser
build_parser = parser
if __name__ == "__main__":
    raise SystemExit(main())

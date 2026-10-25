from __future__ import annotations

import copy
import unittest
from pathlib import Path

from document_core import render_relationships


class RenderRelationshipsMigrationTests(unittest.TestCase):
    def test_new_layer_renders_dependency_impact(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "structured-docs" / "dependency_impact.yaml"
        document = copy.deepcopy(render_relationships.simple_yaml.load(fixture))
        output = render_relationships.render(document)
        self.assertIn("flowchart LR", output)
        self.assertIn("sequenceDiagram", output)


if __name__ == "__main__":
    unittest.main()

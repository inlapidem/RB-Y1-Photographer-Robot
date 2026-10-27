import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MESH_DIR = ROOT / "ros" / "geekseek_fake_robot" / "meshes"
URDF = ROOT / "ros" / "geekseek_fake_robot" / "urdf" / "geekseek_fake_robot.urdf.xacro"


class CadModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((MESH_DIR / "manifest.json").read_text())

    def test_articulated_components_form_one_partition(self) -> None:
        component_indexes = [
            index
            for link in self.manifest["links"].values()
            for index in link["component_indexes"]
        ]
        self.assertEqual(sorted(component_indexes), list(range(1, 36)))
        self.assertEqual(len(component_indexes), len(set(component_indexes)))

    def test_all_manifest_meshes_are_packaged_assets(self) -> None:
        mesh_names = [link["stl"] for link in self.manifest["links"].values()]
        mesh_names += [
            mesh["stl"] for mesh in self.manifest["static_meshes"].values()
        ]
        urdf = URDF.read_text()
        for mesh_name in mesh_names:
            mesh_path = MESH_DIR / mesh_name
            self.assertTrue(mesh_path.is_file(), mesh_name)
            self.assertGreater(mesh_path.stat().st_size, 1024, mesh_name)
            self.assertTrue(
                f"meshes/{mesh_name}" in urdf or f'mesh="{mesh_name}"' in urdf,
                mesh_name,
            )

    def test_kiosk_sources_exclude_articulated_subassembly(self) -> None:
        static_indexes = [
            index
            for mesh in self.manifest["static_meshes"].values()
            for index in mesh["component_indexes"]
        ]
        self.assertEqual(sorted(static_indexes), [1, 2, 3, 5])
        static_names = " ".join(
            name
            for mesh in self.manifest["static_meshes"].values()
            for name in mesh["component_names"]
        )
        self.assertNotIn("joints v54", static_names)


if __name__ == "__main__":
    unittest.main()

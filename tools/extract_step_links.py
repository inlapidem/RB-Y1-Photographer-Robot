#!/usr/bin/env python3
"""Extract articulated robot link meshes from the Assemble_CAM STEP assembly.

This is a build-time tool. Generated STL files are committed so the runtime and
ROS package do not depend on CadQuery/OCP.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from OCP.BRep import BRep_Builder
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Bnd import Bnd_Box
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.StlAPI import StlAPI_Writer
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Compound
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.gp import gp_Trsf, gp_Vec


LINK_SPECS = {
    "base_link": {
        "origin_mm": (0.0, 0.0, 0.0),
        # Ground-side motor housing and pedestal stay fixed.
        "components": (4, 11),
        "compound_solid": "base",
    },
    "base_yaw_link": {
        "origin_mm": (0.0, 0.0, 12.0),
        # Yaw output stack plus the fixed side of the shoulder actuator.
        "components": (1, 2, 3, 7, 9, 10, 12, 13),
    },
    "shoulder_link": {
        "origin_mm": (0.0, 0.0, 129.0),
        # Shoulder output holders/bridge plus the fixed side of the elbow.
        "components": (5, 6, 8, 15, 17, 18, 31, 32),
    },
    "elbow_link": {
        "origin_mm": (0.0, 0.0, 277.0),
        # Elbow output holders plus the fixed side of the wrist pitch joint.
        "components": (14, 16, 21, 23, 24, 33),
    },
    "wrist_pitch_link": {
        "origin_mm": (0.0, 0.0, 385.0),
        # Wrist output frame plus the fixed side of the tool-yaw actuator.
        "components": (19, 20, 22, 27, 29, 30),
    },
    "end_effector_link": {
        "origin_mm": (-108.0, 0.0, 364.5),
        # Tool-yaw output holders, iPad frame mounts, and the display plate.
        "components": (25, 26, 28, 34, 35),
        "compound_solid": "end_effector",
    },
}

STATIC_MESH_SPECS = {
    "kiosk_body": {
        "components": (1, 5),
        "expected_prefixes": ("UI_base v3", "=>"),
    },
    "ipad": {
        "components": (2,),
        "expected_prefixes": ("iPad mini 7th Gen",),
    },
    "kiosk_camera": {
        "components": (3,),
        "expected_prefixes": ("Logitech c270 Assembly",),
    },
}


def label_name(label: TDF_Label) -> str:
    attribute = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return attribute.Get().ToExtString()
    return ""


def referred_label(shape_tool, component: TDF_Label) -> TDF_Label:
    referred = TDF_Label()
    if shape_tool.IsReference_s(component):
        shape_tool.GetReferredShape_s(component, referred)
    return referred


def find_component(shape_tool, assembly: TDF_Label, prefix: str) -> TDF_Label:
    components = TDF_LabelSequence()
    shape_tool.GetComponents_s(assembly, components, False)
    for index in range(1, components.Length() + 1):
        component = components.Value(index)
        if label_name(component).startswith(prefix):
            return component
    raise RuntimeError(f"component not found: {prefix}")


def read_joints(step_path: Path):
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    if reader.ReadFile(str(step_path)) != IFSelect_RetDone or not reader.Transfer(document):
        raise RuntimeError(f"failed to read STEP assembly: {step_path}")

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    if roots.Length() != 1:
        raise RuntimeError(f"expected one STEP root, got {roots.Length()}")

    root = roots.Value(1)
    root_components = TDF_LabelSequence()
    shape_tool.GetComponents_s(root, root_components, False)
    joints_instance = find_component(shape_tool, root, "joints v54")
    robot_to_root = shape_tool.GetShape_s(joints_instance).Location().Transformation()
    joints_definition = referred_label(shape_tool, joints_instance)
    components = TDF_LabelSequence()
    shape_tool.GetComponents_s(joints_definition, components, False)
    if components.Length() != 36:
        raise RuntimeError(f"expected 36 direct joint components, got {components.Length()}")
    return document, shape_tool, components, root_components, robot_to_root


def split_unnamed_compound(shape_tool, components: TDF_LabelSequence):
    compound = shape_tool.GetShape_s(components.Value(36))
    result = {}
    explorer = TopExp_Explorer(compound, TopAbs_SOLID)
    while explorer.More():
        solid = explorer.Current()
        bounds = Bnd_Box()
        BRepBndLib.Add_s(solid, bounds)
        z_min, z_max = bounds.Get()[2], bounds.Get()[5]
        key = "end_effector" if z_min > 300.0 else "base"
        result[key] = solid
        explorer.Next()
    if set(result) != {"base", "end_effector"}:
        raise RuntimeError("could not split unnamed base/end-effector compound")
    return result


def make_compound(shapes):
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def translated_to_link_frame(shape, origin_mm):
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(*(-value for value in origin_mm)))
    return BRepBuilderAPI_Transform(shape, transform, True).Shape()


def write_stl(shape, path: Path) -> None:
    BRepMesh_IncrementalMesh(shape, 0.6, False, 0.35, True).Perform()
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    if not writer.Write(shape, str(path)):
        raise RuntimeError(f"failed to write {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("step", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    document, shape_tool, components, root_components, robot_to_root = read_joints(
        args.step
    )
    split_solids = split_unnamed_compound(shape_tool, components)
    manifest = {"source": str(args.step), "unit": "millimeter", "links": {}}

    for link_name, spec in LINK_SPECS.items():
        indexes = spec["components"]
        shapes = [shape_tool.GetShape_s(components.Value(index)) for index in indexes]
        if "compound_solid" in spec:
            shapes.append(split_solids[spec["compound_solid"]])
        local_shape = translated_to_link_frame(make_compound(shapes), spec["origin_mm"])
        output_path = args.output / f"{link_name}.stl"
        write_stl(local_shape, output_path)
        manifest["links"][link_name] = {
            "origin_mm": spec["origin_mm"],
            "component_indexes": indexes,
            "component_names": [label_name(components.Value(index)) for index in indexes],
            "stl": output_path.name,
        }
        print(f"wrote {output_path} ({output_path.stat().st_size / 1024:.1f} KiB)")

    manifest["static_meshes"] = {}
    root_to_robot = robot_to_root.Inverted()
    for mesh_name, spec in STATIC_MESH_SPECS.items():
        indexes = spec["components"]
        names = [label_name(root_components.Value(index)) for index in indexes]
        if any(
            not name.startswith(prefix)
            for name, prefix in zip(names, spec["expected_prefixes"])
        ):
            raise RuntimeError(f"unexpected top-level components for {mesh_name}: {names}")
        shapes = [
            BRepBuilderAPI_Transform(
                shape_tool.GetShape_s(root_components.Value(index)), root_to_robot, True
            ).Shape()
            for index in indexes
        ]
        output_path = args.output / f"{mesh_name}.stl"
        write_stl(make_compound(shapes), output_path)
        manifest["static_meshes"][mesh_name] = {
            "component_indexes": indexes,
            "component_names": names,
            "stl": output_path.name,
        }
        print(f"wrote {output_path} ({output_path.stat().st_size / 1024:.1f} KiB)")

    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    del document


if __name__ == "__main__":
    main()

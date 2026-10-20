"""STEP product / solid name reader (pure Python, offline)."""
import json

import pytest

from jmag_bridge import cad3d

# Minimal AP214 file in the layout Open CASCADE writes for a named assembly (as gmsh / build123d /
# CadQuery export it): one assembly product, leaf products linked by NEXT_ASSEMBLY_USAGE_OCCURRENCE,
# each leaf's SHAPE_DEFINITION_REPRESENTATION pointing at a B-rep representation that holds the solid.
# Geometry entities are stubs (#90..#93): the reader never follows them.
NAMED = """ISO-10303-21;
HEADER;
FILE_NAME('x.step','2026-10-19',('a;b'),(''),'','','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
ENDSEC;
DATA;
#3 = SHAPE_DEFINITION_REPRESENTATION(#4,#10);
#4 = PRODUCT_DEFINITION_SHAPE('','',#5);
#5 = PRODUCT_DEFINITION('design','',#6,#9);
#6 = PRODUCT_DEFINITION_FORMATION('','',#7);
#7 = PRODUCT('E_core_pair',
  'E_core_pair','',(#8));
#10 = SHAPE_REPRESENTATION('',(#11),#39);
#45 = SHAPE_DEFINITION_REPRESENTATION(#46,#52);
#46 = PRODUCT_DEFINITION_SHAPE('','',#47);
#47 = PRODUCT_DEFINITION('design','',#48,#51);
#48 = PRODUCT_DEFINITION_FORMATION('','',#49);
#49 = PRODUCT('Core_Top',
  'Core_Top','',(#50));
#52 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#11,#53),#39);
#53 = MANIFOLD_SOLID_BREP('Core_Top',#90);
#60 = SHAPE_DEFINITION_REPRESENTATION(#61,#67);
#61 = PRODUCT_DEFINITION_SHAPE('','',#62);
#62 = PRODUCT_DEFINITION('design','',#63,#51);
#63 = PRODUCT_DEFINITION_FORMATION('','',#64);
#64 = PRODUCT('Coil''s; primary','Coil''s; primary','',(#50));
#67 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#11,#68),#39);
#68 = BREP_WITH_VOIDS('Coil',#91,(#92));
#1001 = ( REPRESENTATION_RELATIONSHIP('','',#52,#10)
REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#1002)
SHAPE_REPRESENTATION_RELATIONSHIP() );
#1004 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('1','','',#5,#47,$);
#1005 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('2','','',#5,#62,$);
ENDSEC;
END-ISO-10303-21;
"""

# gmsh's own STEP writer: default product names, a sheet-only product (free faces of a 360 deg
# revolve), a product with two solids, and the representation linked through a plain
# SHAPE_REPRESENTATION_RELATIONSHIP instead of directly.
UNNAMED = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#3 = SHAPE_DEFINITION_REPRESENTATION(#4,#10);
#4 = PRODUCT_DEFINITION_SHAPE('','',#5);
#5 = PRODUCT_DEFINITION('design','',#6,#9);
#6 = PRODUCT_DEFINITION_FORMATION('','',#7);
#7 = PRODUCT('Open CASCADE STEP translator 7.8 1','Open CASCADE STEP translator 7.8 1','',(#8));
#10 = SHAPE_REPRESENTATION('',(#11),#39);
#12 = SHAPE_REPRESENTATION_RELATIONSHIP('','',#10,#13);
#13 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#14,#15),#39);
#14 = MANIFOLD_SOLID_BREP('',#90);
#15 = MANIFOLD_SOLID_BREP('',#91);
#20 = SHAPE_DEFINITION_REPRESENTATION(#21,#26);
#21 = PRODUCT_DEFINITION_SHAPE('','',#22);
#22 = PRODUCT_DEFINITION('design','',#23,#9);
#23 = PRODUCT_DEFINITION_FORMATION('','',#24);
#24 = PRODUCT('','','',(#8));
#26 = MANIFOLD_SURFACE_SHAPE_REPRESENTATION('',(#11,#27),#39);
#27 = SHELL_BASED_SURFACE_MODEL('',(#92));
ENDSEC;
END-ISO-10303-21;
"""


def test_named_assembly_parts_and_jmag_names():
    s = cad3d.parse_step_text(NAMED)
    assert [p.name for p in s.products] == ["E_core_pair", "Core_Top", "Coil's; primary"]
    leaves = s.leaf_products
    assert [p.name for p in leaves] == ["Core_Top", "Coil's; primary"]
    assert s.part_names == ("E_core_pair/Core_Top", "E_core_pair/Coil's; primary")
    assert [p.solids for p in leaves] == [1, 1]          # BREP_WITH_VOIDS counts as a solid
    assert s.products[0].is_assembly and s.products[0].solids == 0   # complex relationship not followed
    assert s.n_solids == 2 and s.n_sheets == 0
    assert s.solid_names == ("Core_Top", "Coil")
    assert s.warnings == ()


def test_unnamed_sheet_and_multi_solid_products_warn():
    s = cad3d.parse_step_text(UNNAMED)
    assert [p.solids for p in s.products] == [2, 0]      # solids reached through the relationship
    assert [p.sheets for p in s.products] == [0, 1]
    assert s.n_solids == 2 and s.n_sheets == 1
    text = " | ".join(s.warnings)
    assert "no usable name" in text and "Open CASCADE STEP translator" in text
    assert "sheet" in text and "2 solids" in text
    assert len(s.warnings) == 4                           # 2 unnamed, 1 sheet-only, 1 multi-solid


def test_duplicate_leaf_names_warn():
    dup = NAMED.replace("'Coil''s; primary','Coil''s; primary'", "'Core_Top','Core_Top'")
    s = cad3d.parse_step_text(dup)
    assert any("duplicate" in w and "Core_Top" in w for w in s.warnings)


def test_compare_names_against_labels():
    s = cad3d.parse_step_text(NAMED)
    cmp = s.compare_names(["Core_Top", "Core_Bottom"])
    assert cmp == {"missing": ["Core_Bottom"], "extra": ["Coil's; primary"]}
    assert s.compare_names(["Core_Top", "Coil's; primary"]) == {"missing": [], "extra": []}


def test_not_a_step_file_raises():
    with pytest.raises(ValueError, match="DATA section"):
        cad3d.parse_step_text("solid ascii stl\nendsolid\n")


def test_read_file_and_cli(tmp_path, capsys):
    f = tmp_path / "m.step"
    f.write_text(NAMED, encoding="utf-8")
    s = cad3d.read_step_names(f)
    assert s.source == str(f) and len(s.leaf_products) == 2
    with pytest.raises(FileNotFoundError):
        cad3d.read_step_names(tmp_path / "missing.step")
    assert cad3d.main([str(f)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["part_names"] == ["E_core_pair/Core_Top", "E_core_pair/Coil's; primary"]
    assert out["n_solids"] == 2 and out["warnings"] == []
    g = tmp_path / "u.step"
    g.write_text(UNNAMED, encoding="utf-8")
    assert cad3d.main([str(g)]) == 1                      # warnings -> exit 1
    capsys.readouterr()


def test_step_string_unicode_escape():
    assert cad3d._decode_string("'A\\X2\\00B5\\X0\\H'") == "AµH"
    assert cad3d._decode_string("'it''s'") == "it's"

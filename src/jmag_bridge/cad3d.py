"""Read the product and solid names of a STEP file before a 3D import. Pure Python, no JMAG.

JMAG-Designer 23.0 names the parts of an imported STEP assembly ``<assembly>/<product>`` (observed on
named Open CASCADE STEP files with one assembly level, 2026-10-19), so the PRODUCT names in the file
are the part names an agent will rename or select by. This module lists them, counts the solids each
product holds, and warns about what was seen to go wrong:

- products without a usable name (gmsh's own writer leaves ``Open CASCADE STEP translator 7.8 n``);
- sheet-only products (free faces left by a 360 deg revolve export as extra products);
- duplicate leaf names, and products holding more than one solid (check the part count after import).

Usage::

    python -m jmag_bridge.cad3d model.step          # JSON summary; exit 1 if there are warnings

    from jmag_bridge.cad3d import read_step_names
    s = read_step_names("model.step"); s.part_names; s.compare_names(["Core_Top", "Coil_Primary"])

Only the product structure is parsed (PRODUCT, PRODUCT_DEFINITION, NEXT_ASSEMBLY_USAGE_OCCURRENCE,
SHAPE_DEFINITION_REPRESENTATION, the representations and plain SHAPE_REPRESENTATION_RELATIONSHIPs);
complex (multi-type) entities such as the placement relationships of an assembly are skipped, so an
assembly does not count its children's solids. Geometry is never read: volumes need a CAD kernel.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["StepProduct", "StepSummary", "parse_step_text", "read_step_names", "main"]

#: Entity types counted as solids (BREP_WITH_VOIDS is a subtype of MANIFOLD_SOLID_BREP).
SOLID_ENTITIES = ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS")
#: Entity types counted as sheets (surface models: free faces, not volumes).
SHEET_ENTITIES = ("SHELL_BASED_SURFACE_MODEL", "FACE_BASED_SURFACE_MODEL")
#: Product names that carry no information (gmsh's STEP writer, empty).
DEFAULT_NAME_PATTERN = re.compile(r"^(|Open CASCADE STEP translator.*|NONE|UNKNOWN)$", re.IGNORECASE)

_TOKEN = re.compile(r"'(?:[^']|'')*'|;|[^';]+")
_ARG_TOKEN = re.compile(r"'(?:[^']|'')*'|[(),]|[^'(),]+")
_RECORD = re.compile(r"\s*#(\d+)\s*=\s*([A-Z0-9_]+)\s*\((.*)\)\s*$", re.DOTALL)
_NEEDED = {"PRODUCT", "PRODUCT_DEFINITION", "PRODUCT_DEFINITION_FORMATION",
           "PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE", "PRODUCT_DEFINITION_SHAPE",
           "SHAPE_DEFINITION_REPRESENTATION", "NEXT_ASSEMBLY_USAGE_OCCURRENCE",
           "SHAPE_REPRESENTATION_RELATIONSHIP"} | set(SOLID_ENTITIES)


@dataclass(frozen=True)
class StepProduct:
    """One PRODUCT of the file.

    ``name`` is the PRODUCT name attribute (its id when the name is empty); ``paths`` are the
    ``/``-joined chains from the root assembly, one per occurrence: the JMAG part name pattern
    (verified for one assembly level only). ``solids`` / ``sheets`` count the solid and surface-model
    items of the product's own representations.
    """
    entity: int
    name: str
    paths: tuple[str, ...]
    is_assembly: bool
    solids: int
    sheets: int


@dataclass(frozen=True)
class StepSummary:
    """Products, solid counts and warnings of one STEP file (``source`` = path or ``"<text>"``)."""
    source: str
    products: tuple[StepProduct, ...]
    n_solids: int
    n_sheets: int
    solid_names: tuple[str, ...]
    warnings: tuple[str, ...] = field(default=())

    @property
    def leaf_products(self) -> tuple[StepProduct, ...]:
        """Products that are not assemblies: the ones that become JMAG parts."""
        return tuple(p for p in self.products if not p.is_assembly)

    @property
    def part_names(self) -> tuple[str, ...]:
        """Expected JMAG part names ``<assembly>/<product>`` of every leaf occurrence."""
        return tuple(path for p in self.leaf_products for path in p.paths)

    def compare_names(self, expected) -> dict:
        """``{"missing": [...], "extra": [...]}`` of leaf product names vs ``expected`` (e.g. labels JSON names)."""
        have = [p.name for p in self.leaf_products]
        return {"missing": [n for n in expected if n not in have],
                "extra": [n for n in have if n not in expected]}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["part_names"] = list(self.part_names)
        return d


def _decode_string(tok: str) -> str:
    """STEP string literal -> text: strip quotes, ``''`` -> ``'``, ``\\X2\\hhhh..\\X0\\`` -> unicode."""
    s = tok[1:-1].replace("''", "'")

    def x2(m: re.Match) -> str:
        h = m.group(1)
        return "".join(chr(int(h[i:i + 4], 16)) for i in range(0, len(h) - len(h) % 4, 4))

    s = re.sub(r"\\X2\\([0-9A-Fa-f]*)\\X0\\", x2, s)
    return re.sub(r"\\X\\([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), s)


def _split_args(body: str) -> list:
    """Top-level attributes of an entity: strings decoded, ``#n`` -> int, lists -> list, else raw text."""
    stack: list[list] = [[]]
    cur: list[str] = []

    def flush():
        raw = "".join(cur).strip()
        cur.clear()
        if raw:
            stack[-1].append(_scalar(raw))

    for tok in _ARG_TOKEN.findall(body):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            flush()
            inner = stack.pop()
            stack[-1].append(inner)
        elif tok == ",":
            flush()
        else:
            cur.append(tok)
    flush()
    return stack[0]


def _scalar(raw: str):
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return _decode_string(raw)
    if re.fullmatch(r"#\d+", raw):
        return int(raw[1:])
    return raw


def _records(text: str):
    """``(id, TYPE, body)`` of every simple entity in the DATA section (complex entities skipped)."""
    m = re.search(r"\bDATA\s*;", text)
    if m is None:
        raise ValueError("not a STEP Part 21 file: no DATA section")
    buf: list[str] = []
    for tok in _TOKEN.findall(text[m.end():]):
        if tok != ";":
            buf.append(tok)
            continue
        rec = "".join(buf)
        buf.clear()
        if rec.strip() == "ENDSEC":
            break
        r = _RECORD.match(rec)
        if r is not None:
            yield int(r.group(1)), r.group(2), r.group(3)


def _refs(value) -> list[int]:
    return [v for v in value if isinstance(v, int)] if isinstance(value, list) else []


def parse_step_text(text: str, source: str = "<text>") -> StepSummary:
    """Summarise the product structure of STEP text. Raises ``ValueError`` without a DATA section."""
    kinds: dict[int, str] = {}
    args: dict[int, list] = {}
    for eid, kind, body in _records(text):
        kinds[eid] = kind
        if kind in _NEEDED or kind.endswith("SHAPE_REPRESENTATION"):
            args[eid] = _split_args(body)

    def of(kind_set) -> list[int]:
        return [i for i, k in kinds.items() if k in kind_set]

    product_name = {i: (a[1] if len(a) > 1 and isinstance(a[1], str) and a[1] else
                        (a[0] if a and isinstance(a[0], str) else ""))
                    for i in of({"PRODUCT"}) for a in [args[i]]}
    formation_product = {i: args[i][2] for i in of({"PRODUCT_DEFINITION_FORMATION",
                                                    "PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE"})}
    pd_product = {i: formation_product.get(args[i][2]) for i in of({"PRODUCT_DEFINITION"})}
    pds_def = {i: args[i][2] for i in of({"PRODUCT_DEFINITION_SHAPE"})}
    links: dict[int, set] = defaultdict(set)
    for i in of({"SHAPE_REPRESENTATION_RELATIONSHIP"}):
        a, b = args[i][2], args[i][3]
        links[a].add(b)
        links[b].add(a)
    pd_reps: dict[int, set] = defaultdict(set)
    for i in of({"SHAPE_DEFINITION_REPRESENTATION"}):
        pd = pds_def.get(args[i][0])
        if pd in pd_product:
            pd_reps[pd].add(args[i][1])
    children: dict[int, list] = defaultdict(list)
    parents: dict[int, list] = defaultdict(list)
    for i in of({"NEXT_ASSEMBLY_USAGE_OCCURRENCE"}):
        parent, child = args[i][3], args[i][4]
        children[parent].append(child)
        parents[child].append(parent)

    def items(pd: int) -> list[int]:
        seen, todo, out = set(), list(pd_reps.get(pd, ())), []
        while todo:
            rep = todo.pop()
            if rep in seen or rep not in args:
                continue
            seen.add(rep)
            if len(args[rep]) > 1:
                out += _refs(args[rep][1])
            todo += links.get(rep, ())
        return out

    def chains(pd: int, guard=()) -> list[list[int]]:
        if not parents.get(pd) or pd in guard:
            return [[pd]]
        return [c + [pd] for par in parents[pd] for c in chains(par, guard + (pd,))]

    products = []
    for pd in sorted(pd_product, key=lambda i: pd_product[i] or 0):
        name = product_name.get(pd_product[pd], "")
        its = items(pd)
        paths = tuple("/".join(product_name.get(pd_product.get(x), "") for x in ch) for ch in chains(pd))
        products.append(StepProduct(entity=pd_product[pd], name=name, paths=paths,
                                    is_assembly=bool(children.get(pd)),
                                    solids=sum(kinds.get(x) in SOLID_ENTITIES for x in its),
                                    sheets=sum(kinds.get(x) in SHEET_ENTITIES for x in its)))
    solid_ids = sorted(of(set(SOLID_ENTITIES)))
    solid_names = tuple(args[i][0] if args[i] and isinstance(args[i][0], str) else "" for i in solid_ids)
    return StepSummary(source=source, products=tuple(products), n_solids=len(solid_ids),
                       n_sheets=len(of(set(SHEET_ENTITIES))), solid_names=solid_names,
                       warnings=tuple(_warnings(products, len(solid_ids))))


def _warnings(products: list[StepProduct], n_solids: int) -> list[str]:
    out = []
    if not products:
        out.append("no PRODUCT entities: JMAG part names will be generic; export a named assembly")
    for p in products:
        if DEFAULT_NAME_PATTERN.match(p.name):
            out.append(f"product #{p.entity} has no usable name ({p.name!r}): JMAG shows it under that name; "
                       "name the solids in the CAD script (gmsh's STEP writer drops names)")
    leaves = [p for p in products if not p.is_assembly]
    for p in leaves:
        if p.solids == 0 and p.sheets > 0:
            out.append(f"product {p.name!r} holds only sheet geometry ({p.sheets} surface model): free faces "
                       "(e.g. left by a 360 deg revolve) become an extra part; remove them in the CAD script")
        elif p.solids > 1:
            out.append(f"product {p.name!r} holds {p.solids} solids: check the JMAG part count after import")
    names = [p.name for p in leaves]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        out.append(f"duplicate leaf product names {dup}: rename by inside point after import")
    counted = sum(p.solids for p in leaves)
    if products and counted != n_solids:
        out.append(f"{n_solids} solids in the file but {counted} reached from leaf products: some solids "
                   "are not under a named product")
    return out


def read_step_names(path) -> StepSummary:
    """:func:`parse_step_text` of a file (read as UTF-8, undecodable bytes replaced)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"STEP file not found: {p}")
    return parse_step_text(p.read_text(encoding="utf-8", errors="replace"), source=str(p))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("step", help="STEP file (.step / .stp)")
    a = ap.parse_args(argv)
    s = read_step_names(a.step)
    json.dump(s.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if s.warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())

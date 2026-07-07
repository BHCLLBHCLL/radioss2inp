#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radioss2vtk.py
==============
Convert an OpenRadioss starter input file (.rad) into a VTK UnstructuredGrid
(.vtk legacy ASCII, or .vtu XML).

Parsing follows the same stream-dispatch approach as radioss2inp.py.  Unlike
the Abaqus converter, TETRA10 nodes keep the native Radioss ordering (no
C3D10M reorder) because VTK_QUADRATIC_TETRA uses the same 10-node layout.

/RWALL/PLANE rigid walls are not meshed in the .rad file; a 400x400 mm quad
is synthesized on the wall plane (same geometry as radioss2inp.py R3D4).
"""

import os
import sys
import math
import struct
import xml.etree.ElementTree as ET
from collections import OrderedDict


VTK_HEXAHEDRON = 12
VTK_TETRA = 10
VTK_QUADRATIC_TETRA = 24
VTK_QUAD = 9

# PartId offset for synthesized /RWALL/PLANE quads (distinct from /PART ids)
RWALL_PART_ID_BASE = 90000


def is_comment(line):
    return line.lstrip().startswith('#')


def is_blank(line):
    return line.strip() == ''


class RadiossToVtk:
    def __init__(self, input_path, output_path, linear_tet=False):
        self.input_path = input_path
        self.output_path = output_path
        self.linear_tet = linear_tet

        self.title = 'OpenRadioss model'
        self.units = ('Mg', 'mm', 's')
        self.nodes = []  # (nid, x, y, z)
        self.parts = OrderedDict()
        self.rwalls = OrderedDict()
        self.elem_count = 0
        self.warnings = []

    # ------------------------------------------------------------------
    # Parsing (subset of radioss2inp.py)
    # ------------------------------------------------------------------
    def parse(self):
        with open(self.input_path, 'r', encoding='utf-8', errors='replace') as f:
            self._parse_stream(iter(f))

    def _parse_stream(self, it):
        for line in it:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('/'):
                self._dispatch_keyword(stripped, it)

    def _dispatch_keyword(self, kw, it):
        parts_kw = kw.split('/')
        head = parts_kw[1] if len(parts_kw) > 1 else ''
        sub = parts_kw[2] if len(parts_kw) > 2 else ''

        def id_of(slot):
            v = parts_kw[slot] if len(parts_kw) > slot else ''
            try:
                return int(v)
            except (ValueError, TypeError):
                return None

        if head == 'BEGIN':
            self._parse_begin(it)
        elif head == 'TITLE':
            title = self._read_optional_data_line(it)
            if title:
                self.title = title.strip()
        elif head == 'NODE':
            self._parse_nodes(it)
        elif head == 'PART':
            self._parse_part(it, id_of(2))
        elif head == 'BRICK':
            self._parse_brick(it, id_of(2))
        elif head == 'TETRA10':
            self._parse_tetra10(it, id_of(2))
        elif head == 'RWALL' and sub == 'PLANE':
            self._parse_rwall_plane(it, id_of(3))
        elif head in ('END', 'MAT', 'PROP', 'FUNCT', 'INIVEL', 'GRNOD',
                      'GRAV', 'INTER', 'SURF', 'DEFAULT', 'DEF_SOLID'):
            self._skip_until_next_keyword(it)
        else:
            self._skip_until_next_keyword(it)

    def _read_data_line(self, it):
        for line in it:
            if is_comment(line) or is_blank(line):
                continue
            return line.rstrip('\n')
        return None

    def _read_optional_data_line(self, it):
        for line in it:
            if is_comment(line) or is_blank(line):
                continue
            s = line.rstrip('\n')
            if s.lstrip().startswith('/'):
                self._dispatch_keyword(s.strip(), it)
                return None
            return s
        return None

    def _skip_until_next_keyword(self, it):
        for line in it:
            s = line.strip()
            if s.startswith('/'):
                self._dispatch_keyword(s, it)
                return

    def _parse_begin(self, it):
        title_line = None
        for line in it:
            if is_comment(line) or is_blank(line):
                continue
            title_line = line.rstrip('\n')
            break
        if title_line:
            self.title = title_line.strip()
        self._read_data_line(it)
        u1 = self._read_data_line(it)
        if u1:
            toks = u1.split()
            if len(toks) >= 3:
                self.units = (toks[0], toks[1], toks[2])
        self._read_data_line(it)

    def _parse_nodes(self, it):
        for line in it:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.startswith('/'):
                self._dispatch_keyword(s, it)
                return
            toks = s.split()
            if len(toks) < 4:
                continue
            try:
                nid = int(toks[0])
                x, y, z = float(toks[1]), float(toks[2]), float(toks[3])
            except ValueError:
                continue
            self.nodes.append((nid, x, y, z))

    def _parse_part(self, it, part_id):
        name = self._read_data_line(it) or f'part{part_id}'
        data = self._read_data_line(it)
        toks = data.split() if data else []
        prop_id = int(toks[0]) if len(toks) > 0 else 0
        mat_id = int(toks[1]) if len(toks) > 1 else 0
        self.parts[part_id] = {
            'id': part_id, 'name': name.strip(),
            'prop_id': prop_id, 'mat_id': mat_id,
            'elem_type': None, 'elems': [],
        }

    def _parse_brick(self, it, part_id):
        if part_id not in self.parts:
            self.parts[part_id] = {
                'id': part_id, 'name': f'part{part_id}',
                'prop_id': 0, 'mat_id': 0,
                'elem_type': 'HEX8', 'elems': []}
        self.parts[part_id]['elem_type'] = 'HEX8'
        for line in it:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.startswith('/'):
                self._dispatch_keyword(s, it)
                return
            toks = s.split()
            if len(toks) < 9:
                continue
            try:
                eid = int(toks[0])
                nids = [int(t) for t in toks[1:9]]
            except ValueError:
                continue
            self.parts[part_id]['elems'].append((eid, nids))
            self.elem_count += 1

    def _parse_tetra10(self, it, part_id):
        if part_id not in self.parts:
            self.parts[part_id] = {
                'id': part_id, 'name': f'part{part_id}',
                'prop_id': 0, 'mat_id': 0,
                'elem_type': 'TETRA10', 'elems': []}
        self.parts[part_id]['elem_type'] = 'TETRA10'
        pending_eid = None
        for line in it:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.startswith('/'):
                self._dispatch_keyword(s, it)
                return
            toks = s.split()
            if pending_eid is None:
                if len(toks) == 1:
                    pending_eid = int(toks[0])
                else:
                    try:
                        eid = int(toks[0])
                        nids = [int(t) for t in toks[1:11]]
                        self.parts[part_id]['elems'].append((eid, nids))
                        self.elem_count += 1
                    except (ValueError, IndexError):
                        continue
            else:
                try:
                    nids = [int(t) for t in toks[:10]]
                except ValueError:
                    pending_eid = None
                    continue
                self.parts[part_id]['elems'].append((pending_eid, nids))
                self.elem_count += 1
                pending_eid = None

    def _parse_rwall_plane(self, it, rw_id):
        name = self._read_data_line(it) or f'rwall{rw_id}'
        d1 = self._read_data_line(it)
        toks1 = d1.split() if d1 else []
        slide = int(toks1[1]) if len(toks1) > 1 else 0
        d2 = self._read_data_line(it)
        toks2 = d2.split() if d2 else []
        dsearch = float(toks2[0]) if len(toks2) > 0 else 0.0
        fric = float(toks2[1]) if len(toks2) > 1 else 0.0
        d3 = self._read_data_line(it)
        toks3 = d3.split() if d3 else []
        xm = float(toks3[0]); ym = float(toks3[1]); zm = float(toks3[2])
        d4 = self._read_data_line(it)
        toks4 = d4.split() if d4 else []
        xm1 = float(toks4[0]); ym1 = float(toks4[1]); zm1 = float(toks4[2])
        self.rwalls[rw_id] = {
            'id': rw_id, 'name': name.strip(), 'slide': slide,
            'dsearch': dsearch, 'fric': fric,
            'XM': xm, 'YM': ym, 'ZM': zm,
            'XM1': xm1, 'YM1': ym1, 'ZM1': zm1,
        }

    @staticmethod
    def _rwall_corners(rw, half_size=200.0):
        """Build 4 corner points of the rigid-wall quad (same as radioss2inp.py)."""
        nx = rw['XM1'] - rw['XM']
        ny = rw['YM1'] - rw['YM']
        nz = rw['ZM1'] - rw['ZM']
        norm = math.sqrt(nx * nx + ny * ny + nz * nz)
        if norm < 1e-12:
            return None
        nx /= norm; ny /= norm; nz /= norm
        if abs(nz) < 0.9:
            u = (ny, -nx, 0.0)
        else:
            u = (0.0, nz, -ny)
        un = math.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
        u = (u[0] / un, u[1] / un, u[2] / un)
        v = (ny * u[2] - nz * u[1], nz * u[0] - nx * u[2], nx * u[1] - ny * u[0])
        corners = []
        for su, sv in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
            corners.append((
                rw['XM'] + su * half_size * u[0] + sv * half_size * v[0],
                rw['YM'] + su * half_size * u[1] + sv * half_size * v[1],
                rw['ZM'] + su * half_size * u[2] + sv * half_size * v[2],
            ))
        return corners

    # ------------------------------------------------------------------
    # Mesh assembly
    # ------------------------------------------------------------------
    def _build_mesh(self):
        """Return (points, node_ids, cells, cell_types, part_ids, mat_ids, elem_ids)."""
        nid_to_idx = {nid: i for i, (nid, *_rest) in enumerate(self.nodes)}
        points = [(x, y, z) for _nid, x, y, z in self.nodes]
        node_ids = [nid for nid, *_ in self.nodes]

        cells = []
        cell_types = []
        part_ids = []
        mat_ids = []
        elem_ids = []

        for pid, part in self.parts.items():
            etype = part.get('elem_type')
            for eid, nids in part['elems']:
                try:
                    idxs = [nid_to_idx[n] for n in nids]
                except KeyError as exc:
                    self.warnings.append(
                        f'Element {eid} (part {pid}) references missing node {exc.args[0]}')
                    continue

                if etype == 'HEX8':
                    if len(idxs) != 8:
                        self.warnings.append(f'Element {eid}: expected 8 nodes, got {len(idxs)}')
                        continue
                    cells.append(idxs)
                    cell_types.append(VTK_HEXAHEDRON)
                elif etype == 'TETRA10':
                    if len(idxs) != 10:
                        self.warnings.append(f'Element {eid}: expected 10 nodes, got {len(idxs)}')
                        continue
                    if self.linear_tet:
                        cells.append(idxs[:4])
                        cell_types.append(VTK_TETRA)
                    else:
                        cells.append(idxs)
                        cell_types.append(VTK_QUADRATIC_TETRA)
                else:
                    self.warnings.append(f'Element {eid}: unsupported type {etype}')
                    continue

                part_ids.append(pid)
                mat_ids.append(part.get('mat_id', 0))
                elem_ids.append(eid)

        # Synthesized rigid-wall floor quads (/RWALL/PLANE) — not in /NODE block
        for rw_id, rw in self.rwalls.items():
            corners = self._rwall_corners(rw)
            if corners is None:
                self.warnings.append(f'RWALL {rw_id}: degenerate normal, skipped')
                continue
            idxs = []
            for i, (cx, cy, cz) in enumerate(corners):
                synth_nid = 9_100_000 + rw_id * 10 + i
                idxs.append(len(points))
                points.append((cx, cy, cz))
                node_ids.append(synth_nid)
            cells.append(idxs)
            cell_types.append(VTK_QUAD)
            part_ids.append(RWALL_PART_ID_BASE + rw_id)
            mat_ids.append(0)
            elem_ids.append(8_000_000 + rw_id)

        return points, node_ids, cells, cell_types, part_ids, mat_ids, elem_ids

    # ------------------------------------------------------------------
    # VTK writers
    # ------------------------------------------------------------------
    def write(self):
        points, node_ids, cells, cell_types, part_ids, mat_ids, elem_ids = self._build_mesh()
        ext = os.path.splitext(self.output_path)[1].lower()
        if ext == '.vtu':
            self._write_vtu(points, node_ids, cells, cell_types,
                            part_ids, mat_ids, elem_ids)
        else:
            self._write_legacy_vtk(points, node_ids, cells, cell_types,
                                   part_ids, mat_ids, elem_ids)

    def _write_legacy_vtk(self, points, node_ids, cells, cell_types,
                        part_ids, mat_ids, elem_ids):
        npts = len(points)
        ncells = len(cells)
        conn_size = sum(len(c) + 1 for c in cells)

        with open(self.output_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('# vtk DataFile Version 3.0\n')
            f.write(f'{self.title}\n')
            f.write('ASCII\n')
            f.write('DATASET UNSTRUCTURED_GRID\n')

            f.write(f'POINTS {npts} double\n')
            for x, y, z in points:
                f.write(f'{x:.6f} {y:.6f} {z:.6f}\n')

            f.write(f'CELLS {ncells} {conn_size}\n')
            for conn in cells:
                f.write(str(len(conn)) + ' ' + ' '.join(str(i) for i in conn) + '\n')

            f.write(f'CELL_TYPES {ncells}\n')
            for ct in cell_types:
                f.write(f'{ct}\n')

            f.write(f'POINT_DATA {npts}\n')
            f.write('SCALARS NodeId int 1\n')
            f.write('LOOKUP_TABLE default\n')
            for nid in node_ids:
                f.write(f'{nid}\n')

            f.write(f'CELL_DATA {ncells}\n')
            f.write('SCALARS PartId int 1\n')
            f.write('LOOKUP_TABLE default\n')
            for v in part_ids:
                f.write(f'{v}\n')
            f.write('SCALARS MaterialId int 1\n')
            f.write('LOOKUP_TABLE default\n')
            for v in mat_ids:
                f.write(f'{v}\n')
            f.write('SCALARS ElementId int 1\n')
            f.write('LOOKUP_TABLE default\n')
            for v in elem_ids:
                f.write(f'{v}\n')

    def _write_vtu(self, points, node_ids, cells, cell_types,
                   part_ids, mat_ids, elem_ids):
        npts = len(points)
        ncells = len(cells)
        conn_size = sum(len(c) for c in cells)

        connectivity = []
        offsets = []
        off = 0
        for conn in cells:
            connectivity.extend(conn)
            off += len(conn)
            offsets.append(off)

        types_bytes = struct.pack(f'{ncells}B', *cell_types)
        conn_bytes = struct.pack(f'{conn_size}i', *connectivity)
        off_bytes = struct.pack(f'{ncells}i', *offsets)
        pts_bytes = struct.pack(f'{3 * npts}d', *(c for p in points for c in p))
        nid_bytes = struct.pack(f'{npts}i', *node_ids)
        pid_bytes = struct.pack(f'{ncells}i', *part_ids)
        mid_bytes = struct.pack(f'{ncells}i', *mat_ids)
        eid_bytes = struct.pack(f'{ncells}i', *elem_ids)

        def b64(data):
            import base64
            return base64.b64encode(data).decode('ascii')

        vtk = ET.Element('VTKFile', type='UnstructuredGrid', version='0.1', byte_order='LittleEndian')
        grid = ET.SubElement(vtk, 'UnstructuredGrid')
        piece = ET.SubElement(grid, 'Piece', NumberOfPoints=str(npts),
                              NumberOfCells=str(ncells))

        pts_elem = ET.SubElement(piece, 'Points')
        da_pts = ET.SubElement(pts_elem, 'DataArray', type='Float64',
                               NumberOfComponents='3', format='binary')
        da_pts.text = b64(pts_bytes)

        cells_elem = ET.SubElement(piece, 'Cells')
        da_conn = ET.SubElement(cells_elem, 'DataArray', type='Int32',
                                 Name='connectivity', format='binary')
        da_conn.text = b64(conn_bytes)
        da_off = ET.SubElement(cells_elem, 'DataArray', type='Int32',
                               Name='offsets', format='binary')
        da_off.text = b64(off_bytes)
        da_types = ET.SubElement(cells_elem, 'DataArray', type='UInt8',
                                 Name='types', format='binary')
        da_types.text = b64(types_bytes)

        pd = ET.SubElement(piece, 'PointData')
        da_nid = ET.SubElement(pd, 'DataArray', type='Int32', Name='NodeId',
                               format='binary')
        da_nid.text = b64(nid_bytes)

        cd = ET.SubElement(piece, 'CellData')
        da_pid = ET.SubElement(cd, 'DataArray', type='Int32', Name='PartId',
                               format='binary')
        da_pid.text = b64(pid_bytes)
        da_mid = ET.SubElement(cd, 'DataArray', type='Int32', Name='MaterialId',
                               format='binary')
        da_mid.text = b64(mid_bytes)
        da_eid = ET.SubElement(cd, 'DataArray', type='Int32', Name='ElementId',
                               format='binary')
        da_eid.text = b64(eid_bytes)

        tree = ET.ElementTree(vtk)
        if hasattr(ET, 'indent'):
            ET.indent(tree, space='  ')
        tree.write(self.output_path, encoding='utf-8', xml_declaration=True)

    def print_summary(self, mesh):
        points, _node_ids, cells, cell_types, *_rest = mesh
        hex_n = sum(1 for ct in cell_types if ct == VTK_HEXAHEDRON)
        tet10_n = sum(1 for ct in cell_types if ct == VTK_QUADRATIC_TETRA)
        tet4_n = sum(1 for ct in cell_types if ct == VTK_TETRA)
        quad_n = sum(1 for ct in cell_types if ct == VTK_QUAD)

        print('==================== CONVERSION SUMMARY ====================')
        print(f'  nodes         : {len(points)}')
        print(f'  cells         : {len(cells)}')
        print(f'    HEX8        : {hex_n}')
        if self.linear_tet:
            print(f'    TETRA4      : {tet4_n}')
        else:
            print(f'    TETRA10     : {tet10_n}')
        print(f'    QUAD (wall) : {quad_n}')
        print(f'  parts         : {len(self.parts)}')
        print(f'  rigid walls   : {len(self.rwalls)}')
        if self.warnings:
            print(f'  warnings      : {len(self.warnings)}')
            for w in self.warnings[:10]:
                print(f'    [W] {w}')
        print('==============================================================')


def _usage():
    print('Usage: python radioss2vtk.py [input.rad] [output.vtk|.vtu] [options]')
    print('Options:')
    print('  --linear-tet    Write TETRA10 as linear VTK_TETRA (4 corner nodes)')
    print('  --vtu           Force .vtu output (binary XML, smaller/faster)')


def main():
    args = sys.argv[1:]
    linear_tet = False
    if '--linear-tet' in args:
        linear_tet = True
        args.remove('--linear-tet')
    force_vtu = False
    if '--vtu' in args:
        force_vtu = True
        args.remove('--vtu')

    in_path = args[0] if len(args) > 0 else 'Cell_Phone_Drop_0000.rad'
    if force_vtu:
        out_path = args[1] if len(args) > 1 else 'Cell_Phone_Drop.vtu'
    else:
        out_path = args[1] if len(args) > 1 else 'Cell_Phone_Drop.vtk'

    if not os.path.exists(in_path):
        print(f'ERROR: input file not found: {in_path}', file=sys.stderr)
        sys.exit(1)

    print(f'Parsing Radioss file: {in_path}')
    conv = RadiossToVtk(in_path, out_path, linear_tet=linear_tet)
    conv.parse()
    print(f'Writing VTK mesh    : {out_path}')
    conv.write()
    mesh = conv._build_mesh()
    conv.print_summary(mesh)
    print('Done.')


if __name__ == '__main__':
    main()

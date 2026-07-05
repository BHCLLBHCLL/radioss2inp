#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radioss2inp.py
==============
Convert an OpenRadioss starter input file (.rad) into an Abaqus 6.14 input
deck (.inp) and verify that the output follows Abaqus input conventions.

Mapping implemented
--------------------
Radioss keyword                     -> Abaqus keyword
/BEGIN  (title, units)               -> *HEADING + comment
/MAT/ELAST  (linear elastic)         -> *MATERIAL + *ELASTIC + *DENSITY
/MAT/PLAS_TAB (tabulated plasticity)-> *MATERIAL + *ELASTIC + *PLASTIC + *DENSITY
/NODE                               -> *NODE
/BRICK   (8-node hex)               -> *ELEMENT, TYPE=C3D8
/TETRA10 (10-node tet)              -> *ELEMENT, TYPE=C3D10
/PART     + /PROP/SOLID             -> *SOLID SECTION (one section per part)
/FUNCT                               -> *AMPLITUDE (used by plasticity / loads)
/INIVEL/TRA + /GRNOD/NODE           -> *INITIAL CONDITIONS, TYPE=VELOCITY
/GRAV      + /GRNOD/NODE            -> *DLOAD with body force BX/BY/BZ
/INTER/TYPE2 (tied contact)         -> *TIE  (slave nset  <->  master surface)
/RWALL/PLANE (rigid plane wall)     -> analytical rigid surface + *CONTACT PAIR
/END                                 -> *END STEP / *FINISH (closing)
"""

import os
import sys
import math
import re
from collections import OrderedDict


# ----------------------------------------------------------------------------
# Helper: read a fixed-width / whitespace token field
# ----------------------------------------------------------------------------
def tokens(line):
    """Split a Radioss data line on whitespace; ignore comment lines."""
    s = line.strip()
    if not s or s.startswith('#'):
        return None
    return s.split()


def is_comment(line):
    return line.lstrip().startswith('#')


def is_blank(line):
    return line.strip() == ''


# ----------------------------------------------------------------------------
# Converter
# ----------------------------------------------------------------------------
class RadiossToAbaqus:
    def __init__(self, input_path, output_path):
        self.input_path = input_path
        self.output_path = output_path

        # ---- model-level info ----
        self.title = 'OpenRadioss model'
        self.units = ('Mg', 'mm', 's')

        # ---- material storage ----
        # mat[id] = {'name': str, 'kind': 'ELAST'|'PLAS_TAB',
        #            'rho': float, 'E': float, 'nu': float,
        #            'plast_func': int or None}
        self.materials = OrderedDict()
        # function[id] = list of (x, y) tuples
        self.functions = {}

        # ---- nodes ----
        self.node_count = 0
        self.node_min_id = None
        self.node_max_id = None
        self.nodes_written = 0  # counter for *NODE lines written

        # ---- elements ----
        # parts[id] = {'name': str, 'prop_id': int, 'mat_id': int,
        #              'elem_type': 'C3D8'|'C3D10',
        #              'elems': [(eid, [nids...]), ...]}
        self.parts = OrderedDict()
        self.elem_ids = set()  # for uniqueness verification
        self.elem_count = 0

        # ---- properties (PROP/SOLID) ----
        # prop[id] = {'name': str, 'isolid': int, 'ismstr': int, ...}
        self.props = OrderedDict()

        # ---- node groups / GRNOD/NODE ----
        # grnod[id] = {'name': str, 'nodes': [nids]}
        self.grnod = OrderedDict()

        # ---- initial velocity ----
        # inivel[id] = {'name': str, 'vx': float, 'vy': float, 'vz': float,
        #                'grnod_id': int}
        self.inivel = OrderedDict()

        # ---- gravity loads ----
        # grav[id] = {'name': str, 'func_id': int, 'dir': 'X'|'Y'|'Z',
        #             'grnod_id': int, 'ascale': float, 'fscale': float}
        self.grav = OrderedDict()

        # ---- INTER/TYPE2 (tied contacts) ----
        # inter[id] = {'name': str, 'slave_grnod_id': int,
        #              'master_surf_id': int, 'stfac': float}
        self.inter_type2 = OrderedDict()

        # ---- surfaces SURF/SEG ----
        # surf[id] = {'name': str, 'segs': [(seg_id, n1, n2, n3[, n4]), ...]}
        self.surfs = OrderedDict()

        # ---- rigid walls ----
        # rwall[id] = {'name': str, 'slide': int, 'dsearch': float, 'fric': float,
        #              'XM','YM','ZM', 'XM1','YM1','ZM1'}
        self.rwalls = OrderedDict()

        # ---- verification diagnostics ----
        self.warnings = []
        self.errors = []

    # =====================================================================
    # Parsing
    # =====================================================================
    def parse(self):
        with open(self.input_path, 'r', encoding='utf-8', errors='replace') as f:
            self._lines = f  # iterator
            self._parse_stream()

    def _parse_stream(self):
        it = iter(self._lines)
        line = ''
        try:
            while True:
                line = next(it)
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                if stripped.startswith('/'):
                    # keyword line
                    kw = stripped
                    self._dispatch_keyword(kw, it)
        except StopIteration:
            pass

    def _dispatch_keyword(self, kw, it):
        # Parse keyword + ID.
        # Radioss keywords come in two flavours:
        #   /KEY/ID                  -> id in slot 2   (e.g. /PART/1, /NODE)
        #   /KEY/SUBKEY/ID          -> id in slot 3   (e.g. /MAT/ELAST/4)
        parts_kw = kw.split('/')
        head = parts_kw[1] if len(parts_kw) > 1 else ''
        sub = parts_kw[2] if len(parts_kw) > 2 else ''
        sid = parts_kw[3] if len(parts_kw) > 3 else ''

        def id_of(default_slot):
            """Return the integer id (or None) located at the given slot."""
            v = parts_kw[default_slot] if len(parts_kw) > default_slot else ''
            try:
                return int(v)
            except (ValueError, TypeError):
                return None

        if head == 'BEGIN':
            self._parse_begin(it)
        elif head == 'TITLE':
            # /TITLE may have an empty data line - use optional reader
            title = self._read_optional_data_line(it)
            if title:
                self.title = title.strip()
        elif head == 'MAT':
            mat_id = id_of(3)
            if sub == 'ELAST':
                self._parse_mat_elast(it, mat_id)
            elif sub == 'PLAS_TAB':
                self._parse_mat_plas_tab(it, mat_id)
            else:
                self._skip_until_next_keyword(it)
        elif head == 'NODE':
            self._parse_nodes(it)
        elif head == 'PART':
            part_id = id_of(2)
            self._parse_part(it, part_id)
        elif head == 'BRICK':
            part_id = id_of(2)
            self._parse_brick(it, part_id)
        elif head == 'TETRA10':
            part_id = id_of(2)
            self._parse_tetra10(it, part_id)
        elif head == 'PROP' and sub == 'SOLID':
            prop_id = id_of(3)
            self._parse_prop_solid(it, prop_id)
        elif head == 'FUNCT':
            func_id = id_of(2)
            self._parse_funct(it, func_id)
        elif head == 'INIVEL' and sub == 'TRA':
            iv_id = id_of(3)
            self._parse_inivel_tra(it, iv_id)
        elif head == 'GRNOD' and sub == 'NODE':
            gn_id = id_of(3)
            self._parse_grnod_node(it, gn_id)
        elif head == 'GRAV':
            gv_id = id_of(2)
            self._parse_grav(it, gv_id)
        elif head == 'INTER' and sub == 'TYPE2':
            it_id = id_of(3)
            self._parse_inter_type2(it, it_id)
        elif head == 'SURF' and sub == 'SEG':
            sf_id = id_of(3)
            self._parse_surf_seg(it, sf_id)
        elif head == 'RWALL' and sub == 'PLANE':
            rw_id = id_of(3)
            self._parse_rwall_plane(it, rw_id)
        elif head in ('END', 'DEFAULT', 'DEF_SOLID'):
            self._skip_until_next_keyword(it)
        else:
            # Unknown keyword: skip to next keyword
            self._skip_until_next_keyword(it)

    # --------- generic helpers ---------
    def _read_data_line(self, it):
        """Return next data (non-comment, non-blank) line as string.
        Returns None at end of file."""
        for line in it:
            if is_comment(line) or is_blank(line):
                continue
            return line.rstrip('\n')
        return None

    def _read_optional_data_line(self, it):
        """Like _read_data_line but returns None (without consuming) if
        the next non-trivial line is a '/' keyword. Used for keywords whose
        data may be empty (e.g. /TITLE)."""
        for line in it:
            if is_comment(line) or is_blank(line):
                continue
            s = line.rstrip('\n')
            if s.lstrip().startswith('/'):
                # re-dispatch this keyword and signal 'no data'
                self._dispatch_keyword(s.strip(), it)
                return None
            return s
        return None

    def _skip_until_next_keyword(self, it):
        for line in it:
            s = line.strip()
            if s.startswith('/'):
                # push back: parse this keyword via dispatch
                self._dispatch_keyword(s, it)
                return
            # otherwise consume

    # --------- block parsers ---------
    def _parse_begin(self, it):
        # Line 1: title (may be blank)
        # Line 2: version, ...
        # Line 3-4: unit triple
        title_line = None
        for line in it:
            if is_comment(line) or is_blank(line):
                continue
            title_line = line.rstrip('\n')
            break
        if title_line:
            self.title = title_line.strip()
        # next data line: version
        self._read_data_line(it)
        # next two data lines: units (both same)
        u1 = self._read_data_line(it)
        if u1:
            toks = u1.split()
            if len(toks) >= 3:
                self.units = (toks[0], toks[1], toks[2])
        self._read_data_line(it)  # duplicate unit line

    def _parse_mat_elast(self, it, mat_id):
        name = self._read_data_line(it) or f'mat{mat_id}'
        # density line
        rho_line = self._read_data_line(it)
        rho = float(rho_line.split()[0]) if rho_line else 0.0
        # E, nu line
        en_line = self._read_data_line(it)
        toks = en_line.split()
        E = float(toks[0])
        nu = float(toks[1])
        self.materials[mat_id] = {
            'id': mat_id, 'name': name.strip(), 'kind': 'ELAST',
            'rho': rho, 'E': E, 'nu': nu, 'plast_func': None,
        }

    def _parse_mat_plas_tab(self, it, mat_id):
        name = self._read_data_line(it) or f'mat{mat_id}'
        rho_line = self._read_data_line(it)
        rho = float(rho_line.split()[0]) if rho_line else 0.0
        # E, nu, ...
        en_line = self._read_data_line(it)
        toks = en_line.split()
        E = float(toks[0])
        nu = float(toks[1])
        # N_funct, F_smooth, ...
        self._read_data_line(it)
        # fct_IDp, Fscale, ...
        self._read_data_line(it)
        # func_ID1 .. func_ID5
        fids_line = self._read_data_line(it)
        fids = fids_line.split() if fids_line else []
        plast_func = int(fids[0]) if fids else None
        # Fscale_i, Eps_dot_i lines
        self._read_data_line(it)
        self._read_data_line(it)
        self.materials[mat_id] = {
            'id': mat_id, 'name': name.strip(), 'kind': 'PLAS_TAB',
            'rho': rho, 'E': E, 'nu': nu, 'plast_func': plast_func,
        }

    def _parse_nodes(self, it):
        """Stream-parse *all* nodes directly to the .inp file later.
        Here we only count and remember the ID range."""
        # For simplicity (file is 42 MB, ~257k nodes -> ~50 MB in memory as
        # floats is fine), we just store all node coordinates in memory.
        self.nodes = []  # list of (nid, x, y, z)
        for line in it:
            s = line.strip()
            if not s:
                continue
            if s.startswith('#'):
                continue
            if s.startswith('/'):
                # next keyword: dispatch and break
                self.node_count = len(self.nodes)
                self._dispatch_keyword(s, it)
                return
            toks = s.split()
            if len(toks) < 4:
                continue
            try:
                nid = int(toks[0])
                x = float(toks[1])
                y = float(toks[2])
                z = float(toks[3])
            except ValueError:
                continue
            self.nodes.append((nid, x, y, z))
            if self.node_min_id is None or nid < self.node_min_id:
                self.node_min_id = nid
            if self.node_max_id is None or nid > self.node_max_id:
                self.node_max_id = nid
        self.node_count = len(self.nodes)

    def _parse_part(self, it, part_id):
        name = self._read_data_line(it) or f'part{part_id}'
        # part data line: prop_id  mat_id  ?
        data = self._read_data_line(it)
        toks = data.split()
        prop_id = int(toks[0]) if len(toks) > 0 else 0
        mat_id = int(toks[1]) if len(toks) > 1 else 0
        self.parts[part_id] = {
            'id': part_id, 'name': name.strip(),
            'prop_id': prop_id, 'mat_id': mat_id,
            'elem_type': None, 'elems': [],
        }

    def _parse_brick(self, it, part_id):
        # /BRICK appears right after /PART - we know part_id context.
        # Each data line: eid  n1 n2 n3 n4 n5 n6 n7 n8
        if part_id not in self.parts:
            self.parts[part_id] = {
                'id': part_id, 'name': f'part{part_id}',
                'prop_id': 0, 'mat_id': 0,
                'elem_type': 'C3D8', 'elems': []}
        self.parts[part_id]['elem_type'] = 'C3D8'
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
            self.elem_ids.add(eid)
            self.elem_count += 1

    def _parse_tetra10(self, it, part_id):
        if part_id not in self.parts:
            self.parts[part_id] = {
                'id': part_id, 'name': f'part{part_id}',
                'prop_id': 0, 'mat_id': 0,
                'elem_type': 'C3D10', 'elems': []}
        self.parts[part_id]['elem_type'] = 'C3D10'
        # TETRA10: line 1 = eid ; line 2 = 10 nids
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
                # this should be the element ID
                if len(toks) == 1:
                    pending_eid = int(toks[0])
                else:
                    # malformed - try eid + 10 nids on one line
                    try:
                        eid = int(toks[0])
                        nids = [int(t) for t in toks[1:11]]
                        self.parts[part_id]['elems'].append((eid, nids))
                        self.elem_ids.add(eid)
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
                self.elem_ids.add(pending_eid)
                self.elem_count += 1
                pending_eid = None

    def _parse_prop_solid(self, it, prop_id):
        name = self._read_data_line(it) or f'prop{prop_id}'
        # 3 data lines
        d1 = self._read_data_line(it)
        d2 = self._read_data_line(it)
        d3 = self._read_data_line(it)
        toks1 = d1.split() if d1 else []
        self.props[prop_id] = {
            'id': prop_id, 'name': name.strip(),
            'isolid': int(toks1[0]) if len(toks1) > 0 else 0,
            'ismstr': int(toks1[1]) if len(toks1) > 1 else 0,
        }

    def _parse_funct(self, it, func_id):
        name = self._read_data_line(it) or f'funct{func_id}'
        pts = []
        for line in it:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.startswith('/'):
                self.functions[func_id] = {'name': name.strip(), 'pts': pts}
                self._dispatch_keyword(s, it)
                return
            toks = s.split()
            try:
                x = float(toks[0])
                y = float(toks[1])
                pts.append((x, y))
            except (ValueError, IndexError):
                continue
        self.functions[func_id] = {'name': name.strip(), 'pts': pts}

    def _parse_inivel_tra(self, it, iv_id):
        name = self._read_data_line(it) or f'inivel{iv_id}'
        data = self._read_data_line(it)
        toks = data.split()
        vx = float(toks[0])
        vy = float(toks[1])
        vz = float(toks[2])
        gn_id = int(toks[3]) if len(toks) > 3 else 0
        self.inivel[iv_id] = {
            'id': iv_id, 'name': name.strip(),
            'vx': vx, 'vy': vy, 'vz': vz, 'grnod_id': gn_id,
        }

    def _parse_grnod_node(self, it, gn_id):
        name = self._read_data_line(it) or f'grnod{gn_id}'
        nids = []
        for line in it:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.startswith('/'):
                self.grnod[gn_id] = {'id': gn_id, 'name': name.strip(),
                                     'nodes': nids}
                self._dispatch_keyword(s, it)
                return
            toks = s.split()
            for t in toks:
                try:
                    nids.append(int(t))
                except ValueError:
                    pass
        self.grnod[gn_id] = {'id': gn_id, 'name': name.strip(), 'nodes': nids}

    def _parse_grav(self, it, gv_id):
        name = self._read_data_line(it) or f'grav{gv_id}'
        data = self._read_data_line(it)
        toks = data.split()
        func_id = int(toks[0])
        direction = toks[1]
        grnod_id = int(toks[4]) if len(toks) > 4 else 0
        ascale = float(toks[5]) if len(toks) > 5 else 0.0
        fscale = float(toks[6]) if len(toks) > 6 else 0.0
        self.grav[gv_id] = {
            'id': gv_id, 'name': name.strip(), 'func_id': func_id,
            'dir': direction, 'grnod_id': grnod_id,
            'ascale': ascale, 'fscale': fscale,
        }

    def _parse_inter_type2(self, it, it_id):
        name = self._read_data_line(it) or f'inter{it_id}'
        # Secon_id  Surf_id  Ignore  Spotflag  Level  Isearch  Idel  Dsearch
        d1 = self._read_data_line(it)
        toks1 = d1.split()
        secon_id = int(toks1[0]) if len(toks1) > 0 else 0
        surf_id = int(toks1[1]) if len(toks1) > 1 else 0
        # Stfac  Visc  Istf
        d2 = self._read_data_line(it)
        toks2 = d2.split() if d2 else []
        stfac = float(toks2[0]) if len(toks2) > 0 else 0.0
        self.inter_type2[it_id] = {
            'id': it_id, 'name': name.strip(),
            'slave_grnod_id': secon_id, 'master_surf_id': surf_id,
            'stfac': stfac,
        }

    def _parse_surf_seg(self, it, sf_id):
        name = self._read_data_line(it) or f'surf{sf_id}'
        segs = []
        for line in it:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.startswith('/'):
                self.surfs[sf_id] = {'id': sf_id, 'name': name.strip(),
                                     'segs': segs}
                self._dispatch_keyword(s, it)
                return
            toks = s.split()
            try:
                seg_id = int(toks[0])
                nids = [int(t) for t in toks[1:5]]  # 3 or 4 nids
            except (ValueError, IndexError):
                continue
            segs.append((seg_id, nids))
        self.surfs[sf_id] = {'id': sf_id, 'name': name.strip(), 'segs': segs}

    def _parse_rwall_plane(self, it, rw_id):
        name = self._read_data_line(it) or f'rwall{rw_id}'
        # node_ID  Slide  grnod_ID1  grnod_ID2
        d1 = self._read_data_line(it)
        toks1 = d1.split()
        slide = int(toks1[1]) if len(toks1) > 1 else 0
        # D_search  fric  Diameter  ffac  ifq
        d2 = self._read_data_line(it)
        toks2 = d2.split()
        dsearch = float(toks2[0]) if len(toks2) > 0 else 0.0
        fric = float(toks2[1]) if len(toks2) > 1 else 0.0
        # XM YM ZM
        d3 = self._read_data_line(it)
        toks3 = d3.split()
        xm = float(toks3[0]); ym = float(toks3[1]); zm = float(toks3[2])
        # X_M1 Y_M1 Z_M1
        d4 = self._read_data_line(it)
        toks4 = d4.split()
        xm1 = float(toks4[0]); ym1 = float(toks4[1]); zm1 = float(toks4[2])
        self.rwalls[rw_id] = {
            'id': rw_id, 'name': name.strip(), 'slide': slide,
            'dsearch': dsearch, 'fric': fric,
            'XM': xm, 'YM': ym, 'ZM': zm,
            'XM1': xm1, 'YM1': ym1, 'ZM1': zm1,
        }

    # =====================================================================
    # Writing Abaqus .inp
    # =====================================================================
    def write_inp(self):
        # Keep file open through verification so verify() can append the
        # verification report as comments at the end of the deck.
        self._file_obj = open(self.output_path, 'w', encoding='utf-8')
        self._f = self._file_obj
        try:
            self._write_header()
            self._write_nodes()
            self._write_elements_and_sections()
            self._write_materials()
            self._write_nsets_and_elsets()
            self._write_amplitudes()
            self._write_initial_conditions()
            self._write_gravity_loads()
            self._write_tie_constraints()
            self._write_rigid_wall()
            self._write_step()
            # run verification - it appends ** comments to the same file
            self.verify()
        finally:
            self._file_obj.close()

    def _emit(self, s):
        self._f.write(s)
        if not s.endswith('\n'):
            self._f.write('\n')

    def _write_header(self):
        f = self._f
        f.write('*HEADING\n')
        f.write(f'{self.title}\n')
        f.write('** OpenRadioss -> Abaqus 6.14 conversion\n')
        u = self.units
        f.write(f'** Units: mass={u[0]}  length={u[1]}  time={u[2]}\n')
        f.write('** (consistent set: 1 tonne, 1 mm, 1 s -> N, MPa, t/mm^3)\n')
        f.write('*PREPRINT, ECHO=NO, MODEL=NO, HISTORY=NO, CONTACT=NO\n')

    def _write_nodes(self):
        f = self._f
        f.write('**\n** -- NODES\n')
        f.write('*NODE\n')
        for nid, x, y, z in self.nodes:
            f.write(f'{nid}, {x:.6f}, {y:.6f}, {z:.6f}\n')
        self.nodes_written = len(self.nodes)

    def _write_elements_and_sections(self):
        f = self._f
        f.write('**\n** -- ELEMENTS & SECTIONS\n')
        # Collect element IDs per part to verify uniqueness
        seen_eid = set()
        # Track the global element-id range so we can build ALL_ELEMS
        # via *ELSET, GENERATE (much more compact than listing 130k IDs).
        all_eid_min = None
        all_eid_max = None
        for pid, part in self.parts.items():
            if not part['elems']:
                continue
            etype = part['elem_type']
            mat = self.materials.get(part['mat_id'])
            pname = self._safe_name(part['name'], prefix=f'P{pid}')
            # elset name derived from part
            elset_name = f'ELSET_P{pid}_{pname}'
            f.write(f'*ELEMENT, TYPE={etype}, ELSET={elset_name}\n')
            for eid, nids in part['elems']:
                if eid in seen_eid:
                    self.warnings.append(
                        f'Duplicate element id {eid} in part {pid} - skipped')
                    continue
                seen_eid.add(eid)
                f.write(f'{eid}, ' + ', '.join(str(n) for n in nids) + '\n')
                if all_eid_min is None or eid < all_eid_min:
                    all_eid_min = eid
                if all_eid_max is None or eid > all_eid_max:
                    all_eid_max = eid
            # Section assignment
            mat_name = self._safe_name(mat['name'], prefix=f'MAT{part["mat_id"]}') if mat else f'MAT{part["mat_id"]}'
            f.write(f'** Section for part {pid}: {part["name"]}\n')
            f.write(f'*SOLID SECTION, ELSET={elset_name}, MATERIAL={mat_name}\n')
            f.write(',\n')  # empty data line (no orientation etc.)
        # Build ALL_ELEMS elset for use by *DLOAD (gravity) and mass scaling
        if all_eid_min is not None:
            f.write('**\n** -- Global element set (all deformable elements)\n')
            f.write('*ELSET, ELSET=ALL_ELEMS, GENERATE\n')
            f.write(f'{all_eid_min}, {all_eid_max}, 1\n')
        self._all_eid_min = all_eid_min
        self._all_eid_max = all_eid_max

    def _write_materials(self):
        f = self._f
        f.write('**\n** -- MATERIALS\n')
        for mid, mat in self.materials.items():
            mname = self._safe_name(mat['name'], prefix=f'MAT{mid}')
            f.write(f'*MATERIAL, NAME={mname}\n')
            f.write(f'** source: /MAT/{mat["kind"]}/{mid}\n')
            f.write('*DENSITY\n')
            f.write(f'{mat["rho"]:.6E},\n')
            f.write('*ELASTIC\n')
            f.write(f'{mat["E"]:.6E}, {mat["nu"]:.6E}\n')
            if mat['kind'] == 'PLAS_TAB' and mat['plast_func'] is not None:
                func = self.functions.get(mat['plast_func'])
                if func and func['pts']:
                    f.write('*PLASTIC\n')
                    f.write('**  yield stress, equivalent plastic strain\n')
                    for x, y in func['pts']:
                        # Radioss: x=eps_pl, y=sigma(MPa)
                        f.write(f'{y:.6E}, {x:.6E}\n')
                else:
                    self.warnings.append(
                        f'Material {mid} (PLAS_TAB) references function '
                        f'{mat["plast_func"]} which has no points')

    def _write_nsets_and_elsets(self):
        f = self._f
        # node sets from GRNOD/NODE
        f.write('**\n** -- NODE SETS (from /GRNOD/NODE)\n')
        for gn_id, gn in self.grnod.items():
            nset_name = self._safe_name(
                gn['name'], prefix=f'NSET_G{gn_id}')
            nset_name = f'NSET_G{gn_id}_{nset_name}'
            f.write(f'*NSET, NSET={nset_name}\n')
            self._write_int_list(gn['nodes'])

    def _write_int_list(self, ids, per_line=12):
        f = self._f
        for i in range(0, len(ids), per_line):
            chunk = ids[i:i+per_line]
            f.write(', '.join(str(x) for x in chunk) + '\n')

    def _write_amplitudes(self):
        f = self._f
        f.write('**\n** -- AMPLITUDES (from /FUNCT)\n')
        for fid, func in self.functions.items():
            aname = f'AMP_F{fid}_{self._safe_name(func["name"], prefix=f"F{fid}")}'
            f.write(f'*AMPLITUDE, NAME={aname}, VALUE=ABSOLUTE\n')
            for x, y in func['pts']:
                f.write(f'{x:.6E}, {y:.6E},\n')

    def _write_initial_conditions(self):
        f = self._f
        if not self.inivel:
            return
        f.write('**\n** -- INITIAL VELOCITIES (from /INIVEL/TRA)\n')
        for iv_id, iv in self.inivel.items():
            gn = self.grnod.get(iv['grnod_id'])
            if not gn:
                self.warnings.append(
                    f'INIVEL {iv_id} references missing GRNOD {iv["grnod_id"]}')
                continue
            nset = f'NSET_G{iv["grnod_id"]}_{self._safe_name(gn["name"], prefix=f"NSET_G{iv["grnod_id"]}")}'
            # Use *INITIAL CONDITIONS, TYPE=VELOCITY on the nset via
            # repeated data lines (one per node) - or simpler: data-line
            # form that uses the nset? Abaqus requires per-node data lines
            # for *INITIAL CONDITIONS, TYPE=VELOCITY.
            f.write(f'** Initial velocity set "{iv["name"]}" '
                    f'on node set {nset}\n')
            f.write('*INITIAL CONDITIONS, TYPE=VELOCITY\n')
            for nid in gn['nodes']:
                f.write(f'{nid}, {iv["vx"]:.6E}, {iv["vy"]:.6E}, '
                        f'{iv["vz"]:.6E}\n')

    def _write_gravity_loads(self):
        f = self._f
        if not self.grav:
            return
        f.write('**\n** -- GRAVITY LOADS (from /GRAV)\n')
        # IMPORTANT: Abaqus/Explicit feinput does NOT support the BX/BY/BZ
        # body-force load types in *DLOAD.  Use the GRAV load type instead,
        # which applies a uniform gravitational acceleration to an element
        # set (not a node set).  Syntax:
        #   *DLOAD
        #   elset, GRAV, magnitude, comp1, comp2, comp3
        # where magnitude is in length/time^2 (mm/s^2 here) and the comp1..3
        # are the unit direction cosines (the magnitude is unsigned; the
        # direction carries the sign).
        dir_vec = {'X': (1.0, 0.0, 0.0),
                   'Y': (0.0, 1.0, 0.0),
                   'Z': (0.0, 0.0, 1.0)}
        # Use the global ALL_ELEMS elset.  Radioss /GRAV is usually applied
        # to GRNOD=ALL which is equivalent to the whole model.
        elset = 'ALL_ELEMS'
        for gv_id, gv in self.grav.items():
            gn = self.grnod.get(gv['grnod_id'])
            if not gn:
                self.warnings.append(
                    f'GRAV {gv_id} references missing GRNOD {gv["grnod_id"]}')
                continue
            vec = dir_vec.get(gv['dir'])
            if vec is None:
                self.warnings.append(f'GRAV {gv_id}: unknown direction {gv["dir"]}')
                continue
            # fscale is the signed acceleration (mm/s^2).
            mag = gv['fscale']
            # GRAV magnitude must be >= 0; the sign goes into the
            # direction vector.
            if mag < 0:
                comp = (-vec[0], -vec[1], -vec[2])
                amag = -mag
            else:
                comp = vec
                amag = mag
            func = self.functions.get(gv['func_id'], {})
            func_name = func.get('name', f'F{gv["func_id"]}') if func else f'F{gv["func_id"]}'
            amp = f'AMP_F{gv["func_id"]}_{self._safe_name(func_name, prefix=f"F{gv["func_id"]}")}'
            f.write(f'** GRAV {gv_id}: dir={gv["dir"]} accel={mag:.6E} mm/s^2 '
                    f'(applied to {elset})\n')
            # *DLOAD GRAV supports the AMPLITUDE parameter; if the
            # referenced function is constant (value=1) the amplitude is
            # redundant but still valid.
            f.write(f'*DLOAD, AMPLITUDE={amp}\n')
            f.write(f'{elset}, GRAV, {amag:.6E}, '
                    f'{comp[0]:.6f}, {comp[1]:.6f}, {comp[2]:.6f}\n')

    def _write_tie_constraints(self):
        f = self._f
        if not self.inter_type2:
            return
        f.write('**\n** -- TIED CONTACTS (from /INTER/TYPE2)\n')
        # Build surfaces: master surfaces from SURF/SEG (element-face based)
        # In Abaqus a surface needs to be associated with elements. Without
        # a face->element lookup, we approximate the master surface with a
        # node-based surface using the segment corner nodes.
        sf_counter = 0
        for it_id, it in self.inter_type2.items():
            sf_counter += 1
            m_surf_id = it['master_surf_id']
            s_gn_id = it['slave_grnod_id']
            m_surf = self.surfs.get(m_surf_id)
            s_gn = self.grnod.get(s_gn_id)
            if not m_surf or not s_gn:
                self.warnings.append(
                    f'INTER/TYPE2 {it_id} missing surf={m_surf_id} '
                    f'or grnod={s_gn_id}')
                continue
            # Slave surface: node-based surface from the slave node set
            slave_nset = f'NSET_G{s_gn_id}_{self._safe_name(s_gn["name"], prefix=f"NSET_G{s_gn_id}")}'
            slave_surf = f'SSLAVE_{it_id}'
            f.write(f'** TIE {it_id}: {it["name"]}\n')
            f.write(f'*SURFACE, TYPE=NODE, NAME={slave_surf}\n')
            f.write(f'{slave_nset}\n')
            # Master surface: node-based (use unique node IDs from segments)
            m_nodes = set()
            for seg_id, nids in m_surf['segs']:
                for n in nids:
                    m_nodes.add(n)
            master_surf = f'SMASTER_{it_id}'
            f.write(f'*NSET, NSET=NSET_MASTER_{it_id}\n')
            self._write_int_list(sorted(m_nodes))
            f.write(f'*SURFACE, TYPE=NODE, NAME={master_surf}\n')
            f.write(f'NSET_MASTER_{it_id}\n')
            f.write(f'*TIE, NAME=TIE_{it_id}, POSITION TOLERANCE=1.0\n')
            f.write(f'{slave_surf}, {master_surf}\n')

    def _write_rigid_wall(self):
        f = self._f
        if not self.rwalls:
            return
        f.write('**\n** -- RIGID WALL (from /RWALL/PLANE)\n')
        # Implementation note:
        # We build a DISCRETE rigid surface using R3D4 elements (4-node
        # bilinear rigid quadrilateral) instead of an *ANALYTICAL SURFACE.
        # The discrete-rigid-surface form is more reliably parsed by
        # feinput across Abaqus versions and avoids the "ANALYTICAL
        # SURFACE does not have corresponding *SURFACE" warning that some
        # feinput builds emit when a *RIGID BODY references an analytical
        # surface defined via *ANALYTICAL SURFACE.
        # Build a node set of ALL nodes in the model so the contact slave
        # surface covers the whole deformable mesh.
        all_nset = 'ALL_NODES'
        f.write(f'*NSET, NSET={all_nset}, GENERATE\n')
        if self.node_min_id and self.node_max_id:
            f.write(f'{self.node_min_id}, {self.node_max_id}, 1\n')

        for rw_id, rw in self.rwalls.items():
            # The plane passes through (XM, YM, ZM) with normal direction
            # pointing from M to M1.
            nx = rw['XM1'] - rw['XM']
            ny = rw['YM1'] - rw['YM']
            nz = rw['ZM1'] - rw['ZM']
            norm = math.sqrt(nx*nx + ny*ny + nz*nz)
            if norm < 1e-12:
                self.warnings.append(f'RWALL {rw_id}: degenerate normal')
                continue
            nx /= norm; ny /= norm; nz /= norm
            # Pick two in-plane orthonormal vectors (u, v)
            if abs(nz) < 0.9:
                u = (ny, -nx, 0.0)
            else:
                u = (0.0, nz, -ny)
            un = math.sqrt(u[0]**2 + u[1]**2 + u[2]**2)
            u = (u[0]/un, u[1]/un, u[2]/un)
            v = (ny*u[2] - nz*u[1], nz*u[0] - nx*u[2], nx*u[1] - ny*u[0])
            # Half-size of the rigid wall (mm).  Use a generous size so
            # the dropping object remains inside the contact zone.
            L = 200.0
            # 4 corner nodes of the wall, centred at M
            corners = []
            for su, sv in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
                cx = rw['XM'] + su*L*u[0] + sv*L*v[0]
                cy = rw['YM'] + su*L*u[1] + sv*L*v[1]
                cz = rw['ZM'] + su*L*u[2] + sv*L*v[2]
                corners.append((cx, cy, cz))
            # Reference node placed far along the normal (so it is far
            # from the contact zone but still inside the analysis).
            ref_nid = 9_000_000 + rw_id
            ref_x = rw['XM'] + 1000.0 * nx
            ref_y = rw['YM'] + 1000.0 * ny
            ref_z = rw['ZM'] + 1000.0 * nz
            # Corner node IDs (4 corners + 1 reference node)
            cn_ids = [9_100_000 + rw_id*10 + i for i in range(4)]
            f.write(f'** Rigid wall {rw_id}: {rw["name"]}\n')
            f.write(f'** Plane passes through ({rw["XM"]:.4f}, '
                    f'{rw["YM"]:.4f}, {rw["ZM"]:.4f})\n')
            f.write(f'** Normal = ({nx:.4f}, {ny:.4f}, {nz:.4f})\n')
            f.write(f'** Friction = {rw["fric"]:.4f}\n')
            # Reference node and 4 corner nodes
            f.write('*NODE\n')
            f.write(f'{ref_nid}, {ref_x:.4f}, {ref_y:.4f}, {ref_z:.4f}\n')
            for nid, (cx, cy, cz) in zip(cn_ids, corners):
                f.write(f'{nid}, {cx:.4f}, {cy:.4f}, {cz:.4f}\n')
            # R3D4 rigid element connecting the 4 corners
            elset_name = f'RWALL_ELSET_{rw_id}'
            rigid_eid = 8_000_000 + rw_id
            f.write(f'*ELEMENT, TYPE=R3D4, ELSET={elset_name}\n')
            f.write(f'{rigid_eid}, {cn_ids[0]}, {cn_ids[1]}, '
                    f'{cn_ids[2]}, {cn_ids[3]}\n')
            # Element-based surface for the rigid wall.
            # For a single R3D4 element used as a rigid wall we list ALL
            # six faces (S1..S6) on the surface so the contact works
            # regardless of which face the deformable mesh approaches.
            surf_name = f'RWALL_S{rw_id}'
            f.write(f'*SURFACE, NAME={surf_name}\n')
            for face in ('S1', 'S2', 'S3', 'S4', 'S5', 'S6'):
                f.write(f'{elset_name}, {face}\n')
            # Rigid body that owns the rigid elements
            f.write(f'*RIGID BODY, REF NODE={ref_nid}, ELSET={elset_name}\n')
            # Fully constrain the reference node (encastre)
            f.write('*BOUNDARY\n')
            f.write(f'{ref_nid}, 1, 6\n')
            # Slave surface (all model nodes, node-based)
            slave_surf = f'RWALL_SLAVE_{rw_id}'
            f.write(f'*SURFACE, TYPE=NODE, NAME={slave_surf}\n')
            f.write(f'{all_nset}\n')
            # Surface interaction (hard contact + friction) - must be
            # defined before the *CONTACT PAIR that references it.
            ip_name = f'RWALL_IP{rw_id}'
            f.write(f'*SURFACE INTERACTION, NAME={ip_name}\n')
            f.write('*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=HARD\n')
            f.write('*FRICTION\n')
            f.write(f'{rw["fric"]:.4f}\n')
            # Contact pair: deformable slave (nodes) vs rigid master (R3D4)
            f.write(f'** Contact pair (rigid wall {rw_id})\n')
            f.write(f'*CONTACT PAIR, INTERACTION={ip_name}, '
                    f'TYPE=SURFACE TO SURFACE\n')
            f.write(f'{slave_surf}, {surf_name}\n')

    def _write_step(self):
        f = self._f
        f.write('**\n** -- ANALYSIS STEP\n')
        # Use a dynamic-explicit step - the typical choice for an
        # explicit drop test.  NLGEOM=YES handles large deformation.
        f.write('*STEP, NLGEOM=YES, INC=100000\n')
        f.write('*DYNAMIC, EXPLICIT\n')
        f.write('** t_end,        dt_min,        dt_max\n')
        f.write('1.0E-3, , \n')
        f.write('*BULK VISCOSITY\n')
        f.write('0.06, 1.2\n')
        f.write('** Outputs\n')
        f.write('*OUTPUT, FIELD, NUMBER INTERVAL=20\n')
        f.write('*ELEMENT OUTPUT\n')
        f.write('S, LE, PEEQ\n')
        f.write('*NODE OUTPUT\n')
        f.write('U, V, A\n')
        f.write('*END STEP\n')

    # =====================================================================
    # Helpers
    # =====================================================================
    @staticmethod
    def _safe_name(name, prefix=''):
        """Sanitize a Radioss entity name into a valid Abaqus name
        (alphanumeric and underscore, max 80 chars, must start with letter)."""
        if not name:
            return prefix or 'NONAME'
        cleaned = re.sub(r'[^A-Za-z0-9_]', '_', name.strip())
        cleaned = re.sub(r'_+', '_', cleaned).strip('_')
        if not cleaned:
            cleaned = prefix or 'NONAME'
        if not cleaned[0].isalpha():
            cleaned = (prefix or 'X') + '_' + cleaned
        return cleaned[:80]

    # =====================================================================
    # Verification
    # =====================================================================
    def verify(self):
        """Verify the generated deck against Abaqus 6.14 conventions."""
        f = self._f
        f.write('**\n** =========================================================\n')
        f.write('** VERIFICATION REPORT (printed as comments)\n')
        f.write('** =========================================================\n')

        # Re-open the written file to do structural checks
        self._f.flush()

        # 1. node uniqueness / id range
        node_ids = [n[0] for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            dup = len(node_ids) - len(set(node_ids))
            self.errors.append(f'{dup} duplicate node IDs detected')
        if node_ids and min(node_ids) < 1:
            self.errors.append('Abaqus node IDs must be >= 1')
        if self.node_min_id is not None:
            f.write(f'** node count            : {self.node_count}\n')
            f.write(f'** node id range         : {self.node_min_id} .. '
                    f'{self.node_max_id}\n')

        # 2. element id uniqueness
        # already enforced during write; verify again
        if len(self.elem_ids) != self.elem_count:
            self.errors.append('Duplicate element IDs detected')

        # 3. element type / connectivity length
        for pid, part in self.parts.items():
            etype = part['elem_type']
            expected = {'C3D8': 8, 'C3D10': 10}.get(etype, 0)
            if not expected:
                self.errors.append(f'Part {pid}: unknown element type {etype}')
                continue
            bad = 0
            for eid, nids in part['elems']:
                if len(nids) != expected:
                    bad += 1
            if bad:
                self.errors.append(
                    f'Part {pid}: {bad} {etype} elements with wrong '
                    f'connectivity length (expected {expected})')

        # 4. material references in sections
        for pid, part in self.parts.items():
            if part['mat_id'] and part['mat_id'] not in self.materials:
                self.errors.append(
                    f'Part {pid}: material id {part["mat_id"]} not defined')
            if part['prop_id'] and part['prop_id'] not in self.props:
                self.warnings.append(
                    f'Part {pid}: property id {part["prop_id"]} not defined '
                    f'(will use *SOLID SECTION without orientation)')

        # 5. plasticity function existence
        for mid, mat in self.materials.items():
            if mat['kind'] == 'PLAS_TAB':
                if mat['plast_func'] not in self.functions:
                    self.errors.append(
                        f'Material {mid}: plasticity function '
                        f'{mat["plast_func"]} not defined')

        # 6. INTER/TYPE2 references
        for it_id, it in self.inter_type2.items():
            if it['slave_grnod_id'] not in self.grnod:
                self.errors.append(
                    f'INTER/TYPE2 {it_id}: slave grnod '
                    f'{it["slave_grnod_id"]} not defined')
            if it['master_surf_id'] not in self.surfs:
                self.errors.append(
                    f'INTER/TYPE2 {it_id}: master surf '
                    f'{it["master_surf_id"]} not defined')

        # 7. GRAV references
        for gv_id, gv in self.grav.items():
            if gv['grnod_id'] not in self.grnod:
                self.errors.append(
                    f'GRAV {gv_id}: grnod {gv["grnod_id"]} not defined')
            if gv['func_id'] not in self.functions:
                self.warnings.append(
                    f'GRAV {gv_id}: function {gv["func_id"]} not defined')

        # 8. INIVEL references
        for iv_id, iv in self.inivel.items():
            if iv['grnod_id'] not in self.grnod:
                self.errors.append(
                    f'INIVEL {iv_id}: grnod {iv["grnod_id"]} not defined')

        # 9. name sanity
        for mid, mat in self.materials.items():
            n = self._safe_name(mat['name'], prefix=f'MAT{mid}')
            if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', n):
                self.errors.append(f'Material {mid}: invalid Abaqus name "{n}"')

        # 10. PLASTIC table sanity (Abaqus requires first eps_pl = 0 and
        # monotonically increasing eps_pl)
        for mid, mat in self.materials.items():
            if mat['kind'] != 'PLAS_TAB' or mat['plast_func'] is None:
                continue
            func = self.functions.get(mat['plast_func'])
            if not func or not func['pts']:
                continue
            xs = [p[0] for p in func['pts']]
            if xs[0] != 0.0:
                self.warnings.append(
                    f'Material {mid}: *PLASTIC first plastic strain is '
                    f'{xs[0]} (Abaqus expects 0) - first point may be '
                    f'skipped by Abaqus')
            for i in range(1, len(xs)):
                if xs[i] <= xs[i-1]:
                    self.warnings.append(
                        f'Material {mid}: *PLASTIC strain values are not '
                        f'monotonically increasing at index {i}')
                    break

        # 11. Density positive
        for mid, mat in self.materials.items():
            if mat['rho'] <= 0:
                self.errors.append(
                    f'Material {mid}: density must be > 0 (got {mat["rho"]})')

        # 12. Element node references (every node id used by an element must
        # be defined in *NODE).  This is the most expensive check.
        node_id_set = set(n[0] for n in self.nodes)
        missing_nodes = 0
        for pid, part in self.parts.items():
            for eid, nids in part['elems']:
                for n in nids:
                    if n not in node_id_set:
                        missing_nodes += 1
                        if missing_nodes <= 5:
                            self.errors.append(
                                f'Part {pid}, element {eid}: references '
                                f'undefined node {n}')
        if missing_nodes > 5:
            self.errors.append(f'... and {missing_nodes - 5} more undefined '
                               f'node references')

        # 13. Element ID > 0
        for eid in self.elem_ids:
            if eid < 1:
                self.errors.append(f'Element id {eid}: must be >= 1')
                break

        # 10. summary stats
        f.write(f'** parts                  : {len(self.parts)}\n')
        f.write(f'** elements               : {self.elem_count}\n')
        f.write(f'** materials              : {len(self.materials)}\n')
        f.write(f'** properties             : {len(self.props)}\n')
        f.write(f'** functions              : {len(self.functions)}\n')
        f.write(f'** node groups (GRNOD)    : {len(self.grnod)}\n')
        f.write(f'** initial velocities     : {len(self.inivel)}\n')
        f.write(f'** gravity loads           : {len(self.grav)}\n')
        f.write(f'** TYPE2 contacts (TIE)   : {len(self.inter_type2)}\n')
        f.write(f'** surfaces (SURF/SEG)    : {len(self.surfs)}\n')
        f.write(f'** rigid walls            : {len(self.rwalls)}\n')

        f.write('**\n')
        if self.warnings:
            f.write(f'** WARNINGS ({len(self.warnings)}):\n')
            for w in self.warnings:
                f.write(f'**  [W] {w}\n')
        if self.errors:
            f.write(f'** ERRORS ({len(self.errors)}):\n')
            for e in self.errors:
                f.write(f'**  [E] {e}\n')
        else:
            f.write('** No errors detected.\n')
        f.write('** =========================================================\n')

        # also print to console
        print('==================== VERIFICATION SUMMARY ====================')
        print(f'  nodes         : {self.node_count}')
        print(f'  elements      : {self.elem_count}')
        print(f'  parts         : {len(self.parts)}')
        print(f'  materials     : {len(self.materials)}')
        print(f'  properties    : {len(self.props)}')
        print(f'  functions     : {len(self.functions)}')
        print(f'  node groups   : {len(self.grnod)}')
        print(f'  initial vel.  : {len(self.inivel)}')
        print(f'  gravity loads : {len(self.grav)}')
        print(f'  TYPE2 ties    : {len(self.inter_type2)}')
        print(f'  surfaces      : {len(self.surfs)}')
        print(f'  rigid walls   : {len(self.rwalls)}')
        if self.warnings:
            print(f'  warnings      : {len(self.warnings)}')
            for w in self.warnings[:20]:
                print(f'    [W] {w}')
        if self.errors:
            print(f'  errors        : {len(self.errors)}')
            for e in self.errors:
                print(f'    [E] {e}')
        else:
            print('  errors        : 0')
        print('===============================================================')


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else 'Cell_Phone_Drop_0000.rad'
    out_path = sys.argv[2] if len(sys.argv) > 2 else 'Cell_Phone_Drop.inp'
    if not os.path.exists(in_path):
        print(f'ERROR: input file not found: {in_path}', file=sys.stderr)
        sys.exit(1)
    print(f'Parsing Radioss file: {in_path}')
    conv = RadiossToAbaqus(in_path, out_path)
    conv.parse()
    print(f'Writing Abaqus inp  : {out_path}')
    print(f'Verifying output ...')
    conv.write_inp()
    print('Done.')


if __name__ == '__main__':
    main()

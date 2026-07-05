import sys
sys.path.insert(0, '.')
from radioss2inp import RadiossToAbaqus

conv = RadiossToAbaqus('Cell_Phone_Drop_0000.rad', 'tmp.inp')
conv.parse()

# Find element 260715
target_eid = 260715
found_pid = None
found_nids = None
for pid, part in conv.parts.items():
    for eid, nids in part['elems']:
        if eid == target_eid:
            found_pid = pid
            found_nids = nids
            break
    if found_pid:
        break

etype = conv.parts[found_pid]['elem_type']
print('Element', target_eid, 'in PART', found_pid, 'type=', etype)
print('Nodes:', found_nids)

# Find node coordinates
node_map = {n[0]: (n[1], n[2], n[3]) for n in conv.nodes}
print('Node coordinates:')
for i, nid in enumerate(found_nids, 1):
    if nid in node_map:
        print('  N', i, '(', nid, '):', node_map[nid])
    else:
        print('  N', i, '(', nid, '): UNDEFINED')

# Check which TIEs involve these nodes
node_set = set(found_nids)
print()
print('TIE involvement:')
for it_id, it in conv.inter_type2.items():
    s_gn = conv.grnod.get(it['slave_grnod_id'])
    m_sf = conv.surfs.get(it['master_surf_id'])
    if not s_gn or not m_sf:
        continue
    slave_nodes = set(s_gn['nodes'])
    master_nodes = set()
    for seg_id, nids in m_sf['segs']:
        for n in nids:
            master_nodes.add(n)
    in_slave = node_set & slave_nodes
    in_master = node_set & master_nodes
    if in_slave or in_master:
        name = it['name'][:30]
        print('  TIE', it_id, '(', name, '): in_slave=', len(in_slave), 'in_master=', len(in_master))
        if in_slave:
            print('    slave nodes:', list(in_slave)[:10])
        if in_master:
            print('    master nodes:', list(in_master)[:10])

# Check if any slave node appears in MULTIPLE TIEs as slave
print()
print('Nodes appearing as slave in MULTIPLE TIEs:')
node_to_ties = {}
for it_id, it in conv.inter_type2.items():
    s_gn = conv.grnod.get(it['slave_grnod_id'])
    if not s_gn:
        continue
    for nid in s_gn['nodes']:
        node_to_ties.setdefault(nid, []).append(it_id)
multi = {n: ts for n, ts in node_to_ties.items() if len(ts) > 1}
print('  count:', len(multi))
if multi:
    for n, ts in list(multi.items())[:5]:
        print('  node', n, 'in TIEs', ts)

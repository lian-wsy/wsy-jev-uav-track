"""Read-only evidence for four exact cooked CarlaAir release assets.

This is an audited byte-layout recipe, NOT a general Unreal/PhysX parser.
All four full-file SHA256/size pins must match before any offsets are used.
Asset-local geometry is never promoted to runtime clearance or flight authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

FROZEN_ASSETS = {
    'Blueprints/BP_FlyingPawn.uasset': (10853, 'f782c035f9073ca5b2e73e6a1bf6e980075f5fa9bd55673f59641fa03d3db880'),
    'Blueprints/BP_FlyingPawn.uexp': (7249, 'c684cf5368a3faf717f5afb53dfe54a5248f54c6a3366a395aa043aa70972445'),
    'Models/QuadRotor1/Quadrotor1.uasset': (2939, 'bec4169739f2af739a53261a758463e252f41fef28cbdebcfeb0fb7d7972c62c'),
    'Models/QuadRotor1/Quadrotor1.uexp': (1226858, 'ba1665cba0750b4b831a56b543f7a1a6cf45c57e191249064d923d755ff14fb3'),
}


def vectors(data, offset, count):
    if (type(data) is not bytes or type(offset) is not int or offset < 0
            or type(count) is not int or not 0 < count <= 65536
            or offset + 12 * count > len(data)):
        raise ValueError('Invalid vector byte span')
    result = list(struct.iter_unpack('<3f', data[offset:offset + 12 * count]))
    if not all(math.isfinite(value) for point in result for value in point):
        raise ValueError('Nonfinite geometry')
    return result


def summarize_vertices(points):
    if (type(points) not in (list, tuple) or not 0 < len(points) <= 65536
            or any(type(point) not in (tuple, list) or len(point) != 3
                   or any(type(x) not in (int, float) or not math.isfinite(x) for x in point)
                   for point in points)):
        raise ValueError('Invalid geometry')
    radius = max(math.hypot(*point) for point in points)
    if not math.isfinite(radius):
        raise ValueError('Nonfinite derived geometry')
    return {'count': len(points), 'min': [min(p[a] for p in points) for a in range(3)],
            'max': [max(p[a] for p in points) for a in range(3)],
            'radius_about_asset_origin': radius}


def _require(value, expected, label):
    if value != expected:
        raise ValueError('Unexpected pinned layout: ' + label)


def _package_tables(blob):
    """The pinned cooked -7 summary: name hashes, 28B imports, 104B exports."""
    _require(struct.unpack_from('<6i', blob), (-1641380927, -7, 0, 0, 0, 0), 'summary')
    _require(struct.unpack_from('<i', blob, 24)[0], len(blob), 'header length')
    name_count, cursor = struct.unpack_from('<ii', blob, 41)
    export_count, export_at, import_count, import_at = struct.unpack_from('<4i', blob, 57)
    names = []
    for _ in range(name_count):
        length = struct.unpack_from('<i', blob, cursor)[0]
        cursor += 4
        if not 0 < length <= 4096 or cursor + length + 4 > len(blob):
            raise ValueError('Invalid pinned name')
        _require(blob[cursor + length - 1], 0, 'name terminator')
        names.append(blob[cursor:cursor + length - 1].decode('utf-8'))
        cursor += length + 4
    _require(cursor, import_at, 'name table end')

    def fname(offset):
        index, number = struct.unpack_from('<ii', blob, offset)
        if not 0 <= index < len(names) or number < 0:
            raise ValueError('Invalid pinned name reference')
        return names[index] + ('_' + str(number - 1) if number else '')

    imports = []
    for i in range(import_count):
        offset = import_at + 28 * i
        imports.append({'name': fname(offset + 20), 'class': fname(offset + 8),
                        'outer': struct.unpack_from('<i', blob, offset + 16)[0]})
    _require(import_at + 28 * import_count, export_at, 'import table end')
    exports = []
    for i in range(export_count):
        offset = export_at + 104 * i
        cls, _, template, outer = struct.unpack_from('<4i', blob, offset)
        size, position = struct.unpack_from('<qq', blob, offset + 28)
        exports.append({'name': fname(offset + 16), 'class_index': cls,
                        'template': template, 'outer': outer, 'bytes': size,
                        'uexp_offset': position - len(blob)})
    return names, imports, exports


def _cooked_convex(blob, offset):
    """PhysX CVXM13 / CLHL8 / CVHL8 vertex prefix; not the whole stream."""
    _require(blob[offset:offset + 8], b'NXS\x01CVXM', 'convex magic/endian')
    _require(struct.unpack_from('<2I', blob, offset + 8), (13, 0), 'convex version/flags')
    _require(blob[offset + 16:offset + 24], b'ICE\x01CLHL', 'CLHL')
    _require(struct.unpack_from('<I', blob, offset + 24)[0], 8, 'CLHL version')
    _require(blob[offset + 28:offset + 36], b'ICE\x01CVHL', 'CVHL')
    _require(struct.unpack_from('<I', blob, offset + 36)[0], 8, 'CVHL version')
    count, edges, polygons, refs = struct.unpack_from('<4I', blob, offset + 40)
    _require((count, edges, polygons, refs), (32, 52, 22, 104), 'hull counts')
    points = vectors(blob, offset + 56, count)
    summary = summarize_vertices(points)
    # HullPolygonData is 20B; this release has no high-bit edge table.
    bounds_offset = offset + 56 + count * 12 + polygons * 20 + refs + edges * 2 + count * 3
    cooked = struct.unpack_from('<8f', blob, bounds_offset)
    if (not all(math.isfinite(x) for x in cooked)
            or any(abs(a - b) > 1e-4 for a, b in
                   zip(summary['min'] + summary['max'], cooked[1:7]))):
        raise ValueError('Cooked hull bounds disagree with vertices')
    return dict(summary, header_offset=offset, vertices_offset=offset + 56,
                vertex_bytes_sha256=hashlib.sha256(blob[offset + 56:offset + 56 + count * 12]).hexdigest(),
                reported_bounds_min=list(cooked[1:4]), reported_bounds_max=list(cooked[4:7])), points


def audit_asset_bytes(assets):
    if type(assets) is not dict or set(assets) != set(FROZEN_ASSETS):
        raise ValueError('Unexpected asset inventory')
    for name, (size, expected) in FROZEN_ASSETS.items():
        raw = assets[name]
        if (type(raw) is not bytes or len(raw) != size
                or hashlib.sha256(raw).hexdigest() != expected):
            raise ValueError('Mismatched frozen asset: ' + name)

    bp_header = assets['Blueprints/BP_FlyingPawn.uasset']
    bp = assets['Blueprints/BP_FlyingPawn.uexp']
    mesh_header = assets['Models/QuadRotor1/Quadrotor1.uasset']
    mesh = assets['Models/QuadRotor1/Quadrotor1.uexp']
    _, imports, exports = _package_tables(bp_header)
    _require(exports[31]['name'], 'BodyMesh_GEN_VARIABLE', 'blueprint body template')
    _require(exports[31]['uexp_offset'], 6311, 'body template position')
    _require(struct.unpack_from('<i', bp, 3749)[0], 32, 'body node template reference')
    roots_count = struct.unpack_from('<i', bp, 6153)[0]
    roots = list(struct.unpack_from('<' + 'i' * roots_count, bp, 6157))
    _require(roots, [17, 18, 20, 21, 22], 'SCS roots')
    _require(struct.unpack_from('<i', bp, 6336)[0], -50, 'body mesh import')
    _require(imports[49], {'name': 'Quadrotor1', 'class': 'StaticMesh', 'outer': -27}, 'mesh import')
    mesh_asset = imports[26]['name']
    _require(mesh_asset, '/AirSim/Models/QuadRotor1/Quadrotor1', 'mesh package')
    _, _, mesh_exports = _package_tables(mesh_header)
    _require((mesh_exports[0]['name'], mesh_exports[0]['outer'], mesh_exports[0]['uexp_offset']),
             ('BodySetup_0', 3, 0), 'body setup export')
    _require(mesh_exports[2]['name'], 'Quadrotor1', 'mesh export')

    # UE tagged AggGeom -> ConvexElems[1] -> VertexData[32], independently
    # walked before freezing this layout. Inner struct-array sizes are int64.
    _require(struct.unpack_from('<i', mesh, 82)[0], 1, 'convex element count')
    _require(struct.unpack_from('<i', mesh, 168)[0], 32, 'raw vertex count')
    raw_points = vectors(mesh, 221, 32)
    raw_summary = summarize_vertices(raw_points)
    box = struct.unpack_from('<6fB', mesh, 691)
    _require(list(box[:6]), raw_summary['min'] + raw_summary['max'], 'serialized ElemBox')
    _require(box[6], 1, 'valid ElemBox')
    transform = {'rotation_xyzw': list(struct.unpack_from('<4f', mesh, 814)),
                 'translation': list(struct.unpack_from('<3f', mesh, 879)),
                 'scale3d': list(struct.unpack_from('<3f', mesh, 940))}
    _require(transform, {'rotation_xyzw': [0., 0., 0., 1.], 'translation': [0., 0., 0.],
                         'scale3d': [1., 1., 1.]}, 'convex element transform')
    build_scale = list(struct.unpack_from('<3f', mesh, 1592))
    _require(build_scale, [12., 12., 12.], 'build scale metadata')
    cooked0, cooked_points = _cooked_convex(mesh, 1686)
    cooked1, mirrored_points = _cooked_convex(mesh, 2971)
    nearest = max(min(math.dist(v, w) for w in cooked_points) for v in raw_points)
    mirrored = max(min(math.dist((-v[0], v[1], v[2]), w) for w in mirrored_points) for v in raw_points)

    # PhysX MESH14, BVH33 id=0, materials + remap + 16-bit indices flags=11.
    _require(mesh[4255:4263], b'NXS\x01MESH', 'triangle magic/endian')
    version, midphase, flags, count, triangles = struct.unpack_from('<5I', mesh, 4263)
    _require((version, midphase, flags, count, triangles), (14, 0, 11, 6484, 12765), 'triangle prefix')
    tri_points = vectors(mesh, 4283, count)
    indices_offset = 4283 + count * 12
    indices = struct.unpack_from('<' + 'H' * (triangles * 3), mesh, indices_offset)
    if any(i >= count for i in indices):
        raise ValueError('Triangle index outside vertex array')
    tri_summary = dict(summarize_vertices(tri_points), triangle_count=triangles,
                       vertices_offset=4283, indices_offset=indices_offset,
                       all_triangle_indices_in_range=True,
                       vertex_bytes_sha256=hashlib.sha256(mesh[4283:indices_offset]).hexdigest())
    return {
        'schema_version': 1, 'status': 'FROZEN_COOKED_BODY_GEOMETRY_EXTRACTED',
        'evidence_class': 'OFFLINE_COOKED_ASSET_NOT_RUNTIME_CALIBRATION',
        'coordinate_units': 'ASSET_LOCAL_UNITS_NO_AUTOMATIC_METRIC_CLEARANCE',
        'files': [{'relative_path': name, 'bytes': size, 'sha256': sha}
                  for name, (size, sha) in FROZEN_ASSETS.items()],
        'blueprint': {'body_mesh_export': 32, 'scs_body_node_export': 17,
                      'scs_root_nodes': roots, 'mesh_import': -50, 'mesh_asset': mesh_asset,
                      'runtime_active_mesh_match': 'NOT_VERIFIED'},
        'serialized_convex': dict(raw_summary, vertices_offset=221, vertices=raw_points),
        'convex_element_transform': transform, 'build_scale3d_metadata': build_scale,
        'build_scale_reapplied': False,
        'cooked_convex': [cooked0, cooked1], 'cooked_triangles': tri_summary,
        'maximum_nearest_raw_to_cooked_error_asset_units': nearest,
        'maximum_nearest_x_mirrored_to_second_cooked_error_asset_units': mirrored,
        'control_ready': False, 'runtime_collision_envelope_m': None, 'new_pass_episodes': 0,
        'limitations': [
            'Pins identify four local asset files, not the currently spawned actor or its component transforms.',
            'Runtime actor/component scale, active shape choice, contact offsets and additional colliding components remain unverified.',
            'BuildScale3D is preserved metadata; multiplying cooked vertices again is not justified by this audit.',
            'No source-to-shipped-binary identity, whole-engine physics proof, dynamic clearance, support-contact exception, startup corridor or braking is certified.',
        ],
    }


def audit_asset_directory(root):
    root = Path(root).resolve(strict=True)
    assets = {}
    for name, (size, _) in FROZEN_ASSETS.items():
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError('Asset path escaped asset root')
        before = path.stat()
        if before.st_size != size:
            raise ValueError('Mismatched frozen asset size: ' + name)
        with path.open('rb') as handle:
            raw = handle.read(size + 1)
        after = path.stat()
        if ((before.st_size, before.st_mtime_ns, before.st_ino)
                != (after.st_size, after.st_mtime_ns, after.st_ino)):
            raise ValueError('Asset changed during read')
        assets[name] = raw
    return audit_asset_bytes(assets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-root', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output is not None:
        if args.output.exists():
            parser.error('output already exists; preserve prior evidence')
        if args.output.resolve().is_relative_to(args.asset_root.resolve()):
            parser.error('output must be outside the read-only asset root')
    report = audit_asset_directory(args.asset_root)
    text = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    if args.output is None:
        print(text, end='')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        print(json.dumps({'status': report['status'], 'output': str(args.output.resolve()),
                          'control_ready': False}))


if __name__ == '__main__':
    main()

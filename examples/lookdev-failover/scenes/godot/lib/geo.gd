extends RefCounted
## Procedural meshes for the look-development scene: chamfered boxes (enclosures, bezels,
## plates) and tubes swept along a polyline (cables). Static, deterministic, no resources.
##
## Winding: Godot treats clockwise triangles (seen from the front) as front faces. `_tri`
## orders each triangle against the outward normal it is given, so callers never think about it.


static func _tri(v: PackedVector3Array, n: PackedVector3Array, uv: PackedVector2Array,
		a: Vector3, b: Vector3, c: Vector3, na: Vector3, nb: Vector3, nc: Vector3,
		ua: Vector2, ub: Vector2, uc: Vector2, out: Vector3) -> void:
	if (b - a).cross(c - a).dot(out) > 0.0:
		var t := b; b = c; c = t
		var tn := nb; nb = nc; nc = tn
		var tu := ub; ub = uc; uc = tu
	v.append_array([a, b, c])
	n.append_array([na, nb, nc])
	uv.append_array([ua, ub, uc])


static func _poly(v: PackedVector3Array, n: PackedVector3Array, uv: PackedVector2Array, pts: Array) -> void:
	# Convex planar polygon given in any order; flat-shaded with its outward normal (the body
	# is centred on the origin, so "outward" points away from it).
	var cc := Vector3.ZERO
	for p in pts:
		cc += p
	cc /= float(pts.size())
	var p0: Vector3 = pts[0]
	var nrm: Vector3 = (pts[1] - p0).cross(pts[2] - p0).normalized()
	if nrm.dot(cc) < 0.0:
		nrm = -nrm
	var u_ax: Vector3 = (pts[0] - cc).normalized()
	var v_ax := nrm.cross(u_ax)
	var sorted := pts.duplicate()
	sorted.sort_custom(func(p, q):
		return atan2((p - cc).dot(v_ax), (p - cc).dot(u_ax)) < atan2((q - cc).dot(v_ax), (q - cc).dot(u_ax)))
	for i in range(1, sorted.size() - 1):
		_tri(v, n, uv, sorted[0], sorted[i], sorted[i + 1], nrm, nrm, nrm,
			Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, nrm)


## A box of `size` centred on the origin with every edge cut by a 45-degree chamfer of width `c`.
## The chamfers are separate flat faces, so they catch the key light as thin highlights.
static func chamfer_box(size: Vector3, c: float) -> ArrayMesh:
	var h := size * 0.5
	c = minf(c, minf(h.x, minf(h.y, h.z)) * 0.9)
	var v := PackedVector3Array()
	var n := PackedVector3Array()
	var uv := PackedVector2Array()
	var signs := [-1.0, 1.0]
	for axis in 3:
		var o1 := (axis + 1) % 3
		var o2 := (axis + 2) % 3
		# main face
		for s in signs:
			var pts := []
			for a in signs:
				for b in signs:
					var p := Vector3()
					p[axis] = s * h[axis]
					p[o1] = a * (h[o1] - c)
					p[o2] = b * (h[o2] - c)
					pts.append(p)
			_poly(v, n, uv, pts)
		# the four chamfer strips running along `axis`
		for s1 in signs:
			for s2 in signs:
				var pts := []
				for e in signs:
					var p := Vector3()
					p[axis] = e * (h[axis] - c)
					p[o1] = s1 * h[o1]
					p[o2] = s2 * (h[o2] - c)
					pts.append(p)
					var q := Vector3()
					q[axis] = e * (h[axis] - c)
					q[o1] = s1 * (h[o1] - c)
					q[o2] = s2 * h[o2]
					pts.append(q)
				_poly(v, n, uv, pts)
	# corner triangles
	for sx in signs:
		for sy in signs:
			for sz in signs:
				_poly(v, n, uv, [
					Vector3(sx * h.x, sy * (h.y - c), sz * (h.z - c)),
					Vector3(sx * (h.x - c), sy * h.y, sz * (h.z - c)),
					Vector3(sx * (h.x - c), sy * (h.y - c), sz * h.z),
				])
	return _mesh(v, n, uv)


## A tube of radius `r` swept along `pts`. UV.x is the arc-length fraction (0 at pts[0]) and
## UV.y the angle around the tube / TAU, measured from the frame's "up" toward +z.
static func tube(pts: PackedVector3Array, r: float, radial: int) -> ArrayMesh:
	var cum := PackedFloat32Array([0.0])
	for i in range(1, pts.size()):
		cum.append(cum[i - 1] + pts[i].distance_to(pts[i - 1]))
	var total := cum[cum.size() - 1]
	var rings := []  # per point: [positions, normals]
	for i in pts.size():
		var t: Vector3 = (pts[mini(i + 1, pts.size() - 1)] - pts[maxi(i - 1, 0)]).normalized()
		var side := Vector3(0, 0, 1)
		var up := side.cross(t).normalized()  # ~ +y for a cable running along +x
		side = t.cross(up).normalized()
		var ps := []
		var ns := []
		for j in radial + 1:
			var a := TAU * float(j) / float(radial)
			var d := up * cos(a) + side * sin(a)
			ps.append(pts[i] + d * r)
			ns.append(d)
		rings.append([ps, ns])
	var v := PackedVector3Array()
	var n := PackedVector3Array()
	var uv := PackedVector2Array()
	for i in pts.size() - 1:
		var p0: Array = rings[i][0]
		var n0: Array = rings[i][1]
		var p1: Array = rings[i + 1][0]
		var n1: Array = rings[i + 1][1]
		var s0 := cum[i] / total
		var s1 := cum[i + 1] / total
		for j in radial:
			var f0 := float(j) / float(radial)
			var f1 := float(j + 1) / float(radial)
			var out: Vector3 = n0[j] + n0[j + 1]
			_tri(v, n, uv, p0[j], p0[j + 1], p1[j], n0[j], n0[j + 1], n1[j],
				Vector2(s0, f0), Vector2(s0, f1), Vector2(s1, f0), out)
			_tri(v, n, uv, p0[j + 1], p1[j + 1], p1[j], n0[j + 1], n1[j + 1], n1[j],
				Vector2(s0, f1), Vector2(s1, f1), Vector2(s1, f0), out)
	return _mesh(v, n, uv)


static func _mesh(v: PackedVector3Array, n: PackedVector3Array, uv: PackedVector2Array) -> ArrayMesh:
	var arr := []
	arr.resize(Mesh.ARRAY_MAX)
	arr[Mesh.ARRAY_VERTEX] = v
	arr[Mesh.ARRAY_NORMAL] = n
	arr[Mesh.ARRAY_TEX_UV] = uv
	var m := ArrayMesh.new()
	m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
	return m

# https://github.com/null138

import lzma, math, os, struct, sys
from itertools import product

# max units between faces to count them as the same face. compiled bsp sometimes gets faces shifted away idk why
# raise this if it fails to detect faces
TOLERANCE = 4.0

def load_ti(data, lumps):
	raw = lumpBytes(data, lumps[6])
	out = []
	for i in range(len(raw) // 72):
		v = struct.unpack_from('<8f8fii', raw, i * 72)
		out.append({'lm_vecs': (v[8:12], v[12:16]), 'flags': v[16]})
	return out

def load_pln(data, lumps):
	raw = lumpBytes(data, lumps[1])
	out = []
	for i in range(len(raw) // 20):
		nx, ny, nz, dist, _ = struct.unpack_from('<4fi', raw, i * 20)
		out.append(((nx, ny, nz), dist))
	return out

def blockLen(faces, ltLen):
	used = sorted((f[9], i) for i, f in enumerate(faces) if f[9] >= 0)
	lengths = {}
	for idx, (ofs, face_i) in enumerate(used):
		if idx + 1 < len(used):
			nxt = used[idx + 1][0]
		else:
			nxt = ltLen
		lengths[face_i] = max(0, nxt - ofs)
	return {i: lengths.get(i, 0) for i in range(len(faces))}

def cptLight(faces, knownLen, lighting):
	newLt = bytearray()
	for i, f in enumerate(faces):
		if f[9] < 0:
			continue
		length = knownLen.get(i, 0)
		oldOfs = f[9]
		block = bytes(lighting[oldOfs:oldOfs + length])
		newOfs = len(newLt)
		newLt.extend(block)
		f[9] = newOfs
	return bytes(newLt)

def load_edg(data, lumps):
	raw = lumpBytes(data, lumps[12])
	return [struct.unpack_from('<HH', raw, i * 4) for i in range(len(raw) // 4)]

def lumpBytes(data, lump):
	raw = bytes(data[lump[1]:lump[1] + lump[2]])
	if raw[0:4] != b'LZMA':
		return raw
	if len(raw) < 17:
		raise ValueError('truncated LZMA lump')
	_, actual, lzma_size, props = struct.unpack_from('<4sII5s', raw, 0)
	comp = raw[17:17 + lzma_size]
	return lzma.decompress(props + struct.pack('<Q', actual) + comp, format=lzma.FORMAT_ALONE)

def rebuildBsp(tgtData, tgtLumps, repl):
	out = bytearray(tgtData[:1036])
	body = bytearray()
	newLumps = []
	for lump in tgtLumps:
		idx, ofs, length, ver, cc = lump
		if idx in repl:
			payload, compress = repl[idx]
			if compress:
				alone = lzma.compress(payload, format=lzma.FORMAT_ALONE)
				raw = struct.pack('<4sII5s', b'LZMA', len(payload), len(alone) - 13, alone[0:5]) + alone[13:]
				ccNew = len(payload)
			else:
				raw = payload
				ccNew = 0
		else:
			raw = bytes(tgtData[ofs:ofs + length])
			ccNew = cc
		pad = (-len(body)) % 4
		if pad:
			body.extend(b'\x00' * pad)
		newOfs = 1036 + len(body)
		if idx == 35 and idx not in repl and length > 0:
			delta = newOfs - ofs
			if delta != 0 and len(raw) >= 4:
				compressed = raw[0:4] == b'LZMA'
				if compressed:
					if len(raw) < 17:
						raise ValueError('truncated LZMA lump')
					_, actual, lzma_size, props = struct.unpack_from('<4sII5s', raw, 0)
					comp = raw[17:17 + lzma_size]
					payload_gl = bytearray(lzma.decompress(props + struct.pack('<Q', actual) + comp, format=lzma.FORMAT_ALONE))
				else:
					payload_gl = bytearray(raw)
				if len(payload_gl) >= 4:
					count = struct.unpack_from('<i', payload_gl, 0)[0]
					pos = 4
					for _ in range(count):
						if pos + 16 > len(payload_gl):
							break
						fileofs = struct.unpack_from('<i', payload_gl, pos + 8)[0]
						if fileofs:
							struct.pack_into('<i', payload_gl, pos + 8, fileofs + delta)
						pos += 16
					if compressed:
						alone = lzma.compress(bytes(payload_gl), format=lzma.FORMAT_ALONE)
						raw = struct.pack('<4sII5s', b'LZMA', len(payload_gl), len(alone) - 13, alone[0:5]) + alone[13:]
					else:
						raw = bytes(payload_gl)
		body.extend(raw)
		newLumps.append([idx, newOfs, len(raw), ver, ccNew])
	out.extend(body)
	for lump in newLumps:
		idx, ofs, length, ver, cc = lump
		struct.pack_into('<iiI4s', out, 8 + idx * 16, ofs, length, ver, cc.to_bytes(4, 'little'))
	return out

def load_vts(data, lumps):
	raw = lumpBytes(data, lumps[3])
	return [struct.unpack_from('<3f', raw, i * 12) for i in range(len(raw) // 12)]

def stylecnt(styles):
	n = 0
	for s in styles:
		if s == 255:
			break
		n += 1
	return max(n, 1)

def vecDot(a, b):
	return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]

def txOfs(w, h, smp, stIdx, sx, sy, bmpI):
	stStride = w * h * smp * 4
	return stIdx * stStride + (sy * w + sx) * smp * 4 + bmpI * 4

def doResample(source, target, out):
	srcData, _, srcLumps = readBsp(source)
	tgtData, _, tgtLumps = readBsp(target)

	srcFaces = parseF(lumpBytes(srcData, srcLumps[7]))
	tgtFaces = parseF(lumpBytes(tgtData, tgtLumps[7]))
	tgtFacesCompr = isCompr(tgtData, tgtLumps[7])

	sv = load_vts(srcData, srcLumps)
	se = load_edg(srcData, srcLumps)
	sse = loadSedg(srcData, srcLumps)
	tv = load_vts(tgtData, tgtLumps)
	te = load_edg(tgtData, tgtLumps)
	tse = loadSedg(tgtData, tgtLumps)

	srcPlanes = load_pln(srcData, srcLumps)
	tgtPlanes = load_pln(tgtData, tgtLumps)
	srcTi = load_ti(srcData, srcLumps)
	tgtTi = load_ti(tgtData, tgtLumps)
	srcTex = loadTexn(srcData, srcLumps)
	tgtTex = loadTexn(tgtData, tgtLumps)

	# seems to work lol
	pairs, used = matchF(srcFaces, tgtFaces, sv, se, sse, tv, te, tse, srcTex, tgtTex, TOLERANCE)

	srcLt = bytearray(lumpBytes(srcData, srcLumps[8]))
	tgtLt = bytearray(lumpBytes(tgtData, tgtLumps[8]))
	tgtLtCompr = isCompr(tgtData, tgtLumps[8])

	tgtOrigLen = blockLen(tgtFaces, len(tgtLt))
	modLdr, wrLdr, cpStats, hLdr = cptExact(pairs, srcFaces, tgtFaces, srcLt, tgtLt)

	srcCands = buildCandi(srcFaces, sv, se, sse, srcPlanes, srcTi, srcTex)
	modR, wrR, rsStats = resampleP(tgtFaces, tgtTi, tgtPlanes, tgtTex, srcCands, hLdr, srcLt, tgtLt)
	modLdr = modR or modLdr
	wrLdr.update(wrR)

	unt = len(tgtFaces) - len(hLdr) - sum(rsStats[k] for k in ('full', 'partial'))
	print(
		f'[LDR] target faces: {len(tgtFaces)} | matched: {len(used)} '
		f'(copied {cpStats["copied"]}, identical {cpStats["identical"]}, '
		f'size-mismatch {cpStats["size_mismatch"]}, no-block {cpStats["no_block"]}) | '
		f'resampled: full {rsStats["full"]}, partial/dilated {rsStats["partial"]}, '
		f'zero-hit {rsStats["no_hit"]}, no-texture {rsStats["no_texture"]}, '
		f'no-candidates {rsStats["no_candidates"]} | left completely untouched: {unt}'
	)
	
	doHdr = tgtLumps[58][2] > 0 and srcLumps[58][2] > 0
	modHdr = False
	tgtFacesHdr = []
	tgtLtHdr = []
	tgtFacesHdrCompr = False
	tgtLtHdrCompr = False

	if doHdr:
		srcFacesHdr = parseF(lumpBytes(srcData, srcLumps[58]))
		tgtFacesHdr = parseF(lumpBytes(tgtData, tgtLumps[58]))
		tgtFacesHdrCompr = isCompr(tgtData, tgtLumps[58])

		srcLtHdr = bytearray(lumpBytes(srcData, srcLumps[53]))
		tgtLtHdr = bytearray(lumpBytes(tgtData, tgtLumps[53]))
		tgtLtHdrCompr = isCompr(tgtData, tgtLumps[53])

		tgtOrigLenHdr = blockLen(tgtFacesHdr, len(tgtLtHdr))
		modHdr, wrHdr, cpStatsHdr, hHdr = cptExact(pairs, srcFacesHdr, tgtFacesHdr, srcLtHdr, tgtLtHdr)

		srcCandsHdr = buildCandi(srcFacesHdr, sv, se, sse, srcPlanes, srcTi, srcTex)
		modRHdr, wrRHdr, rsStatsHdr = resampleP(tgtFacesHdr, tgtTi, tgtPlanes, tgtTex, srcCandsHdr, hHdr, srcLtHdr, tgtLtHdr)
		modHdr = modRHdr or modHdr
		wrHdr.update(wrRHdr)

		untHdr = len(tgtFacesHdr) - len(hHdr) - sum(rsStatsHdr[k] for k in ('full', 'partial'))
		print(
			f'[HDR] target faces: {len(tgtFacesHdr)} | matched: {len(used)} '
			f'(copied {cpStatsHdr["copied"]}, identical {cpStatsHdr["identical"]}, '
			f'size-mismatch {cpStatsHdr["size_mismatch"]}, no-block {cpStatsHdr["no_block"]}) | '
			f'resampled: full {rsStatsHdr["full"]}, partial/dilated {rsStatsHdr["partial"]}, '
			f'zero-hit {rsStatsHdr["no_hit"]}, no-texture {rsStatsHdr["no_texture"]}, '
			f'no-candidates {rsStatsHdr["no_candidates"]} | left completely untouched: {untHdr}'
		)

	if not modLdr and not modHdr:
		print('Nothing to copy') # Exact map or nothing which can safely be copied
		return

	if modLdr:
		knLdr = dict(tgtOrigLen)
		knLdr.update(wrLdr)
		tgtLt = bytearray(cptLight(tgtFaces, knLdr, tgtLt))
	if modHdr:
		knHdr = dict(tgtOrigLenHdr)
		knHdr.update(wrHdr)
		tgtLtHdr = bytearray(cptLight(tgtFacesHdr, knHdr, tgtLtHdr))

	repl = {}
	if modLdr:
		repl[7] = (b''.join(struct.pack('<HBBihhhh4sifiiiiiHHI', *f) for f in tgtFaces), tgtFacesCompr)
		repl[8] = (bytes(tgtLt), tgtLtCompr)
	if modHdr:
		repl[58] = (b''.join(struct.pack('<HBBihhhh4sifiiiiiHHI', *f) for f in tgtFacesHdr), tgtFacesHdrCompr)
		repl[53] = (bytes(tgtLtHdr), tgtLtHdrCompr)

	outData = rebuildBsp(tgtData, tgtLumps, repl)

	with open(out, 'wb') as f:
		f.write(outData)
	print('Wrote ' + out)

def txfill1(out, ctx, sy, sx, nSty):
	m0, m1, lvs, normal, dist, origin, ua, va, cands, srcLt, smp, w, h = ctx
	lv0, lv1 = lvs
	uA = [
		[lv0[0], lv0[1], lv0[2]],
		[lv1[0], lv1[1], lv1[2]],
		[normal[0], normal[1], normal[2]],
	]
	ub = [m0 + sx - lv0[3], m1 + sy - lv1[3], dist]
	ud = (uA[0][0] * (uA[1][1]*uA[2][2] - uA[1][2]*uA[2][1])
			- uA[0][1] * (uA[1][0]*uA[2][2] - uA[1][2]*uA[2][0])
			+ uA[0][2] * (uA[1][0]*uA[2][1] - uA[1][1]*uA[2][0]))
	if abs(ud) < 1e-9:
		P = None
	else:
		usol = []
		for ucol in range(3):
			um = [urow[:] for urow in uA]
			for urow_i in range(3):
				um[urow_i][ucol] = ub[urow_i]
			usol.append((um[0][0] * (um[1][1]*um[2][2] - um[1][2]*um[2][1])
					- um[0][1] * (um[1][0]*um[2][2] - um[1][2]*um[2][0])
					+ um[0][2] * (um[1][0]*um[2][1] - um[1][1]*um[2][0])) / ud)
		P = tuple(usol)
	if P is None:
		return False
	pu = vecDot(vecSub(P, origin), ua)
	pv = vecDot(vecSub(P, origin), va)
	best_fb = None
	best_d = None
	c = None
	for cand in cands:
		bx0, by0, bz0, bx1, by1, bz1 = cand['bbox']
		if not (bx0 <= P[0] <= bx1 and by0 <= P[1] <= by1 and bz0 <= P[2] <= bz1):
			continue
		poly2d = []
		for v in cand['poly']:
			dv = vecSub(v, origin)
			poly2d.append((vecDot(dv, ua), vecDot(dv, va)))
		inside = False
		n = len(poly2d)
		for i in range(n):
			x1, y1 = poly2d[i]
			x2, y2 = poly2d[(i + 1) % n]
			if (y1 > pv) != (y2 > pv):
				xin = x1 + (pv - y1) * (x2 - x1) / (y2 - y1 + 1e-12)
				if pu < xin + 0.01:
					inside = not inside
		if inside:
			c = cand
			break
		fbest = float('inf')
		for i in range(n):
			x1, y1 = poly2d[i]
			x2, y2 = poly2d[(i + 1) % n]
			dx, dy = x2 - x1, y2 - y1
			seg2 = dx*dx + dy*dy
			if seg2 < 1e-12:
				proj = 0.0
			else:
				proj = max(0.0, min(1.0, ((pu - x1)*dx + (pv - y1)*dy) / seg2))
			cx = x1 + proj * dx
			cy = y1 + proj * dy
			fd = ((pu - cx)**2 + (pv - cy)**2) ** 0.5
			if fd < fbest:
				fbest = fd
		if best_d is None or fbest < best_d:
			best_fb = cand
			best_d = fbest
	else:
		if best_fb is not None and best_d < 2.0:
			c = best_fb
	if c is None:
		return False
	lv0, lv1 = c['lvs']
	s_src = vecDot(P, lv0[:3]) + lv0[3] - c['m0']
	t_src = vecDot(P, lv1[:3]) + lv1[3] - c['m1']
	for si in range(nSty):
		ss = min(si, c['ns'] - 1)
		base = txOfs(w, h, smp, si, sx, sy, 0)
		for bi in range(smp):
			sb = bi if bi < c['smp'] else 0
			bs = max(0.0, min(c['w'] - 1.0, s_src))
			bt = max(0.0, min(c['h'] - 1.0, t_src))
			bx0i = int(bs); by0i = int(bt)
			bx1i = min(bx0i + 1, c['w'] - 1)
			by1i = min(by0i + 1, c['h'] - 1)
			bfx = bs - bx0i; bfy = bt - by0i

			def bpx(bsx, bsy):
				boff = c['ofs'] + txOfs(c['w'], c['h'], c['smp'], ss, bsx, bsy, sb)
				b4 = srcLt[boff:boff + 4]
				br, bg, bb, be = b4
				if be >= 128:
					be -= 256
				bscale = 2.0 ** be
				return (br * bscale, bg * bscale, bb * bscale)

			bc00 = bpx(bx0i, by0i); bc10 = bpx(bx1i, by0i)
			bc01 = bpx(bx0i, by1i); bc11 = bpx(bx1i, by1i)
			bout = []
			for bch in range(3):
				btop = bc00[bch] * (1 - bfx) + bc10[bch] * bfx
				bbot = bc01[bch] * (1 - bfx) + bc11[bch] * bfx
				bout.append(btop * (1 - bfy) + bbot * bfy)
			br, bg, bb = (max(0.0, bcv) for bcv in bout)
			bmaxc = max(br, bg, bb)
			if bmaxc < 1e-9:
				rgbe = bytes([0, 0, 0, 0])
			else:
				bexp = max(-128, min(127, math.ceil(math.log2(bmaxc / 255.0))))
				binv = 2.0 ** bexp
				rgbe = bytes([
					max(0, min(255, int(round(br / binv)))),
					max(0, min(255, int(round(bg / binv)))),
					max(0, min(255, int(round(bb / binv)))),
				]) + struct.pack('b', bexp)
			out[base + bi*4 : base + bi*4 + 4] = rgbe
	return True

def cptExact(pairs, srcFaces, tgtFaces, srcLt, tgtLt):
	srcBlkLen = blockLen(srcFaces, len(srcLt))
	modified = False
	wrLen = {}
	handled = set()
	stats = {'copied': 0, 'identical': 0, 'size_mismatch': 0, 'no_block': 0}
	for src_i, tgt_i in pairs:
		sf = srcFaces[src_i]
		tf = tgtFaces[tgt_i]
		if (sf[13], sf[14]) != (tf[13], tf[14]):
			stats['size_mismatch'] += 1
			continue
		blkLen = srcBlkLen.get(src_i, 0)
		if blkLen <= 0 or sf[9] < 0:
			stats['no_block'] += 1
			continue
		block = bytes(srcLt[sf[9] : sf[9] + blkLen])
		if tf[9] >= 0:
			existing = bytes(tgtLt[tf[9] : tf[9] + blkLen])
			if existing == block:
				stats['identical'] += 1
				handled.add(tgt_i)
				continue
		newOfs = len(tgtLt)
		tgtLt.extend(block)
		tf[9] = newOfs
		tf[8] = sf[8]
		tgtFaces[tgt_i] = tf
		wrLen[tgt_i] = blkLen
		stats['copied'] += 1
		handled.add(tgt_i)
		modified = True
	return modified, wrLen, stats, handled

def readBsp(path):
	with open(path, 'rb') as f:
		data = bytearray(f.read())
	if data[0:4] != b'VBSP':
		raise ValueError(f'{path}: not a BSP file')
	version = struct.unpack_from('<i', data, 4)[0]
	lumps = []
	off = 8
	for i in range(64):
		o, l, v = struct.unpack_from('<iii', data, off)
		cc = struct.unpack_from('<I', data, off + 12)[0]
		lumps.append([i, o, l, v, cc])
		off += 16
	return data, version, lumps

def fCent(face, verts, edges, sfEdg):
	pts = fPoly(face, verts, edges, sfEdg)
	if len(pts) == 0:
		return None
	xs = [p[0] for p in pts]
	ys = [p[1] for p in pts]
	zs = [p[2] for p in pts]
	cx = (min(xs) + max(xs)) / 2
	cy = (min(ys) + max(ys)) / 2
	cz = (min(zs) + max(zs)) / 2
	return (cx, cy, cz)

def matchF(srcFaces, tgtFaces, sv, se, sse, tv, te, tse, srcTex, tgtTex, tol):
	cellSz = max(tol, 0.001)
	tgtBkt = {}
	tgtCent = {}
	for i, f in enumerate(tgtFaces):
		c = fCent(f, tv, te, tse)
		if c is None:
			continue
		tgtCent[i] = c
		if f[5] >= 0:
			txNm = tgtTex.get(f[5])
		else:
			txNm = None
		cat = (txNm, f[13], f[14])
		cell = (round(c[0] / cellSz), round(c[1] / cellSz), round(c[2] / cellSz))
		tgtBkt.setdefault((cat, cell), []).append(i)

	srcCent = {}
	for i, f in enumerate(srcFaces):
		if f[9] < 0:
			continue
		c = fCent(f, sv, se, sse)
		if c is not None:
			srcCent[i] = c

	used = set()
	pairs = []
	for src_i, c in srcCent.items():
		f = srcFaces[src_i]
		if f[5] >= 0:
			txNm = srcTex.get(f[5])
		else:
			txNm = None
		cat = (txNm, f[13], f[14])
		cell = (round(c[0] / cellSz), round(c[1] / cellSz), round(c[2] / cellSz))
		nbrs = []
		for dx, dy, dz in product((-1, 0, 1), repeat=3):
			key = (cat, (cell[0] + dx, cell[1] + dy, cell[2] + dz))
			bkt = tgtBkt.get(key)
			if bkt:
				nbrs.extend(bkt)
		best = None
		bestD = None
		for candI in nbrs:
			if candI in used:
				continue
			c2 = tgtCent[candI]
			d = ((c[0] - c2[0])**2 + (c[1] - c2[1])**2 + (c[2] - c2[2])**2) ** 0.5
			if d <= tol and (bestD is None or d < bestD):
				best = candI
				bestD = d
		if best is not None:
			used.add(best)
			pairs.append((src_i, best))
	return pairs, used

def loadSedg(data, lumps):
	raw = lumpBytes(data, lumps[13])
	n = len(raw) // 4
	if n == 0:
		return ()
	return struct.unpack_from(f'<{n}i', raw, 0)

def resampleF(tf, tgtTi, tgtPlanes, cands, srcLt):
	ti = tgtTi[tf[5]]
	pn, pd = tgtPlanes[tf[0]]
	normal, dist = ((-pn[0], -pn[1], -pn[2]), -pd) if tf[1] else (pn, pd)
	w = tf[13] + 1
	h = tf[14] + 1
	smp = 4 if (ti['flags'] & 0x400) else 1
	nSty = stylecnt(tf[8])
	nl = vecDot(normal, normal) ** 0.5
	nn = (0.0, 0.0, 0.0) if nl < 1e-9 else (normal[0]/nl, normal[1]/nl, normal[2]/nl)
	ref = (1.0, 0.0, 0.0) if abs(nn[0]) < 0.9 else (0.0, 1.0, 0.0)
	vx = (ref[1]*nn[2] - ref[2]*nn[1], ref[2]*nn[0] - ref[0]*nn[2], ref[0]*nn[1] - ref[1]*nn[0])
	vxl = vecDot(vx, vx) ** 0.5
	uu = (0.0, 0.0, 0.0) if vxl < 1e-9 else (vx[0]/vxl, vx[1]/vxl, vx[2]/vxl)
	ua, va = uu, (nn[1]*uu[2] - nn[2]*uu[1], nn[2]*uu[0] - nn[0]*uu[2], nn[0]*uu[1] - nn[1]*uu[0])
	origin = (normal[0]*dist, normal[1]*dist, normal[2]*dist)
	ctx = (tf[11], tf[12], ti['lm_vecs'], normal, dist, origin, ua, va, cands, srcLt, smp, w, h)
	out = bytearray(w * h * smp * 4 * nSty)
	total = w * h
	hit = [[False] * w for _ in range(h)]
	hits = 0

	for sy, sx in product(range(h), range(w)):
		if txfill1(out, ctx, sy, sx, nSty):
			hit[sy][sx] = True
			hits += 1
	if 0 < hits < total:
		filled = [row[:] for row in hit]
		pending = [(y, x) for y in range(h) for x in range(w) if not hit[y][x]]
		nbrs = ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (-1, 1), (1, -1), (1, 1))
		while pending:
			prog = False
			nextPend = []
			for y, x in pending:
				src = None
				for dy, dx in nbrs:
					ny, nx = y + dy, x + dx
					if 0 <= ny < h and 0 <= nx < w and filled[ny][nx]:
						src = (ny, nx)
						break
				if src is None:
					nextPend.append((y, x))
					continue
				sy2, sx2 = src
				for si in range(nSty):
					dst_base = txOfs(w, h, smp, si, x, y, 0)
					src_base = txOfs(w, h, smp, si, sx2, sy2, 0)
					out[dst_base:dst_base + smp * 4] = out[src_base:src_base + smp * 4]
				filled[y][x] = True
				prog = True
			pending = nextPend
			if not prog:
				break
	return bytes(out), hits, total - hits

def resampleP(tgtFaces, tgtTi, tgtPlanes, tgtTex, srcCands, used, srcLt, tgtLt):
	modified = False
	wrLen = {}
	stats = {'full': 0, 'partial': 0, 'no_hit': 0, 'no_texture': 0, 'no_candidates': 0}
	for tgt_i, tf in enumerate(tgtFaces):
		if tgt_i in used or tf[5] < 0:
			continue
		txNm = tgtTex.get(tf[5])
		if txNm is None:
			stats['no_texture'] += 1
			continue
		cands = srcCands.get(txNm)
		if not cands:
			stats['no_candidates'] += 1
			continue
		block, hits, misses = resampleF(tf, tgtTi, tgtPlanes, cands, srcLt)
		if hits == 0:
			stats['no_hit'] += 1
			continue
		if misses > 0:
			stats['partial'] += 1
		else:
			stats['full'] += 1
		newOfs = len(tgtLt)
		tgtLt.extend(block)
		tf[9] = newOfs
		if all(s == 255 for s in tf[8]):
			tf[8] = bytes([0, 255, 255, 255])
		tgtFaces[tgt_i] = tf
		wrLen[tgt_i] = len(block)
		modified = True
	return modified, wrLen, stats

def isCompr(data, lump):
	return bytes(data[lump[1]:lump[1] + 4]) == b'LZMA'

def buildCandi(faces, verts, edges, sfEdg, planes, texInf, texNms):
	byTex = {}
	for i, f in enumerate(faces):
		if f[9] < 0 or f[5] < 0:
			continue
		txNm = texNms.get(f[5])
		if txNm is None:
			continue
		ti = texInf[f[5]]
		w = f[13] + 1
		h = f[14] + 1
		pn, pd = planes[f[0]]
		normal, dist = ((-pn[0], -pn[1], -pn[2]), -pd) if f[1] else (pn, pd)
		poly = fPoly(f, verts, edges, sfEdg)
		if len(poly) < 3:
			continue
		smp = 4 if (ti['flags'] & 0x400) else 1
		xs = [p[0] for p in poly]
		ys = [p[1] for p in poly]
		zs = [p[2] for p in poly]
		bbox = (min(xs)-1, min(ys)-1, min(zs)-1, max(xs)+1, max(ys)+1, max(zs)+1)
		cand = {
			'poly': poly, 'n': normal, 'd': dist, 'bbox': bbox,
			'w': w, 'h': h,
			'm0': f[11], 'm1': f[12],
			'lvs': ti['lm_vecs'], 'ofs': f[9],
			'smp': smp, 'ns': stylecnt(f[8]),
		}
		byTex.setdefault(txNm, []).append(cand)
	return byTex

def parseF(raw):
	return [list(struct.unpack_from('<HBBihhhh4sifiiiiiHHI', raw, i * 56)) for i in range(len(raw) // 56)]

def loadTexn(data, lumps):
	ti = lumpBytes(data, lumps[6])
	td = lumpBytes(data, lumps[2])
	stab = lumpBytes(data, lumps[44])
	sdat = lumpBytes(data, lumps[43])

	texDat = [struct.unpack_from('<3fiiiii', td, i * 32) for i in range(len(td) // 32)]
	nTab = len(stab) // 4
	offs = struct.unpack_from(f'<{nTab}i', stab, 0) if nTab else ()
	out = {}
	for i in range(len(ti) // 72):
		v = struct.unpack_from('<8f8fii', ti, i * 72)
		tdi = v[-1]
		if not (0 <= tdi < len(texDat)):
			continue
		sid = texDat[tdi][3]
		if not (0 <= sid < len(offs)):
			continue
		start = offs[sid]
		end = sdat.find(b'\x00', start)
		if end == -1:
			end = len(sdat)
		out[i] = sdat[start:end].decode('latin-1', 'replace')
	return out

def fPoly(face, verts, edges, sfEdg):
	first = face[3]
	pts = []
	for k in range(face[4]):
		se = sfEdg[first + k]
		v0 = edges[se][0] if se >= 0 else edges[-se][1]
		pts.append(verts[v0])
	return pts

def vecSub(a, b):
	return (a[0]-b[0], a[1]-b[1], a[2]-b[2])

def main():
	argv = sys.argv[1:]
	opts = dict(zip(argv[::2], argv[1::2]))

	source = opts.get('--source')
	target = opts.get('--target')
	out = opts.get('--out')

	if not source or not target or not out:
		sys.exit('Usage: bsp_vrad5.py --source X --target Y --out Z')

	if not os.path.isfile(source):
		sys.exit('File not found: ' + source)

	if not os.path.isfile(target):
		sys.exit('File not found: ' + target)

	doResample(source, target, out)
	
# selfnote: optimize or add	multiprocessing (excluding resampleP because it might get broken)
main()
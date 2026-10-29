"""Registro local de retículas OMR a partir de los anillos impresos.

No recibe claves ni nombres esperados. Ajusta cada región por su geometría,
usando centenares de anillos vacíos para no seguir los trazos de lápiz.
"""
import cv2
import numpy as np


def _basis(points, origin, span, quadratic):
    uv = (points - origin) / span
    u, v = uv[:, 0], uv[:, 1]
    if quadratic:
        return np.column_stack((np.ones(len(u)), u, v, u*v, u*u, v*v))
    return np.column_stack((np.ones(len(u)), u))


def register_region(gray, groups, diameter_mm, sx, sy):
    """Ajusta centros en px mediante polinomio robusto y los devuelve en mm."""
    shape = np.asarray(groups, dtype=float).shape
    expected_mm = np.asarray(groups, dtype=float).reshape(-1, 2)
    expected = expected_mm * [sx, sy]
    radius = diameter_mm * sx / 2
    # El ajuste queda dentro de media celda; nunca salta a una vecina.
    spacing = 4.1 if diameter_mm == 3 else 5.65 if len(groups) > 1 else 7.5
    limit = min(spacing * .40 * sx, radius * 1.1)
    lo = np.floor(expected.min(axis=0) - radius - limit - 2).astype(int)
    hi = np.ceil(expected.max(axis=0) + radius + limit + 2).astype(int)
    lo = np.maximum(lo, 0); hi = np.minimum(hi, [gray.shape[1], gray.shape[0]])
    crop = gray[lo[1]:hi[1], lo[0]:hi[0]]
    threshold = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY_INV, 61, 12)
    contours, _ = cv2.findContours(threshold, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        x, y, w, h = cv2.boundingRect(contour)
        perimeter = cv2.arcLength(contour, True)
        if not (radius*1.35 < w < radius*2.65 and radius*1.35 < h < radius*2.65):
            continue
        if not (.65 < w/max(h, 1) < 1.5 and area > radius*radius*.85):
            continue
        if perimeter <= 0 or 4*np.pi*area/(perimeter*perimeter) < .55:
            continue
        candidates.append((x+w/2+lo[0], y+h/2+lo[1]))
    diagnostic = {"method": "nominal", "expected": len(expected), "matched": 0,
                  "reliable": False, "max_offset_mm": 0., "fit_error_mm": None}
    if not candidates:
        return groups, diagnostic
    candidates = np.asarray(candidates)
    distances = np.sum((expected[:, None, :] - candidates[None, :, :])**2, axis=2)
    indices = distances.argmin(axis=1)
    valid = distances[np.arange(len(expected)), indices] < limit*limit
    minimum = max(4, int(len(expected)*.65))
    if int(valid.sum()) < minimum:
        diagnostic["matched"] = int(valid.sum())
        return groups, diagnostic
    observed = candidates[indices]
    quadratic = len(expected) >= 20
    origin = expected.mean(axis=0)
    span = np.maximum(np.ptp(expected, axis=0), 1.)
    basis = _basis(expected, origin, span, quadratic)
    for _ in range(5):
        coefficients, *_ = np.linalg.lstsq(basis[valid], (observed-expected)[valid], rcond=None)
        residual = np.linalg.norm(observed-expected-basis@coefficients, axis=1)
        tolerance = max(2., float(np.median(residual[valid]))*3)
        keep = valid & (residual < tolerance)
        if int(keep.sum()) < minimum or np.array_equal(keep, valid):
            break
        valid = keep
    adjustment = basis @ coefficients
    max_offset = float(np.linalg.norm(adjustment, axis=1).max())
    fit_error = float(np.median(residual[valid]))
    reliable = bool(max_offset <= limit and fit_error <= radius*.20)
    diagnostic.update(method="quadratic_rings" if quadratic else "linear_rings",
                      matched=int(valid.sum()), reliable=reliable,
                      max_offset_mm=round(max_offset/sx, 3), fit_error_mm=round(fit_error/sx, 3))
    if not reliable:
        return groups, diagnostic
    adjusted = ((expected+adjustment)/[sx, sy]).reshape(shape).tolist()
    return adjusted, diagnostic


def register_sector_guides(gray, groups, points, sx, sy):
    """Cuatro cuadrados fuera de las burbujas dan una referencia por sector.

    Las fichas anteriores no los tienen: en ese caso se conservan los centros
    nominales y el registro por anillos, sin inventar referencias.
    """
    status = {"matched": 0, "used": False}
    if len(points)!=4:
        return groups,status
    expected = np.asarray(points, dtype=float)*[sx,sy]
    observed = []
    for px,py in expected:
        side = int(2.2*sx)
        x,y = int(round(px)),int(round(py))
        crop = gray[y-side:y+side+1,x-side:x+side+1]
        if crop.shape != (2*side+1,2*side+1):
            continue
        threshold = min(170.,float(np.percentile(crop,85))-55.)
        binary = (crop<threshold).astype(np.uint8)*255
        contours,_ = cv2.findContours(binary,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        matches = []
        for contour in contours:
            bx,by,w,h = cv2.boundingRect(contour)
            if not (.75*sx<w<1.7*sx and .75*sy<h<1.7*sy and .72<w/h<1.38):
                continue
            if cv2.contourArea(contour)/(w*h)<.72:
                continue
            center = np.array([x-side+bx+w/2,y-side+by+h/2])
            distance = np.linalg.norm(center-[px,py])
            if distance<1.5*sx:
                matches.append((distance,center))
        if matches:
            observed.append((len(observed),min(matches,key=lambda item:item[0])[1]))
    status["matched"] = len(observed)
    if len(observed)!=4:
        return groups,status
    target = np.asarray([item[1] for item in observed])
    basis = np.column_stack((np.ones(4),expected))
    coefficients,*_ = np.linalg.lstsq(basis,target-expected,rcond=None)
    residual = float(np.max(np.linalg.norm(target-expected-basis@coefficients,axis=1)))
    shape = np.asarray(groups).shape
    centers = np.asarray(groups,dtype=float).reshape(-1,2)*[sx,sy]
    adjustment = np.column_stack((np.ones(len(centers)),centers))@coefficients
    if residual>.25*sx or np.max(np.linalg.norm(adjustment,axis=1))>1.8*sx:
        return groups,status
    status.update(used=True,fit_error_mm=round(residual/sx,3))
    return ((centers+adjustment)/[sx,sy]).reshape(shape).tolist(),status


def register_geometry(gray, geometry, sx, sy):
    """Registra nombres, colegio, grado y respuestas de forma independiente."""
    g = dict(geometry)
    diagnostic = {}
    for field, key, diameter in (("name", "name_columns", 3.), ("school", "school_columns", 3.),
                                   ("answers", "answer_rows", geometry["answer_diameter_mm"])):
        seeded,guides = register_sector_guides(gray,geometry[key],geometry.get("sector_guides",{}).get(field,[]),sx,sy)
        g[key], diagnostic[field] = register_region(gray, seeded, diameter, sx, sy)
        diagnostic[field]["sector_guides"] = guides
        shift = np.asarray(g[key])-np.asarray(geometry[key])
        diagnostic[field]["max_offset_mm"] = round(float(np.linalg.norm(shift,axis=-1).max()),3)
    seeded,guides = register_sector_guides(gray,[geometry["grade_centers"]],geometry.get("sector_guides",{}).get("grade",[]),sx,sy)
    groups, diagnostic["grade"] = register_region(gray, seeded,
                                                  geometry["grade_diameter_mm"], sx, sy)
    diagnostic["grade"]["sector_guides"] = guides
    shift = np.asarray(groups[0])-np.asarray(geometry["grade_centers"])
    diagnostic["grade"]["max_offset_mm"] = round(float(np.linalg.norm(shift,axis=-1).max()),3)
    g["grade_centers"] = groups[0]
    return g, diagnostic

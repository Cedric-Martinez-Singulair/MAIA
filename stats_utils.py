"""
Évolution des dommages d'une éolienne entre deux campagnes d'inspection.

Deux analyses, deux logiques distinctes :

- Fissures (cracks) : chaque fissure a une identité physique, retrouvée d'une
  campagne à l'autre par sa position dans l'espace de la pale. On compare
  fissure par fissure, avec un seuil de croissance qui dépend de la taille.

- Érosion du bord d'attaque (LEE) : les zones érodées fusionnent et se scindent,
  elles n'ont pas d'identité stable. La référence est la longueur érodée totale
  de la pale, avec un seuil unique de 5%.

Traduit de l'ancien code de génération de rapports : mêmes calculs, mêmes
seuils, sortie structurée au lieu de phrases.

Lancement :
    python stats_utils.py <turbine_id> <planification_id> [previous_planification_id]
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass, field

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

from maia_utils import TW_DB_CURSOR
from wind.wind_calculator import WINDCalculator

# =========================================================================== #
# Constantes communes
# =========================================================================== #
FACES = ["Pressure side", "Leading Edge", "Suction side", "Trailing Edge"]
FACE_RANKS = {face: rank for rank, face in enumerate(FACES)}
BLADES = ["A", "B", "C"]

# L'érosion ne concerne que le bord d'attaque et son débordement latéral.
LEE_FACES = ["Pressure side", "Leading Edge", "Suction side"]

# Classements servant à choisir la valeur dominante d'un groupe de dommages.
# Les niveaux "+" doivent y figurer : un index() sur une valeur absente lève.
WIND_RANKING = ["Error", "C1", "C2", "C2+", "C3", "M1", "M2", "M2+", "M3",
                "S1", "S2", "S2+", "S3"]
PART_RANKING = ["Coat", "Laminate", "Bonding line", "Tip end"]
SHAPE_RANKING = [None, "straight", "curved", "stripes", "multibranched"]

# WIND pour lesquels l'aggravation était prévue
WIND_GROWTH_PREDICTED = ["M2", "M3", "S2", "S3"]


# =========================================================================== #
# Constantes fissures
# =========================================================================== #
CRACK_DEFECT_TYPE = "Crack"

# Croissance significative : (taille précédente en mm) -> % minimum.
# Valeurs statistiques reprises de make_crack_size_sentence.
CRACK_GROWTH_THRESHOLDS = {
    (0, 30): 50.60,
    (30, 50): 32.40,
    (50, 100): 21.80,
    (100, 150): 19.10,
    (150, 250): 20.70,
    (250, math.inf): 11.90,
}

# Fissures mesurées en surface plutôt qu'en longueur. L'ancien code ne comparait
# pas ces cas : on remonte la taille courante sans delta.
AREA_SHAPES = ["multibranched", "stripes"]


# =========================================================================== #
# Constantes érosion
# =========================================================================== #
LEE_DEFECT_TYPE = "Leading Edge Erosion"

# Variation minimale pour être significative, en %.
LEE_GROWTH_THRESHOLD_PCT = 5.0

# Recouvrement minimal, en fraction de la zone, pour apparier deux zones érodées.
LEE_SECTION_MATCH_MIN_OVERLAP = 0.25


# =========================================================================== #
# Accès base
# =========================================================================== #
# TW_DB_CURSOR vient de maia_utils : c'est un curseur psycopg2 classique, il
# renvoie des tuples et non des dictionnaires. D'où cursor_to_dataframe plus bas.
WW_DB_PARAMS = dict(dbname="windwatch", user="singulair", password="singulair",
                    host="localhost")


# Campagnes d'inspection d'une turbine, de la plus récente à la plus ancienne.
# asset_scope garantit que la turbine était bien dans le périmètre inspecté.
PLANIFICATION_HISTORY_QUERY = """
    SELECT _asset_scope.planification_id, date
    FROM (SELECT id, date FROM planifications WHERE deleted_at IS NULL) _planifications
    JOIN (SELECT planification_id, turbine_id FROM asset_scope WHERE deleted_at IS NULL) _asset_scope
      ON _asset_scope.planification_id = _planifications.id
    JOIN (SELECT turbine_id, planification_id, count(*) FROM incident_records
          WHERE deleted_at IS NULL GROUP BY planification_id, turbine_id) _incident_records
      ON _incident_records.planification_id = _asset_scope.planification_id
     AND _incident_records.turbine_id = _asset_scope.turbine_id
    WHERE _asset_scope.turbine_id = %s
      AND date < (SELECT date FROM planifications WHERE id = %s)
    ORDER BY date DESC
    LIMIT 1
"""


# Les jointures INNER sont conservées même quand leur colonne n'est pas
# sélectionnée : elles filtrent les lignes, les retirer changerait le résultat.
# Le filtre sur le type de défaut est fait en SQL, pas en pandas.
CRACK_QUERY = """
    SELECT
        _incidents.id AS damage_id,
        components_turbines.name AS blade,
        parts.label_en AS side,
        FLOOR(radius) AS radius,
        components.label_en AS part_damaged,
        CASE WHEN criticality_id = 6 THEN 0 ELSE criticality_id END AS criticality,
        damage_axes.name AS crack_axis,
        damage_shapes.name AS crack_shape,
        wind, wind_severity, wind_desc, wind_new_desc, wind_new_sev,
        xy_coordinates, conv_pixelh_m, conv_pixelw_m,
        models.name AS model,
        models.blade_length AS blade_length,
        turbines.tower_height AS tower_height,
        turbines.altitude, turbines.distance_to_sea,
        turbines.latitude, turbines.longitude,
        turbines.soil_texture_id, turbines.temperatures,
        "Sensor_resolution_height" AS image_height,
        turbines.wind_speed,
        dp_start, dp_end, radius_img
    FROM (
        SELECT * FROM incident_records
        WHERE dismissed = FALSE AND decision_id != 0 AND deleted_at IS NULL
        AND planification_id = %s AND turbine_id = %s
    ) _incidents
    JOIN components ON components.id = _incidents.component_id
    JOIN defect_types ON defect_types.id = _incidents.defect_type_id
    JOIN analysis ON analysis.id = _incidents.analysi_id
    JOIN parts ON parts.id = _incidents.part_id
    LEFT JOIN crack_infos ON crack_infos.damage_id = _incidents.id
    LEFT JOIN damage_axes ON damage_axes.id = damage_axis_id
    LEFT JOIN damage_shapes ON damage_shapes.id = damage_shape_id
    JOIN components_turbines ON components_turbines.id = _incidents.component_turbine_id
    JOIN turbines ON turbines.id = _incidents.turbine_id
    JOIN models ON models.id = turbines.model_name
    WHERE conv_pixelh_m IS NOT NULL AND conv_pixelw_m IS NOT NULL
      AND defect_types.label_en = %s
      AND components.label_en != 'Drain Hole'
"""

# L'érosion n'a pas besoin des colonnes de position spatiale : elle s'apparie
# par plage de rayons, pas par bounding box.
EROSION_QUERY = """
    SELECT
        components_turbines.name AS blade,
        parts.label_en AS side,
        FLOOR(radius) AS radius,
        components.label_en AS part_damaged,
        CASE WHEN criticality_id = 6 THEN 0 ELSE criticality_id END AS criticality,
        wind, wind_severity, wind_desc, wind_new_desc, wind_new_sev,
        xy_coordinates, conv_pixelh_m, conv_pixelw_m,
        models.name AS model,
        models.blade_length AS blade_length,
        turbines.tower_height AS tower_height,
        turbines.altitude, turbines.distance_to_sea,
        turbines.latitude, turbines.longitude,
        turbines.soil_texture_id, turbines.temperatures,
        turbines.wind_speed
    FROM (
        SELECT * FROM incident_records
        WHERE dismissed = FALSE AND decision_id != 0 AND deleted_at IS NULL
        AND planification_id = %s AND turbine_id = %s
    ) _incidents
    JOIN components ON components.id = _incidents.component_id
    JOIN defect_types ON defect_types.id = _incidents.defect_type_id
    JOIN analysis ON analysis.id = _incidents.analysi_id
    JOIN parts ON parts.id = _incidents.part_id
    JOIN components_turbines ON components_turbines.id = _incidents.component_turbine_id
    JOIN turbines ON turbines.id = _incidents.turbine_id
    JOIN models ON models.id = turbines.model_name
    WHERE conv_pixelh_m IS NOT NULL AND conv_pixelw_m IS NOT NULL
      AND defect_types.label_en = %s
"""


def resolve_previous_planification(turbine_id, planification_id):
    """Campagne d'inspection précédant celle donnée, ou None s'il n'y en a pas."""
    TW_DB_CURSOR.execute(PLANIFICATION_HISTORY_QUERY, (turbine_id, planification_id))
    row = TW_DB_CURSOR.fetchone()
    return row[0] if row else None  # planification_id, première colonne du SELECT


# =========================================================================== #
# Préparation commune
# =========================================================================== #
def sort_data_by_face_and_radius(faces, radius, data_lists):
    """Tri en place par face (ordre FACE_RANKS) puis par rayon croissant."""
    for dmg_i in range(len(faces)):
        min_dmg_i = dmg_i
        for following_dmg_i in range(dmg_i + 1, len(faces)):
            if FACE_RANKS[faces[following_dmg_i]] < FACE_RANKS[faces[min_dmg_i]]:
                min_dmg_i = following_dmg_i
            elif (faces[following_dmg_i] == faces[min_dmg_i]
                  and radius[following_dmg_i] < radius[min_dmg_i]):
                min_dmg_i = following_dmg_i

        faces[dmg_i], faces[min_dmg_i] = faces[min_dmg_i], faces[dmg_i]
        radius[dmg_i], radius[min_dmg_i] = radius[min_dmg_i], radius[dmg_i]
        for data in data_lists:
            data[dmg_i], data[min_dmg_i] = data[min_dmg_i], data[dmg_i]


def get_dmg_size(row):
    """Dimensions physiques du dommage, en mm, depuis la bbox pixel."""
    rectangle = json.loads(str(row["xy_coordinates"]))[0]
    x1, _ = rectangle[0]
    x2, y2 = rectangle[1]
    _, y3 = rectangle[2]

    return pd.Series(
        [abs(x2 - x1) * float(row["conv_pixelw_m"]),
         abs(y3 - y2) * float(row["conv_pixelh_m"])],
        index=["width", "height"],
    )


def add_wind_and_size(blade_data):
    """Ajoute l'index WIND et les dimensions physiques à un DataFrame de pale."""
    blade_data = blade_data.copy()
    wind_calculator = WINDCalculator()
    blade_data.loc[:, ("wind_val",)] = blade_data.apply(wind_calculator.getDmgWIND, axis=1)
    blade_data.loc[:, ("width", "height")] = blade_data.apply(get_dmg_size, axis=1)
    return blade_data


def cursor_to_dataframe(cursor):
    """Curseur psycopg2 classique -> DataFrame avec les vrais noms de colonnes.

    Sans le paramètre columns, pandas numéroterait les colonnes 0, 1, 2... et
    tous les accès par nom du reste du fichier échoueraient.
    """
    rows = cursor.fetchall()
    columns = [column[0] for column in cursor.description]
    return pd.DataFrame(rows, columns=columns)


def select_blade(full_data, blade):
    """Lignes d'une pale, ou DataFrame vide."""
    if full_data.empty:
        return pd.DataFrame()
    return full_data.loc[full_data["blade"] == blade]


# =========================================================================== #
# FISSURES — regroupement
# =========================================================================== #
def boxes_overlap(box1, box2):
    """Chevauchement de deux boîtes [x_min, x_max, y_min, y_max]."""
    if box1 is None or box2 is None:
        return False
    try:
        box1 = [float(i) for i in box1]
        box2 = [float(i) for i in box2]
    except (ValueError, TypeError):
        return False
    if len(box1) != 4 or len(box2) != 4:
        return False

    x1_min, x1_max, y1_min, y1_max = box1
    x2_min, x2_max, y2_min, y2_max = box2
    if x1_min >= x2_max or x2_min >= x1_max:
        return False
    if y1_min >= y2_max or y2_min >= y1_max:
        return False
    return True


def get_bounding_box_in_space(row):
    """Position du dommage dans l'espace de la pale : c'est elle qui permet l'appariement."""
    try:
        if (pd.isna(row["radius_img"]) or not row["radius_img"] or not row["image_height"]
                or not row["conv_pixelh_m"] or not row["blade_length"]
                or not row["dp_start"] or not row["dp_end"] or not row["height"]):
            return pd.Series([None], index=["bbox_espace"])

        coords = json.loads(str(row["xy_coordinates"]))
        if not coords or not isinstance(coords[0], list):
            return pd.Series([None], index=["bbox_espace"])

        y_min = min(point[1] for point in coords[0][:-1])
        bas_bbox_m = ((y_min - float(row["image_height"]) / 2)
                      * float(row["conv_pixelh_m"])) / 1000

        blade_length = float(row["blade_length"])
        hauteur = min(float(row["radius_img"]) + bas_bbox_m, blade_length)

        return pd.Series([[
            float(row["dp_start"]),
            float(row["dp_end"]),
            hauteur,
            hauteur + float(row["height"]) / 1000,
        ]], index=["bbox_espace"])

    except Exception as exc:
        print(f"bbox impossible pour damage_id={row.get('damage_id', 'inconnu')} : {exc}")
        return pd.Series([None], index=["bbox_espace"])


def group_cracks_by_face(crack_raw_data):
    """
    Classe les fissures par face et par rayon.

    Les fissures verticales dont les rayons se chevauchent sont fusionnées en une
    section unique : on cumule la hauteur et on garde les valeurs dominantes
    (WIND, partie endommagée, sévérité, largeur, forme) ainsi qu'un id
    représentatif — celui de la fissure la plus grave de la section.
    """
    if crack_raw_data is None or crack_raw_data.empty:
        return None

    ids = list(crack_raw_data["damage_id"])
    radius = list(crack_raw_data["radius"])
    heights = list(crack_raw_data["height"])
    widths = list(crack_raw_data["width"])
    winds = list(crack_raw_data["wind_val"])
    parts = list(crack_raw_data["part_damaged"])
    sevs = list(crack_raw_data["criticality"])
    faces = list(crack_raw_data["side"])
    axis = list(crack_raw_data["crack_axis"])
    shapes = list(crack_raw_data["crack_shape"])
    bboxes = list(crack_raw_data["bbox_espace"])

    sort_data_by_face_and_radius(
        faces, radius, [ids, heights, widths, winds, parts, sevs, axis, shapes, bboxes])

    cracks = []
    section = None
    last_radius_end = -1.0

    for (dmg_id, dmg_radius, dmg_height, dmg_width, dmg_wind, dmg_part,
         dmg_sev, dmg_face, dmg_axis, dmg_shape, dmg_bbox) in zip(
            ids, radius, heights, widths, winds, parts, sevs, faces, axis, shapes, bboxes):

        if dmg_wind is None:
            dmg_wind = "Error"

        # mm -> m
        dmg_height = dmg_height / 1000.0
        dmg_width = dmg_width / 1000.0
        dmg_radius_start = float(dmg_radius) + 0.5 - dmg_height / 2
        dmg_radius_end = float(dmg_radius) + 0.5 + dmg_height / 2

        # Fissure non verticale : conservée telle quelle, jamais fusionnée
        if dmg_axis != "vertical":
            cracks.append({
                "id": dmg_id, "radius": float(dmg_radius),
                "height": dmg_height, "width": dmg_width,
                "orientation": dmg_axis, "wind": dmg_wind, "part": dmg_part,
                "sev": dmg_sev, "face": dmg_face, "shape": dmg_shape,
                "bbox_espace": dmg_bbox,
            })
            continue

        same_section = (section is not None
                        and dmg_face == section["face"]
                        and dmg_radius_start <= last_radius_end)

        if same_section:
            section["height"] += dmg_height - (last_radius_end - dmg_radius_start)

            if WIND_RANKING.index(dmg_wind) > WIND_RANKING.index(section["wind"]):
                section["wind"] = dmg_wind
                section["id"] = dmg_id
            if PART_RANKING.index(dmg_part) > PART_RANKING.index(section["part"]):
                section["part"] = dmg_part
                section["id"] = dmg_id
            if dmg_sev > section["sev"]:
                section["sev"] = dmg_sev
                section["id"] = dmg_id
            if dmg_width > section["width"]:
                section["width"] = dmg_width
            if SHAPE_RANKING.index(dmg_shape) > SHAPE_RANKING.index(section["shape"]):
                section["shape"] = dmg_shape
        else:
            if section is not None:
                cracks.append(section)

            section = {
                "id": dmg_id, "radius": dmg_radius_start,
                "height": dmg_height, "width": dmg_width,
                "orientation": "vertical", "wind": dmg_wind, "part": dmg_part,
                "sev": dmg_sev, "face": dmg_face, "shape": dmg_shape,
                "bbox_espace": dmg_bbox,
            }

        last_radius_end = dmg_radius_end

    if section is not None:
        cracks.append(section)

    cracks_by_face = {face: [] for face in FACES}
    for crack in cracks:
        if crack["id"] not in [existing["id"] for existing in cracks_by_face[crack["face"]]]:
            cracks_by_face[crack["face"]].append(crack)

    return cracks_by_face


def enrich_cracks_with_measures(cracks_by_face):
    """Ajoute la mesure saisie manuellement (base windwatch), convertie en mètres."""
    if cracks_by_face is None:
        return None

    all_cracks = [crack for face_cracks in cracks_by_face.values() for crack in face_cracks]
    if not all_cracks:
        return cracks_by_face

    crack_ids = [crack["id"] for crack in all_cracks]

    connection = psycopg2.connect(**WW_DB_PARAMS)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT id, measure FROM incident_records WHERE id = ANY(%s)",
                (crack_ids,),
            )
            measures = {row["id"]: row["measure"] for row in cursor.fetchall()}
    finally:
        connection.close()

    for crack in all_cracks:
        raw = measures.get(crack["id"])
        if raw is None:
            crack["measure"] = None
            continue
        try:
            crack["measure"] = float(str(raw).replace(" ", "").replace(",", ".")) / 1000
        except ValueError:
            crack["measure"] = None

    return cracks_by_face


def find_matching_previous_crack(crack, previous_cracks):
    """Apparie une fissure à celle de la campagne précédente, par chevauchement de bbox."""
    if not previous_cracks or crack.get("bbox_espace") is None:
        return None

    for previous in previous_cracks:
        if previous.get("orientation") != crack.get("orientation"):
            continue
        if previous.get("shape") != crack.get("shape"):
            continue
        if boxes_overlap(crack["bbox_espace"], previous.get("bbox_espace")):
            return previous

    return None


# =========================================================================== #
# FISSURES — comparaison
# =========================================================================== #
def get_crack_growth_threshold(size_m):
    """% de croissance minimum pour être significative, selon la taille précédente."""
    size_mm = size_m * 1000
    for (low, high), value in CRACK_GROWTH_THRESHOLDS.items():
        if low <= size_mm < high:
            return value
    return None


def get_crack_measurement(crack):
    """(valeur, unité) : surface en m2 pour les formes étendues, longueur en m sinon."""
    if crack.get("shape") in AREA_SHAPES:
        area = crack.get("measure")
        if area is None:
            area = crack["width"] * crack["height"]
        return area, "area"

    length = crack.get("measure")
    if length is None:
        if crack["orientation"] == "horizontal":
            length = crack["width"]
        elif crack["orientation"] == "vertical":
            length = crack["height"]
        else:
            length = math.sqrt(crack["height"] ** 2 + crack["width"] ** 2)
    return length, "length"


def measure_source(damage):
    """db = mesure saisie, image = mesure calculée depuis la photo."""
    return "db" if damage.get("measure") is not None else "image"


@dataclass
class CrackEvolution:
    damage_id: int
    previous_damage_id: int | None
    blade: str
    face: str
    status: str                       # new / grown / shrunk / stable / repaired
    orientation: str | None = None
    shape: str | None = None
    part: str | None = None
    radius: float | None = None

    measured_as: str = "length"       # length (m) ou area (m2)
    size: float | None = None
    previous_size: float | None = None
    size_delta: float | None = None
    growth_percentage: float | None = None
    growth_threshold: float | None = None
    significant: bool | None = None

    severity: int | None = None
    previous_severity: int | None = None
    wind: str | None = None
    previous_wind: str | None = None
    growth_was_predicted: bool = False

    # Un delta entre deux sources différentes n'est pas comparable.
    measure_source: str | None = None
    previous_measure_source: str | None = None


def compare_blade_cracks(cracks_by_face, previous_cracks_by_face, blade):
    """Apparie les fissures d'une pale entre deux campagnes et calcule leur évolution."""
    evolutions = []

    for face in FACES:
        current = (cracks_by_face or {}).get(face, [])
        previous = (previous_cracks_by_face or {}).get(face, [])
        matched_previous_ids = set()

        for crack in current:
            match = find_matching_previous_crack(crack, previous)
            if match is not None:
                matched_previous_ids.add(match["id"])

            size, unit = get_crack_measurement(crack)
 
            evolution = CrackEvolution(
                damage_id=crack["id"],
                previous_damage_id=match["id"] if match else None,
                blade=blade,
                face=face,
                status="new" if match is None else "stable",
                orientation=crack.get("orientation"),
                shape=crack.get("shape"),
                part=crack.get("part"),
                radius=crack.get("radius"),
                measured_as=unit,
                size=size,
                severity=crack.get("sev"),
                wind=crack.get("wind"),
                measure_source=measure_source(crack),
            )

            if match is not None:
                previous_size, _ = get_crack_measurement(match)

                evolution.previous_size = previous_size
                evolution.previous_severity = match.get("sev")
                evolution.previous_wind = match.get("wind")
                evolution.previous_measure_source = measure_source(match)

                # Les surfaces ne sont pas comparées : l'ancien code ne le faisait pas
                if unit == "length":
                    delta = size - round(previous_size, 2)
                    evolution.size_delta = delta
                    evolution.growth_percentage = (
                        0.0 if previous_size == 0 else (abs(delta) / previous_size) * 100)
                    evolution.growth_threshold = get_crack_growth_threshold(previous_size)
                    evolution.significant = (
                        evolution.growth_threshold is not None
                        and evolution.growth_percentage >= evolution.growth_threshold)

                    if evolution.significant:
                        evolution.status = "grown" if delta > 0 else "shrunk"
                        if delta > 0 and match.get("wind") in WIND_GROWTH_PREDICTED:
                            evolution.growth_was_predicted = True

            evolutions.append(evolution)

        # Fissures présentes avant, absentes maintenant
        for previous_crack in previous:
            if previous_crack["id"] in matched_previous_ids:
                continue
            previous_size, unit = get_crack_measurement(previous_crack)
            evolutions.append(CrackEvolution(
                damage_id=previous_crack["id"],
                previous_damage_id=previous_crack["id"],
                blade=blade,
                face=face,
                status="repaired",
                orientation=previous_crack.get("orientation"),
                shape=previous_crack.get("shape"),
                part=previous_crack.get("part"),
                radius=previous_crack.get("radius"),
                measured_as=unit,
                previous_size=previous_size,
                previous_severity=previous_crack.get("sev"),
                previous_wind=previous_crack.get("wind"),
                previous_measure_source=measure_source(previous_crack),
            ))

    return [e for e in evolutions if not (e.size_delta is not None and e.size_delta < 0)]


def fetch_crack_data(planification_id, turbine_id):
    TW_DB_CURSOR.execute(CRACK_QUERY, (planification_id, turbine_id, CRACK_DEFECT_TYPE))
    return cursor_to_dataframe(TW_DB_CURSOR)


def build_cracks_by_face(blade_data):
    """DataFrame d'une pale -> fissures classées par face, mesures incluses."""
    if blade_data.empty:
        return None

    cracks = add_wind_and_size(blade_data)
    cracks = cracks.join(cracks.apply(get_bounding_box_in_space, axis=1))

    return enrich_cracks_with_measures(group_cracks_by_face(cracks))


# =========================================================================== #
# ÉROSION — regroupement
# =========================================================================== #
def empty_erosion():
    """(zones, longueur totale, longueur laminate, zones intrados, zones extrados)."""
    return [], 0.0, 0.0, 0, 0


def build_lee_sections(lee_raw_data):
    """
    Regroupe les dommages d'érosion en zones continues.

    Une zone est une plage de rayons sur une même face où les dommages se
    chevauchent. On retient sa longueur érodée réelle, sa sévérité maximale, le
    WIND le plus élevé, et la partie la plus profondément atteinte.
    Toutes les sévérités sont prises en compte.
    """
    if lee_raw_data is None or lee_raw_data.empty:
        return empty_erosion()

    radius = list(lee_raw_data["radius"])
    faces = list(lee_raw_data["side"])
    heights = list(lee_raw_data["height"])
    winds = list(lee_raw_data["wind_val"])
    parts = list(lee_raw_data["part_damaged"])
    sevs = list(lee_raw_data["criticality"])

    sort_data_by_face_and_radius(faces, radius, [heights, winds, parts, sevs])

    total_lee = 0.0
    total_laminate = 0.0
    sections = []
    section = None
    last_radius_end = -1.0

    for dmg_radius, dmg_face, dmg_height, dmg_wind, dmg_part, dmg_sev in zip(
            radius, faces, heights, winds, parts, sevs):

        if dmg_face not in LEE_FACES or dmg_height is None:
            continue
        if dmg_wind is None:
            dmg_wind = "Error"

        dmg_height = dmg_height / 1000.0
        dmg_radius_start = float(dmg_radius) + 0.5 - dmg_height / 2
        dmg_radius_end = float(dmg_radius) + 0.5 + dmg_height / 2

        same_section = (section is not None
                        and dmg_face == section["face"]
                        and dmg_radius_start <= last_radius_end)

        # Seule la portion non déjà couverte compte : sans ça, deux dommages qui
        # se chevauchent gonfleraient artificiellement la longueur érodée.
        if same_section:
            added = dmg_height - (min(dmg_radius_end, last_radius_end) - dmg_radius_start)
        else:
            added = dmg_height

        if dmg_face == "Leading Edge":
            total_lee += added
        if dmg_part == "Laminate":
            total_laminate += added

        if same_section:
            section["height"] += added
            section["end"] = dmg_radius_end

            if WIND_RANKING.index(dmg_wind) > WIND_RANKING.index(section["wind"]):
                section["wind"] = dmg_wind
            if dmg_sev > section["max_sev"]:
                section["max_sev"] = dmg_sev
                section["deepest_part"] = dmg_part
        else:
            if section is not None:
                sections.append(section)

            section = {"start": dmg_radius_start, "end": dmg_radius_end,
                       "face": dmg_face, "height": dmg_height, "wind": dmg_wind,
                       "max_sev": dmg_sev, "deepest_part": dmg_part}

        last_radius_end = dmg_radius_end

    if section is not None:
        sections.append(section)

    by_face = {face: [] for face in LEE_FACES}
    for item in sections:
        by_face[item["face"]].append(item)

    compute_lateral_spread(by_face)

    return (by_face["Leading Edge"], total_lee, total_laminate,
            len(by_face["Pressure side"]), len(by_face["Suction side"]))


def compute_lateral_spread(by_face):
    """
    Pour chaque zone du bord d'attaque, quelle fraction déborde sur les flancs.

    C'est le signal que l'érosion ne progresse plus seulement en longueur mais
    aussi en largeur, ce qui change la nature de la réparation.
    """
    for le_section in by_face["Leading Edge"]:
        le_size = le_section["end"] - le_section["start"]

        for side in ["Pressure side", "Suction side"]:
            le_section[side] = 0.0
            if le_size <= 0:
                continue
            for side_section in by_face[side]:
                overlap = (min(side_section["end"], le_section["end"])
                           - max(side_section["start"], le_section["start"]))
                if overlap > 0:
                    le_section[side] += overlap / le_size


# =========================================================================== #
# ÉROSION — comparaison
# =========================================================================== #
def find_matching_previous_section(section, previous_sections):
    """Apparie deux zones érodées par recouvrement de leur plage de rayons."""
    size = section["end"] - section["start"]
    if size <= 0:
        return None

    best = None
    best_overlap = 0.0

    for previous in previous_sections:
        overlap = (min(previous["end"], section["end"])
                   - max(previous["start"], section["start"]))
        if overlap > best_overlap:
            best_overlap = overlap
            best = previous

    if best is None or best_overlap / size < LEE_SECTION_MATCH_MIN_OVERLAP:
        return None
    return best


def classify_erosion_change(size, previous_size):
    """(status, delta, pourcentage) selon le seuil de 5%."""
    delta = size - previous_size
    percentage = 0.0 if previous_size == 0 else (abs(delta) / previous_size) * 100

    if percentage < LEE_GROWTH_THRESHOLD_PCT:
        return "stable", delta, percentage
    return ("grown" if delta > 0 else "shrunk"), delta, percentage


@dataclass
class ErodedAreaEvolution:
    """Une zone érodée du bord d'attaque, comparée à la campagne précédente."""

    blade: str
    radius_start: float
    radius_end: float
    status: str                        # new / grown / shrunk / stable / repaired
    eroded_length: float | None = None
    previous_eroded_length: float | None = None
    length_delta: float | None = None
    growth_percentage: float | None = None

    deepest_part: str | None = None    # Coat / Laminate / LE Tape
    max_severity: int | None = None
    previous_max_severity: int | None = None
    wind: str | None = None
    previous_wind: str | None = None
    growth_was_predicted: bool = False

    spread_pressure_side: float | None = None
    spread_suction_side: float | None = None


@dataclass
class BladeErosionEvolution:
    """Bilan d'une pale : c'est la longueur érodée totale qui fait référence."""

    blade: str
    status: str                        # new / grown / shrunk / stable / repaired
    total_eroded_length: float
    previous_total_eroded_length: float
    length_delta: float
    growth_percentage: float

    eroded_areas_count: int = 0
    previous_eroded_areas_count: int = 0

    laminate_length: float = 0.0
    previous_laminate_length: float = 0.0

    spreads_to_pressure_side: bool = False
    spreads_to_suction_side: bool = False
    previously_spread_to_pressure_side: bool = False
    previously_spread_to_suction_side: bool = False

    eroded_areas: list = field(default_factory=list)


def compare_blade_erosion(current, previous, blade, include_areas=True):
    """Compare le bilan d'érosion d'une pale entre deux campagnes."""
    sections, total, laminate, nb_ps, nb_ss = current
    (previous_sections, previous_total, previous_laminate,
     previous_nb_ps, previous_nb_ss) = previous

    if total == 0 and previous_total == 0:
        return None

    if previous_total == 0:
        status, delta, percentage = "new", total, 100.0
    elif total == 0:
        status, delta, percentage = "repaired", -previous_total, 100.0
    else:
        status, delta, percentage = classify_erosion_change(total, previous_total)

    evolution = BladeErosionEvolution(
        blade=blade,
        status=status,
        total_eroded_length=total,
        previous_total_eroded_length=previous_total,
        length_delta=delta,
        growth_percentage=percentage,
        eroded_areas_count=len(sections),
        previous_eroded_areas_count=len(previous_sections),
        laminate_length=laminate,
        previous_laminate_length=previous_laminate,
        spreads_to_pressure_side=nb_ps > 0,
        spreads_to_suction_side=nb_ss > 0,
        previously_spread_to_pressure_side=previous_nb_ps > 0,
        previously_spread_to_suction_side=previous_nb_ss > 0,
    )

    if include_areas:
        evolution.eroded_areas = compare_eroded_areas(sections, previous_sections, blade)

    return evolution


def compare_eroded_areas(sections, previous_sections, blade):
    """Détail zone par zone, apparié par recouvrement de rayons."""
    areas = []
    matched = set()

    for section in sections:
        match = find_matching_previous_section(section, previous_sections)
        if match is not None:
            matched.add(id(match))

        area = ErodedAreaEvolution(
            blade=blade,
            radius_start=section["start"],
            radius_end=section["end"],
            status="new" if match is None else "stable",
            eroded_length=section["height"],
            deepest_part=section["deepest_part"],
            max_severity=section["max_sev"],
            wind=section["wind"],
            spread_pressure_side=section.get("Pressure side"),
            spread_suction_side=section.get("Suction side"),
        )

        if match is not None:
            status, delta, percentage = classify_erosion_change(
                section["height"], match["height"])

            area.status = status
            area.previous_eroded_length = match["height"]
            area.length_delta = delta
            area.growth_percentage = percentage
            area.previous_max_severity = match["max_sev"]
            area.previous_wind = match["wind"]

            if status == "grown" and match["wind"] in WIND_GROWTH_PREDICTED:
                area.growth_was_predicted = True

        areas.append(area)

    for previous_section in previous_sections:
        if id(previous_section) in matched:
            continue
        areas.append(ErodedAreaEvolution(
            blade=blade,
            radius_start=previous_section["start"],
            radius_end=previous_section["end"],
            status="repaired",
            previous_eroded_length=previous_section["height"],
            previous_max_severity=previous_section["max_sev"],
            previous_wind=previous_section["wind"],
        ))

    return areas


def fetch_erosion_data(planification_id, turbine_id):
    TW_DB_CURSOR.execute(EROSION_QUERY, (planification_id, turbine_id, LEE_DEFECT_TYPE))
    return cursor_to_dataframe(TW_DB_CURSOR)


def build_blade_erosion(blade_data):
    """DataFrame d'une pale -> zones érodées et totaux."""
    if blade_data.empty:
        return empty_erosion()
    return build_lee_sections(add_wind_and_size(blade_data))


# =========================================================================== #
# Analyses
# =========================================================================== #
def analyse_crack_evolution(turbine_id, planification_id, previous_planification_id,
                            blade=None, only_changed=False):
    """Compare les fissures des pales entre deux planifications."""
    full_data = fetch_crack_data(planification_id, turbine_id)
    previous_full_data = fetch_crack_data(previous_planification_id, turbine_id)

    evolutions = []

    for current_blade in ([blade] if blade else BLADES):
        blade_data = select_blade(full_data, current_blade)
        previous_blade_data = select_blade(previous_full_data, current_blade)

        if blade_data.empty and previous_blade_data.empty:
            continue

        evolutions.extend(compare_blade_cracks(
            build_cracks_by_face(blade_data),
            build_cracks_by_face(previous_blade_data),
            current_blade,
        ))

    if only_changed:
        evolutions = [e for e in evolutions if e.status != "stable"]

    return evolutions


def analyse_erosion_evolution(turbine_id, planification_id, previous_planification_id,
                              blade=None, include_areas=True):
    """Compare l'érosion du bord d'attaque des pales entre deux planifications."""
    full_data = fetch_erosion_data(planification_id, turbine_id)
    previous_full_data = fetch_erosion_data(previous_planification_id, turbine_id)

    evolutions = []

    for current_blade in ([blade] if blade else BLADES):
        evolution = compare_blade_erosion(
            build_blade_erosion(select_blade(full_data, current_blade)),
            build_blade_erosion(select_blade(previous_full_data, current_blade)),
            current_blade,
            include_areas,
        )
        if evolution is not None:
            evolutions.append(evolution)

    return evolutions

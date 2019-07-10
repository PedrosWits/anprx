"""Methods for wrangling and pre-processing anpr data and related datasets."""
# ------------------------------------------------------------------------------

from   .utils               import log
from   .utils               import save_fig
from   .utils               import settings
from   .plot                import plot_G
from   .helpers             import add_edge_directions
from   .helpers             import get_quadrant
from   .helpers             import cut
from   .helpers             import common_words
from   .core                import get_meanpoint
from   .core                import edges_by_distance
from   .core                import Point
from   .core                import RelativeMargins
from   .core                import bbox_from_points

import re
import math
import time
import numpy                as np
import osmnx                as ox
import pandas               as pd
import networkx             as nx
import geopandas            as gpd
import logging              as lg
import shapely.geometry     as geometry
from   collections          import OrderedDict
from   itertools            import chain
from   functools            import reduce

# ------------------------------------------------------------------------------

direction_regex = (r'(East\/West|North\/South|Northbound|Eastbound|'
                    'Southbound|Westbound|Northhbound|Southhbound)')
address_regex   = (r'(East\/West|North\/South|Northbound|Eastbound|'
                    'Southbound|Westbound|Site \d|Camera \d|Camera|Site)')
road_ref_regex  = r'(A\d+|B\d+|C\d+)'
car_park_regex  = r'(car park)'
directions_separator = "/|&"

def infer_road_attr(
    descriptions,
    direction_regex      = direction_regex,
    address_regex        = address_regex,
    road_ref_regex       = road_ref_regex,
    car_park_regex       = car_park_regex,
    directions_separator = directions_separator,
):
    """
    Hello
    """
    directions = descriptions.str.extract(direction_regex,
                                          flags = re.IGNORECASE,
                                          expand = False)

    both_directions = directions.str.contains(directions_separator, na=False)

    directions_wrangled = directions \
        .dropna() \
        .str.split(directions_separator) \
        .apply(lambda x: x[0][0] if len(x) == 1 \
                         else "{}-{}".format(x[0][0], x[1][0]))

    directions = pd.concat([directions[directions.isnull()],
                            directions_wrangled])\
                    .sort_index()

    road_refs = descriptions.str.extract(road_ref_regex,
                                         flags = re.IGNORECASE,
                                         expand = False)

    addresses = descriptions.str.replace(address_regex, '',regex = True)\
                                .replace(' +', ' ', regex = True)\
                                .replace('^ +', '', regex = True)

    car_parks = descriptions.str.contains(car_park_regex, case = False)

    return pd.DataFrame({
        'direction'      : directions,
        'both_directions': both_directions,
        'ref'            : road_refs,
        'address'        : addresses,
        'is_carpark'     : car_parks
    })

def filter_by_attr_distance(
    row,
    df,
    filter_by_max_common = True,
    filter_by_same_ref = True,
    filter_by_same_direction = True,
    distance_threshold = 50.0
):
    """
    Hello
    """
    cdf = df

    if 'address' in row:
        c_address = row['address']
        c_ref     = row['ref']

        cdf = cdf\
            .assign(common_address_words = cdf.address\
                .apply(lambda other: common_words(c_address, other, " ")))\
            .assign(same_ref = cdf.ref.apply(lambda other: c_ref == other))

        if filter_by_max_common:
            max_common = cdf['common_address_words'].max()
            cdf        = cdf[cdf.common_address_words == max_common]

        if filter_by_same_ref:
            cdf = cdf[cdf.same_ref == True]

    if len(cdf) == 0:
        log("Camera {}: No other cameras with the same address and ref."\
                .format(row['id']),
            level = lg.INFO)
        return cdf

    # filter by direction
    oldlen = len(cdf)

    if 'direction' in row:
        c_dir = row['direction']
        cdf = cdf.assign(same_dir = cdf.direction\
                    .apply(lambda other: c_dir == other))

        if filter_by_same_direction:
            cdf = cdf[cdf.same_dir == True]

    if len(cdf) == 0:
        log(("Camera {}: found {} other cameras with the same address but none "
             "pointing in the same direction.")\
                .format(row['id'], oldlen),
            level = lg.INFO)
        return cdf

    oldlen = len(cdf)

    c_p       = row['geometry']
    cdf = cdf.assign(dist = cdf.geometry.apply(lambda other: c_p.distance(other)))
    cdf = cdf[cdf.dist <= distance_threshold]

    log_cols = ['id','address','ref','direction','lat','lon']

    if len(cdf) == 0:
        log(("Camera {}: found {} other cameras with the same address and "
             "direction, but none within {} meters.")\
                .format(row['id'], oldlen, distance_threshold),
            level = lg.INFO)
    else:
        log(("Camera {}: found {} other cameras that can be merged together\n{}")\
                .format(row['id'], len(cdf),
                        pd.concat([ pd.DataFrame(dict(row[log_cols]),
                                                index = [0]),
                                    cdf[log_cols] ],
                                  axis = 0, ignore_index=True)),
            level = lg.INFO)

    return cdf


def wrangle_cameras(
    cameras,
    test_camera_col       = "name",
    is_commissioned_col   = "is_commissioned",
    road_attr_col         = "description",
    drop_car_park         = True,
    drop_na_direction     = True,
    direction_regex       = direction_regex,
    address_regex         = address_regex,
    road_ref_regex        = road_ref_regex,
    car_park_regex        = car_park_regex,
    distance_threshold    = 50.0,
    sort_by               = "id",
    utm_crs               = {'datum': 'WGS84',
                             'ellps': 'WGS84',
                             'proj' : 'utm',
                             'units': 'm'}
):
    """
    Wrangles a raw dataset of ANPR camera data.

    Parameters
    ----------
    cameras : pd.DataFrame
        dataframe

    Returns
    -------
    pd.DataFrame
        The same point given by latitude and longitude.

    """
    nrows = len(cameras)
    log("Wrangling cameras dataset with {} rows and colnames: {}"\
            .format(nrows, ",".join(cameras.columns.values)),
        level = lg.INFO)

    start_time = time.time()

    mandatory_columns = {'id', 'lat', 'lon'}

    log("Checking if input dataframe contains mandatory columns {}."\
            .format(mandatory_columns),
        level = lg.INFO)

    cols = set(cameras.columns.values)

    assert mandatory_columns.issubset(cols)

    other_cols = set(
        filter(lambda x: x is None,
               [test_camera_col, is_commissioned_col, road_attr_col]))

    unrecognised_cols = cols - mandatory_columns - other_cols
    if len(unrecognised_cols) > 0:
        log("Skipping {} unrecognised columns: {}."\
                .format(len(unrecognised_cols), unrecognised_cols),
            level = lg.INFO)

    # Some asserts about the input data
    log("Checking if 'id' is unique."\
            .format(mandatory_columns),
        level = lg.INFO)
    assert len(cameras['id']) == len(cameras['id'].unique())

    # Find and drop cameras that are labelled as "test"
    if test_camera_col:
        oldlen = len(cameras)

        cameras = cameras.assign(
            is_test = cameras[test_camera_col].str.contains('test',
                                                            case = False))
        cameras = cameras[(cameras.is_test == False)]
        cameras = cameras.drop(columns = 'is_test')

        log("Dropping {} rows with 'test' in name"\
                .format(oldlen - len(cameras)),
            level = lg.INFO)

    # Find and drop cameras that not commissioned
    if is_commissioned_col:
        oldlen = len(cameras)

        cameras = cameras[cameras[is_commissioned_col] == True]
        cameras = cameras.drop(is_commissioned_col, axis = 1)

        log("Dropping {} rows with '{}' == False."\
                .format(oldlen - len(cameras), is_commissioned_col),
            level = lg.INFO)

    # Infer road attributes
    if road_attr_col:
        log(("Inferring new cols 'direction', 'address', 'ref' and 'is_carpark'"
             " based on column '{}'.")\
                .format(road_attr_col),
            level = lg.INFO)

        road_attr = infer_road_attr(cameras[road_attr_col])
        cameras   = pd.concat([cameras, road_attr], axis = 1)
        # Find and drop cameras that are in car parks
        if drop_car_park:
            oldlen = len(cameras)

            cameras = cameras[(cameras.is_carpark == False)]
            cameras = cameras.drop(columns = 'is_carpark')

            log("Dropping {} rows with 'is_carpark' == True."\
                    .format(oldlen - len(cameras)),
                level = lg.INFO)

        count_na_direction = len(cameras[pd.isna(cameras.direction)])
        log(("There are {} cameras (ids = {}) with missing direction.")\
                .format(count_na_direction,
                        cameras[pd.isna(cameras.direction)]['id'].tolist()),
            level = lg.WARNING)

        if drop_na_direction:
            cameras = cameras[~pd.isna(cameras.direction)]
            log(("Dropping {} cameras with no direction.")\
                    .format(count_na_direction),
                level = lg.WARNING)

    if len(cameras) == 0:
        log("No more cameras to process..", level = lg.WARNING)
        return pd.DataFrame(columns = cameras.columns.values)

    # Project coordinates
    log("Projecting cameras to utm and adding geometry column.",
        level = lg.INFO)

    cameras.reset_index(drop=True, inplace = True)

    camera_points = gpd.GeoSeries(
        [ geometry.Point(x,y) for x, y in zip(
            cameras['lon'],
            cameras['lat'])
        ])

    cameras_geodf = gpd.GeoDataFrame(index    = cameras.index,
                                     geometry = camera_points)
    cameras_geodf.crs = {'init' :'epsg:4326'}

    avg_longitude = cameras_geodf['geometry'].unary_union.centroid.x
    utm_zone = int(math.floor((avg_longitude + 180) / 6.) + 1)
    utm_crs["zone"] = utm_zone

    proj_cameras = cameras_geodf.to_crs(utm_crs)
    cameras = proj_cameras.join(cameras, how = 'inner')

    # Hard bit: merge cameras close by
    # ---------
    # First identify merges
    # ---------
    # Some roads have multiple cameras, side by side, one for each lane
    # When this happens, we should merge the nearby cameras, pointing in the
    # same direction, as a single camera. To do this, we compare the road attr
    # and compute the distance to every camera with the same road attrs

    to_merge = []
    for index, camera in cameras.iterrows():
        all_other_cameras = cameras.drop(index = index, axis = 0)

        within_distance = filter_by_attr_distance(
            camera,
            all_other_cameras,
            distance_threshold = distance_threshold)

        if len(within_distance) > 0:
            to_merge.append(
                frozenset(within_distance.index.values.tolist() + [index]))

    to_merge = list(map(lambda x: tuple(x), set(to_merge)))

    log("Identified the following camera merges: {}"\
            .format(to_merge),
        level = lg.INFO)

    # ---------
    # Actual merge
    # ---------
    oldlen = len(cameras)

    elements = set(map(lambda x: x[0], to_merge)) | \
               set(map(lambda x: x[1], to_merge))

    unaffected_cameras = cameras.drop(index = elements, axis = 0)

    cameras_list = []
    for id1,id2 in to_merge:
        c1 = dict(cameras.loc[id1])
        c2 = dict(cameras.loc[id2])

        c1['id'] = "{}-{}".format(c1['id'], c2['id'])
        c1['name'] = "{}-{}".format(c1['name'], c2['name'])
        # we use one of the geometries. Using the centroid of the 2 points might
        # not be a good idea as this might negatively impact the merge of
        # cameras onto the road network

        # inefficient but works
        newdf = pd.DataFrame(columns = c1.keys())
        newdf.loc[id1] = list(c1.values())

        cameras_list.append(newdf)

    cameras_list.append(unaffected_cameras)
    cameras = pd.concat(cameras_list, axis = 0)

    log("Merged {} cameras that were in the same location as other cameras."\
            .format(oldlen - len(cameras)),
        level = lg.INFO)

    # Sorting and resetting index

    log("Sorting by {} and resetting index."\
            .format(sort_by),
        level = lg.INFO)

    # sort and reset_index
    cameras = cameras.sort_values(by=[sort_by])
    cameras.reset_index(drop=True, inplace=True)

    log("Wrangled cameras in {:,.3f} seconds. Dropped {} rows, total is {}."\
            .format(time.time()-start_time, nrows - len(cameras), len(cameras)),
        level = lg.INFO)

    return cameras

# ------------------------------------------------------------------------------

def network_from_cameras(
    cameras,
    filter_residential = True,
    clean_intersections = False,
    tolerance = 30,
    min_bbox_length_km = 0.2,
    max_bbox_length_km = 50,
    bbox_margin = 0.10,
    plot = True,
    **plot_kwargs
):
    """
    Get the road graph encompassing a set of ANPR cameras from OpenStreetMap.
    """
    log("Getting road network for cameras dataset of length {}"\
            .format(len(cameras)),
        level = lg.INFO)

    start_time = time.time()

    if filter_residential:
        osm_road_filter = \
            ('["area"!~"yes"]["highway"~"motorway|trunk|primary|'
             'secondary|tertiary"]["motor_vehicle"!~"no"]["motorcar"!~"no"]'
             '["access"!~"private"]')
    else:
        osm_road_filter = None

    xs = [p.x for p in cameras['geometry']]
    ys = [p.y for p in cameras['geometry']]

    center_x = min(xs) + (max(xs) - min(xs))/2
    center_y = min(ys) + (max(ys) - min(ys))/2

    dists_center = list(map(
        lambda p: math.sqrt((p.x - center_x) ** 2 + (p.y - center_y) ** 2),
        cameras['geometry']))

    length = max(dists_center)

    # 10% margin
    length = length + bbox_margin * length

    # if length is still very small
    if length < min_bbox_length_km * 1000:
        length = min_bbox_length_km * 1000

    elif length > max_bbox_length_km * 1000:
        raise ValueError("This exception prevents accidently querying large networks")

    # but now we need center point in lat,lon
    points = [Point(lat,lng) for lat,lng in zip(cameras['lat'], cameras['lon'])]

    lat = cameras['lat']
    lon = cameras['lon']

    center_lat = min(lat) + (max(lat) - min(lat))/2
    center_lon = min(lon) + (max(lon) - min(lon))/2

    log("Center point = {}, distance = {}"\
            .format((center_lat, center_lon), length),
        level = lg.INFO)
    checkpoint = time.time()

    G = ox.graph_from_point(
        center_point = (center_lat, center_lon),
        distance = length,
        custom_filter = osm_road_filter
    )

    log("Returned road graph in {:,.3f} sec"\
            .format(time.time() - start_time),
        level = lg.INFO)
    checkpoint = time.time()

    G = add_edge_directions(G)

    G = ox.project_graph(G)

    log("Added edge directions and projected graph in {:,.3f} sec"\
            .format(time.time() - checkpoint),
        level = lg.INFO)
    checkpoint = time.time()

    if plot:
        _, _, filename = plot_G(
            G,
            name = "road_graph",
            **plot_kwargs)

        log("Saved image of the road graph to disk {} in {:,.3f} sec"\
                .format(filename, time.time() - checkpoint),
            level = lg.INFO)
        checkpoint = time.time()

    if clean_intersections:
        G = ox.clean_intersections(G, tolerance = tolerance)

        log("Cleaned intersections (tol = {}) in {:,.3f} sec"\
                .format(tolerance, time.time() - checkpoint),
            level = lg.INFO)
        checkpoint = time.time()

        if plot:
            _, _, filename = plot_G(
                G,
                name = "road_graph_cleaned",
                **plot_kwargs)

            log("Saved image of cleaned road graph to disk {} in {:,.3f} sec"\
                    .format(filename, time.time() - checkpoint),
                level = lg.INFO)
            checkpoint = time.time()

    log("Retrieved road network from points in {:,.3f} sec"\
        .format(time.time() - start_time))

    if plot:
        if 'geometry' in cameras.columns:

            plot_kwargs['label'] = 'cameras'
            plot_kwargs['legend'] = True

            _, _, filename = plot_G(
                G,
                name = "road_graph_cameras",
                points = (cameras['geometry'].x, cameras['geometry'].y),
                **plot_kwargs)

            log("Saved image of cleaned road graph to disk {} in {:,.3f} sec"\
                    .format(filename, time.time() - checkpoint),
                level = lg.INFO)

            close_up_plots(G, cameras, **plot_kwargs)

        else:
            log(("Skipped making image of road graph with cameras because "
                "no geometry was available"),
                level = lg.WARNING)

    return G

def close_up_plots(
    G,
    cameras = None,
    bbox_distance = 400,
    **plot_kwargs
):
    """
    Close up plots of every camera.

    If cameras is provided, close-up plots of cameras are done using geometry
    coordinates for each row in dataframe (unmerged network).

    If cameras is not provided, then
    """
    filenames = []

    if cameras is None:
        merged = True
        subdir = "cameras/merged"
        points = ([d['x'] for _, d in G.nodes(data = True) if d['is_camera']],
                  [d['y'] for _, d in G.nodes(data = True) if d['is_camera']])

        camera_nodes = [ data for node, data in G.nodes(data = True) \
                         if data['is_camera'] ]

        ids = [ data['id'] for node, data in G.nodes(data = True)\
                if data['is_camera'] ]

        node_ids = [ node for node, data in G.nodes(data = True)\
                     if data['is_camera'] ]

        cameras = pd.DataFrame(camera_nodes).assign(node = node_ids)
    else:
        merged = False
        subdir = "cameras/unmerged"
        points = ([p.x for p in cameras['geometry']],
                  [p.y for p in cameras['geometry']])
        ids    = cameras['id'].tolist()

    for index, row in cameras.iterrows():

        if merged:
            x = row['x']
            y = row['y']
        else:
            x = row['geometry'].x
            y = row['geometry'].y

        bbox = (y + bbox_distance, y - bbox_distance,
                x + bbox_distance, x - bbox_distance)

        # filter points outside the bounding box
        poly = geometry.box(x - bbox_distance, y - bbox_distance,
                            x + bbox_distance, y + bbox_distance)

        subpoints = [ (id,x,y) for id,x,y in zip(ids, points[0], points[1]) \
                      if geometry.Point((x,y)).within(poly)]

        subids, tmp0, tmp1 = zip(*subpoints)
        subpoints = (tmp0, tmp1)

        checkpoint = time.time()

        _, _, filename = plot_G(
            G,
            subdir = subdir,
            name = row['id'],
            points = subpoints,
            bbox = bbox,
            labels = subids,
            **plot_kwargs
        )

        log("Saved image of close up camera {} to disk {} in {:,.3f} sec"\
                .format(row['id'], filename, time.time() - checkpoint),
            level = lg.INFO)

        filenames.append(filename)

    return filenames


def camera_candidate_edges(
    G,
    camera,
    camera_range = 40.0
):
    """
    Identify valid candidate edges
    """

    direction = camera['direction']
    address = camera['address']
    x = camera['geometry'].x
    y = camera['geometry'].y

    address_words = set(address.split(" "))

    # Get nearest edges to
    nedges = edges_by_distance(G, (y,x))

    # identify the edges that are in range
    distances = np.array(list(map(lambda x: x[1], nedges)))

    out_of_range_idx = np.argwhere(distances > camera_range)\
                         .reshape(-1)[0]
    in_range_slice = slice(0, (out_of_range_idx))

    candidate_edges = nedges[in_range_slice]

    if len(candidate_edges) == 0:
        return candidate_edges

    # filter out candidates not pointing in same direction and re-arrange
    # by address
    geometries = list(map(lambda x: x[0][0], nedges[in_range_slice]))
    u_nodes = list(map(lambda x: x[0][1], candidate_edges))
    v_nodes = list(map(lambda x: x[0][2], candidate_edges))

    points_u = [ (G.node[u]['x'], (G.node[u]['y'])) for u in u_nodes]
    points_v = [ (G.node[v]['x'], (G.node[v]['y'])) for v in v_nodes]

    uv_vecs = [ (pv[0] - pu[0], pv[1] - pu[1]) \
                for pu,pv in zip(points_u, points_v)]

    uv_dirs = [ get_quadrant(np.rad2deg(
                    math.atan2(vec[1], vec[0]))) for vec in uv_vecs]

    same_dir = [ direction in uv_dir for uv_dir in uv_dirs ]

    # Names and refs might be None, str or list, must handle each case
    uv_refs = []
    uv_addresses = []
    for u,v in zip(u_nodes, v_nodes):
        attr = G.edges[u,v,0]
        if 'name' in attr:
            if isinstance(attr['name'], str):
                edge_address = attr['name'].split(" ")
            else:
                edge_address = " ".join(attr['name']).split(" ")
        else:
            edge_address = []
        uv_addresses.append(edge_address)

        if 'ref' in attr:
            if isinstance(attr['ref'], str):
                edge_ref = [attr['ref']]
            else:
                edge_ref = " ".join(attr['ref']).split(" ")
        else:
            edge_ref = []
        uv_refs.append(edge_ref)

    same_ref = [ len(set(uv_ref) & (address_words)) \
                 for uv_ref in uv_refs ]

    same_address = [len(set(uv_address) & (address_words)) \
                    for uv_address in uv_addresses ]

    candidates = \
        pd.DataFrame({
            'u' : u_nodes,
            'v' : v_nodes,
            'distance' : distances[in_range_slice],
            'point_u' : points_u,
            'point_v' : points_v,
            'geometry' : geometries,
            'dir_uv' : uv_dirs,
            'same_dir' : same_dir,
            'ref' : uv_refs,
            'same_ref' : same_ref,
            'address' : uv_addresses,
            'same_address' : same_address}
        )

    return candidates


def identify_cameras_merge(
    G,
    cameras,
    camera_range = 40.0
):
    """
    Identify camera merges
    """
    # Input validation
    required_cols = {'id', 'geometry', 'direction', 'address'}

    if not required_cols.issubset(set(cameras.columns.values)):
        raise ValueError("The following required columns are not available: {}"\
                         .format(required_cols))

    edges_to_remove = []
    edges_to_add = {}
    cameras_to_add = {}
    untreated = []
    untreatable = []

    # We assume that there is a road within 40 meters of each camera
    for index, row in cameras.iterrows():
        id = row['id']

        candidates = camera_candidate_edges(G, row, camera_range)

        if len(candidates) == 0:
            log(("({}) - Camera {} has no edge within {} meters. "
                 "Appending to untreatable list.")\
                    .format(index, id, camera_range),
                level = lg.WARNING)
            untreatable.append(index)
            continue

        # filter candidates not same dir and arrange by same address
        valid_candidates = candidates[candidates.same_dir == True]
        valid_candidates = valid_candidates.assign(
            same_ref_address = (valid_candidates.same_ref     > 0) &
                               (valid_candidates.same_address > 0))

        valid_candidates = valid_candidates.sort_values(
            by = ['same_ref_address', 'same_address', 'same_ref', 'distance'],
            ascending = [False, False, False, True])

        # If there was no suitable candidate
        if len(valid_candidates) == 0:
            log(("({}) - Camera {} has 0 valid candidate edges pointing in the same "
                 "direction as the camera. Flagging as untreatable.")\
                    .format(index, id),
                level = lg.ERROR)
            untreatable.append(index)
            continue

        log(("({}) - Camera {} has {}/{} edges pointing in the same direction "
             "{} and {} edges with the same reference and address, ({} same "
             "ref, {} same address). It's located on {}")\
                .format(index, id, len(valid_candidates), len(candidates),
                    row['direction'],
                    len(valid_candidates[valid_candidates.same_ref_address]),
                    len(valid_candidates[valid_candidates.same_ref     > 0]),
                    len(valid_candidates[valid_candidates.same_address > 0]),
                        row['address']),
            level = lg.INFO)

        chosen_edge = valid_candidates.iloc[0]

        line = chosen_edge['geometry']
        edge = (chosen_edge['u'], chosen_edge['v'])
        distance = chosen_edge['distance']
        ref = " ".join(chosen_edge['ref'])
        edge_address = " ".join(chosen_edge['address'])
        edge_dir = chosen_edge['dir_uv']

        log(("({}) - Camera {}: Picking top valid candidate edge {}. "
             "Distance: {:,.2f} meters, ref: {}, address: {}")\
                .format(index, id, edge, distance, ref, edge_address),
            level = lg.INFO)

        # Is this edge already assigned to a different camera?
        if edge in edges_to_remove:
            log(("({}) - Camera {}: another camera is already pointing at "
                "this edge. Appending to untreated list.")\
                    .format(index, id),
                level = lg.WARNING)

            untreated.append(index)
            continue

        # We get the attributes of G
        attr_uv = G.edges[edge[0], edge[1], 0]

        # Set the new node label
        camera_label = "c_{}".format(id)

        # It's this simple afterall
        camera_point = row['geometry']
        midpoint_dist = line.project(camera_point)
        sublines = cut(line, midpoint_dist)

        midpoint = line.interpolate(midpoint_dist).coords[0]

        # We have split the geometries and have the new point for the camera
        # belonging to both new geoms

        if len(sublines) == 1:
            # corner case:
            # camera overlaps with point in graph: cut again a few meters away
            # does it overlap with u or v?
            pu = geometry.Point(chosen_edge['point_u'])
            pv = geometry.Point(chosen_edge['point_v'])

            dists = (camera_point.distance(pu), camera_point.distance(pv))

            closest = np.argmin(dists)
            length = line.length

            if length > 6.000:
                cutoff = 5 if closest == 0 else line.length - 5
            else:
                cutoff = length/2

            sublines = cut(line, cutoff)

            midpoint = line.interpolate(cutoff).coords[0]

        # common case:
        # edge is split in two
        geom_u_camera = sublines[0]
        geom_camera_v = sublines[1]

        # Set the new edge attributes
        attr_u_camera = dict(attr_uv)
        attr_camera_v = dict(attr_uv)

        attr_u_camera['geometry'] = geom_u_camera
        attr_u_camera['length'] = attr_u_camera['geometry'].length

        attr_camera_v['geometry'] = geom_camera_v
        attr_camera_v['length'] = attr_camera_v['geometry'].length

        # I hate having to do this, but will do for now..
        new_row = dict(row)
        new_row['x'] = midpoint[0]
        new_row['y'] = midpoint[1]

        # Appending to output lists/dicts
        if camera_label in cameras_to_add.keys():
            # If this is a camera that sees in both directions, we still
            # just want to add one new node on the network but with edges
            # in both directions. We don't want duplicate camera entries
            # here, so if it already exists we update it's 'direction' key
            first_direction = cameras_to_add[camera_label]['direction']

            cameras_to_add[camera_label]['direction'] = \
                "{}-{}".format(first_direction, row['direction'])
        else:
            cameras_to_add[camera_label] = new_row

        edges_to_remove.append((edge[0], edge[1]))
        edges_to_add[(edge[0],camera_label)] = attr_u_camera
        edges_to_add[(camera_label,edge[1])] = attr_camera_v

        # Check if the resulting geom has the expected length
        if (geom_u_camera.length + geom_camera_v.length) - (line.length) > 1e-3:
            log(("({}) - Camera {}: There is a mismatch between the prior"
                 "and posterior geometries lengths:"
                 "{} -> {}, {} | {} != {} + {}")\
                    .format(index, id, edge, (edge[0],camera_label),
                            (camera_label,edge[1]), line.length,
                            geom_u_camera.length, geom_camera_v.length),
                level = lg.ERROR)

        log(("({}) - Camera {}: Scheduled the removal of "
             "edge {} and the addition of edges {}, {}.")\
                .format(index, id, edge,
                        (edge[0],camera_label), (camera_label,edge[1])),
            level = lg.INFO)


    return (cameras_to_add, edges_to_remove, edges_to_add,
            untreated, untreatable)

###
###

def merge_cameras_network(
    G,
    cameras,
    passes = 3,
    camera_range = 40.0,
    plot = True,
    **plot_kwargs
):
    """
    Merge
    """
    log("Merging {} cameras with road network with {} nodes and {} edges"\
            .format(len(cameras), len(G.nodes), len(G.edges)),
        level = lg.INFO)

    start_time = time.time()

    # Adding new attribute to nodes
    nx.set_node_attributes(G, False, 'is_camera')

    if 'both_directions' in cameras.columns.values:

        both_directions_mask = cameras.both_directions\
                                      .apply(lambda x: x == 1)

        tmp = cameras[both_directions_mask]

        tmp1 = tmp.assign(direction = tmp.direction.str\
                                         .split(pat = "-").str[0])
        tmp2 = tmp.assign(direction = tmp.direction.str\
                                         .split(pat = "-").str[1])

        to_merge = pd.concat([tmp1, tmp2, cameras[~both_directions_mask]])\
                     .reset_index(drop = True)

        log("Duplicated {} rows for cameras that see in both directions"\
                .format(len(cameras[both_directions_mask])),
            level = lg.INFO)

    else:
        to_merge = cameras

        log("No column 'both_directions' was found, dataframe is unchanged",
            level = lg.WARNING)

    all_untreatable = set()

    for i in range(passes):

        if len(to_merge) == 0:
            log("Pass {}/{}: Identifying edges to be added and removed."\
                    .format(i+1, passes),
                level = lg.INFO)
            break

        log("Pass {}/{}: Identifying edges to be added and removed."\
                .format(i+1, passes),
            level = lg.INFO)

        cameras_to_add, edges_to_remove, edges_to_add, untreated, untreatable =\
            identify_cameras_merge(G, to_merge)

        all_untreatable.update(untreatable)

        log("Pass {}/{}: Adding {} cameras."\
                .format(i+1, passes, len(cameras_to_add)),
            level = lg.INFO)

        for label, row in cameras_to_add.items():
            d = dict(row)
            d['osmid'] = None
            d['is_camera'] = True

            G.add_node(label, **d)

        log("Pass {}/{}: Adding {} new edges."\
                .format(i+1, passes, len(edges_to_add)),
            level = lg.INFO)

        for edge, attr in edges_to_add.items():
            G.add_edge(edge[0], edge[1], **attr)

        log("Pass {}/{}: Removing {} stale edges."\
                .format(i+1, passes, len(edges_to_remove)),
            level = lg.INFO)

        for edge in edges_to_remove:
            G.remove_edge(*edge)

        log("Pass {}/{}: G has now {} nodes and {} edges."\
                .format(i+1, passes, len(G.nodes()), len(G.edges())),
            level = lg.INFO)

        to_merge = to_merge.loc[untreated]
        if len(to_merge) == 0:
            break
        else:
            log(("Pass {}/{}: {} cameras were not merged because their edge "
                 "overlapped with another camera.")\
                    .format(i+1, passes, len(untreated)),
                level = lg.INFO)


    checkpoint = time.time()
    log("Finished merging cameras with the road graph in {:,.2f} sec."\
            .format(checkpoint - start_time),
        level = lg.INFO)

    if len(all_untreatable) > 0:
        log(("{} cameras ({}) were flagged as 'untreatable' because there were "
             "no edges nearby that fit the distance and direction requirements."
             "Because of this they were not merged.")\
            .format(len(all_untreatable),
                    cameras.loc[all_untreatable]['id'].tolist()),
        level = lg.WARNING)
    else:
        log(("No cameras were flagged as 'untreatable'.")\
                .format(i+1, passes, len(untreated)),
            level = lg.INFO)

    if len(to_merge) > 0:
        log("Cameras that could not be merged in {} passes: {}"\
                .format(passes, list(to_merge['id'])),
            level = lg.INFO)

    if plot:
        plot_kwargs['legend'] = True
        plot_kwargs['label'] = 'cameras'
        plot_kwargs['points_marker'] = '.'

        points = [ (data['x'],data['y']) for _, data in \
                    G.nodes(data = True) if data['is_camera'] ]

        _, _, filename = plot_G(
            G,
            name = "road_graph_merged",
            # key = "is_camera",
            points = ( list(map(lambda x: x[0], points)),
                       list(map(lambda x: x[1], points))),
            **plot_kwargs)

        log("Saved image of merged road graph to disk {} in {:,.2f} sec"\
                .format(filename, time.time() - checkpoint),
            level = lg.INFO)

        close_up_plots(G, **plot_kwargs)

    return G


def camera_pairs_from_graph(G):

    start_time = time.time()

    camera_nodes = [ data for node, data in G.nodes(data = True) \
                        if data['is_camera'] ]

    node_ids = [ node for node, data in G.nodes(data = True)\
                 if data['is_camera'] ]

    cameras = pd.DataFrame(camera_nodes).assign(node = node_ids)

    log("Computing valid pairs of cameras from {} possible combinations"
            .format(len(cameras) ** 2),
        level = lg.INFO)

    d = cameras['direction']

    N = np.array(d == 'N', dtype = int, ndmin = 2)
    S = np.array(d == 'S', dtype = int, ndmin = 2)
    E = np.array(d == 'E', dtype = int, ndmin = 2)
    W = np.array(d == 'W', dtype = int, ndmin = 2)
    NS = np.array((d == 'N-S') | (d == 'S-N'), dtype = int, ndmin = 2)
    EW = np.array((d == 'E-W') | (d == 'W-E'), dtype = int, ndmin = 2)

    not_N = np.array(d != 'N', dtype = int, ndmin = 2)
    not_S = np.array(d != 'S', dtype = int, ndmin = 2)
    not_E = np.array(d != 'E', dtype = int, ndmin = 2)
    not_W = np.array(d != 'W', dtype = int, ndmin = 2)

    # Don't allow camera pairs of cameras pointing in opposite directions
    # Camera pointing in both directions can pair with any other camera
    D = (N.T * not_S) + (S.T * not_N) + (E.T * not_W) + (W.T * not_E) + \
        (NS.T * np.ones(shape = (len(d),1), dtype = int)) + \
        (EW.T * np.ones(shape = (len(d),1), dtype = int))

    # Don't allow camera pairs with the same origin and destination
    np.fill_diagonal(D, 0)

    # Select the valid camera pairs
    source_idx, target_idx = np.where(D == 1)

    source = cameras.iloc[source_idx]['node']
    target = cameras.iloc[target_idx]['node']

    directions = list(zip(cameras.iloc[source_idx]['direction'],
                          cameras.iloc[target_idx]['direction']))

    log(("Computing shortest paths for each valid camera pair. "
         "This may take a while."),
        level = lg.INFO)

    # shortest paths for these pairs (split by direction later for plotting?)
    paths = []
    distances = []

    # Takes ~30 seconds

    start_time = time.time()

    for s, t in zip(source, target):
        try:
            spath = nx.shortest_path(G, s, t, weight = 'length')

            edges = [(u,v) for u,v in zip(spath, spath[1:])]
            lengths = [ G.edges[u,v,0]['length'] for u,v in edges ]
            distance = reduce(lambda x,y: x+y, lengths)

            # log(("Found a path between {} and {} of length {} meters.")
            #         .format(s, t, distance),
            #     level = lg.DEBUG)

            if distance < 50:
                log(("Distance between cameras {} and {} is less than 50 "
                     "meters. Are these two cameras mergeable into one?")
                        .format(s,t),
                    level = lg.WARNING)

        except nx.NetworkXNoPath:
            spath = None
            edges = []
            distance = -1
            log(("Could not find a path between {} and {}.")
                    .format(s,t),
                level = lg.ERROR)

        paths.append(spath)
        distances.append(distance)

    log("Computed paths in {:,.1f} minutes"\
            .format((time.time() - start_time)/60.0),
        level = lg.INFO)

    # This should always be True unless I've coded something wrong
    assert len(source) == len(target) == len(distances) == \
           len(paths) == len(directions)

    camera_pairs = pd.DataFrame(
        data = {
            'origin' : np.array(source),
            'destination' : np.array(target),
            'distance' : distances,
            'direction' : directions,
            'path' : paths}
    )

    # drop rows with path = None
    camera_pairs = camera_pairs.drop(
        camera_pairs.loc[pd.isna(camera_pairs.path)].index.values.tolist()
    )

    total_combinations = len(cameras) ** 2
    log(("Out of {} possible camera pairs, {} were filtered out, resulting in a "
         "total of {} valid camera pairs.")\
            .format(total_combinations, total_combinations - len(camera_pairs),
                    len(camera_pairs)),
        level = lg.INFO)

    return camera_pairs

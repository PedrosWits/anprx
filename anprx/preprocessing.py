"""Methods for wrangling and pre-processing anpr data and related datasets."""
# ------------------------------------------------------------------------------

from   .utils               import log
from   .utils               import save_fig
from   .utils               import settings
from   .helpers             import add_edge_directions
from   .helpers             import get_quadrant
from   .helpers             import cut
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
import geopandas            as gpd
import logging              as lg
import shapely.geometry     as geometry
from   collections          import OrderedDict

# ------------------------------------------------------------------------------

infer_direction_regex = \
('(East\/West|North\/South|Northbound|Eastbound|Southbound|Westbound)')

address_regex = \
(r'(East\/West|North\/South|Northbound|Eastbound|Southbound|Westbound|Site \d'
'|Camera \d|Camera|Site)')

road_category_regex = \
r'(A\d+|B\d+|C\d+)'

# ------------------------------------------------------------------------------
def wrangle_cameras(
    cameras,
    infer_direction_col      = "description",
    infer_direction_re       = infer_direction_regex,
    drop_car_park            = "description",
    drop_is_test             = "name",
    drop_is_not_commissioned = True,
    extract_address          = "description",
    address_regex            = address_regex,
    extract_road_category    = "description",
    road_category_regex      = road_category_regex,
    sort_by                  = "id",
    project_coords           = True,
    utm_crs                  = {'datum': 'WGS84',
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
    optional_columns  = {'name', 'description', 'is_commissioned', 'type'}

    log("Checking if input dataframe contains mandatory columns {}."\
            .format(mandatory_columns),
        level = lg.INFO)

    cols = set(cameras.columns.values)

    assert mandatory_columns.issubset(cols)

    log("Detected {}/{} optional columns: {}"\
            .format(len(optional_columns & cols),
                    len(optional_columns),
                    optional_columns & cols),
        level = lg.INFO)

    log("Skipping {} unrecognised columns: {}."\
            .format(len(cols - optional_columns - mandatory_columns),
                    cols - optional_columns - mandatory_columns),
        level = lg.INFO)

    # Some asserts about the input data
    log("Checking if 'id' is unique."\
            .format(mandatory_columns),
        level = lg.INFO)
    assert len(cameras['id']) == len(cameras['id'].unique())

    # Find and drop cameras that are labelled as "test"
    if drop_is_test:
        oldlen = len(cameras)

        cameras = cameras.assign(
            is_test = cameras[drop_is_test].str.contains('test',
                                                         case = False))
        cameras = cameras[(cameras.is_test == False)]
        cameras = cameras.drop(columns = 'is_test')

        log("Dropping {} rows with 'test' in name"\
                .format(oldlen - len(cameras)),
            level = lg.INFO)


    # Find and drop cameras that not commissioned
    if drop_is_not_commissioned:
        oldlen = len(cameras)

        cameras = cameras[(cameras.is_commissioned == True)]
        cameras = cameras.drop('is_commissioned', axis = 1)

        log("Dropping {} rows with 'is_commissioned' == True."\
                .format(oldlen - len(cameras)),
            level = lg.INFO)

    # Find and drop cameras that are in car parks
    if drop_car_park:
        oldlen = len(cameras)

        cameras = cameras.assign(
            is_carpark = cameras[drop_car_park].str.contains('Car Park',
                                                             case = False))
        cameras = cameras[(cameras.is_carpark == False)]
        cameras = cameras.drop(columns = 'is_carpark')

        log("Dropping {} rows with 'is_carpark' == True."\
                .format(oldlen - len(cameras)),
            level = lg.INFO)

    if len(cameras) == 0:
        log("No more cameras to process..", level = lg.WARNING)
        return None

    # Get direction from other fields
    if infer_direction_col:
        log("Inferring direction based on column '{}'."\
                .format(infer_direction_col),
            level = lg.INFO)

        cameras = cameras.assign(
            direction = cameras[infer_direction_col].str.extract(
                infer_direction_re,
                flags = re.IGNORECASE))

        cameras = cameras.assign(
            both_directions =
                (cameras.direction.str.contains("/|&", na=False))
        )

        # ugly code, but will have to do for now
        cameras.loc[~cameras.both_directions, 'direction'] = \
            cameras.loc[~cameras.both_directions].direction.str[0]

        cameras.loc[cameras.both_directions, 'direction'] = \
            cameras.loc[cameras.both_directions].direction.str\
                .split(pat = "/")\
                .apply(lambda x: "{}-{}".format(x[0][0], x[1][0]))
    else:
        log("Skipping inferring direction", level = lg.INFO)

    # Computing new column 'address'
    if extract_address:
        cameras = cameras.assign(
            address = cameras[extract_address]\
                        .str.replace(address_regex, '',regex = True)\
                            .replace(' +', ' ', regex = True))

        log("Extracting address from '{}'.".format(extract_address),
            level = lg.INFO)
    else:
        log("Skipping extracting address", level = lg.INFO)


    # Computing new column 'road category'
    if extract_road_category:
        cameras = cameras.assign(
            road_category = cameras[extract_road_category]\
                                .str.extract(road_category_regex))
        cameras = cameras.assign(road_category = cameras.road_category.str[0])
    else:
        log("Skipping extracting road category", level = lg.INFO)

    # Merge cameras:
    #   Combinations of lat/lon should be unique. If not, this may mean that
    #   we have multiple cameras in the same location. Furthermore, if these
    #   are pointing in the same direciton we should merge this into
    #   the same entity, otherwise it will cause problems later on
    oldlen = len(cameras)

    groups = cameras.groupby([cameras['lat'].apply(lambda x: round(x,8)),
                              cameras['lon'].apply(lambda x: round(x,8)),
                              cameras['direction']
                             ])
    ids = groups['id'].apply(lambda x: "-".join(x))\
                      .reset_index()\
                      .sort_values(by = ['lat', 'lon', 'direction'])['id']\
                      .reset_index(drop = True)

    names = groups['name'].apply(lambda x: "-".join(x))\
                          .reset_index()\
                          .sort_values(by = ['lat', 'lon', 'direction'])['name']\
                          .reset_index(drop = True)

    # Really janky way to do this, but I couldn't figure out the right way
    # to do this. I guess that should be done using the aggregate function.
    # But it's really unintuitive. Tidyverse is so much better.
    cameras  = cameras[groups['id'].cumcount() == 0]\
                    .sort_values(by = ['lat', 'lon', 'direction'])\
                    .reset_index(drop = True)

    cameras['id'] = ids
    cameras['name'] = names

    log("Merged {} cameras that were in the same location as other cameras."\
            .format(oldlen - len(cameras)),
        level = lg.INFO)


    log("Sorting by {} and resetting index."\
            .format(sort_by),
        level = lg.INFO)

    # sort and reset_index
    cameras = cameras.sort_values(by=[sort_by])
    cameras.reset_index(drop=True, inplace=True)

    if project_coords:
        log("Projecting cameras to utm and adding geometry column.",
            level = lg.INFO)

        camera_points = gpd.GeoSeries(
            [ geometry.Point(x,y) for x, y in zip(
                cameras['lon'],
                cameras['lat'])
            ])

        cameras_geodf = gpd.GeoDataFrame(index = cameras.index,
                                         geometry=camera_points)
        cameras_geodf.crs = {'init' :'epsg:4326'}

        avg_longitude = cameras_geodf['geometry'].unary_union.centroid.x
        utm_zone = int(math.floor((avg_longitude + 180) / 6.) + 1)
        utm_crs["zone"] = utm_zone,

        proj_cameras = cameras_geodf.to_crs(utm_crs)
        cameras = proj_cameras.join(cameras, how = 'inner')
    else:
        log("Skipping projecting coordinates to UTM", level = lg.INFO)

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
    make_plots = True,
    file_format = 'svg',
    margins = [0.1, 0.1, 0.1, 0.1],
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

    points = [Point(lat,lng) for lat,lng in zip(cameras['lat'], cameras['lon'])]

    bbox = bbox_from_points(
        points = points,
        max_area = 1000000000,
        rel_margins = RelativeMargins(margins[0], margins[1],
                                      margins[2], margins[3])
    )

    log("Returning bbox from camera points: {}"\
            .format(bbox),
        level = lg.INFO)

    G = ox.graph_from_bbox(
            north = bbox.north,
            south = bbox.south,
            east = bbox.east,
            west = bbox.west,
            custom_filter = osm_road_filter
    )

    log("Returned road graph in {:,.3f} sec"\
            .format(time.time() - start_time),
        level = lg.INFO)
    checkpoint = time.time()

    G = add_edge_directions(G)

    G = ox.project_graph(G)

    log("Added edge directions and projected graph in {:,.3f} sec"\
            .format(checkpoint- start_time),
        level = lg.INFO)
    checkpoint = time.time()

    if make_plots:
        fig_height      = plot_kwargs.get('fig_height', 10)
        fig_width       = plot_kwargs.get('fig_width', 14)

        node_size       = plot_kwargs.get('node_size', 30)
        node_alpha      = plot_kwargs.get('node_alpha', 1)
        node_zorder     = plot_kwargs.get('node_zorder', 2)
        node_color      = plot_kwargs.get('node_color', '#66ccff')
        node_edgecolor  = plot_kwargs.get('node_edgecolor', 'k')

        edge_color      = plot_kwargs.get('edge_color', '#999999')
        edge_linewidth  = plot_kwargs.get('edge_linewidth', 1)
        edge_alpha      = plot_kwargs.get('edge_alpha', 1)


        fig, ax = ox.plot_graph(
            G, fig_height=fig_height, fig_width=fig_width,
            node_alpha=node_alpha, node_zorder=node_zorder,
            node_size = node_size, node_color=node_color,
            node_edgecolor=node_edgecolor, edge_color=edge_color,
            edge_linewidth = edge_linewidth, edge_alpha = edge_alpha,
            use_geom = True, annotate = False, save = False, show = False
        )

        image_name = "road_graph"
        save_fig(fig, ax, image_name, file_format = file_format, dpi = 320)

        filename = "{}/{}/{}.{}".format(
                        settings['app_folder'],
                        settings['images_folder_name'],
                        image_name, file_format)

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

        if make_plots:
            image_name = "road_graph_cleaned"
            filename = "{}/{}/{}.{}".format(
                            settings['app_folder'],
                            settings['images_folder_name'],
                            image_name, file_format)

            fig, ax = ox.plot_graph(
                G, fig_height=fig_height, fig_width=fig_width,
                node_alpha=node_alpha, node_zorder=node_zorder,
                node_size = node_size, node_color=node_color,
                node_edgecolor=node_edgecolor, edge_color=edge_color,
                edge_linewidth = edge_linewidth, edge_alpha = edge_alpha,
                use_geom = True, annotate = False, save = False, show = False
            )

            save_fig(fig, ax, image_name, file_format = file_format, dpi = 320)

            log("Saved image of cleaned road graph to disk {} in {:,.3f} sec"\
                    .format(filename, time.time() - checkpoint),
                level = lg.INFO)
            checkpoint = time.time()

    if make_plots:
        if 'geometry' in cameras.columns:
            cameras_color  = plot_kwargs.get('cameras_color', '#D91A35')
            cameras_marker = plot_kwargs.get('cameras_marker', '*')
            cameras_size   = plot_kwargs.get('cameras_size', 100)
            cameras_zorder = plot_kwargs.get('cameras_zorder', 20)

            camera_points = ax.scatter(
                cameras['geometry'].x,
                cameras['geometry'].y,
                marker = cameras_marker,
                color = cameras_color,
                s = cameras_size,
                zorder = cameras_zorder,
                label = "cameras"
            )
            ax.legend()

            image_name = "road_graph_cleaned_cameras" if clean_intersections \
                         else "road_graph_cameras"
            filename = "{}/{}/{}.{}".format(
                            settings['app_folder'],
                            settings['images_folder_name'],
                            image_name, file_format)

            save_fig(fig, ax, image_name, file_format = file_format, dpi = 320)

            log("Saved image of cleaned road graph to disk {} in {:,.3f} sec"\
                    .format(filename, time.time() - checkpoint),
                level = lg.INFO)
        else:
            log(("Skipped making image of road graph with cameras because "
                "no geometry was available"),
                level = lg.WARNING)

    log("Retrieved road network from points in {:,.3f}"\
        .format(time.time() - start_time))

    return G


def identify_cameras_merge(G, cameras, camera_range = 40.0):

    # Input validation
    required_cols = {'id', 'geometry', 'direction', 'address'}

    if not required_cols.issubset(set(cameras.columns.values)):
        raise ValueError("The following required columns are not available: {}"\
                         .format(required_cols))

    edges_to_remove = []
    edges_to_add = {}
    cameras_to_add = {}
    untreated = []

    # We assume that there is a road within 40 meters of each camera
    for index, row in cameras.iterrows():

        id = row['id']
        direction = row['direction']
        address = row['address']
        x = row['geometry'].x
        y = row['geometry'].y

        # Get nearest edges to
        nedges = edges_by_distance(G, (y,x))

        # identify the edges that are in range
        distances = np.array(list(map(lambda x: x[1], nedges)))
        # log(("({}) - Camera {} top 5 closest distances: {}")\
        #         .format(index, id, distances[0:5]),
        #     level = lg.INFO)

        out_of_range_idx = np.argwhere(distances > camera_range)\
                             .reshape(-1)[0]
        in_range_slice = slice(0, (out_of_range_idx))

        log(("({}) - Camera {} has {} edges in range, is located on {} "
             "and points towards {}")\
                .format(index, id, len(nedges[in_range_slice]),
                        address, direction),
            level = lg.INFO)

        if len(nedges[in_range_slice]) == 0:
            log(("({}) - Camera {} has no edge within {} meters. "
                 "Appending to untreated list.")\
                    .format(index, id, camera_range),
                level = lg.WARNING)
            untreated.append(index)
            continue

        for i in range(len(nedges)):

            geom,u,v = nedges[in_range_slice][i][0]
            distance = nedges[in_range_slice][i][1]

            origin = geometry.Point([row['geometry'].x, row['geometry'].y])

            # Direction of (u,v)
            point_u = np.array((G.nodes[u]['x'], G.nodes[u]['y']))
            point_v = np.array((G.nodes[v]['x'], G.nodes[v]['y']))

            vec_uv = point_v - point_u
            vec_uv_phi = np.rad2deg(math.atan2(vec_uv[1], vec_uv[0]))

            if vec_uv_phi < 0:
                vec_uv_phi = vec_uv_phi + 360

            dir_uv = get_quadrant(vec_uv_phi)

            log(("({}) - Camera {}: Analysing candidate {} edge {}, "
                 "distance = {:.2f} m, direction = {}")\
                    .format(index, id, i+1, (u,v), distance, dir_uv, i),
                level = lg.INFO)

            # If the direction does not match the camera's direction
            if direction not in dir_uv:
                log(("({}) - Camera {}: candidate {} edge {}, is pointing in "
                     "the opposite direction {} != {}. Trying next edge.")\
                        .format(index, id, i+1, (u,v), dir_uv, direction),
                    level = lg.WARNING)
                # SKIP TO NEXT EDGE
                if i == out_of_range_idx:
                    log(("({}) - Camera {}: no more candidate edges in range."
                         "Appending to list of untreated.")\
                            .format(index, id),
                        level = lg.ERROR)
                    untreated.append(index)
                    break

                else:
                    continue

            # We get the attributes of G
            attr_uv = G.edges[u,v,0]

            # Is this edge already assigned to a different camera?
            if (u,v) in edges_to_remove:
                log(("({}) - Camera {}: Another camera is already pointing at "
                     "this edge. Appending to untreated list.")\
                        .format(index, id),
                    level = lg.WARNING)

                untreated.append(index)
                break

            # Get the edge address just for validation/logging purposes
            edge_ref = attr_uv['ref'] if 'ref' in attr_uv else "NULL"
            edge_name = attr_uv['name'] if 'name' in attr_uv else "NULL"
            edge_address = "{} {}".format(edge_ref, edge_name)

            log(("({}) - Camera {}: Mapping to candidate {} edge {} available "
                 "at address = {}")\
                    .format(index, id, i+1, (u,v), edge_address),
                level = lg.INFO)

            # Set the new node label
            camera_label = "c_{}".format(id)

            # It's this simple afterall
            midpoint_dist = geom.project(origin)
            sublines = cut(geom, midpoint_dist)

            midpoint = geom.interpolate(midpoint_dist).coords[0]

            # We have split the geometries and have the new point for the camera
            # belonging to both new geoms
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
                    "{}-{}".format(first_direction, direction)
            else:
                cameras_to_add[camera_label] = new_row

            edges_to_remove.append((u,v))
            edges_to_add[(u,camera_label)] = attr_u_camera
            edges_to_add[(camera_label,v)] = attr_camera_v

            if (geom_u_camera.length + geom_camera_v.length) - (geom.length) > 1e-3:
                log(("({}) - Camera {}: There is a mismatch between the prior"
                     "and posterior geometries lengths:"
                     "{} -> {}, {} | {} != {} + {}")\
                        .format(index, id, (u,v), (u,camera_label),
                                (camera_label,v), geom.length,
                                geom_u_camera.length, geom_camera_v.length),
                    level = lg.ERROR)


            log(("({}) - Camera {}: Scheduled the removal of candidate {}"
                 "edge {} and the addition of edges {}, {}.")\
                    .format(index, id, i+1, (u,v), (u,camera_label),
                            (camera_label,v)),
                level = lg.INFO)

            break

    return (cameras_to_add, edges_to_remove, edges_to_add, untreated)

###
###

def merge_cameras_network(G, cameras, passes = 2, camera_range = 40.0):
    """
    Merge
    """
    log("Merging {} cameras with road network with {} nodes and {} edges"\
            .format(len(cameras), len(G.nodes), len(G.edges)),
        level = lg.INFO)

    start_time = time.time()

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

        log("No column 'both_directions' was found, dataframe is unchanged"\
                .format(len(cameras[both_directions_mask])),
            level = lg.WARNING)


    for i in range(passes):

        if len(to_merge) == 0:
            log("Pass {}/{}: Identifying edges to be added and removed."\
                    .format(i+1, passes),
                level = lg.INFO)
            break

        log("Pass {}/{}: Identifying edges to be added and removed."\
                .format(i+1, passes),
            level = lg.INFO)

        cameras_to_add, edges_to_remove, edges_to_add, untreated = \
            identify_cameras_merge(G, to_merge)

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

        log(("Pass {}/{}: {} cameras were not merged because their edge "
             "overlapped with another camera.")\
                .format(i+1, passes, len(untreated)),
            level = lg.INFO)

        to_merge = to_merge.iloc[untreated]

    log("Finished merging cameras with the road graph in {:,.2f}."\
            .format(time.time() - start_time),
        level = lg.INFO)


    return G, to_merge

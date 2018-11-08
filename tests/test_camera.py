import os
import pytest
import numpy as np
import osmnx as ox
import logging as lg
import networkx as nx
import anprx.core as core
from anprx.plot import plot_camera
from anprx.animate import animate_camera

def get_network(distance = 1000, center = (54.97351, -1.62545)):

    network_pickle_filename = "tests/data/test_network_USB_{}.pkl".format(distance)

    if os.path.exists(network_pickle_filename):
        network = nx.read_gpickle(path = network_pickle_filename)
    else:
        network = ox.graph_from_point(
            center_point = center,
            distance = distance, #meters
            distance_type='bbox',
            network_type="drive_service")
        nx.write_gpickle(G = network, path = network_pickle_filename)

    return network

test_camera = core.Camera(
    network = get_network(distance = 1000),
    id = "c1",
    point = core.Point(lat = 54.974537, lng = -1.625644),
    address = "Pitt Street, Newcastle Upon Tyne, UK")

test_camera_addressless = core.Camera(
    network = get_network(distance = 1000),
    id = "c2",
    point = core.Point(lat = 54.974537, lng = -1.625644))

#-----------#
#-----------#
#-----------#

def test_camera_p_cedges():
    camera = test_camera

    p_cedges = camera.p_cedges
    p_cedges_values = np.array(list(camera.p_cedges.values()))

    assert (p_cedges_values >= 0  ).all() and \
           (p_cedges_values <= 1.0).all()

    assert len(p_cedges) == len(camera.lsystem['cedges'])


def test_camera_edge():
    assert test_camera.edge == \
        core.Edge(u=3709385867, v=827266956, k=0)

    assert test_camera_addressless.edge == \
        core.Edge(u=3709385867, v=827266956, k=0)


def test_plot():
    camera = test_camera

    # Just the network
    plot_camera(camera,
                show_camera = False,
                color_near_nodes = False,
                color_candidate_edges = False,
                draw_colorbar = False)

    # + the camera
    plot_camera(camera,
                show_camera = True,
                color_near_nodes = False,
                color_candidate_edges = False,
                draw_colorbar = False)

    # + near nodes
    plot_camera(camera,
                show_camera = True,
                color_near_nodes = True,
                color_candidate_edges = False,
                draw_colorbar = False)

    # + near edges
    plot_camera(camera,
                show_camera = True,
                color_near_nodes = True,
                color_candidate_edges = True,
                draw_colorbar = False)

    # default
    plot_camera(camera,
                save = True,
                filename = "TEST_CAMERA")


@pytest.mark.skipif(
    'SKIP_TEST_ANIMATE' in os.environ and os.environ['SKIP_TEST_ANIMATE'] == 'true',
    reason="No need to test this in travis")
def test_animation_mp4():
    animate_camera(test_camera,
                   filename = "TEST_CAMERA",
                   save_as = 'mp4')

@pytest.mark.skipif(
    'SKIP_TEST_ANIMATE' in os.environ and os.environ['SKIP_TEST_ANIMATE'] == 'true',
    reason="No need to test this in travis")
def test_animation_gif():
    animate_camera(test_camera_addressless,
                   filename = "TEST_CAMERA",
                   save_as = 'gif',
                   camera_markersize = 15,
                   labels_fontsize = 12,
                   node_size = 100,
                   edge_linewidth=3,
                   colorbar_ticks_fontsize = 7,
                   subtitle_fontsize = 13,
                   sample_point_size = 6,
                   colorbar_rect = [0.15, 0.25, 0.20, 0.02])

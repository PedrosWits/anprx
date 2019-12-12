import pytest
import numpy as np
import networkx as nx
from shapely.geometry import LineString


from anprx.helpers import cut
from anprx.helpers import is_in
from anprx.helpers import flatten
from anprx.helpers import unit_vector
from anprx.helpers import dot2d
from anprx.helpers import angle_between
from anprx.helpers import flatten_dict
from anprx.network import edges_with_any_property
from anprx.network import edges_with_all_properties


###
###

def test_is_in():
    test_value_1 = 1
    test_value_2 = 0
    test_value_3 = [0,1]
    test_value_4 = [1,2]
    test_value_5 = [-1,0]

    values_set = {1,2,3,4,5}

    assert is_in(test_value_1, values_set)
    assert not is_in(test_value_2, values_set)
    assert is_in(test_value_3, values_set)
    assert is_in(test_value_4, values_set)
    assert not is_in(test_value_5, values_set)



G = nx.MultiDiGraph()
G.add_node(1, label='one')
G.add_node(2, label='fish')
G.add_node(3, label='two')
G.add_node(4, label='fish')

G.add_edge(1,2,color='red', size = "big")
G.add_edge(2,3,color='blue', size = "small")
G.add_edge(1,4,color=['blue', 'dark_blue'], size = "big")

def test_edges_with_at_least_one_property():
    blue = set(
        edges_with_any_property(
            G = G,
            properties = {"color" : {"blue"}})
    )

    assert blue == set([(2,3,0), (1,4,0)])

    big_or_red = set(
        edges_with_any_property(
            G = G,
            properties = {"color" : {"red"}, "size" : {"big"}})
    )

    assert big_or_red == set([(1,2,0), (1,4,0)])

    small_or_big = set(
        edges_with_any_property(
            G = G,
            properties = {"size" : {"small", "big"}})
    )

    assert small_or_big == set([(1,2,0), (2,3,0), (1,4,0)])

    fish_type = set(
        edges_with_any_property(
            G = G,
            properties = {"type" : {"shark", "whale"}})
    )

    assert fish_type == set([])


def test_edges_with_all_properties():
    blue = set(
        edges_with_all_properties(
            G = G,
            properties = {"color" : {"blue"}})
    )

    assert blue == set([(2,3,0), (1,4,0)])

    big_and_red = set(
        edges_with_all_properties(
            G = G,
            properties = {"color" : {"red"}, "size" : {"big"}})
    )

    assert big_and_red == set([(1,2,0)])

    small_or_big = set(
        edges_with_all_properties(
            G = G,
            properties = {"size" : {"small", "big"}})
    )

    assert small_or_big == set([(1,2,0), (2,3,0), (1,4,0)])

    big_and_purple = set(
        edges_with_all_properties(
            G = G,
            properties = {"color" : {"purple"}, "size" : {"big"}})
    )

    assert big_and_purple == set([])

    fish_type = set(
        edges_with_all_properties(
            G = G,
            properties = {"type" : {"shark", "whale"}})
    )

    assert fish_type == set([])

def test_flatten():
    l1 = list(range(0,6))
    l2 = list(range(6,10))
    l3 = [l1, l2]

    assert list(flatten(l1)) == l1
    assert list(flatten(l2)) == l2
    assert list(flatten(l1 + [l2])) == l1 + l2
    assert list(flatten(l3)) == l1 + l2

def test_unit_vector():
    v = np.array([1,2,1,1,0,1])
    v = np.reshape(v, (3,2))
    vunit = unit_vector(v)

    assert np.shape(vunit) == np.shape(v)
    for i in range(0, len(vunit)):
        np.testing.assert_almost_equal(sum(vunit[i] ** 2), 1.0)

def test_dot2d():
    v1 = np.reshape(np.array([1,2,1,1,0,1]), (3,2))
    v2 = np.reshape(np.array([2,1,3,2,1,0]), (3,2))

    np.testing.assert_array_equal(
        dot2d(v1,v2, method = "einsum"),
        dot2d(v1,v2, method = "loop")
    )

    with pytest.raises(ValueError):
        dot2d(v1,v2, method = "geronimo")


def test_angle_between():
    v1 = np.reshape(np.array([1,2,1,1,0,1,2,2,1,2]), (5,2))
    v2 = np.reshape(np.array([2,1,3,2,1,0,1,1,-2,-1]), (5,2))

    angles = angle_between(v1,v2)

    np.testing.assert_array_almost_equal(
        np.array([36.8698976,
                  11.3099325,
                  90,
                  0,
                  36.8698976]),
        angles)

def test_cut():
    line = LineString([(0, 0), (1, 1)])

    line_parts = cut(line, 0.41421)

    assert (line_parts[0].length - 0.41421) <= 0.0001
    assert (line_parts[1].length - 1.00000) <= 0.0001

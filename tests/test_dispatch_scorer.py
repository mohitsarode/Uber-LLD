from src.engine.dispatch_scorer import DispatchScorer


def test_scorer_distance_decay():
    scorer = DispatchScorer(weight_distance=1.0, weight_rating=0.0, weight_bearing=0.0)
    pickup_lat, pickup_lng = 37.7749, -122.4194

    # Driver A: close (50m away)
    driver_a = {"driver_id": "a", "lat": 37.7753, "lng": -122.4194, "cell": "8828308281fffff", "heading": 0.0, "speed": 20.0, "rating": 5.0}
    # Driver B: farther (2000m away)
    driver_b = {"driver_id": "b", "lat": 37.7929, "lng": -122.4194, "cell": "8828308281fffff", "heading": 0.0, "speed": 20.0, "rating": 5.0}

    ranked = scorer.rank_candidates([driver_b, driver_a], pickup_lat, pickup_lng)
    assert len(ranked) == 2
    assert ranked[0].driver_id == "a"
    assert ranked[0].distance_m < ranked[1].distance_m
    assert ranked[0].final_score > ranked[1].final_score


def test_scorer_bearing_heading_bonus():
    # If distance is equal, driver heading toward pickup scores higher than driver heading away
    scorer = DispatchScorer(weight_distance=0.0, weight_rating=0.0, weight_bearing=1.0)
    pickup_lat, pickup_lng = 37.7800, -122.4194

    # Driver is South of pickup (desired bearing is 0 deg / North)
    # Driver A is heading North (0 deg) -> aligned
    driver_a = {"driver_id": "a", "lat": 37.7700, "lng": -122.4194, "cell": "c1", "heading": 0.0, "speed": 30.0, "rating": 4.8}
    # Driver B is heading South (180 deg) -> heading away
    driver_b = {"driver_id": "b", "lat": 37.7700, "lng": -122.4194, "cell": "c1", "heading": 180.0, "speed": 30.0, "rating": 4.8}

    ranked = scorer.rank_candidates([driver_b, driver_a], pickup_lat, pickup_lng)
    assert ranked[0].driver_id == "a"
    assert ranked[0].bearing_score > ranked[1].bearing_score

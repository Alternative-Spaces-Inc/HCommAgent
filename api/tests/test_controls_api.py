import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jsonschema  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import controls  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_controls_route_is_public_and_lists_every_control():
    resp = client.get("/v1/controls")
    assert resp.status_code == 200
    body = resp.json()
    assert [c["control"] for c in body] == list(controls.CONTROLS)
    for spec in body:
        assert set(spec) == {"control", "request_schema", "answer_schema", "example"}


def test_examples_validate_against_their_own_schemas():
    for spec in client.get("/v1/controls").json():
        jsonschema.validate(spec["example"]["request"], spec["request_schema"])
        jsonschema.validate(spec["example"]["answer"]["value"], spec["answer_schema"])
        # The example also passes the server side validators the create route uses.
        options = [SimpleNamespace(description=None, **o) for o in spec["example"]["request"]["options"]]
        controls.validate_request_shape(spec["control"], options, "Example", None, "caller")
        controls.validate_answer_value(spec["control"], spec["example"]["answer"]["value"],
                                       [o["id"] for o in spec["example"]["request"]["options"]])


def test_option_bounds_match_the_library():
    for spec in client.get("/v1/controls").json():
        lib = controls.CONTROLS[spec["control"]]
        opts = spec["request_schema"]["properties"]["options"]
        assert opts["minItems"] == lib["min_options"]
        assert opts["maxItems"] == lib["max_options"]

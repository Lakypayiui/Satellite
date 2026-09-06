from satellite_py.validation import validate_input, validate_output


def test_validation_required_and_nested_path():
    schema = {"type": "object", "required": ["user"], "properties": {"user": {"type": "object", "required": ["id"]}}}
    error = validate_input({"user": {}}, schema)
    assert error is not None
    assert "user.id" in error


def test_validation_jsonschema_keywords():
    schema = {
        "type": "object",
        "required": ["value", "items"],
        "properties": {
            "value": {"type": "integer", "minimum": 1, "maximum": 5},
            "items": {"type": "array", "minItems": 1, "items": {"type": "number"}},
        },
    }
    assert validate_input({"value": 3, "items": [1.5]}, schema) is None
    assert validate_input({"value": 7, "items": []}, schema) is not None


def test_output_validation():
    assert validate_output({"result": 4}, {"type": "object", "required": ["result"]}) is None
    assert validate_output({}, {"type": "object", "required": ["result"]}) is not None

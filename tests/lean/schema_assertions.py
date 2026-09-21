from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def assert_matches_schema(value: Any, schema: Mapping[str, Any]) -> None:
    """Validate the JSON Schema features used by the worker protocol."""

    def resolve(reference: str) -> Mapping[str, Any]:
        if not reference.startswith("#/"):
            raise AssertionError(f"unsupported external schema reference: {reference}")
        target: Any = schema
        for component in reference[2:].split("/"):
            target = target[component]
        return target

    def validate(instance: Any, rule: Mapping[str, Any], path: str) -> None:
        if "$ref" in rule:
            validate(instance, resolve(rule["$ref"]), path)
            return

        if "const" in rule and instance != rule["const"]:
            raise AssertionError(f"{path}: expected constant {rule['const']!r}, got {instance!r}")
        if "enum" in rule and instance not in rule["enum"]:
            raise AssertionError(f"{path}: {instance!r} is not one of {rule['enum']!r}")

        allowed_types = rule.get("type")
        if isinstance(allowed_types, str):
            allowed_types = [allowed_types]
        if allowed_types is not None:
            matches = {
                "object": lambda item: isinstance(item, dict),
                "array": lambda item: isinstance(item, list),
                "string": lambda item: isinstance(item, str),
                "boolean": lambda item: isinstance(item, bool),
                "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
                "number": lambda item: isinstance(item, (int, float))
                and not isinstance(item, bool),
                "null": lambda item: item is None,
            }
            if not any(matches[kind](instance) for kind in allowed_types):
                raise AssertionError(f"{path}: expected type {allowed_types!r}, got {instance!r}")

        if isinstance(instance, dict) and rule.get("type") == "object":
            required = set(rule.get("required", []))
            missing = required - instance.keys()
            if missing:
                raise AssertionError(f"{path}: missing required properties {sorted(missing)!r}")
            properties = rule.get("properties", {})
            if rule.get("additionalProperties") is False:
                additional = instance.keys() - properties.keys()
                if additional:
                    raise AssertionError(f"{path}: unexpected properties {sorted(additional)!r}")
            for key, child in instance.items():
                if key in properties:
                    validate(child, properties[key], f"{path}.{key}")

        if isinstance(instance, list) and "items" in rule:
            for index, item in enumerate(instance):
                validate(item, rule["items"], f"{path}[{index}]")

        if isinstance(instance, str) and len(instance) < rule.get("minLength", 0):
            raise AssertionError(f"{path}: string is shorter than minLength")
        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in rule and instance < rule["minimum"]:
                raise AssertionError(f"{path}: number is below minimum {rule['minimum']}")

    validate(value, schema, "$")
